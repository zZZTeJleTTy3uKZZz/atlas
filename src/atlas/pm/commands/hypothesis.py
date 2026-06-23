"""CLI-команды `atlas hypothesis ...`.

Atlas Hypothesis Ledger (Подсистема 1 hypothesis-lab): реестр гипотез +
эффективность. Цикл «опыт → гипотеза → эксперимент → замер → решение».

Команды:
- ``add``    — создать гипотезу (slug/number авто или явно). Резолв project
               обязателен; task — опц. (проверяем, что в том же проекте).
- ``list``   — список гипотез (фильтры по project / status / verdict /
               confidence / archived).
- ``get``    — карточка гипотезы (по number, slug, full UUID или short prefix).
- ``update`` — изменить поля; status-переходы авто-timestamp (testing→tested_at,
               closed→closed_at).
- ``close``  — обёртка: status=closed + verdict + closed_at + опц. поля замера.
- ``delete`` — soft archive (по умолчанию) или ``--hard`` для физ. удаления.

Паттерн — как `pm_tasks.py` (typer sub-app, резолверы, _log_action,
build_task_slug + generate_unique_slug, number через max+1).
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import typer
from clikit import command, emit_data, emit_table, is_json
from rich.console import Console
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlas.pm._time import local_now
from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.models import (
    ActionLog,
    Hypothesis,
    Participant,
    Project,
)
from atlas.pm.slugs import (
    AmbiguousRefError,
    SlugGenerationError,
    UUID_SHORT_MIN,
    _is_full_uuid,
    _looks_like_uuid_prefix,
    build_task_slug,
    generate_unique_slug,
    resolve_project_ref,
    resolve_task_ref,
    slugify_text,
)

hypothesis_app = typer.Typer(
    no_args_is_help=True,
    help="Hypotheses: реестр гипотез (Atlas Hypothesis Ledger), CRUD.",
)
console = Console()

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

VALID_STATUSES = {"draft", "testing", "measured", "closed"}
VALID_CONFIDENCE = {"H", "M", "L"}
VALID_VERDICTS = {"accept", "reject", "iterate", "inconclusive"}
SLUG_PART_RE = re.compile(r"^[a-z0-9-]{2,50}$")
DEFAULT_ACTOR_SLUG = "dmitry"


# --------------------------------------------------------------------------- #
# DB helpers                                                                  #
# --------------------------------------------------------------------------- #


def _db_url() -> str:
    return resolve_db_url()


def _actor_id(session: Session) -> Optional[str]:
    actor = session.execute(
        select(Participant).where(Participant.slug == DEFAULT_ACTOR_SLUG)
    ).scalar_one_or_none()
    return actor.id if actor else None


def _log_action(
    session: Session,
    *,
    action: str,
    entity_id: str,
    details: dict[str, Any],
) -> None:
    entry = ActionLog(
        actor_id=_actor_id(session),
        entity_type="hypothesis",
        entity_id=entity_id,
        action=action,
        details_json=json.dumps(details, ensure_ascii=False, default=str),
    )
    session.add(entry)


def _slug_exists_fn(session: Session):
    def _check(candidate: str) -> bool:
        return session.execute(
            select(Hypothesis.id).where(Hypothesis.slug == candidate)
        ).scalar_one_or_none() is not None
    return _check


def _next_hypothesis_number(session: Session) -> int:
    """Следующий свободный глобальный номер = MAX(Hypothesis.number) + 1."""
    current_max = session.execute(select(func.max(Hypothesis.number))).scalar()
    if current_max is None:
        return 1
    return int(current_max) + 1


def _resolve_hypothesis_ref(session: Session, ref: str) -> Optional[Hypothesis]:
    """Найти Hypothesis по number / slug / UUID full / UUID short prefix.

    Семантика — как у resolve_task_ref: короткий int = number; длинная hex-строка
    пробуется как UUID prefix, затем fallback на number.
    """
    if not ref:
        return None

    # 1. Короткое число → number
    if ref.isdigit() and len(ref) < UUID_SHORT_MIN:
        n = int(ref)
        return session.execute(
            select(Hypothesis).where(Hypothesis.number == n)
        ).scalar_one_or_none()

    # 2. Slug (точное совпадение)
    h = session.execute(
        select(Hypothesis).where(Hypothesis.slug == ref)
    ).scalar_one_or_none()
    if h is not None:
        return h

    # 3. Full UUID
    if _is_full_uuid(ref):
        return session.execute(
            select(Hypothesis).where(Hypothesis.id == ref)
        ).scalar_one_or_none()

    # 4. UUID short prefix
    if len(ref) >= UUID_SHORT_MIN and _looks_like_uuid_prefix(ref):
        matches = session.execute(
            select(Hypothesis).where(Hypothesis.id.like(f"{ref}%"))
        ).scalars().all()
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousRefError(
                f"UUID prefix '{ref}' матчит {len(matches)} гипотез; "
                "уточни больше символов"
            )

    # 5. Fallback: длинное число → number
    if ref.isdigit():
        n = int(ref)
        return session.execute(
            select(Hypothesis).where(Hypothesis.number == n)
        ).scalar_one_or_none()

    return None


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def _validate_slug_part(slug: str) -> None:
    if not SLUG_PART_RE.match(slug):
        console.print(
            f"[red]Невалидный slug '{slug}': допустимы [a-z0-9-], длина 2-50.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_status(status: str) -> None:
    if status not in VALID_STATUSES:
        console.print(
            f"[red]Невалидный status '{status}': "
            f"допустимы {sorted(VALID_STATUSES)}.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_confidence(conf: str) -> None:
    if conf not in VALID_CONFIDENCE:
        console.print(
            f"[red]Невалидный confidence '{conf}': "
            f"допустимы {sorted(VALID_CONFIDENCE)}.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_verdict(verdict: str) -> None:
    if verdict not in VALID_VERDICTS:
        console.print(
            f"[red]Невалидный verdict '{verdict}': "
            f"допустимы {sorted(VALID_VERDICTS)}.[/red]"
        )
        raise typer.Exit(code=1)


def _resolve_project_or_die(session: Session, ref: str) -> Project:
    try:
        proj = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if proj is None:
        console.print(f"[red]Project '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return proj


def _resolve_task_or_die(session: Session, ref: str):
    try:
        task = resolve_task_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if task is None:
        console.print(f"[red]Task '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return task


def _resolve_hypothesis_or_die(session: Session, ref: str) -> Hypothesis:
    try:
        h = _resolve_hypothesis_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if h is None:
        console.print(f"[red]Hypothesis '{ref}' не найдена.[/red]")
        raise typer.Exit(code=1)
    return h


def _apply_status_timestamps(h: Hypothesis, new_status: str, diffs: dict) -> None:
    """status-переходы авто-timestamp: testing→tested_at, closed→closed_at."""
    now = local_now()
    if new_status == "testing" and h.tested_at is None:
        h.tested_at = now
        diffs["tested_at"] = {"old": None, "new": now}
    elif new_status == "closed" and h.closed_at is None:
        h.closed_at = now
        diffs["closed_at"] = {"old": None, "new": now}


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


@hypothesis_app.command("add")
@command
def add_cmd(
    project: str = typer.Option(..., "--project", help="Project ref (slug | UUID)"),
    title: str = typer.Option(..., "--title", help="Короткое имя гипотезы"),
    statement: Optional[str] = typer.Option(
        None, "--statement",
        help="«если X, то метрика Y ↑ на Z, потому что <механизм>»",
    ),
    metric: Optional[str] = typer.Option(None, "--metric", help="Какую метрику двигаем"),
    baseline: Optional[str] = typer.Option(None, "--baseline", help="Стартовое значение"),
    target: Optional[str] = typer.Option(None, "--target", help="Порог принятия"),
    method: Optional[str] = typer.Option(
        None, "--method", help="Как тестируем (A/B, до/после, выборка, срок)"
    ),
    task: Optional[str] = typer.Option(
        None, "--task", help="Task ref (опц. связь, в том же проекте)"
    ),
    confidence: str = typer.Option("M", "--confidence", help="H | M | L"),
    status: str = typer.Option("draft", "--status", help="draft|testing|measured|closed"),
    slug: Optional[str] = typer.Option(
        None, "--slug",
        help="Часть slug после prefix-проекта ([a-z0-9-], 2-50). Авто из --title.",
    ),
) -> None:
    """Создать гипотезу."""
    _validate_confidence(confidence)
    _validate_status(status)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        proj = _resolve_project_or_die(session, project)
        if proj.prefix is None:
            console.print(
                f"[red]У проекта '{proj.slug}' нет prefix — "
                f"нельзя сгенерировать hypothesis slug.[/red]"
            )
            raise typer.Exit(code=1)

        # ----- task (опц.) -----
        task_obj = None
        if task:
            task_obj = _resolve_task_or_die(session, task)
            if task_obj.project_id != proj.id:
                console.print(
                    f"[red]Task '{task}' принадлежит другому проекту — "
                    f"гипотеза и задача должны быть в одном проекте.[/red]"
                )
                raise typer.Exit(code=1)

        # ----- slug -----
        slug_auto = False
        if slug:
            _validate_slug_part(slug)
            final_slug = build_task_slug(proj.prefix, slug)
            if _slug_exists_fn(session)(final_slug):
                console.print(
                    f"[red]Slug '{final_slug}' занят. "
                    f"Попробуйте '{slug}-2' или другой.[/red]"
                )
                raise typer.Exit(code=1)
        else:
            base_part = slugify_text(title)
            if not base_part:
                console.print(
                    f"[red]Не удалось сгенерировать slug из '{title}': "
                    f"передайте --slug явно.[/red]"
                )
                raise typer.Exit(code=1)
            base_full = build_task_slug(proj.prefix, base_part)
            try:
                final_slug = generate_unique_slug(base_full, _slug_exists_fn(session))
            except SlugGenerationError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            slug_auto = True

        # ----- number -----
        number = _next_hypothesis_number(session)

        # ----- create -----
        hyp = Hypothesis(
            number=number,
            slug=final_slug,
            project_id=proj.id,
            task_id=task_obj.id if task_obj else None,
            title=title,
            statement=statement,
            metric=metric,
            baseline=baseline,
            target=target,
            method=method,
            confidence=confidence,
            status=status,
        )
        # авто-timestamp если стартуем уже в testing/closed
        _apply_status_timestamps(hyp, status, {})
        session.add(hyp)
        session.flush()

        _log_action(
            session,
            action="hypothesis_created",
            entity_id=hyp.id,
            details={
                "number": number,
                "slug": final_slug,
                "project": proj.slug,
                "title": title,
                "status": status,
                "confidence": confidence,
                "task": task_obj.slug if task_obj else None,
            },
        )
        session.commit()

        def _render(d: dict) -> None:
            if d["slug_auto"]:
                console.print(f"[dim]slug auto-generated: {d['slug']}[/dim]")
            task_part = f" · task: {d['task']}" if d["task"] else ""
            console.print(
                f"[green]✓ Hypothesis #{d['number']} '{d['slug']}' created[/green] · "
                f"{d['title']} · {d['status']} · conf={d['confidence']}{task_part}"
            )

        emit_data(
            {
                "id": hyp.id,
                "number": number,
                "slug": final_slug,
                "title": title,
                "status": status,
                "confidence": confidence,
                "project": proj.slug,
                "task": task_obj.slug if task_obj else None,
                "slug_auto": slug_auto,
            },
            text_renderer=_render,
        )


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


@hypothesis_app.command("list")
@command
def list_cmd(
    project: Optional[str] = typer.Option(None, "--project", help="Project ref"),
    status: Optional[str] = typer.Option(None, "--status"),
    verdict: Optional[str] = typer.Option(None, "--verdict"),
    confidence: Optional[str] = typer.Option(None, "--confidence"),
    archived: bool = typer.Option(
        False, "--archived/--no-archived",
        help="Показывать архивные (по умолчанию скрыты)",
    ),
) -> None:
    """Список гипотез (табличный вывод)."""
    if status is not None:
        _validate_status(status)
    if verdict is not None:
        _validate_verdict(verdict)
    if confidence is not None:
        _validate_confidence(confidence)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        # task-slug подтягиваем отдельным under-select, чтобы не плодить join
        from atlas.pm.models import Task

        stmt = (
            select(
                Hypothesis.id,
                Hypothesis.number,
                Hypothesis.slug,
                Hypothesis.title,
                Hypothesis.status,
                Hypothesis.verdict,
                Hypothesis.metric,
                Hypothesis.delta,
                Hypothesis.confidence,
                Hypothesis.archived_at,
                Project.slug.label("project_slug"),
                Task.slug.label("task_slug"),
            )
            .join(Project, Hypothesis.project_id == Project.id)
            .join(Task, Hypothesis.task_id == Task.id, isouter=True)
        )

        if project is not None:
            proj = _resolve_project_or_die(session, project)
            stmt = stmt.where(Hypothesis.project_id == proj.id)
        if status is not None:
            stmt = stmt.where(Hypothesis.status == status)
        if verdict is not None:
            stmt = stmt.where(Hypothesis.verdict == verdict)
        if confidence is not None:
            stmt = stmt.where(Hypothesis.confidence == confidence)
        if not archived:
            stmt = stmt.where(Hypothesis.archived_at.is_(None))

        rows = session.execute(stmt).all()

    # Новые сверху (number desc).
    sorted_rows = sorted(rows, key=lambda r: -(r.number or 0))

    data = []
    for row in sorted_rows:
        metric_delta = None
        if row.metric or row.delta:
            metric_delta = f"{row.metric or '—'}: {row.delta or '—'}"
        # title для text-режима: strike-маркер если archived. В JSON отдаём
        # сырые title + archived (см. columns ниже — format только для text).
        archived = row.archived_at is not None
        if archived:
            title_text = f"[strike]{row.title}[/strike] [dim](archived)[/dim]"
        else:
            title_text = row.title
        data.append({
            "number": row.number,
            "slug": row.slug,
            "title": row.title,
            "status": row.status,
            "verdict": row.verdict,
            "confidence": row.confidence,
            "metric": row.metric,
            "delta": row.delta,
            "metric_delta": metric_delta,
            "project": row.project_slug,
            "task": row.task_slug,
            "archived": archived,
            "_title_text": title_text,
        })

    emit_table(
        [{k: v for k, v in row.items() if k != "_title_text"} for row in data]
        if is_json() else data,
        columns=[
            {"key": "number", "header": "#", "justify": "right", "style": "bold",
             "format": lambda v: f"#{v}" if v else "—"},
            {"key": "slug", "header": "Slug", "style": "cyan", "no_wrap": True,
             "format": lambda v: v or "—"},
            {"key": "_title_text", "header": "Title"},
            {"key": "status", "header": "Status", "style": "magenta"},
            {"key": "verdict", "header": "Verdict", "style": "yellow",
             "format": lambda v: v or "—"},
            {"key": "confidence", "header": "C", "justify": "center", "style": "bold"},
            {"key": "metric_delta", "header": "Metric Δ", "style": "green",
             "format": lambda v: v or "—"},
            {"key": "project", "header": "Project", "style": "dim"},
            {"key": "task", "header": "Task", "style": "dim",
             "format": lambda v: v or "—"},
        ],
        title=f"Hypotheses ({len(data)})",
        empty_message="[yellow]Гипотез не найдено.[/yellow]",
    )


# --------------------------------------------------------------------------- #
# get                                                                         #
# --------------------------------------------------------------------------- #


@hypothesis_app.command("get")
@command
def get_cmd(
    ref: str = typer.Argument(..., help="number | slug | full UUID | short UUID prefix"),
) -> None:
    """Показать карточку гипотезы."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        from atlas.pm.models import Task

        hyp = _resolve_hypothesis_or_die(session, ref)

        proj = session.get(Project, hyp.project_id)
        task = session.get(Task, hyp.task_id) if hyp.task_id else None

        log_rows = session.execute(
            select(ActionLog)
            .where(ActionLog.entity_type == "hypothesis")
            .where(ActionLog.entity_id == hyp.id)
            .order_by(ActionLog.timestamp.desc())
            .limit(5)
        ).scalars().all()

        data = {
            "id": hyp.id,
            "number": hyp.number,
            "slug": hyp.slug,
            "title": hyp.title,
            "statement": hyp.statement,
            "status": hyp.status,
            "confidence": hyp.confidence,
            "verdict": hyp.verdict,
            "metric": hyp.metric,
            "baseline": hyp.baseline,
            "target": hyp.target,
            "method": hyp.method,
            "result_value": hyp.result_value,
            "delta": hyp.delta,
            "lesson": hyp.lesson,
            "consolidated_into": hyp.consolidated_into,
            "project": proj.slug if proj else None,
            "project_name": proj.name if proj else None,
            "task": (task.slug or task.id) if task else None,
            "created_at": hyp.created_at.isoformat() if hyp.created_at else None,
            "updated_at": hyp.updated_at.isoformat() if hyp.updated_at else None,
            "tested_at": hyp.tested_at.isoformat() if hyp.tested_at else None,
            "closed_at": hyp.closed_at.isoformat() if hyp.closed_at else None,
            "archived_at": hyp.archived_at.isoformat() if hyp.archived_at else None,
            "recent_activity": [
                {
                    "timestamp": e.timestamp.strftime("%Y-%m-%d %H:%M")
                    if e.timestamp else None,
                    "action": e.action,
                }
                for e in log_rows
            ],
        }

    def _render(d: dict) -> None:
        archived_marker = ""
        if d["archived_at"] is not None:
            archived_marker = (
                f"  [bold red]ARCHIVED[/bold red] ({d['archived_at'][:10]})"
            )
        number_str = f"#{d['number']}" if d["number"] else "—"
        console.print(
            f"[bold cyan]{d['slug'] or '—'}[/bold cyan]  {number_str} — "
            f"{d['title']}{archived_marker}"
        )
        console.print(f"  ID:         {d['id']}")
        console.print(f"  Number:     {number_str}")
        console.print(f"  Slug:       {d['slug'] or '—'}")
        if d["statement"]:
            console.print(f"  Statement:  {d['statement']}")
        console.print(f"  Status:     {d['status']}")
        console.print(f"  Confidence: {d['confidence']}")
        console.print(f"  Verdict:    {d['verdict'] or '—'}")
        if d["metric"]:
            console.print(f"  Metric:     {d['metric']}")
        if d["baseline"]:
            console.print(f"  Baseline:   {d['baseline']}")
        if d["target"]:
            console.print(f"  Target:     {d['target']}")
        if d["method"]:
            console.print(f"  Method:     {d['method']}")
        if d["result_value"]:
            console.print(f"  Result:     {d['result_value']}")
        if d["delta"]:
            console.print(f"  Delta:      {d['delta']}")
        if d["lesson"]:
            console.print(f"  Lesson:     {d['lesson']}")
        if d["consolidated_into"]:
            console.print(f"  Consolidated into: {d['consolidated_into']}")
        if d["project"]:
            console.print(f"  Project:    {d['project']} ({d['project_name']})")
        if d["task"]:
            console.print(f"  Task:       {d['task']}")
        else:
            console.print("  Task:       —")

        console.print(f"\n  Created:    {d['created_at']}")
        console.print(f"  Updated:    {d['updated_at']}")
        if d["tested_at"]:
            console.print(f"  Tested:     {d['tested_at']}")
        if d["closed_at"]:
            console.print(f"  Closed:     {d['closed_at']}")
        if d["archived_at"]:
            console.print(f"  Archived:   {d['archived_at']}")

        if d["recent_activity"]:
            console.print("\n[bold]Recent activity:[/bold]")
            for entry in d["recent_activity"]:
                console.print(f"  • {entry['timestamp']} — {entry['action']}")

    emit_data(data, text_renderer=_render)


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #


@hypothesis_app.command("update")
@command
def update_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    title: Optional[str] = typer.Option(None, "--title"),
    statement: Optional[str] = typer.Option(None, "--statement"),
    metric: Optional[str] = typer.Option(None, "--metric"),
    baseline: Optional[str] = typer.Option(None, "--baseline"),
    target: Optional[str] = typer.Option(None, "--target"),
    method: Optional[str] = typer.Option(None, "--method"),
    result_value: Optional[str] = typer.Option(None, "--result-value"),
    delta: Optional[str] = typer.Option(None, "--delta"),
    confidence: Optional[str] = typer.Option(None, "--confidence"),
    status: Optional[str] = typer.Option(None, "--status"),
    lesson: Optional[str] = typer.Option(None, "--lesson"),
    consolidated_into: Optional[str] = typer.Option(None, "--consolidated-into"),
    slug: Optional[str] = typer.Option(
        None, "--slug", help="ЗАПРЕЩЕНО: slug — immutable. Используй delete + add.",
    ),
    number: Optional[int] = typer.Option(
        None, "--number", help="ЗАПРЕЩЕНО: number — immutable.",
    ),
    project: Optional[str] = typer.Option(
        None, "--project", help="ЗАПРЕЩЕНО: переезд между проектами сломает slug.",
    ),
) -> None:
    """Обновить поля гипотезы (status-переходы авто-timestamp)."""
    if slug is not None:
        console.print(
            "[red]Изменение slug запрещено: slug — immutable ID. "
            "delete + add если нужно.[/red]"
        )
        raise typer.Exit(code=1)
    if number is not None:
        console.print("[red]Изменение number запрещено: number — immutable.[/red]")
        raise typer.Exit(code=1)
    if project is not None:
        console.print(
            "[red]Изменение project запрещено: сломает slug-prefix. delete + add.[/red]"
        )
        raise typer.Exit(code=1)

    if confidence is not None:
        _validate_confidence(confidence)
    if status is not None:
        _validate_status(status)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        hyp = _resolve_hypothesis_or_die(session, ref)

        diffs: dict[str, dict[str, Any]] = {}

        def _maybe_update(field: str, new_value: Any) -> None:
            if new_value is None:
                return
            old_value = getattr(hyp, field)
            if old_value != new_value:
                diffs[field] = {"old": old_value, "new": new_value}
                setattr(hyp, field, new_value)

        _maybe_update("title", title)
        _maybe_update("statement", statement)
        _maybe_update("metric", metric)
        _maybe_update("baseline", baseline)
        _maybe_update("target", target)
        _maybe_update("method", method)
        _maybe_update("result_value", result_value)
        _maybe_update("delta", delta)
        _maybe_update("confidence", confidence)
        _maybe_update("lesson", lesson)
        _maybe_update("consolidated_into", consolidated_into)

        # status — особая логика для tested_at / closed_at
        if status is not None and hyp.status != status:
            diffs["status"] = {"old": hyp.status, "new": status}
            hyp.status = status
            _apply_status_timestamps(hyp, status, diffs)

        if not diffs:
            emit_data(
                {"number": hyp.number, "slug": hyp.slug, "updated": False,
                 "diffs": {}},
                text_renderer=lambda d: console.print(
                    "[yellow]Нечего обновлять.[/yellow]"
                ),
            )
            return

        _log_action(
            session,
            action="hypothesis_updated",
            entity_id=hyp.id,
            details=diffs,
        )
        session.commit()

        def _render(d: dict) -> None:
            console.print(
                f"[green]✓ Hypothesis #{d['number']} '{d['slug']}' updated[/green] "
                f"({len(d['diffs'])} field(s))"
            )
            for field, diff in d["diffs"].items():
                console.print(
                    f"  {field}: [dim]{diff['old']}[/dim] → [bold]{diff['new']}[/bold]"
                )

        emit_data(
            {"number": hyp.number, "slug": hyp.slug, "updated": True,
             "diffs": diffs},
            text_renderer=_render,
        )


# --------------------------------------------------------------------------- #
# close                                                                       #
# --------------------------------------------------------------------------- #


@hypothesis_app.command("close")
@command
def close_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    verdict: str = typer.Option(
        ..., "--verdict", help="accept | reject | iterate | inconclusive"
    ),
    result_value: Optional[str] = typer.Option(None, "--result-value"),
    delta: Optional[str] = typer.Option(None, "--delta"),
    lesson: Optional[str] = typer.Option(None, "--lesson"),
    consolidated_into: Optional[str] = typer.Option(None, "--consolidated-into"),
) -> None:
    """Закрыть гипотезу: status=closed, closed_at=now, verdict + опц. поля замера."""
    _validate_verdict(verdict)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        hyp = _resolve_hypothesis_or_die(session, ref)

        diffs: dict[str, dict[str, Any]] = {}

        def _maybe_update(field: str, new_value: Any) -> None:
            if new_value is None:
                return
            old_value = getattr(hyp, field)
            if old_value != new_value:
                diffs[field] = {"old": old_value, "new": new_value}
                setattr(hyp, field, new_value)

        _maybe_update("result_value", result_value)
        _maybe_update("delta", delta)
        _maybe_update("lesson", lesson)
        _maybe_update("consolidated_into", consolidated_into)

        if hyp.verdict != verdict:
            diffs["verdict"] = {"old": hyp.verdict, "new": verdict}
            hyp.verdict = verdict

        if hyp.status != "closed":
            diffs["status"] = {"old": hyp.status, "new": "closed"}
            hyp.status = "closed"
        _apply_status_timestamps(hyp, "closed", diffs)

        _log_action(
            session,
            action="hypothesis_closed",
            entity_id=hyp.id,
            details=diffs,
        )
        session.commit()

        emit_data(
            {"number": hyp.number, "slug": hyp.slug, "status": "closed",
             "verdict": verdict},
            text_renderer=lambda d: console.print(
                f"[green]✓ Hypothesis #{d['number']} '{d['slug']}' closed[/green] · "
                f"verdict={d['verdict']}"
            ),
        )


# --------------------------------------------------------------------------- #
# delete                                                                      #
# --------------------------------------------------------------------------- #


@hypothesis_app.command("delete")
@command
def delete_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    hard: bool = typer.Option(
        False, "--hard",
        help="Физически удалить (после confirm). По умолчанию — soft archive.",
    ),
) -> None:
    """Удалить гипотезу (soft по умолчанию: archived_at, статус не меняется)."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        hyp = _resolve_hypothesis_or_die(session, ref)

        slug_for_msg = hyp.slug or "(no slug)"
        number_for_msg = hyp.number
        hyp_id = hyp.id

        if hard:
            confirmed = typer.confirm(
                f"Физически удалить hypothesis #{number_for_msg} '{slug_for_msg}'?"
            )
            if not confirmed:
                console.print("[yellow]Отменено.[/yellow]")
                raise typer.Exit(code=1)

            _log_action(
                session,
                action="hypothesis_deleted",
                entity_id=hyp_id,
                details={"slug": slug_for_msg, "number": number_for_msg},
            )
            session.delete(hyp)
            session.commit()
            emit_data(
                {"number": number_for_msg, "slug": slug_for_msg,
                 "deleted": True, "mode": "hard"},
                text_renderer=lambda d: console.print(
                    f"[red]✗ Hypothesis #{d['number']} '{d['slug']}' "
                    f"физически удалена.[/red]"
                ),
            )
            return

        if hyp.archived_at is not None:
            archived_at_iso = hyp.archived_at.isoformat()
            emit_data(
                {"number": number_for_msg, "slug": slug_for_msg,
                 "deleted": False, "mode": "soft", "already_archived": True,
                 "archived_at": archived_at_iso},
                text_renderer=lambda d: console.print(
                    f"[yellow]Hypothesis #{d['number']} '{d['slug']}' уже archived "
                    f"({d['archived_at']}).[/yellow]"
                ),
            )
            return

        hyp.archived_at = local_now()
        _log_action(
            session,
            action="hypothesis_archived",
            entity_id=hyp_id,
            details={
                "slug": slug_for_msg,
                "number": number_for_msg,
                "at": hyp.archived_at.isoformat(),
            },
        )
        session.commit()
        emit_data(
            {"number": number_for_msg, "slug": slug_for_msg,
             "deleted": True, "mode": "soft",
             "archived_at": hyp.archived_at.isoformat()},
            text_renderer=lambda d: console.print(
                f"[green]✓ Hypothesis #{d['number']} archived[/green]"
            ),
        )
