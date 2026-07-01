"""CLI `atlas issue …` + глагол `atlas task handoff` — структурированные жалобы и
передача задачи агент→агент с БЛОКИРУЮЩЕЙ валидацией полноты (issuekit).

Главный кейс мультиагентности: один агент сдаёт задачу другому → тело передачи
(шаблон issuekit handoff: что сделано / осталось / как проверить / ЦКП / контекст)
проверяется ``issuekit.lint`` ПЕРЕД записью — неполную не пускаем (как обязательный
ЦКП у задачи). Регистрируется импортом модуля (см. ``atlas/cli.py``).
"""
from __future__ import annotations

import json as _json
from typing import Any, Optional

import typer
from clikit import CliError, command, emit_data, emit_table
from issuekit import lint, list_kinds, new_template
from rich.console import Console
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas._time import local_now
from atlas.commands.task import _resolve_task_or_die, task_app
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.lease import resolve_actor
from atlas.models import ActionLog, Issue, Participant, Task
from atlas.slugs import generate_unique_slug, slugify_text

issue_app = typer.Typer(
    no_args_is_help=True,
    help="Структурированные жалобы (bug/feature/handoff) + валидатор полноты (issuekit).",
)
console = Console()


def _db_url() -> str:
    return resolve_db_url()


def _read_body(body: Optional[str], body_file: Optional[str]) -> str:
    import sys
    from pathlib import Path

    if body_file:
        return Path(body_file).read_text(encoding="utf-8")
    if body == "-":
        return sys.stdin.read()
    if body is not None:
        return body
    raise CliError("no_body", "Нужно --body '<текст>' / --body - (stdin) / --body-file <md> "
                             "(шаблон: atlas issue template --kind <k>).")


def _validate_or_die(body: str, kind: str) -> None:
    """Блокирующая проверка полноты жалобы через issuekit (неполную не пускаем)."""
    if kind not in list_kinds():
        raise CliError("bad_kind", f"kind '{kind}': {', '.join(list_kinds())}.")
    res = lint(body, kind)
    if not res.ok:
        raise CliError(
            "incomplete",
            f"Жалоба неполная (балл {res.score}). Не хватает обязательного: "
            f"{', '.join(res.missing)}. Добей секции "
            f"(шаблон: atlas issue template --kind {kind}).",
        )


def _slug_exists(session: Session):
    def _c(s: str) -> bool:
        return session.execute(
            select(Issue.id).where(Issue.slug == s)
        ).scalar_one_or_none() is not None
    return _c


def _resolve_issue_or_die(session: Session, ref: str) -> Issue:
    it = session.execute(select(Issue).where(Issue.slug == ref)).scalar_one_or_none()
    if it is not None:
        return it
    it = session.get(Issue, ref)
    if it is not None:
        return it
    if len(ref) >= 7:
        matches = session.execute(
            select(Issue).where(Issue.id.like(f"{ref}%"))
        ).scalars().all()
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise CliError("ambiguous_ref", f"Неоднозначный ref '{ref}'.")
    raise CliError("not_found", f"Issue '{ref}' не найден.")


def _issue_data(session: Session, it: Issue) -> dict[str, Any]:
    task = session.get(Task, it.task_id) if it.task_id else None
    parts = {p.id: p.slug for p in session.execute(select(Participant)).scalars().all()}
    return {
        "ref": it.slug or it.id[:8], "id": it.id, "kind": it.kind, "title": it.title,
        "status": it.status, "task": (task.number if task else None),
        "author": parts.get(it.author_id), "target": parts.get(it.target_id),
        "body": it.body,
    }


def _log(session: Session, action: str, issue_id: str, details: dict, actor_id) -> None:
    session.add(ActionLog(
        actor_id=actor_id, entity_type="issue", entity_id=issue_id,
        action=action, details_json=_json.dumps(details, ensure_ascii=False, default=str),
    ))


def _create_issue(
    session: Session, *, kind: str, title: str, body: str,
    task: Optional[Task], author_id, target_id,
) -> Issue:
    _validate_or_die(body, kind)
    slug = generate_unique_slug(slugify_text(title) or "issue", _slug_exists(session))
    it = Issue(
        slug=slug, task_id=(task.id if task else None), kind=kind, title=title,
        body=body, status="open", author_id=author_id, target_id=target_id,
    )
    session.add(it)
    session.flush()
    return it


# --------------------------------------------------------------------------- #
# atlas issue template / add / list / show / resolve                          #
# --------------------------------------------------------------------------- #


@issue_app.command("template")
@command
def template_cmd(
    kind: str = typer.Option("handoff", "--kind", "-k", help="bug | feature | handoff."),
    title: Optional[str] = typer.Option(None, "--title"),
) -> None:
    """Вывести пустой шаблон жалобы (markdown) — заполни и подай в issue add/handoff."""
    if kind not in list_kinds():
        raise CliError("bad_kind", f"kind '{kind}': {', '.join(list_kinds())}.")
    print(new_template(kind, title=title), end="")


@issue_app.command("add")
@command
def add_cmd(
    kind: str = typer.Option("bug", "--kind", "-k", help="bug | feature | handoff."),
    title: str = typer.Option(..., "--title"),
    task: Optional[str] = typer.Option(None, "--task", help="Привязать к задаче (ref)."),
    body: Optional[str] = typer.Option(None, "--body", help="Тело ('-' — stdin)."),
    body_file: Optional[str] = typer.Option(None, "--body-file", help="Файл тела (md)."),
    actor: Optional[str] = typer.Option(None, "--actor", help="Автор (slug)."),
) -> None:
    """Завести структурированную жалобу (валидируется issuekit — неполную блокирует)."""
    text = _read_body(body, body_file)
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        t = _resolve_task_or_die(session, task) if task else None
        author = resolve_actor(session, actor)
        it = _create_issue(session, kind=kind, title=title, body=text,
                            task=t, author_id=author.id, target_id=None)
        _log(session, "issue_created", it.id, {"kind": kind, "task": task}, author.id)
        session.commit()
        data = _issue_data(session, it)
    emit_data(data, text_renderer=lambda d: console.print(
        f"[green]✓ Issue {d['ref']}[/green] ({d['kind']}) — {d['title']}"
    ))


@issue_app.command("list")
@command
def list_cmd(
    task: Optional[str] = typer.Option(None, "--task", help="Только этой задачи."),
    status: str = typer.Option("open", "--status", help="open | resolved | wontfix | all."),
    kind: Optional[str] = typer.Option(None, "--kind", help="Фильтр по виду."),
) -> None:
    """Список жалоб (фильтры по задаче/статусу/виду)."""
    engine = make_engine(_db_url())
    rows: list[dict[str, Any]] = []
    with make_session(engine) as session:
        q = select(Issue).where(Issue.archived_at.is_(None))
        if status != "all":
            q = q.where(Issue.status == status)
        if kind:
            q = q.where(Issue.kind == kind)
        if task:
            t = _resolve_task_or_die(session, task)
            q = q.where(Issue.task_id == t.id)
        for it in session.execute(q.order_by(Issue.created_at.desc())).scalars().all():
            d = _issue_data(session, it)
            rows.append({"ref": d["ref"], "kind": d["kind"], "title": d["title"][:48],
                         "task": d["task"] or "—", "status": d["status"],
                         "target": d["target"] or "—"})
    emit_table(
        rows, title=f"Issues ({len(rows)})",
        columns=[
            {"key": "ref", "header": "ref", "style": "cyan"},
            {"key": "kind", "header": "вид", "justify": "center"},
            {"key": "title", "header": "заголовок", "style": "white"},
            {"key": "task", "header": "задача", "justify": "center"},
            {"key": "target", "header": "кому", "style": "green"},
            {"key": "status", "header": "статус"},
        ],
        empty_message="[yellow]Issues нет.[/yellow]",
    )


@issue_app.command("show")
@command
def show_cmd(ref: str = typer.Argument(..., help="slug | UUID issue")) -> None:
    """Карточка жалобы (с телом)."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        data = _issue_data(session, _resolve_issue_or_die(session, ref))
    emit_data(data, text_renderer=lambda d: (
        console.print(f"[bold]{d['ref']}[/bold] ({d['kind']}, {d['status']}) — {d['title']}"),
        console.print(f"  задача: {d['task'] or '—'} · автор: {d['author'] or '—'}"
                      f" · кому: {d['target'] or '—'}"),
        console.print(f"\n{d['body']}"),
    ))


@issue_app.command("resolve")
@command
def resolve_cmd(
    ref: str = typer.Argument(..., help="slug | UUID issue"),
    wontfix: bool = typer.Option(False, "--wontfix", help="Закрыть как wontfix."),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Закрыть жалобу (resolved / --wontfix)."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        it = _resolve_issue_or_die(session, ref)
        act = resolve_actor(session, actor)
        it.status = "wontfix" if wontfix else "resolved"
        it.resolved_at = local_now()
        _log(session, "issue_resolved", it.id, {"status": it.status}, act.id)
        session.commit()
        ref_out = it.slug or it.id[:8]
        st = it.status
    emit_data({"ref": ref_out, "status": st},
              text_renderer=lambda d: console.print(f"[green]✓ Issue {d['ref']} → {d['status']}[/green]"))


# --------------------------------------------------------------------------- #
# atlas task handoff — передача задачи агент→агент (на task_app)               #
# --------------------------------------------------------------------------- #


@task_app.command("handoff")
@command
def handoff_cmd(
    ref: str = typer.Argument(..., help="задача (number | slug | UUID)"),
    to: str = typer.Option(..., "--to", help="кому передаём (participant slug)."),
    title: Optional[str] = typer.Option(None, "--title", help="Заголовок передачи."),
    body: Optional[str] = typer.Option(None, "--body", help="Тело передачи ('-' — stdin)."),
    body_file: Optional[str] = typer.Option(None, "--body-file", help="Файл тела (md)."),
    actor: Optional[str] = typer.Option(None, "--actor", help="Кто сдаёт (slug)."),
) -> None:
    """Передать задачу другому агенту с БОГАТЫМ контекстом (handoff-issue).

    Тело (шаблон issuekit handoff: что сделано / осталось / как проверить / ЦКП /
    контекст) валидируется БЛОКИРУЮЩЕ — неполную передачу не пускаем. Создаёт
    issue(handoff), переназначает задачу на ``--to`` и снимает lease сдающего."""
    from atlas import lease as L

    text = _read_body(body, body_file)
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        task = _resolve_task_or_die(session, ref)
        author = resolve_actor(session, actor)
        target = resolve_actor(session, to)
        htitle = title or f"Передача задачи #{task.number}: {task.title}"
        it = _create_issue(session, kind="handoff", title=htitle, body=text,
                           task=task, author_id=author.id, target_id=target.id)
        # переназначить + снять lease сдающего (принимающий сделает task start)
        task.assignee_id = target.id
        task.last_touched_at = local_now()
        try:
            L.release_task(session, task, author)
        except Exception:
            pass
        _log(session, "task_handoff", it.id,
             {"task": task.number, "from": author.slug, "to": target.slug}, author.id)
        session.commit()
        out = {"ok": True, "issue": it.slug or it.id[:8], "task": task.number,
               "to": target.slug, "title": htitle}
    emit_data(out, text_renderer=lambda d: console.print(
        f"[green]✓ Задача #{d['task']} передана → {d['to']}[/green] "
        f"(handoff {d['issue']}). Принимающий: atlas issue show {d['issue']} → task start {d['task']}."
    ))
