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
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm._time import msk_now
from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.models import (
    ActionLog,
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
    sprint: Optional[str] = typer.Option(
        None, "--sprint",
        help="Sprint ref (UUID или slug). MVP: nullable, без FK-проверки.",
    ),
    quality_tier: Optional[str] = typer.Option(None, "--quality-tier"),
) -> None:
    """Создать задачу."""
    _validate_priority(priority)
    _validate_status(status)
    if quality_tier is not None:
        _validate_quality_tier(quality_tier)

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

        # ----- number -----
        number = next_task_number(session)

        # ----- create -----
        task = Task(
            number=number,
            slug=final_slug,
            project_id=proj.id,
            sprint_id=sprint,  # MVP: храним как опциональный raw ref
            assignee_id=assignee_obj.id if assignee_obj else None,
            title=title,
            description=description,
            cpp_description=cpp,
            status=status,
            priority=priority,
            story_points=story_points,
            due_date=due_dt,
            quality_tier=quality_tier,
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


@pm_tasks_app.command("list")
def list_cmd(
    project: Optional[str] = typer.Option(None, "--project", help="Project ref"),
    status: Optional[str] = typer.Option(None, "--status"),
    assignee: Optional[str] = typer.Option(None, "--assignee"),
    sprint: Optional[str] = typer.Option(None, "--sprint"),
    archived: bool = typer.Option(
        False, "--archived/--no-archived",
        help="Показывать архивные (по умолчанию скрыты)",
    ),
) -> None:
    """Список задач (табличный вывод)."""
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
                Project.slug.label("project_slug"),
                Participant.slug.label("assignee_slug"),
            )
            .join(Project, Task.project_id == Project.id)
            .join(Participant, Task.assignee_id == Participant.id, isouter=True)
        )

        if project is not None:
            proj = _resolve_project_or_die(session, project)
            stmt = stmt.where(Task.project_id == proj.id)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        if assignee is not None:
            ass_obj = _resolve_assignee_or_die(session, assignee)
            stmt = stmt.where(Task.assignee_id == ass_obj.id)
        if sprint is not None:
            stmt = stmt.where(Task.sprint_id == sprint)
        if not archived:
            stmt = stmt.where(Task.archived_at.is_(None))

        rows = session.execute(stmt).all()

    if not rows:
        console.print("[yellow]Задач не найдено.[/yellow]")
        return

    # Sort: priority asc (P0 first), number desc (новые сверху).
    sorted_rows = sorted(
        rows,
        key=lambda r: (
            _PRIORITY_RANK.get(r.priority, 99),
            -(r.number or 0),
        ),
    )

    table = Table(title=f"PM Tasks ({len(sorted_rows)})")
    table.add_column("#", justify="right", style="bold")
    table.add_column("Slug", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Status", style="magenta")
    table.add_column("P", justify="center", style="bold")
    table.add_column("Assignee", style="green")
    table.add_column("Due", style="dim")
    table.add_column("Project", style="dim")

    for row in sorted_rows:
        title = row.title
        if row.archived_at is not None:
            title = f"[strike]{row.title}[/strike] [dim](archived)[/dim]"
        due = row.due_date.strftime("%Y-%m-%d") if row.due_date else "—"
        table.add_row(
            f"#{row.number}" if row.number else "—",
            row.slug or "—",
            title,
            row.status,
            row.priority,
            row.assignee_slug or "—",
            due,
            row.project_slug,
        )
    console.print(table)


# --------------------------------------------------------------------------- #
# get                                                                         #
# --------------------------------------------------------------------------- #


@pm_tasks_app.command("get")
def get_cmd(
    ref: str = typer.Argument(..., help="number | slug | full UUID | short UUID prefix"),
) -> None:
    """Показать карточку задачи."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        task = _resolve_task_or_die(session, ref)

        proj = session.get(Project, task.project_id)
        assignee = (
            session.get(Participant, task.assignee_id) if task.assignee_id else None
        )

        log_rows = session.execute(
            select(ActionLog)
            .where(ActionLog.entity_type == "task")
            .where(ActionLog.entity_id == task.id)
            .order_by(ActionLog.timestamp.desc())
            .limit(5)
        ).scalars().all()

    archived_marker = ""
    if task.archived_at is not None:
        archived_marker = (
            f"  [bold red]ARCHIVED[/bold red] "
            f"({task.archived_at.strftime('%Y-%m-%d')})"
        )
    number_str = f"#{task.number}" if task.number else "—"
    console.print(
        f"[bold cyan]{task.slug or '—'}[/bold cyan]  {number_str} — "
        f"{task.title}{archived_marker}"
    )
    console.print(f"  ID:        {task.id}")
    console.print(f"  Number:    {number_str}")
    console.print(f"  Slug:      {task.slug or '—'}")
    console.print(f"  CPP:       {task.cpp_description}")
    if task.description:
        console.print(f"  Description: {task.description}")
    console.print(f"  Status:    {task.status}")
    console.print(f"  Priority:  {task.priority}")
    if task.story_points is not None:
        console.print(f"  Story pts: {task.story_points}")
    if task.due_date:
        console.print(f"  Due:       {task.due_date.strftime('%Y-%m-%d')}")
    if proj:
        console.print(f"  Project:   {proj.slug} ({proj.name})")
    if assignee:
        console.print(
            f"  Assignee:  {assignee.slug} ({assignee.name}, {assignee.kind})"
        )
    else:
        console.print("  Assignee:  —")
    if task.sprint_id:
        console.print(f"  Sprint:    {task.sprint_id}")
    if task.quality_tier:
        console.print(f"  Quality:   {task.quality_tier}")

    integrations = []
    if task.notion_page_id:
        integrations.append(f"  Notion:    {task.notion_page_id}")
    if task.git_branch:
        integrations.append(f"  Branch:    {task.git_branch}")
    if task.git_pr_url:
        integrations.append(f"  PR:        {task.git_pr_url}")
    if task.superpowers_spec_path:
        integrations.append(f"  Spec:      {task.superpowers_spec_path}")
    if task.superpowers_plan_path:
        integrations.append(f"  Plan:      {task.superpowers_plan_path}")
    if integrations:
        console.print("\n[bold]Integrations:[/bold]")
        for line in integrations:
            console.print(line)

    console.print(f"\n  Created:   {task.created_at}")
    console.print(f"  Updated:   {task.updated_at}")
    if task.started_at:
        console.print(f"  Started:   {task.started_at}")
    if task.completed_at:
        console.print(f"  Completed: {task.completed_at}")
    if task.archived_at:
        console.print(f"  Archived:  {task.archived_at}")

    if log_rows:
        console.print("\n[bold]Recent activity:[/bold]")
        for entry in log_rows:
            ts = entry.timestamp.strftime("%Y-%m-%d %H:%M")
            console.print(f"  • {ts} — {entry.action}")


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
    sprint: Optional[str] = typer.Option(None, "--sprint"),
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
        _maybe_update("sprint_id", sprint)
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
