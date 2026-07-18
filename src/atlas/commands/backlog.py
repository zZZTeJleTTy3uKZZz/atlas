"""CLI `atlas backlog …` — пул идей-интейка (DB-first) → преобразование в задачу/проект.

`backlog` = сырьё ДО задачи: лёгкая запись (ЦКП не обязателен, проект опционален —
global-пул «между проектами»). Просматривается отдельно от задач и **конвертируется**
в `todo`-задачу (ЦКП появляется тут) или зачаток проекта.

Унификация: `backlog list` показывает И новые backlog_items, И legacy-проекты уровня
идеи/инбокса (`entity_kind in idea,inbox`) — единый вид без деструктивной миграции.
Команды `atlas idea`/`atlas inbox` остаются (deprecated-указатель на `backlog`).
"""
from __future__ import annotations

from typing import Any, Optional

import typer
from clikit import CliError, command, emit_data, emit_table
from rich.console import Console
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from atlas._time import local_now
from atlas.appconfig import default_actor, load_config
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import ActionLog, BacklogItem, Participant, Project
from atlas.slugs import (
    AmbiguousRefError,
    generate_unique_slug,
    resolve_project_ref,
    slugify_text,
)

backlog_app = typer.Typer(
    no_args_is_help=True,
    help="Пул идей-интейка (DB-first): add / list / show / edit / convert (→ task|project) / archive.",
)
console = Console()

VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}


def _db_url() -> str:
    return resolve_db_url()


def _actor_id(session: Session) -> Optional[str]:
    actor = session.execute(
        select(Participant).where(Participant.slug == default_actor())
    ).scalar_one_or_none()
    return actor.id if actor else None


def _log(session: Session, action: str, item_id: str, details: dict) -> None:
    import json as _json

    session.add(ActionLog(
        actor_id=_actor_id(session), entity_type="backlog_item", entity_id=item_id,
        action=action, details_json=_json.dumps(details, ensure_ascii=False, default=str),
    ))


def _slug_exists(session: Session):
    def _check(slug: str) -> bool:
        return session.execute(
            select(BacklogItem.id).where(BacklogItem.slug == slug)
        ).scalar_one_or_none() is not None
    return _check


def _resolve_item_or_die(session: Session, ref: str) -> BacklogItem:
    """Найти BacklogItem по slug / full-UUID / short-UUID-prefix (≥7)."""
    item = session.execute(
        select(BacklogItem).where(BacklogItem.slug == ref)
    ).scalar_one_or_none()
    if item is not None:
        return item
    item = session.get(BacklogItem, ref)
    if item is not None:
        return item
    if len(ref) >= 7:
        matches = session.execute(
            select(BacklogItem).where(BacklogItem.id.like(f"{ref}%"))
        ).scalars().all()
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise CliError("ambiguous_ref", f"Неоднозначный ref '{ref}' (>1 идеи).")
    raise CliError("not_found", f"Идея '{ref}' не найдена.")


def _proj_or_die(session: Session, ref: str) -> Project:
    try:
        p = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        raise CliError("ambiguous_ref", str(exc))
    if p is None:
        raise CliError("not_found", f"Проект '{ref}' не найден.")
    return p


def _item_data(session: Session, it: BacklogItem) -> dict[str, Any]:
    proj = session.get(Project, it.project_id) if it.project_id else None
    return {
        "ref": it.slug or it.id[:8],
        "id": it.id,
        "title": it.title,
        "note": it.note,
        "project": proj.slug if proj else None,
        "scope": "project" if proj else "global",
        "priority": it.priority,
        "status": it.status,
        "converted_kind": it.converted_kind,
        "converted_ref": it.converted_ref,
        "source": it.source,
    }


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


@backlog_app.command("add")
@command
def add_cmd(
    title: str = typer.Option(..., "--title", help="Суть идеи."),
    note: Optional[str] = typer.Option(None, "--note", help="Тело/контекст (опц.)."),
    project: Optional[str] = typer.Option(
        None, "--project", help="Привязать к проекту (без — global-пул «между проектами»)."
    ),
    priority: Optional[str] = typer.Option(None, "--priority", help="P0|P1|P2|P3 (опц.)."),
    slug: Optional[str] = typer.Option(None, "--slug", help="Явный slug (иначе из title)."),
    source: str = typer.Option(
        "native", "--source",
        help="Источник: native | inbox (сырьё-свалка на разбор) | др. (виден в list)."
    ),
    md: bool = typer.Option(False, "--md", help="Зарезервировать md_path (материализация позже)."),
) -> None:
    """Завести идею в пул backlog (global или привязанную к проекту)."""
    if priority and priority not in VALID_PRIORITIES:
        raise CliError("bad_priority", f"priority '{priority}': P0|P1|P2|P3.")
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        project_id = None
        if project:
            project_id = _proj_or_die(session, project).id
        base = slug or slugify_text(title)
        final_slug = generate_unique_slug(base, _slug_exists(session)) if base else None
        item = BacklogItem(
            title=title, note=note, project_id=project_id, priority=priority,
            slug=final_slug, status="open", source=source,
            md_path=("(reserved)" if md else None),
        )
        session.add(item)
        session.flush()
        _log(session, "backlog_added", item.id,
             {"title": title, "project": project, "scope": "project" if project_id else "global"})
        session.commit()
        data = _item_data(session, item)
    emit_data(data, text_renderer=lambda d: console.print(
        f"[green]✓ Идея в backlog:[/green] {d['ref']} — {d['title']} "
        f"[grey50]({d['scope']}{'/' + d['project'] if d['project'] else ''})[/grey50]"
    ))


# --------------------------------------------------------------------------- #
# list (унифицированный вид: backlog_items + legacy idea/inbox)               #
# --------------------------------------------------------------------------- #


@backlog_app.command("list")
@command
def list_cmd(
    project: Optional[str] = typer.Option(None, "--project", help="Только этого проекта."),
    glob: bool = typer.Option(False, "--global", help="Только global-идеи (без проекта)."),
    status: str = typer.Option("open", "--status", help="open | converted | archived | all."),
) -> None:
    """Список идей: новые backlog_items + legacy idea/inbox-проекты (единый вид)."""
    engine = make_engine(_db_url())
    rows: list[dict[str, Any]] = []
    with make_session(engine) as session:
        proj_id = _proj_or_die(session, project).id if project else None
        q = select(BacklogItem).where(BacklogItem.archived_at.is_(None))
        if status != "all":
            q = q.where(BacklogItem.status == status)
        if proj_id:
            q = q.where(BacklogItem.project_id == proj_id)
        elif glob:
            q = q.where(BacklogItem.project_id.is_(None))
        for it in session.execute(q.order_by(BacklogItem.created_at.desc())).scalars().all():
            d = _item_data(session, it)
            rows.append({"ref": d["ref"], "title": d["title"][:50], "scope": d["scope"],
                         "project": d["project"] or "—", "priority": d["priority"] or "—",
                         "status": d["status"], "source": d["source"]})

        # legacy idea/inbox-проекты в том же виде (унификация без миграции).
        if status in ("open", "all") and not glob:
            lq = select(Project).where(
                Project.archived_at.is_(None),
                or_(Project.entity_kind == "idea", Project.entity_kind == "inbox"),
            )
            if proj_id:
                lq = lq.where(Project.id == proj_id)
            for p in session.execute(lq).scalars().all():
                rows.append({"ref": p.slug, "title": (p.name or "")[:50], "scope": "global",
                             "project": "—", "priority": p.priority or "—",
                             "status": "open", "source": f"legacy-{p.entity_kind}"})

    emit_table(
        rows,
        title=f"Backlog — идеи ({len(rows)})",
        columns=[
            {"key": "ref", "header": "ref", "style": "cyan", "no_wrap": True},
            {"key": "title", "header": "идея", "style": "white"},
            {"key": "scope", "header": "scope", "justify": "center"},
            {"key": "project", "header": "проект", "style": "grey62"},
            {"key": "priority", "header": "P", "justify": "center"},
            {"key": "source", "header": "источник", "style": "dim"},
        ],
        empty_message="[yellow]Идей в backlog нет. Заведи: atlas backlog add --title …[/yellow]",
    )


# --------------------------------------------------------------------------- #
# show / edit / archive                                                       #
# --------------------------------------------------------------------------- #


@backlog_app.command("show")
@command
def show_cmd(ref: str = typer.Argument(..., help="slug | UUID идеи")) -> None:
    """Карточка идеи."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        item = _resolve_item_or_die(session, ref)
        data = _item_data(session, item)
    emit_data(data, text_renderer=lambda d: (
        console.print(f"[bold]{d['ref']}[/bold] — {d['title']}"),
        console.print(f"  scope: {d['scope']}" + (f" / {d['project']}" if d['project'] else "")),
        console.print(f"  priority: {d['priority'] or '—'} · status: {d['status']}"
                      + (f" → {d['converted_kind']} {d['converted_ref']}" if d['converted_ref'] else "")),
        console.print(f"\n{d['note']}" if d['note'] else ""),
    ))


@backlog_app.command("edit")
@command
def edit_cmd(
    ref: str = typer.Argument(..., help="slug | UUID идеи"),
    title: Optional[str] = typer.Option(None, "--title"),
    note: Optional[str] = typer.Option(None, "--note"),
    project: Optional[str] = typer.Option(None, "--project", help="Привязать к проекту."),
    priority: Optional[str] = typer.Option(None, "--priority"),
) -> None:
    """Правка полей идеи."""
    if priority and priority not in VALID_PRIORITIES:
        raise CliError("bad_priority", f"priority '{priority}': P0|P1|P2|P3.")
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        item = _resolve_item_or_die(session, ref)
        if title is not None:
            item.title = title
        if note is not None:
            item.note = note
        if project is not None:
            item.project_id = _proj_or_die(session, project).id
        if priority is not None:
            item.priority = priority
        item.updated_at = local_now()
        session.commit()
        data = _item_data(session, item)
    emit_data(data, text_renderer=lambda d: console.print(f"[green]✓ Обновлено:[/green] {d['ref']}"))


@backlog_app.command("archive")
@command
def archive_cmd(
    ref: str = typer.Argument(..., help="slug | UUID идеи"),
    hard: bool = typer.Option(False, "--hard", help="Удалить навсегда (по умолч. soft)."),
) -> None:
    """Архивировать идею (soft по умолчанию)."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        item = _resolve_item_or_die(session, ref)
        ref_out = item.slug or item.id[:8]
        if hard:
            session.delete(item)
        else:
            item.status = "archived"
            item.archived_at = local_now()
        _log(session, "backlog_archived", item.id, {"hard": hard})
        session.commit()
    emit_data({"ref": ref_out, "archived": True, "hard": hard},
              text_renderer=lambda d: console.print(f"[green]✓ Архивирована:[/green] {d['ref']}"))


# --------------------------------------------------------------------------- #
# convert → task | project                                                    #
# --------------------------------------------------------------------------- #


@backlog_app.command("convert")
@command
def convert_cmd(
    ref: str = typer.Argument(..., help="slug | UUID идеи"),
    as_: str = typer.Option("task", "--as", help="task (→ todo-задача) | project (зачаток)."),
    project: Optional[str] = typer.Option(
        None, "--project", help="Проект задачи (для --as task; иначе берётся из идеи)."
    ),
    cpp: Optional[str] = typer.Option(None, "--cpp", help="ЦКП задачи (обязателен для --as task)."),
    priority: Optional[str] = typer.Option(None, "--priority", help="P0|P1|P2|P3."),
    no_review: bool = typer.Option(False, "--no-review", help="Не заводить reviewer у задачи (для --as task)."),
    type_: Optional[str] = typer.Option(None, "--type", help="Тип проекта (для --as project)."),
    slug: Optional[str] = typer.Option(None, "--slug", help="slug нового проекта."),
    setup_layout: bool = typer.Option(
        True, "--setup-layout/--no-setup-layout",
        help="[--as project] создать _storage/<slug>/ + junction (как `idea promote`)."
    ),
    canonical: bool = typer.Option(
        True, "--canonical/--no-canonical",
        help="[--as project] дописать README/AGENTS/.gitignore в _storage/<slug>/."
    ),
    init_git: bool = typer.Option(
        False, "--init-git/--no-init-git", help="[--as project] git init + remote + push."
    ),
    private: bool = typer.Option(True, "--private/--public", help="[--as project + --init-git]."),
    group: Optional[str] = typer.Option(None, "--group", help="[--as project + --init-git] git-namespace."),
) -> None:
    """Преобразовать идею в `todo`-задачу или зачаток проекта; идея → converted.

    `--as project` материализует проект как прежний `idea promote`: layout/junction
    (`--setup-layout`), canonical-файлы (`--canonical`), опц. git (`--init-git`)."""
    if as_ not in ("task", "project"):
        raise CliError("bad_as", "--as: task | project.")
    if priority and priority not in VALID_PRIORITIES:
        raise CliError("bad_priority", f"priority '{priority}': P0|P1|P2|P3.")
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        item = _resolve_item_or_die(session, ref)
        if item.status == "archived" or item.archived_at is not None:
            raise CliError("archived", f"Идея '{ref}' архивирована — нельзя преобразовать.")
        if item.status == "converted":
            raise CliError("already", f"Идея '{ref}' уже преобразована "
                                      f"({item.converted_kind} {item.converted_ref}).")
        cfg = load_config()
        if as_ == "task":
            result = _convert_to_task(session, cfg, item, project, cpp, priority, no_review)
        else:
            result = _convert_to_project(
                session, item, type_, slug, priority,
                setup_layout=setup_layout, canonical=canonical,
                init_git=init_git, private=private, group=group,
            )
        item.status = "converted"
        item.converted_kind = as_
        item.converted_ref = str(result["ref"])
        item.updated_at = local_now()
        _log(session, "backlog_converted", item.id, {"as": as_, "ref": result["ref"]})
        session.commit()
        out = {"ok": True, "idea": item.slug or item.id[:8], "as": as_, **result}
    emit_data(out, text_renderer=lambda d: console.print(
        f"[green]✓ Идея '{d['idea']}' → {d['as']}:[/green] {d['ref']} ({d.get('title', '')})"
    ))


def _convert_to_task(session, cfg, item, project, cpp, priority, no_review) -> dict[str, Any]:
    from atlas.commands.task import _create_one_task

    if not cpp:
        raise CliError("no_cpp", "--as task требует --cpp (ЦКП задачи — измеримый результат).")
    # Резолвим проект ЗДЕСЬ через _proj_or_die (CliError → чистый JSON-контракт),
    # а не внутри _create_one_task (там _resolve_project_or_die → typer.Exit).
    if project is not None:
        proj_slug = _proj_or_die(session, project).slug
    elif item.project_id is not None:
        proj_slug = session.get(Project, item.project_id).slug
    else:
        raise CliError("no_project", "Идея global — укажи --project для задачи.")
    spec = {
        "project": proj_slug, "title": item.title, "cpp": cpp,
        "priority": priority or item.priority, "status": "todo",
        "description": item.note, "no_review": no_review,
    }
    created = _create_one_task(session, cfg, spec, idx=1)
    return {"ref": created["number"], "title": created["title"], "task_slug": created["slug"]}


def _convert_to_project(
    session, item, type_, slug, priority, *,
    setup_layout: bool = True, canonical: bool = True,
    init_git: bool = False, private: bool = True, group=None,
) -> dict[str, Any]:
    from atlas.models import ProjectStatus, ProjectType
    from atlas.slugs import generate_prefix_from_slug
    from atlas.slugs import generate_unique_slug as _gus

    type_slug = type_ or "personal-project"
    pt = session.execute(
        select(ProjectType).where(ProjectType.slug == type_slug)
    ).scalar_one_or_none()
    if pt is None:
        raise CliError("bad_type", f"Тип проекта '{type_slug}' не найден "
                                   f"(см. atlas type list); укажи --type.")
    ps = session.execute(
        select(ProjectStatus).order_by(ProjectStatus.order_idx)
    ).scalars().first()
    if ps is None:
        raise CliError("no_status", "Нет статусов проектов (seed). ")

    def _pslug_exists(s: str) -> bool:
        return session.execute(
            select(Project.id).where(Project.slug == s)
        ).scalar_one_or_none() is not None

    base = slug or slugify_text(item.title)
    final = _gus(base, _pslug_exists) if base else None
    if final is None:
        raise CliError("slug_gen", "Не сгенерить slug проекта — задай --slug.")

    # prefix (для будущих task-slug'ов проекта) — как `project add` / прежний `idea add`.
    from atlas.commands.projects import _generate_unique_prefix
    try:
        prefix = _generate_unique_prefix(session, generate_prefix_from_slug(final))
    except Exception:
        prefix = None

    proj = Project(
        slug=final, prefix=prefix, name=item.title, description=item.note,
        one_line_summary=(item.note or item.title)[:200],
        type_id=pt.id, status_id=ps.id,
        priority=priority or item.priority or "P2",
        entity_kind="project",
    )
    session.add(proj)
    session.flush()

    result: dict[str, Any] = {"ref": proj.slug, "title": proj.name}

    # ── Материализация (эквивалент прежнего `idea promote`) — best-effort ──
    storage = None
    if setup_layout:
        from atlas.commands.projects import _setup_storage_and_junction
        try:
            logical, storage, _junction = _setup_storage_and_junction(proj.slug, type_slug)
            proj.local_path = str(logical)
            result["storage"] = str(storage)
        except Exception as exc:  # layout best-effort — проект уже создан в БД
            result["layout_error"] = str(exc)
            storage = None

    if canonical and storage is not None:
        from atlas.commands.projects import (
            _create_canonical_files,
            _ensure_atlas_prompt_in_dir,
        )
        try:
            created = _create_canonical_files(
                storage, project=proj, type_slug=type_slug,
                status_slug=ps.slug, tag_slugs=[], logical_rel=proj.slug,
            )
            if created:
                result["canonical_files"] = list(created)
            _ensure_atlas_prompt_in_dir(storage)
        except Exception as exc:
            result["canonical_error"] = str(exc)

    if init_git and storage is not None:
        from atlas.commands.projects_git import DEFAULT_COMMIT_MESSAGE, perform_git_init
        try:
            git_result = perform_git_init(
                session, proj, group=group, private=private,
                commit_message=DEFAULT_COMMIT_MESSAGE,
            )
            result["git_url"] = git_result.get("url")
        except Exception as exc:
            result["git_error"] = str(exc)

    if not setup_layout:
        result["hint"] = "провижн/раскладку позже — atlas project layout init / git init"
    return result
