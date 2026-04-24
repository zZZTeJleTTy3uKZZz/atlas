"""CLI-команды `atlas projects ...`.

CRUD по проектам портфеля + init.

Команды:
- ``init``    — создать БД, применить миграции, seed справочников.
- ``add``     — создать проект (slug/prefix авто или явно).
- ``list``    — список проектов (фильтры по type / status / archived).
- ``get``     — карточка проекта (по slug, full UUID или short UUID prefix).
- ``update``  — изменить поля проекта (любые, кроме slug).
- ``delete``  — soft archive (по умолчанию) или ``--hard`` для физ. удаления.

Справочники types/statuses вынесены в отдельные top-level subapp
(`atlas types ...`, `atlas statuses ...`) — см. types.py и statuses.py.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm.db import DEFAULT_DB_PATH, make_engine, make_session
from atlas.pm.models import (
    ActionLog,
    Participant,
    Project,
    ProjectParticipant,
    ProjectStatus,
    ProjectType,
)
from atlas.pm.seeds import seed_all
from atlas.pm.slugs import (
    AmbiguousRefError,
    SlugGenerationError,
    generate_prefix_from_slug,
    generate_unique_slug,
    resolve_project_ref,
    slugify_text,
)

projects_app = typer.Typer(
    no_args_is_help=True,
    help="Projects management: проекты портфеля (PM-БД), CRUD.",
)
console = Console()

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
SLUG_RE = re.compile(r"^[a-z0-9-]{2,50}$")
PREFIX_RE = re.compile(r"^[a-z0-9]{1,5}$")
DEFAULT_ACTOR_SLUG = "dmitry"


# --------------------------------------------------------------------------- #
# DB helpers                                                                  #
# --------------------------------------------------------------------------- #


def _db_url() -> str:
    """Получить URL БД: env var → default."""
    return os.environ.get("ATLAS_DB_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


def _find_project_root() -> Path:
    """Найти корень проекта (где alembic.ini)."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "alembic.ini").exists():
            return parent
    raise RuntimeError("Не найден alembic.ini: не могу определить корень проекта")


def _actor_id(session: Session) -> Optional[str]:
    """Получить id участника-актора (Дмитрий) из seed для action_log."""
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
    """Добавить запись в action_log (commit вызывает caller)."""
    entry = ActionLog(
        actor_id=_actor_id(session),
        entity_type="project",
        entity_id=entity_id,
        action=action,
        details_json=json.dumps(details, ensure_ascii=False, default=str),
    )
    session.add(entry)


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def _validate_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        console.print(
            f"[red]Невалидный slug '{slug}': допустимы [a-z0-9-], длина 2-50.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_prefix(prefix: str) -> None:
    if not PREFIX_RE.match(prefix):
        console.print(
            f"[red]Невалидный prefix '{prefix}': допустимы [a-z0-9], длина 1-5.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_priority(priority: str) -> None:
    if priority not in VALID_PRIORITIES:
        console.print(
            f"[red]Невалидный priority '{priority}': допустимы {sorted(VALID_PRIORITIES)}.[/red]"
        )
        raise typer.Exit(code=1)


def _slug_exists_fn(session: Session):
    def _check(candidate: str) -> bool:
        return session.execute(
            select(Project.id).where(Project.slug == candidate)
        ).scalar_one_or_none() is not None
    return _check


def _prefix_exists_fn(session: Session):
    def _check(candidate: str) -> bool:
        return session.execute(
            select(Project.id).where(Project.prefix == candidate)
        ).scalar_one_or_none() is not None
    return _check


def _generate_unique_prefix(
    session: Session,
    base: str,
    *,
    max_attempts: int = 100,
) -> str:
    """Авто-prefix с числовым суффиксом: cf, cf2, cf3, ...

    Отдельная функция от ``generate_unique_slug`` потому что суффикс цифровой
    без дефиса (prefix не имеет дефисов по контракту PREFIX_RE).
    """
    exists = _prefix_exists_fn(session)
    if not exists(base):
        return base
    for n in range(2, max_attempts + 1):
        candidate = f"{base}{n}"
        # суффикс может перевалить за 5 chars — если так, обрезаем base
        if len(candidate) > 5:
            trimmed_base = base[: 5 - len(str(n))]
            candidate = f"{trimmed_base}{n}"
        if not exists(candidate):
            return candidate
    raise SlugGenerationError(
        f"Не удалось подобрать уникальный prefix на основе '{base}' "
        f"за {max_attempts} попыток"
    )


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #


@projects_app.command("init")
def init_cmd(
    db_url: Optional[str] = typer.Option(
        None, "--db-url", help="URL БД (override env ATLAS_DB_URL и default)"
    ),
) -> None:
    """Инициализировать PM-БД: apply migrations + seed справочников."""
    url = db_url or _db_url()
    console.print(f"[bold]Database:[/bold] {url}")

    console.print("[cyan]1. Применяю миграции Alembic...[/cyan]")
    env = os.environ.copy()
    env["ATLAS_DB_URL"] = url
    project_root = _find_project_root()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print("[red]Ошибка миграций:[/red]")
        console.print(result.stderr)
        raise typer.Exit(code=1)
    console.print("[green]   ✓ миграции применены[/green]")

    console.print(
        "[cyan]2. Заселяю справочники (project_types, project_statuses, participants)...[/cyan]"
    )
    engine = make_engine(url)
    with make_session(engine) as session:
        counts = seed_all(session)
    console.print(
        f"[green]   ✓ project_types={counts['project_types']}, "
        f"project_statuses={counts['project_statuses']}, "
        f"participants={counts['participants']}[/green]"
    )

    console.print("[bold green]Готово.[/bold green] PM-БД инициализирована.")


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


@projects_app.command("add")
def add_cmd(
    name: str = typer.Option(..., "--name", help="Человекочитаемое название проекта"),
    type_slug: str = typer.Option(..., "--type", help="Тип: client-project / business-product / ..."),
    slug: Optional[str] = typer.Option(
        None, "--slug",
        help="Уникальный slug ([a-z0-9-], 2-50). Если не задан — авто из --name.",
    ),
    prefix: Optional[str] = typer.Option(
        None, "--prefix",
        help="Префикс ([a-z0-9], 1-5). Если не задан — авто из slug.",
    ),
    priority: str = typer.Option("P2", "--priority", help="P0 | P1 | P2 | P3"),
    status_slug: str = typer.Option("experiment", "--status", help="Lifecycle status slug"),
    description: Optional[str] = typer.Option(None, "--description"),
    one_line: Optional[str] = typer.Option(None, "--one-line", help="Краткое описание (1 строка)"),
    deadline: Optional[str] = typer.Option(None, "--deadline", help="ISO-дата YYYY-MM-DD"),
    git_repo_url: Optional[str] = typer.Option(None, "--git-repo-url"),
    local_path: Optional[str] = typer.Option(None, "--local-path"),
) -> None:
    """Создать новый проект в портфеле."""
    _validate_priority(priority)

    deadline_dt: Optional[datetime] = None
    if deadline:
        try:
            deadline_dt = datetime.fromisoformat(deadline)
        except ValueError:
            console.print(
                f"[red]Невалидный deadline '{deadline}': ожидаю YYYY-MM-DD.[/red]"
            )
            raise typer.Exit(code=1)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        pt = session.execute(
            select(ProjectType).where(ProjectType.slug == type_slug)
        ).scalar_one_or_none()
        if pt is None:
            console.print(
                f"[red]Тип '{type_slug}' не найден. См. `atlas projects types`.[/red]"
            )
            raise typer.Exit(code=1)

        ps = session.execute(
            select(ProjectStatus).where(ProjectStatus.slug == status_slug)
        ).scalar_one_or_none()
        if ps is None:
            console.print(
                f"[red]Статус '{status_slug}' не найден. См. `atlas projects statuses`.[/red]"
            )
            raise typer.Exit(code=1)

        # ----- slug -----
        slug_auto = False
        if slug:
            _validate_slug(slug)
            if _slug_exists_fn(session)(slug):
                console.print(
                    f"[red]Slug '{slug}' занят. "
                    f"Попробуйте '{slug}-2' или выберите другой.[/red]"
                )
                raise typer.Exit(code=1)
            final_slug = slug
        else:
            base = slugify_text(name)
            if not base:
                console.print(
                    f"[red]Не удалось сгенерировать slug из '{name}': "
                    f"передайте --slug явно.[/red]"
                )
                raise typer.Exit(code=1)
            try:
                final_slug = generate_unique_slug(base, _slug_exists_fn(session))
            except SlugGenerationError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            slug_auto = True

        # ----- prefix -----
        prefix_auto = False
        if prefix:
            _validate_prefix(prefix)
            if _prefix_exists_fn(session)(prefix):
                console.print(
                    f"[red]Prefix '{prefix}' занят. Выберите другой.[/red]"
                )
                raise typer.Exit(code=1)
            final_prefix = prefix
        else:
            base_prefix = generate_prefix_from_slug(final_slug)
            if not base_prefix:
                console.print(
                    f"[red]Не удалось сгенерировать prefix из slug '{final_slug}': "
                    f"передайте --prefix явно.[/red]"
                )
                raise typer.Exit(code=1)
            try:
                final_prefix = _generate_unique_prefix(session, base_prefix)
            except SlugGenerationError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            prefix_auto = True

        # ----- create -----
        project = Project(
            slug=final_slug,
            prefix=final_prefix,
            name=name,
            type_id=pt.id,
            status_id=ps.id,
            priority=priority,
            description=description,
            one_line_summary=one_line or "",
            estimated_deadline=deadline_dt,
            git_repo_url=git_repo_url,
            local_path=local_path,
        )
        session.add(project)
        session.flush()  # получить project.id

        _log_action(
            session,
            action="project_created",
            entity_id=project.id,
            details={
                "slug": final_slug,
                "prefix": final_prefix,
                "name": name,
                "type": type_slug,
                "priority": priority,
                "status": status_slug,
            },
        )
        session.commit()

        if slug_auto:
            console.print(f"[dim]slug auto-generated: {final_slug}[/dim]")
        if prefix_auto:
            console.print(f"[dim]prefix auto-generated: {final_prefix}[/dim]")

        console.print(f"[green]✓ Project '{final_slug}' created[/green]")
        console.print(f"  Name:     {name}")
        console.print(f"  Type:     {type_slug}")
        console.print(f"  Prefix:   {final_prefix}")
        console.print(f"  Priority: {priority}")
        console.print(f"  Status:   {status_slug}")


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


@projects_app.command("list")
def list_cmd(
    type_slug: Optional[str] = typer.Option(None, "--type", help="Фильтр: slug типа"),
    status_slug: Optional[str] = typer.Option(None, "--status", help="Фильтр: slug статуса"),
    archived: bool = typer.Option(
        False, "--archived/--no-archived",
        help="Показывать архивные (по умолчанию скрыты)",
    ),
) -> None:
    """Список проектов (табличный вывод)."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        stmt = select(
            Project.slug,
            Project.prefix,
            Project.name,
            Project.priority,
            Project.last_touched_at,
            Project.archived_at,
            ProjectType.slug.label("type_slug"),
            ProjectStatus.slug.label("status_slug"),
        ).join(
            ProjectType, Project.type_id == ProjectType.id
        ).join(
            ProjectStatus, Project.status_id == ProjectStatus.id
        ).order_by(Project.priority, Project.name)

        if type_slug:
            stmt = stmt.where(ProjectType.slug == type_slug)
        if status_slug:
            stmt = stmt.where(ProjectStatus.slug == status_slug)
        if not archived:
            stmt = stmt.where(Project.archived_at.is_(None))

        rows = session.execute(stmt).all()

    if not rows:
        console.print("[yellow]Проектов не найдено.[/yellow]")
        return

    table = Table(title=f"Projects ({len(rows)})")
    table.add_column("slug", style="cyan", no_wrap=True)
    table.add_column("prefix", style="dim")
    table.add_column("name")
    table.add_column("type", style="magenta")
    table.add_column("status", style="green")
    table.add_column("P", justify="center", style="bold")
    table.add_column("last touched", style="dim")

    for row in rows:
        last_touched = (
            row.last_touched_at.strftime("%Y-%m-%d") if row.last_touched_at else "—"
        )
        name_display = row.name
        if row.archived_at is not None:
            name_display = f"[strike]{row.name}[/strike] [dim](archived)[/dim]"
        table.add_row(
            row.slug,
            row.prefix or "—",
            name_display,
            row.type_slug,
            row.status_slug,
            row.priority,
            last_touched,
        )
    console.print(table)


# --------------------------------------------------------------------------- #
# get                                                                         #
# --------------------------------------------------------------------------- #


@projects_app.command("get")
def get_cmd(
    ref: str = typer.Argument(..., help="slug | full UUID | short UUID prefix (≥ 7 chars)"),
) -> None:
    """Показать карточку проекта."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        try:
            project = resolve_project_ref(session, ref)
        except AmbiguousRefError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        if project is None:
            console.print(f"[red]Project '{ref}' не найден.[/red]")
            raise typer.Exit(code=1)

        pt = session.get(ProjectType, project.type_id)
        ps = session.get(ProjectStatus, project.status_id)

        # участники
        link_rows = session.execute(
            select(ProjectParticipant, Participant)
            .join(Participant, ProjectParticipant.participant_id == Participant.id)
            .where(ProjectParticipant.project_id == project.id)
        ).all()

        # последние записи action_log
        log_rows = session.execute(
            select(ActionLog)
            .where(ActionLog.entity_type == "project")
            .where(ActionLog.entity_id == project.id)
            .order_by(ActionLog.timestamp.desc())
            .limit(5)
        ).scalars().all()

    # вывод
    archived_marker = ""
    if project.archived_at is not None:
        archived_marker = (
            f"  [bold red]ARCHIVED[/bold red] "
            f"({project.archived_at.strftime('%Y-%m-%d')})"
        )
    console.print(
        f"[bold cyan]{project.slug}[/bold cyan]  — {project.name}{archived_marker}"
    )
    console.print(f"  ID:        {project.id}")
    console.print(f"  Prefix:    {project.prefix or '—'}")
    if pt:
        console.print(f"  Type:      {pt.slug} ({pt.name})")
    if ps:
        console.print(f"  Status:    {ps.slug} ({ps.name})")
    console.print(f"  Priority:  {project.priority}")
    if project.description:
        console.print(f"  Description: {project.description}")
    if project.one_line_summary:
        console.print(f"  One-line:  {project.one_line_summary}")
    if project.estimated_deadline:
        console.print(f"  Deadline:  {project.estimated_deadline.strftime('%Y-%m-%d')}")
    if project.git_repo_url:
        console.print(f"  Git:       {project.git_repo_url}")
    if project.local_path:
        console.print(f"  Path:      {project.local_path}")
    console.print(f"  Created:   {project.created_at}")
    console.print(f"  Updated:   {project.updated_at}")
    if project.last_touched_at:
        console.print(f"  Touched:   {project.last_touched_at}")

    if link_rows:
        console.print("\n[bold]Participants:[/bold]")
        for link, participant in link_rows:
            hours = (
                f", {link.allocated_weekly_hours}h/нед"
                if link.allocated_weekly_hours else ""
            )
            console.print(
                f"  • {participant.name} — {link.role_in_project}{hours}"
            )
    else:
        console.print("\n[dim]Participants: —[/dim]")

    if log_rows:
        console.print("\n[bold]Recent activity:[/bold]")
        for entry in log_rows:
            ts = entry.timestamp.strftime("%Y-%m-%d %H:%M")
            console.print(f"  • {ts} — {entry.action}")


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #


@projects_app.command("update")
def update_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    name: Optional[str] = typer.Option(None, "--name"),
    priority: Optional[str] = typer.Option(None, "--priority", help="P0 | P1 | P2 | P3"),
    status_slug: Optional[str] = typer.Option(None, "--status"),
    description: Optional[str] = typer.Option(None, "--description"),
    one_line: Optional[str] = typer.Option(None, "--one-line"),
    deadline: Optional[str] = typer.Option(None, "--deadline", help="YYYY-MM-DD"),
    git_repo_url: Optional[str] = typer.Option(None, "--git-repo-url"),
    local_path: Optional[str] = typer.Option(None, "--local-path"),
    prefix: Optional[str] = typer.Option(None, "--prefix"),
    slug: Optional[str] = typer.Option(
        None, "--slug",
        help="ЗАПРЕЩЕНО менять slug — это часть task IDs. Используй delete + add.",
    ),
) -> None:
    """Обновить поля проекта (любые, кроме slug)."""
    if slug is not None:
        console.print(
            "[red]Изменение slug запрещено: slug участвует в task IDs. "
            "Если действительно нужно — `delete` + `add`.[/red]"
        )
        raise typer.Exit(code=1)

    if priority is not None:
        _validate_priority(priority)
    if prefix is not None:
        _validate_prefix(prefix)

    deadline_dt: Optional[datetime] = None
    if deadline is not None:
        try:
            deadline_dt = datetime.fromisoformat(deadline)
        except ValueError:
            console.print(f"[red]Невалидный deadline '{deadline}'.[/red]")
            raise typer.Exit(code=1)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        try:
            project = resolve_project_ref(session, ref)
        except AmbiguousRefError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        if project is None:
            console.print(f"[red]Project '{ref}' не найден.[/red]")
            raise typer.Exit(code=1)

        diffs: dict[str, dict[str, Any]] = {}

        def _maybe_update(field: str, new_value: Any) -> None:
            if new_value is None:
                return
            old_value = getattr(project, field)
            if old_value != new_value:
                diffs[field] = {"old": old_value, "new": new_value}
                setattr(project, field, new_value)

        _maybe_update("name", name)
        _maybe_update("priority", priority)
        _maybe_update("description", description)
        _maybe_update("one_line_summary", one_line)
        _maybe_update("estimated_deadline", deadline_dt)
        _maybe_update("git_repo_url", git_repo_url)
        _maybe_update("local_path", local_path)

        if status_slug is not None:
            ps = session.execute(
                select(ProjectStatus).where(ProjectStatus.slug == status_slug)
            ).scalar_one_or_none()
            if ps is None:
                console.print(
                    f"[red]Статус '{status_slug}' не найден.[/red]"
                )
                raise typer.Exit(code=1)
            if project.status_id != ps.id:
                # сохраним slug в diff (читабельнее, чем UUID)
                old_status = session.get(ProjectStatus, project.status_id)
                diffs["status"] = {
                    "old": old_status.slug if old_status else None,
                    "new": status_slug,
                }
                project.status_id = ps.id

        if prefix is not None and project.prefix != prefix:
            if _prefix_exists_fn(session)(prefix):
                console.print(
                    f"[red]Prefix '{prefix}' занят. Выберите другой.[/red]"
                )
                raise typer.Exit(code=1)
            diffs["prefix"] = {"old": project.prefix, "new": prefix}
            project.prefix = prefix

        if not diffs:
            console.print("[yellow]Нечего обновлять.[/yellow]")
            return

        project.last_touched_at = datetime.utcnow()
        _log_action(
            session,
            action="project_updated",
            entity_id=project.id,
            details=diffs,
        )
        session.commit()

        console.print(
            f"[green]✓ Project '{project.slug}' updated[/green] "
            f"({len(diffs)} field(s))"
        )
        for field, diff in diffs.items():
            console.print(
                f"  {field}: [dim]{diff['old']}[/dim] → [bold]{diff['new']}[/bold]"
            )


# --------------------------------------------------------------------------- #
# delete                                                                      #
# --------------------------------------------------------------------------- #


@projects_app.command("delete")
def delete_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    hard: bool = typer.Option(
        False, "--hard",
        help="Физически удалить (ломает FK у tasks). По умолчанию — soft archive.",
    ),
) -> None:
    """Удалить проект (soft по умолчанию: archived_at, статус не меняется)."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        try:
            project = resolve_project_ref(session, ref)
        except AmbiguousRefError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        if project is None:
            console.print(f"[red]Project '{ref}' не найден.[/red]")
            raise typer.Exit(code=1)

        slug_for_msg = project.slug
        project_id = project.id

        if hard:
            confirmed = typer.confirm(
                f"Физически удалить '{slug_for_msg}'? Это сломает FK у tasks."
            )
            if not confirmed:
                console.print("[yellow]Отменено.[/yellow]")
                raise typer.Exit(code=1)

            _log_action(
                session,
                action="project_hard_deleted",
                entity_id=project_id,
                details={"slug": slug_for_msg},
            )
            session.delete(project)
            session.commit()
            console.print(
                f"[red]✗ Project '{slug_for_msg}' физически удалён.[/red]"
            )
            return

        if project.archived_at is not None:
            console.print(
                f"[yellow]Project '{slug_for_msg}' уже archived ({project.archived_at}).[/yellow]"
            )
            return

        project.archived_at = datetime.utcnow()
        _log_action(
            session,
            action="project_archived",
            entity_id=project_id,
            details={"slug": slug_for_msg, "at": project.archived_at.isoformat()},
        )
        session.commit()
        console.print(f"[green]✓ Project '{slug_for_msg}' archived[/green]")


# --------------------------------------------------------------------------- #
# Note: справочники types/statuses вынесены в отдельные top-level subapp:    #
# `atlas types ...` (src/atlas/pm/commands/types.py)                         #
# `atlas statuses ...` (src/atlas/pm/commands/statuses.py)                   #
# --------------------------------------------------------------------------- #
