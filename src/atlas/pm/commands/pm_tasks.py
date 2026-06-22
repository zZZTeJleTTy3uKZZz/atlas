"""CLI-команды `atlas pm-tasks ...`.

CRUD по задачам PM-БД (NP-005).

Команды:
- ``add``     — создать задачу (slug/number авто или явно).
- ``list``    — список задач (фильтры по project / status / assignee / archived).
- ``get``     — карточка задачи (по number, slug, full UUID или short UUID prefix).
- ``update``  — изменить поля задачи (любые, кроме slug/number/project).
- ``delete``  — soft archive (по умолчанию) или ``--hard`` для физ. удаления.

Имя команды — `pm-tasks` (а не `tasks`), чтобы не конфликтовать с
существующим `atlas tasks` (Notion-таскер).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional

import typer
from clikit import command, emit_data
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from atlas.pm._time import msk_now
from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.models import (
    ActionLog,
    Epic,
    Participant,
    Project,
    Task,
)
from atlas.pm.slugs import (
    AmbiguousRefError,
    SlugGenerationError,
    build_task_slug,
    generate_unique_slug,
    next_task_number,
    resolve_project_ref,
    resolve_task_ref,
    slugify_text,
)
from atlas.pm.sync import outbox as _outbox


def _sync_portal_id() -> str:
    """Slug портала-стора этого Atlas для ``source_portal_id`` событий синка —
    из активного конфига (``cfg.portal_id``), а НЕ хардкод. Ядро резолвит
    slug→portal по этому значению; неправильный slug → событие зависает pending."""
    from atlas.appconfig import load_config
    return load_config().portal_id


pm_tasks_app = typer.Typer(
    no_args_is_help=True,
    help="PM Tasks: задачи портфеля (PM-БД), CRUD.",
)
console = Console()

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
VALID_STATUSES = {
    "backlog", "todo", "in_progress", "review",
    "done", "blocked", "cancelled",
}
VALID_QUALITY_TIERS = {"T1", "T2", "T3"}
VALID_ORIGINS = {"native", "injected", "imported", "split"}
SLUG_PART_RE = re.compile(r"^[a-z0-9-]{2,50}$")
DEFAULT_ACTOR_SLUG = "dmitry"

# Приоритет → числовой ранг для сортировки (P0 = выше всех).
_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


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
        entity_type="task",
        entity_id=entity_id,
        action=action,
        details_json=json.dumps(details, ensure_ascii=False, default=str),
    )
    session.add(entry)


def _slug_exists_fn(session: Session):
    def _check(candidate: str) -> bool:
        return session.execute(
            select(Task.id).where(Task.slug == candidate)
        ).scalar_one_or_none() is not None
    return _check


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def _validate_slug_part(slug: str) -> None:
    if not SLUG_PART_RE.match(slug):
        console.print(
            f"[red]Невалидный slug '{slug}': допустимы [a-z0-9-], длина 2-50.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_priority(priority: str) -> None:
    if priority not in VALID_PRIORITIES:
        console.print(
            f"[red]Невалидный priority '{priority}': "
            f"допустимы {sorted(VALID_PRIORITIES)}.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_status(status: str) -> None:
    if status not in VALID_STATUSES:
        console.print(
            f"[red]Невалидный status '{status}': "
            f"допустимы {sorted(VALID_STATUSES)}.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_quality_tier(tier: str) -> None:
    if tier not in VALID_QUALITY_TIERS:
        console.print(
            f"[red]Невалидный quality tier '{tier}': "
            f"допустимы {sorted(VALID_QUALITY_TIERS)}.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_origin(origin: str) -> None:
    if origin not in VALID_ORIGINS:
        console.print(
            f"[red]Невалидный origin '{origin}': "
            f"допустимы {sorted(VALID_ORIGINS)}.[/red]"
        )
        raise typer.Exit(code=1)


def _parse_date(value: str, label: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        console.print(
            f"[red]Невалидный {label} '{value}': ожидаю YYYY-MM-DD.[/red]"
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


def _resolve_assignee_or_die(session: Session, slug: str) -> Participant:
    p = session.execute(
        select(Participant).where(Participant.slug == slug)
    ).scalar_one_or_none()
    if p is None:
        console.print(f"[red]Участник '{slug}' не найден.[/red]")
        raise typer.Exit(code=1)
    return p


def _resolve_epic_or_die(session: Session, ref: str) -> Epic:
    """Найти Epic по slug / full UUID / short UUID prefix (≥7). Нет → Exit(1).

    По образцу resolve_project_ref: сначала slug (exact), затем UUID full,
    затем UUID short prefix через LIKE 'ref%'. Неоднозначный short prefix
    или отсутствие → typer.Exit(1).
    """
    from atlas.pm.slugs import (
        UUID_SHORT_MIN,
        _is_full_uuid,
        _looks_like_uuid_prefix,
    )

    epic: Optional[Epic] = None
    # 1. slug (exact)
    epic = session.execute(
        select(Epic).where(Epic.slug == ref)
    ).scalar_one_or_none()
    if epic is not None:
        return epic
    # 2. full UUID
    if _is_full_uuid(ref):
        epic = session.execute(
            select(Epic).where(Epic.id == ref)
        ).scalar_one_or_none()
    # 3. short UUID prefix
    elif len(ref) >= UUID_SHORT_MIN and _looks_like_uuid_prefix(ref):
        matches = session.execute(
            select(Epic).where(Epic.id.like(f"{ref}%"))
        ).scalars().all()
        if len(matches) > 1:
            console.print(
                f"[red]UUID prefix '{ref}' матчит {len(matches)} эпиков; "
                f"уточни больше символов.[/red]"
            )
            raise typer.Exit(code=1)
        epic = matches[0] if matches else None

    if epic is None:
        console.print(f"[red]Epic '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return epic


def _resolve_task_or_die(session: Session, ref: str) -> Task:
    try:
        task = resolve_task_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if task is None:
        console.print(f"[red]Task '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return task


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


@pm_tasks_app.command("add")
def add_cmd(
    project: str = typer.Option(..., "--project", help="Project ref (slug | UUID)"),
    title: str = typer.Option(..., "--title"),
    cpp: str = typer.Option(..., "--cpp", help="ЦКП — Ценный Конечный Продукт"),
    slug: Optional[str] = typer.Option(
        None, "--slug",
        help="Часть slug после prefix-проекта ([a-z0-9-], 2-50). Авто из --title.",
    ),
    description: Optional[str] = typer.Option(None, "--description"),
    priority: str = typer.Option("P2", "--priority", help="P0 | P1 | P2 | P3"),
    status: str = typer.Option("backlog", "--status"),
    story_points: Optional[int] = typer.Option(None, "--story-points"),
    due_date: Optional[str] = typer.Option(None, "--due-date", help="YYYY-MM-DD"),
    assignee: Optional[str] = typer.Option(None, "--assignee", help="participant slug"),
    epic: Optional[str] = typer.Option(
        None, "--epic",
        help="Epic ref (slug | UUID). Резолвится в epic_id (FK→epics.id).",
    ),
    quality_tier: Optional[str] = typer.Option(None, "--quality-tier"),
    source_project: Optional[str] = typer.Option(
        None, "--source-project",
        help="Проект-источник (slug | UUID). Задаёт provenance + origin='injected'.",
    ),
    rationale: Optional[str] = typer.Option(
        None, "--rationale", help="Почему/по какому принципу заведена задача.",
    ),
    origin: Optional[str] = typer.Option(
        None, "--origin", help="native | injected | imported | split.",
    ),
    injected_by: Optional[str] = typer.Option(
        None, "--injected-by", help="Participant slug, кто инжектировал.",
    ),
) -> None:
    """Создать задачу."""
    _validate_priority(priority)
    _validate_status(status)
    if quality_tier is not None:
        _validate_quality_tier(quality_tier)
    if origin is not None:
        _validate_origin(origin)

    due_dt: Optional[datetime] = None
    if due_date:
        due_dt = _parse_date(due_date, "due-date")

    if not cpp.strip():
        console.print("[red]Поле --cpp не может быть пустым (ЦКП обязателен).[/red]")
        raise typer.Exit(code=1)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        proj = _resolve_project_or_die(session, project)
        if proj.prefix is None:
            console.print(
                f"[red]У проекта '{proj.slug}' нет prefix — "
                f"нельзя сгенерировать task slug.[/red]"
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

        # ----- assignee -----
        assignee_obj: Optional[Participant] = None
        if assignee:
            assignee_obj = _resolve_assignee_or_die(session, assignee)

        # ----- epic -----
        epic_obj: Optional[Epic] = None
        if epic:
            epic_obj = _resolve_epic_or_die(session, epic)

        # ----- provenance / инвариант origin↔source -----
        source_id: Optional[str] = None
        source_slug: Optional[str] = None
        final_origin = origin or "native"
        injected_at: Optional[datetime] = None
        injector: Optional[Participant] = None
        if source_project is not None:
            src = _resolve_project_or_die(session, source_project)
            if src.id == proj.id:
                # self-inject — это не инжект: warning, остаёмся native.
                console.print(
                    "[yellow]Предупреждение: source-project совпадает с целевым — "
                    "это не инжект, origin остаётся 'native'.[/yellow]"
                )
            else:
                source_id = src.id
                source_slug = src.slug
                if origin is None:
                    final_origin = "injected"
                injected_at = msk_now()
                if injected_by is not None:
                    injector = _resolve_assignee_or_die(session, injected_by)

        # ----- number -----
        number = next_task_number(session)

        # ----- create -----
        task = Task(
            number=number,
            slug=final_slug,
            project_id=proj.id,
            epic_id=epic_obj.id if epic_obj else None,
            assignee_id=assignee_obj.id if assignee_obj else None,
            title=title,
            description=description,
            cpp_description=cpp,
            status=status,
            priority=priority,
            story_points=story_points,
            due_date=due_dt,
            quality_tier=quality_tier,
            source_project_id=source_id,
            origin=final_origin,
            rationale=rationale,
            injected_by=injector.id if injector else None,
            injected_at=injected_at,
        )
        session.add(task)
        session.flush()

        _log_action(
            session,
            action="task_created",
            entity_id=task.id,
            details={
                "number": number,
                "slug": final_slug,
                "project": proj.slug,
                "title": title,
                "priority": priority,
                "status": status,
                "assignee": assignee,
                "origin": final_origin,
                "source_project": source_slug,
                "rationale": rationale,
            },
        )
        # F3c: поставить в outbox для синка наружу (если политика проекта разрешает)
        try:
            _portal_id = _sync_portal_id()
            _outbox.enqueue(
                session, "create", "task", task, project=proj, portal_id=_portal_id,
            )
        except Exception:
            # синк — best-effort; падение enqueue не должно срывать создание задачи
            pass
        session.commit()

        if slug_auto:
            console.print(f"[dim]slug auto-generated: {final_slug}[/dim]")

        assignee_part = f" · assignee: {assignee}" if assignee else ""
        console.print(
            f"[green]✓ Task #{number} '{final_slug}' created[/green] · "
            f"{title} · {status} · {priority}{assignee_part}"
        )


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


_SRC_PROJECT = aliased(Project)


@pm_tasks_app.command("list")
@command
def list_cmd(
    project: Optional[str] = typer.Option(None, "--project", help="Project ref"),
    status: Optional[str] = typer.Option(None, "--status"),
    assignee: Optional[str] = typer.Option(None, "--assignee"),
    epic: Optional[str] = typer.Option(
        None, "--epic", help="Epic ref (slug | UUID) — фильтр по epic_id.",
    ),
    source_project: Optional[str] = typer.Option(
        None, "--source-project", help="Фильтр по проекту-источнику (provenance).",
    ),
    archived: bool = typer.Option(
        False, "--archived/--no-archived",
        help="Показывать архивные (по умолчанию скрыты)",
    ),
) -> None:
    """Список задач (--json по умолчанию; --text — таблица)."""
    if status is not None:
        _validate_status(status)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        stmt = (
            select(
                Task.id,
                Task.number,
                Task.slug,
                Task.title,
                Task.status,
                Task.priority,
                Task.due_date,
                Task.archived_at,
                Task.origin,
                Project.slug.label("project_slug"),
                Participant.slug.label("assignee_slug"),
                _SRC_PROJECT.slug.label("source_project_slug"),
            )
            .join(Project, Task.project_id == Project.id)
            .join(Participant, Task.assignee_id == Participant.id, isouter=True)
            .join(_SRC_PROJECT, Task.source_project_id == _SRC_PROJECT.id, isouter=True)
        )

        if project is not None:
            proj = _resolve_project_or_die(session, project)
            stmt = stmt.where(Task.project_id == proj.id)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        if assignee is not None:
            ass_obj = _resolve_assignee_or_die(session, assignee)
            stmt = stmt.where(Task.assignee_id == ass_obj.id)
        if epic is not None:
            epic_obj = _resolve_epic_or_die(session, epic)
            stmt = stmt.where(Task.epic_id == epic_obj.id)
        if source_project is not None:
            src = _resolve_project_or_die(session, source_project)
            stmt = stmt.where(Task.source_project_id == src.id)
        if not archived:
            stmt = stmt.where(Task.archived_at.is_(None))

        rows = session.execute(stmt).all()

    # Sort: priority asc (P0 first), number desc (новые сверху).
    sorted_rows = sorted(
        rows,
        key=lambda r: (
            _PRIORITY_RANK.get(r.priority, 99),
            -(r.number or 0),
        ),
    )

    data = [
        {
            "id": r.id,
            "number": r.number,
            "slug": r.slug,
            "title": r.title,
            "status": r.status,
            "priority": r.priority,
            "assignee": r.assignee_slug,
            "due_date": r.due_date.strftime("%Y-%m-%d") if r.due_date else None,
            "project": r.project_slug,
            "origin": r.origin,
            "source_project": r.source_project_slug,
            "archived": r.archived_at is not None,
        }
        for r in sorted_rows
    ]

    emit_data(data, text_renderer=_render_task_list)


def _render_task_list(data: list[dict[str, Any]]) -> None:
    if not data:
        console.print("[yellow]Задач не найдено.[/yellow]")
        return

    table = Table(title=f"PM Tasks ({len(data)})")
    table.add_column("#", justify="right", style="bold")
    table.add_column("Slug", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Status", style="magenta")
    table.add_column("P", justify="center", style="bold")
    table.add_column("Origin", style="yellow")
    table.add_column("Assignee", style="green")
    table.add_column("Due", style="dim")
    table.add_column("Project", style="dim")

    for row in data:
        title = row["title"]
        if row["archived"]:
            title = f"[strike]{row['title']}[/strike] [dim](archived)[/dim]"
        # Origin-маркер: injected/imported/split — со ссылкой на источник.
        if row["origin"] != "native":
            src = row["source_project"]
            origin_cell = f"{row['origin']} ←{src}" if src else row["origin"]
        else:
            origin_cell = "—"
        table.add_row(
            f"#{row['number']}" if row["number"] else "—",
            row["slug"] or "—",
            title,
            row["status"],
            row["priority"],
            origin_cell,
            row["assignee"] or "—",
            row["due_date"] or "—",
            row["project"],
        )
    console.print(table)


# --------------------------------------------------------------------------- #
# get                                                                         #
# --------------------------------------------------------------------------- #


@pm_tasks_app.command("get")
@command
def get_cmd(
    ref: str = typer.Argument(..., help="number | slug | full UUID | short UUID prefix"),
) -> None:
    """Показать карточку задачи (--json по умолчанию; --text — карточка)."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        task = _resolve_task_or_die(session, ref)

        proj = session.get(Project, task.project_id)
        assignee = (
            session.get(Participant, task.assignee_id) if task.assignee_id else None
        )

        # ----- epic (резолв epic_id → slug) -----
        epic_slug = None
        if task.epic_id:
            ep = session.get(Epic, task.epic_id)
            epic_slug = (ep.slug or ep.id) if ep is not None else task.epic_id

        # ----- provenance -----
        source_slug = None
        source_name = None
        if task.source_project_id:
            src = session.get(Project, task.source_project_id)
            if src is not None:
                source_slug = src.slug
                source_name = src.name
            else:
                source_slug = task.source_project_id
        injector_slug = None
        if task.injected_by:
            inj = session.get(Participant, task.injected_by)
            injector_slug = inj.slug if inj else task.injected_by

        log_rows = session.execute(
            select(ActionLog)
            .where(ActionLog.entity_type == "task")
            .where(ActionLog.entity_id == task.id)
            .order_by(ActionLog.timestamp.desc())
            .limit(5)
        ).scalars().all()

        recent = [
            {"timestamp": e.timestamp.strftime("%Y-%m-%d %H:%M"), "action": e.action}
            for e in log_rows
        ]

    data = {
        "id": task.id,
        "number": task.number,
        "slug": task.slug,
        "title": task.title,
        "cpp": task.cpp_description,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "story_points": task.story_points,
        "due_date": task.due_date.strftime("%Y-%m-%d") if task.due_date else None,
        "project": proj.slug if proj else None,
        "project_name": proj.name if proj else None,
        "assignee": assignee.slug if assignee else None,
        "epic": epic_slug,
        "quality_tier": task.quality_tier,
        # provenance
        "origin": task.origin,
        "source_project": source_slug,
        "source_project_name": source_name,
        "rationale": task.rationale,
        "injected_by": injector_slug,
        "injected_at": task.injected_at.isoformat() if task.injected_at else None,
        # integrations
        "notion_page_id": task.notion_page_id,
        "git_branch": task.git_branch,
        "git_pr_url": task.git_pr_url,
        "superpowers_spec_path": task.superpowers_spec_path,
        "superpowers_plan_path": task.superpowers_plan_path,
        # timestamps
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "archived_at": task.archived_at.isoformat() if task.archived_at else None,
        "recent_activity": recent,
    }

    emit_data(data, text_renderer=_render_task_get)


def _render_task_get(d: dict[str, Any]) -> None:
    archived_marker = ""
    if d["archived_at"]:
        archived_marker = f"  [bold red]ARCHIVED[/bold red] ({d['archived_at']})"
    number_str = f"#{d['number']}" if d["number"] else "—"
    console.print(
        f"[bold cyan]{d['slug'] or '—'}[/bold cyan]  {number_str} — "
        f"{d['title']}{archived_marker}"
    )
    console.print(f"  ID:        {d['id']}")
    console.print(f"  Number:    {number_str}")
    console.print(f"  Slug:      {d['slug'] or '—'}")
    console.print(f"  CPP:       {d['cpp']}")
    if d["description"]:
        console.print(f"  Description: {d['description']}")
    console.print(f"  Status:    {d['status']}")
    console.print(f"  Priority:  {d['priority']}")
    if d["story_points"] is not None:
        console.print(f"  Story pts: {d['story_points']}")
    if d["due_date"]:
        console.print(f"  Due:       {d['due_date']}")
    if d["project"]:
        console.print(f"  Project:   {d['project']} ({d['project_name']})")
    if d["assignee"]:
        console.print(f"  Assignee:  {d['assignee']}")
    else:
        console.print("  Assignee:  —")
    if d["epic"]:
        console.print(f"  Epic:      {d['epic']}")
    if d["quality_tier"]:
        console.print(f"  Quality:   {d['quality_tier']}")

    # Provenance — только если задача не нативная или есть источник.
    if d["origin"] != "native" or d["source_project"]:
        src = d["source_project"]
        if src and d["source_project_name"]:
            src = f"{src} ({d['source_project_name']})"
        console.print("\n[bold]Provenance:[/bold]")
        console.print(f"  Source project: {src or '—'}")
        console.print(f"  Origin:         {d['origin']}")
        console.print(f"  Rationale:      {d['rationale'] or '—'}")
        console.print(f"  Injected by:    {d['injected_by'] or '—'}")
        console.print(f"  Injected at:    {d['injected_at'] or '—'}")

    integrations = []
    if d["notion_page_id"]:
        integrations.append(f"  Notion:    {d['notion_page_id']}")
    if d["git_branch"]:
        integrations.append(f"  Branch:    {d['git_branch']}")
    if d["git_pr_url"]:
        integrations.append(f"  PR:        {d['git_pr_url']}")
    if d["superpowers_spec_path"]:
        integrations.append(f"  Spec:      {d['superpowers_spec_path']}")
    if d["superpowers_plan_path"]:
        integrations.append(f"  Plan:      {d['superpowers_plan_path']}")
    if integrations:
        console.print("\n[bold]Integrations:[/bold]")
        for line in integrations:
            console.print(line)

    console.print(f"\n  Created:   {d['created_at']}")
    console.print(f"  Updated:   {d['updated_at']}")
    if d["started_at"]:
        console.print(f"  Started:   {d['started_at']}")
    if d["completed_at"]:
        console.print(f"  Completed: {d['completed_at']}")
    if d["archived_at"]:
        console.print(f"  Archived:  {d['archived_at']}")

    if d["recent_activity"]:
        console.print("\n[bold]Recent activity:[/bold]")
        for entry in d["recent_activity"]:
            console.print(f"  • {entry['timestamp']} — {entry['action']}")


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #


@pm_tasks_app.command("update")
def update_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    title: Optional[str] = typer.Option(None, "--title"),
    cpp: Optional[str] = typer.Option(None, "--cpp"),
    description: Optional[str] = typer.Option(None, "--description"),
    status: Optional[str] = typer.Option(None, "--status"),
    priority: Optional[str] = typer.Option(None, "--priority"),
    story_points: Optional[int] = typer.Option(None, "--story-points"),
    due_date: Optional[str] = typer.Option(None, "--due-date", help="YYYY-MM-DD"),
    assignee: Optional[str] = typer.Option(None, "--assignee"),
    epic: Optional[str] = typer.Option(
        None, "--epic", help="Epic ref (slug | UUID) → epic_id.",
    ),
    quality_tier: Optional[str] = typer.Option(None, "--quality-tier"),
    notion_page_id: Optional[str] = typer.Option(None, "--notion-page-id"),
    git_branch: Optional[str] = typer.Option(None, "--git-branch"),
    git_pr_url: Optional[str] = typer.Option(None, "--git-pr-url"),
    superpowers_spec_path: Optional[str] = typer.Option(None, "--superpowers-spec-path"),
    superpowers_plan_path: Optional[str] = typer.Option(None, "--superpowers-plan-path"),
    slug: Optional[str] = typer.Option(
        None, "--slug",
        help="ЗАПРЕЩЕНО: slug — immutable. Используй delete + add.",
    ),
    number: Optional[int] = typer.Option(
        None, "--number",
        help="ЗАПРЕЩЕНО: number — immutable.",
    ),
    project: Optional[str] = typer.Option(
        None, "--project",
        help="ЗАПРЕЩЕНО: переезд между проектами сломает slug. delete + add.",
    ),
) -> None:
    """Обновить поля задачи (любые, кроме slug/number/project)."""
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
            "[red]Изменение project запрещено: сломает slug-prefix. "
            "delete + add.[/red]"
        )
        raise typer.Exit(code=1)

    if priority is not None:
        _validate_priority(priority)
    if status is not None:
        _validate_status(status)
    if quality_tier is not None:
        _validate_quality_tier(quality_tier)

    due_dt: Optional[datetime] = None
    if due_date is not None:
        due_dt = _parse_date(due_date, "due-date")

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        task = _resolve_task_or_die(session, ref)

        diffs: dict[str, dict[str, Any]] = {}

        def _maybe_update(field: str, new_value: Any) -> None:
            if new_value is None:
                return
            old_value = getattr(task, field)
            if old_value != new_value:
                diffs[field] = {"old": old_value, "new": new_value}
                setattr(task, field, new_value)

        _maybe_update("title", title)
        _maybe_update("cpp_description", cpp)
        _maybe_update("description", description)
        _maybe_update("priority", priority)
        _maybe_update("story_points", story_points)
        _maybe_update("due_date", due_dt)
        _maybe_update("quality_tier", quality_tier)
        _maybe_update("notion_page_id", notion_page_id)
        _maybe_update("git_branch", git_branch)
        _maybe_update("git_pr_url", git_pr_url)
        _maybe_update("superpowers_spec_path", superpowers_spec_path)
        _maybe_update("superpowers_plan_path", superpowers_plan_path)

        # assignee — нужен resolve через slug
        if assignee is not None:
            ass_obj = _resolve_assignee_or_die(session, assignee)
            if task.assignee_id != ass_obj.id:
                diffs["assignee"] = {
                    "old": _slug_for_assignee(session, task.assignee_id),
                    "new": assignee,
                }
                task.assignee_id = ass_obj.id

        # epic — нужен resolve через ref (slug | UUID); diff логируем slug'ами
        if epic is not None:
            epic_obj = _resolve_epic_or_die(session, epic)
            if task.epic_id != epic_obj.id:
                diffs["epic"] = {
                    "old": _slug_for_epic(session, task.epic_id),
                    "new": epic_obj.slug or epic_obj.id,
                }
                task.epic_id = epic_obj.id

        # status — особая логика для started_at / completed_at
        if status is not None and task.status != status:
            old_status = task.status
            diffs["status"] = {"old": old_status, "new": status}
            task.status = status

            now = msk_now()
            if status == "in_progress" and task.started_at is None:
                task.started_at = now
                diffs["started_at"] = {"old": None, "new": now}
            elif status == "done":
                if task.started_at is None:
                    task.started_at = now
                    diffs["started_at"] = {"old": None, "new": now}
                task.completed_at = now
                diffs["completed_at"] = {"old": None, "new": now}
            elif old_status == "done" and status != "done":
                # откат из done → очистить completed_at
                if task.completed_at is not None:
                    diffs["completed_at"] = {"old": task.completed_at, "new": None}
                    task.completed_at = None

        if not diffs:
            console.print("[yellow]Нечего обновлять.[/yellow]")
            return

        _log_action(
            session,
            action="task_updated",
            entity_id=task.id,
            details=diffs,
        )
        # F3e: enqueue update в outbox (best-effort)
        try:
            _proj = session.get(Project, task.project_id)
            if _proj is not None:
                _outbox.enqueue(session, "update", "task", task, project=_proj, portal_id=_sync_portal_id())
        except Exception:
            pass
        session.commit()

        console.print(
            f"[green]✓ Task #{task.number} '{task.slug}' updated[/green] "
            f"({len(diffs)} field(s))"
        )
        for field, diff in diffs.items():
            console.print(
                f"  {field}: [dim]{diff['old']}[/dim] → [bold]{diff['new']}[/bold]"
            )


def _slug_for_assignee(session: Session, assignee_id: Optional[str]) -> Optional[str]:
    if assignee_id is None:
        return None
    p = session.get(Participant, assignee_id)
    return p.slug if p else None


def _slug_for_epic(session: Session, epic_id: Optional[str]) -> Optional[str]:
    if epic_id is None:
        return None
    e = session.get(Epic, epic_id)
    return (e.slug or e.id) if e else None


# --------------------------------------------------------------------------- #
# delete                                                                      #
# --------------------------------------------------------------------------- #


@pm_tasks_app.command("delete")
def delete_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    hard: bool = typer.Option(
        False, "--hard",
        help="Физически удалить (после confirm). По умолчанию — soft archive.",
    ),
) -> None:
    """Удалить задачу (soft по умолчанию: archived_at, статус не меняется)."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        task = _resolve_task_or_die(session, ref)

        slug_for_msg = task.slug or "(no slug)"
        number_for_msg = task.number
        task_id = task.id

        if hard:
            confirmed = typer.confirm(
                f"Физически удалить task #{number_for_msg} '{slug_for_msg}'?"
            )
            if not confirmed:
                console.print("[yellow]Отменено.[/yellow]")
                raise typer.Exit(code=1)

            _log_action(
                session,
                action="task_deleted",
                entity_id=task_id,
                details={"slug": slug_for_msg, "number": number_for_msg},
            )
            session.delete(task)
            session.commit()
            console.print(
                f"[red]✗ Task #{number_for_msg} '{slug_for_msg}' "
                f"физически удалён.[/red]"
            )
            return

        if task.archived_at is not None:
            console.print(
                f"[yellow]Task #{number_for_msg} '{slug_for_msg}' уже archived "
                f"({task.archived_at}).[/yellow]"
            )
            return

        task.archived_at = msk_now()
        _log_action(
            session,
            action="task_archived",
            entity_id=task_id,
            details={
                "slug": slug_for_msg,
                "number": number_for_msg,
                "at": task.archived_at.isoformat(),
            },
        )
        # F3e: enqueue delete в outbox (best-effort)
        try:
            _proj = session.get(Project, task.project_id)
            if _proj is not None:
                _outbox.enqueue(session, "delete", "task", task, project=_proj, portal_id=_sync_portal_id())
        except Exception:
            pass
        session.commit()
        console.print(
            f"[green]✓ Task #{number_for_msg} archived[/green]"
        )
