"""CLI-команды `atlas projects ...`.

CRUD по проектам портфеля + init + archive engine.

Команды:
- ``init``       — создать БД, применить миграции, seed справочников.
- ``add``        — создать проект (slug/prefix авто или явно).
- ``list``       — список проектов (фильтры по type / status / archived).
- ``get``        — карточка проекта (по slug, full UUID или short UUID prefix).
- ``update``     — изменить поля проекта (любые, кроме slug).
- ``delete``     — soft archive (по умолчанию) или ``--hard`` для физ. удаления.

Archive engine (см. NP-005 ARCHITECTURE.md §2.7, ADR-001):
- ``archive``    — физический mv в ``_Archive/<group>/`` + обновление БД.
- ``unarchive``  — обратный mv + установка статуса (default: active).
- ``renew``      — инкремент renewal_count + опц. unarchive (только client-project).
- ``move``       — сменить project_type, физ. mv если группа другая.
- ``reorganize`` — проверить + починить расхождения БД ↔ файловая система.

Справочники types/statuses вынесены в отдельные top-level subapp
(`atlas types ...`, `atlas statuses ...`) — см. types.py и statuses.py.
"""
from __future__ import annotations

import json
import os
import re
import shutil
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

from atlas.pm._time import msk_now
from atlas.pm.db import DEFAULT_DB_PATH, make_engine, make_session
from atlas.pm.models import (
    ActionLog,
    Participant,
    Project,
    ProjectParticipant,
    ProjectStatus,
    ProjectTag,
    ProjectType,
    Tag,
)
from atlas.pm.paths import (
    archive_path,
    expected_project_path,
    get_projects_root,
    group_path,
    type_slug_to_group,
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
from atlas.pm.tags import (
    AmbiguousTagRefError,
    InvalidTagCategoryError,
    attach_tags,
    detach_tags,
    filter_projects_by_tags,
    list_project_tags,
    resolve_tag_ref,
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

# Статусы, с которыми можно архивировать проект (status в момент archive).
VALID_ARCHIVE_STATUSES = {"completed", "paused", "frozen", "archived"}


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


def _resolve_tags_or_die(session: Session, tag_refs: list[str]) -> list[Tag]:
    """Резолв списка tag-refs: raise typer.Exit на несуществующий.

    Подсказка в сообщении: `atlas tags add --slug ... --category ...`.
    """
    resolved: list[Tag] = []
    for ref in tag_refs:
        try:
            tag = resolve_tag_ref(session, ref)
        except (AmbiguousTagRefError, InvalidTagCategoryError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        if tag is None:
            console.print(
                f"[red]Tag '{ref}' не найден. "
                f"Создайте: `atlas tags add --slug ... --category ...`.[/red]"
            )
            raise typer.Exit(code=1)
        resolved.append(tag)
    return resolved


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
        "[cyan]2. Заселяю справочники (project_types, project_statuses, participants, tags)...[/cyan]"
    )
    engine = make_engine(url)
    with make_session(engine) as session:
        counts = seed_all(session)
    tags_counts = counts.get("tags", {"created": 0, "skipped": 0})
    console.print(
        f"[green]   ✓ project_types={counts['project_types']}, "
        f"project_statuses={counts['project_statuses']}, "
        f"participants={counts['participants']}[/green]"
    )
    console.print(
        f"[green]   ✓ Tags: created {tags_counts['created']}, "
        f"skipped {tags_counts['skipped']}[/green]"
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
    tags: Optional[list[str]] = typer.Option(
        None, "--tag", "-t",
        help="Тег: 'slug', 'category:slug' или UUID. Можно несколько раз.",
    ),
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

        # ----- tags -----
        tag_slugs_for_log: list[str] = []
        if tags:
            resolved_tags = _resolve_tags_or_die(session, tags)
            tag_slugs_for_log = [t.slug for t in resolved_tags]
            attach_tags(session, project.id, [t.id for t in resolved_tags])

        details: dict[str, Any] = {
            "slug": final_slug,
            "prefix": final_prefix,
            "name": name,
            "type": type_slug,
            "priority": priority,
            "status": status_slug,
        }
        if tag_slugs_for_log:
            details["tags"] = tag_slugs_for_log

        _log_action(
            session,
            action="project_created",
            entity_id=project.id,
            details=details,
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
    tags: Optional[list[str]] = typer.Option(
        None, "--tag", "-t",
        help="Фильтр по тегу (AND-семантика, можно несколько раз).",
    ),
) -> None:
    """Список проектов (табличный вывод)."""
    url = _db_url()
    engine = make_engine(url)

    # AND-фильтр по тегам отдельной функцией.
    # Если есть теги — сначала получаем id'шники проходящих, потом
    # добавляем их в общий запрос как фильтр.
    tag_project_ids: Optional[set[str]] = None
    if tags:
        engine_tmp = engine
        with make_session(engine_tmp) as session_tmp:
            # Резолвим каждый tag ref и собираем фактические slug'и.
            resolved_tags = _resolve_tags_or_die(session_tmp, tags)
            resolved_slugs = [t.slug for t in resolved_tags]
            matching = filter_projects_by_tags(
                session_tmp, resolved_slugs, archived=archived,
            )
            tag_project_ids = {p.id for p in matching}
        if not tag_project_ids:
            console.print("[yellow]Проектов не найдено.[/yellow]")
            return

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
        if tag_project_ids is not None:
            stmt = stmt.where(Project.id.in_(tag_project_ids))

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

        # теги
        project_tags = list_project_tags(session, project.id)

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

    if project_tags:
        console.print("\n[bold]Tags:[/bold]")
        tags_table = Table(show_header=True, header_style="bold")
        tags_table.add_column("Category", style="magenta")
        tags_table.add_column("Slug", style="cyan")
        tags_table.add_column("Name")
        tags_table.add_column("Color", style="dim")
        for tag in project_tags:
            tags_table.add_row(
                tag.category,
                tag.slug,
                tag.name,
                tag.color or "—",
            )
        console.print(tags_table)
    else:
        console.print("\n[dim]Tags: —[/dim]")

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

        project.last_touched_at = msk_now()
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

        project.archived_at = msk_now()
        _log_action(
            session,
            action="project_archived",
            entity_id=project_id,
            details={"slug": slug_for_msg, "at": project.archived_at.isoformat()},
        )
        session.commit()
        console.print(f"[green]✓ Project '{slug_for_msg}' archived[/green]")


# --------------------------------------------------------------------------- #
# add-tags / remove-tags                                                      #
# --------------------------------------------------------------------------- #


@projects_app.command("add-tags")
def add_tags_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
    tags: list[str] = typer.Option(
        ..., "--tag", "-t",
        help="Тег (можно несколько --tag).",
    ),
) -> None:
    """Прикрепить теги к проекту (идемпотентно)."""
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

        resolved = _resolve_tags_or_die(session, tags)
        slugs = [t.slug for t in resolved]
        added = attach_tags(session, project.id, [t.id for t in resolved])

        _log_action(
            session,
            action="project_tags_added",
            entity_id=project.id,
            details={"tag_slugs": slugs, "added": added},
        )
        session.commit()

        console.print(
            f"[green]✓ Project '{project.slug}': attached {added} "
            f"tag(s) ({', '.join(slugs)})[/green]"
        )


@projects_app.command("remove-tags")
def remove_tags_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
    tags: list[str] = typer.Option(
        ..., "--tag", "-t",
        help="Тег (можно несколько --tag).",
    ),
) -> None:
    """Открепить теги от проекта (graceful — отсутствующая связь игнорируется)."""
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

        resolved = _resolve_tags_or_die(session, tags)
        slugs = [t.slug for t in resolved]
        removed = detach_tags(session, project.id, [t.id for t in resolved])

        _log_action(
            session,
            action="project_tags_removed",
            entity_id=project.id,
            details={"tag_slugs": slugs, "removed": removed},
        )
        session.commit()

        console.print(
            f"[green]✓ Project '{project.slug}': detached {removed} "
            f"tag(s) ({', '.join(slugs)})[/green]"
        )


# --------------------------------------------------------------------------- #
# Archive engine helpers                                                      #
# --------------------------------------------------------------------------- #


def _resolve_project_or_die(session: Session, ref: str) -> Project:
    """Resolve project ref с выводом ошибок и typer.Exit."""
    try:
        project = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if project is None:
        console.print(f"[red]Project '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return project


def _status_by_slug_or_die(session: Session, status_slug: str) -> ProjectStatus:
    ps = session.execute(
        select(ProjectStatus).where(ProjectStatus.slug == status_slug)
    ).scalar_one_or_none()
    if ps is None:
        console.print(
            f"[red]Статус '{status_slug}' не найден. См. `atlas statuses list`.[/red]"
        )
        raise typer.Exit(code=1)
    return ps


def _move_folder(src: Path, dst: Path) -> bool:
    """Физически переместить src → dst.

    - Возвращает True если перемещение выполнено; False если src не существует
      (тогда вызывающий продолжит с warning).
    - Создаёт dst.parent через mkdir(parents=True, exist_ok=True).
    - На Windows ``shutil.move`` умеет cross-drive (fallback на copy+delete).
    - Если dst уже существует → ValueError (консистентность — не перезаписываем).
    """
    if not src.exists():
        return False
    if dst.exists():
        raise FileExistsError(
            f"Target уже существует: {dst}. "
            f"Руками проверьте и уберите конфликт."
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return True


# --------------------------------------------------------------------------- #
# archive                                                                     #
# --------------------------------------------------------------------------- #


@projects_app.command("archive")
def archive_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    status: str = typer.Option(
        ..., "--status",
        help=f"Статус в архиве: {' | '.join(sorted(VALID_ARCHIVE_STATUSES))}",
    ),
    keep_path: bool = typer.Option(
        False, "--keep-path",
        help="Не выполнять физический mv, только БД update.",
    ),
) -> None:
    """Архивировать проект: mv в _Archive/<group>/ + обновить БД.

    Маппинг group: client-project → clients, business-product → products, test → tests,
    personal-utility/personal-project/shared-infrastructure → products.
    """
    if status not in VALID_ARCHIVE_STATUSES:
        console.print(
            f"[red]Невалидный --status '{status}': допустимы "
            f"{sorted(VALID_ARCHIVE_STATUSES)}.[/red]"
        )
        raise typer.Exit(code=1)

    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)

        if project.archived_at is not None:
            console.print(
                f"[red]Project '{project.slug}' уже archived "
                f"({project.archived_at}). Используйте `unarchive`.[/red]"
            )
            raise typer.Exit(code=1)

        pt = session.get(ProjectType, project.type_id)
        if pt is None:
            console.print(f"[red]Broken data: project.type_id не найден.[/red]")
            raise typer.Exit(code=1)

        try:
            group = type_slug_to_group(pt.slug)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        target_status = _status_by_slug_or_die(session, status)

        old_local_path = project.local_path
        moved_from: Optional[str] = None
        moved_to: Optional[str] = None
        warning: Optional[str] = None

        if not keep_path and project.local_path:
            src = Path(project.local_path)
            dst = archive_path(root, group, project.slug)
            try:
                moved = _move_folder(src, dst)
            except FileExistsError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            if moved:
                moved_from = str(src)
                moved_to = str(dst)
                project.local_path = str(dst)
            else:
                warning = (
                    f"Source path '{src}' не существует — продолжаю с БД update."
                )
                console.print(f"[yellow]⚠ {warning}[/yellow]")

        # БД-обновления.
        now = msk_now()
        project.archived_at = now
        project.archived_group = group
        project.status_id = target_status.id
        project.last_touched_at = now

        details = {
            "status": status,
            "archived_group": group,
            "moved_from": moved_from,
            "moved_to": moved_to,
            "keep_path": keep_path,
        }
        if warning:
            details["warning"] = warning

        _log_action(
            session,
            action="project_archived",
            entity_id=project.id,
            details=details,
        )
        session.commit()

    console.print(
        f"[green]✓ Project '{project.slug}' archived with status '{status}'[/green]"
    )
    if moved_from and moved_to:
        console.print(f"  Moved: [dim]{moved_from}[/dim] → [bold]{moved_to}[/bold]")
    elif keep_path:
        console.print("  [dim](--keep-path: физический mv пропущен)[/dim]")
    elif old_local_path:
        console.print(f"  [dim](src не существовал: {old_local_path})[/dim]")
    else:
        console.print("  [dim](local_path не задан — только БД update)[/dim]")


# --------------------------------------------------------------------------- #
# unarchive                                                                   #
# --------------------------------------------------------------------------- #


@projects_app.command("unarchive")
def unarchive_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    status: str = typer.Option(
        "active", "--status",
        help="Статус после unarchive (default: active).",
    ),
    keep_path: bool = typer.Option(
        False, "--keep-path",
        help="Не выполнять физический mv, только БД update.",
    ),
) -> None:
    """Вернуть проект из архива: mv из _Archive/ обратно + status=active."""
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)

        if project.archived_at is None:
            console.print(
                f"[red]Project '{project.slug}' не архивирован.[/red]"
            )
            raise typer.Exit(code=1)

        pt = session.get(ProjectType, project.type_id)
        if pt is None:
            console.print(f"[red]Broken data: project.type_id не найден.[/red]")
            raise typer.Exit(code=1)

        # Группа для возврата берётся из актуального type (если type_id изменился
        # между archive и unarchive — возвращаемся в новую группу).
        try:
            target_group = type_slug_to_group(pt.slug)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        type_changed_warning = None
        if project.archived_group and project.archived_group != target_group:
            type_changed_warning = (
                f"project_type изменился после archive: "
                f"archived_group='{project.archived_group}', "
                f"новая group='{target_group}'. Возвращаю в новую."
            )
            console.print(f"[yellow]⚠ {type_changed_warning}[/yellow]")

        target_status = _status_by_slug_or_die(session, status)
        old_status = session.get(ProjectStatus, project.status_id)
        old_status_slug = old_status.slug if old_status else None

        moved_from: Optional[str] = None
        moved_to: Optional[str] = None
        warning: Optional[str] = None

        if not keep_path and project.local_path:
            src = Path(project.local_path)
            dst = group_path(root, pt.slug, project.slug)
            try:
                moved = _move_folder(src, dst)
            except FileExistsError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            if moved:
                moved_from = str(src)
                moved_to = str(dst)
                project.local_path = str(dst)
            else:
                warning = (
                    f"Source path '{src}' не существует — продолжаю с БД update."
                )
                console.print(f"[yellow]⚠ {warning}[/yellow]")

        # БД-обновления.
        now = msk_now()
        project.archived_at = None
        project.archived_group = None
        project.status_id = target_status.id
        project.last_touched_at = now

        details = {
            "old_status": old_status_slug,
            "new_status": status,
            "moved_from": moved_from,
            "moved_to": moved_to,
            "keep_path": keep_path,
        }
        if warning:
            details["warning"] = warning
        if type_changed_warning:
            details["type_changed_warning"] = type_changed_warning

        _log_action(
            session,
            action="project_unarchived",
            entity_id=project.id,
            details=details,
        )
        session.commit()

    console.print(
        f"[green]✓ Project '{project.slug}' unarchived to '{status}'[/green]"
    )
    if moved_from and moved_to:
        console.print(f"  Moved: [dim]{moved_from}[/dim] → [bold]{moved_to}[/bold]")


# --------------------------------------------------------------------------- #
# renew                                                                       #
# --------------------------------------------------------------------------- #


@projects_app.command("renew")
def renew_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
) -> None:
    """Инкремент renewal_count для client-project.

    Если проект в архиве — unarchive + status=active + renewal_count++.
    Если активен — только status=active + renewal_count++.
    """
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)

        pt = session.get(ProjectType, project.type_id)
        if pt is None:
            console.print("[red]Broken data: project.type_id не найден.[/red]")
            raise typer.Exit(code=1)

        if pt.slug != "client-project":
            console.print(
                f"[red]renew имеет смысл только для client-project "
                f"(у проекта тип '{pt.slug}'). "
                f"Для остальных используйте `unarchive`.[/red]"
            )
            raise typer.Exit(code=1)

        was_archived = project.archived_at is not None
        count_before = project.renewal_count
        old_status = session.get(ProjectStatus, project.status_id)
        old_status_slug = old_status.slug if old_status else None

        active_status = _status_by_slug_or_die(session, "active")

        moved_from: Optional[str] = None
        moved_to: Optional[str] = None

        if was_archived:
            # Физический mv из _Archive/<group>/ обратно.
            try:
                target_group = type_slug_to_group(pt.slug)
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)

            if project.local_path:
                src = Path(project.local_path)
                dst = group_path(root, pt.slug, project.slug)
                try:
                    moved = _move_folder(src, dst)
                except FileExistsError as exc:
                    console.print(f"[red]{exc}[/red]")
                    raise typer.Exit(code=1)
                if moved:
                    moved_from = str(src)
                    moved_to = str(dst)
                    project.local_path = str(dst)

            project.archived_at = None
            project.archived_group = None

        project.renewal_count = count_before + 1
        project.status_id = active_status.id
        project.last_touched_at = msk_now()

        details = {
            "renewal_count_before": count_before,
            "renewal_count_after": project.renewal_count,
            "was_archived": was_archived,
            "previous_status": old_status_slug,
            "new_status": "active",
            "moved_from": moved_from,
            "moved_to": moved_to,
        }

        _log_action(
            session,
            action="project_renewed",
            entity_id=project.id,
            details=details,
        )
        session.commit()

    console.print(
        f"[green]✓ Project '{project.slug}' renewed "
        f"(renewal #{project.renewal_count})[/green]"
    )
    if moved_from and moved_to:
        console.print(f"  Moved: [dim]{moved_from}[/dim] → [bold]{moved_to}[/bold]")
    if old_status_slug and old_status_slug != "active":
        console.print(
            f"  Status: [dim]{old_status_slug}[/dim] → [bold]active[/bold]"
        )


# --------------------------------------------------------------------------- #
# move                                                                        #
# --------------------------------------------------------------------------- #


@projects_app.command("move")
def move_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    to_type: str = typer.Option(..., "--to-type", help="Новый project_type.slug"),
) -> None:
    """Сменить project_type проекта + физический mv между группами (если нужно).

    Если старая и новая группы совпадают (e.g. personal-utility → business-product,
    обе → products) — физика не меняется, только БД.
    Для архивного проекта операция запрещена — сначала unarchive.
    """
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)

        if project.archived_at is not None:
            console.print(
                f"[red]Project '{project.slug}' archived — сначала `unarchive`, "
                f"потом `move`.[/red]"
            )
            raise typer.Exit(code=1)

        old_type = session.get(ProjectType, project.type_id)
        if old_type is None:
            console.print("[red]Broken data: project.type_id не найден.[/red]")
            raise typer.Exit(code=1)

        new_type = session.execute(
            select(ProjectType).where(ProjectType.slug == to_type)
        ).scalar_one_or_none()
        if new_type is None:
            console.print(
                f"[red]Тип '{to_type}' не найден. См. `atlas types list`.[/red]"
            )
            raise typer.Exit(code=1)

        if old_type.id == new_type.id:
            console.print(
                f"[yellow]Тип уже '{to_type}' — нечего менять.[/yellow]"
            )
            return

        try:
            old_group = type_slug_to_group(old_type.slug)
            new_group = type_slug_to_group(new_type.slug)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        physical_move = old_group != new_group
        moved_from: Optional[str] = None
        moved_to: Optional[str] = None

        if physical_move and project.local_path:
            src = Path(project.local_path)
            dst = group_path(root, new_type.slug, project.slug)
            try:
                moved = _move_folder(src, dst)
            except FileExistsError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            if moved:
                moved_from = str(src)
                moved_to = str(dst)
                project.local_path = str(dst)

        project.type_id = new_type.id
        project.last_touched_at = msk_now()

        details = {
            "old_type": old_type.slug,
            "new_type": new_type.slug,
            "old_group": old_group,
            "new_group": new_group,
            "moved_from": moved_from,
            "moved_to": moved_to,
            "physical_move": physical_move,
        }

        _log_action(
            session,
            action="project_type_changed",
            entity_id=project.id,
            details=details,
        )
        session.commit()

    console.print(
        f"[green]✓ Project '{project.slug}' type changed: "
        f"[dim]{old_type.slug}[/dim] → [bold]{new_type.slug}[/bold][/green]"
    )
    if moved_from and moved_to:
        console.print(f"  Moved: [dim]{moved_from}[/dim] → [bold]{moved_to}[/bold]")
    elif not physical_move:
        console.print(
            f"  [dim](обе группы = '{new_group}' — физика не меняется)[/dim]"
        )


# --------------------------------------------------------------------------- #
# reorganize                                                                  #
# --------------------------------------------------------------------------- #


@projects_app.command("reorganize")
def reorganize_cmd(
    dry_run: bool = typer.Option(
        True, "--dry-run/--apply",
        help="По умолчанию --dry-run. --apply выполнит фактические изменения.",
    ),
) -> None:
    """Синхронизировать БД ↔ файловая система.

    Действия:
    - **В sync**: expected существует, local_path == expected → OK.
    - **DB drift**: expected существует, local_path ≠ expected → update local_path.
    - **Physical drift**: local_path существует, expected не существует → mv.
    - **Без local_path**: skip (проект без физики — OK).
    - **Broken**: local_path задан, но ни одно место не существует — warning.
    """
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    actions: list[dict[str, Any]] = []

    with make_session(engine) as session:
        projects = session.execute(select(Project)).scalars().all()

        for project in projects:
            pt = session.get(ProjectType, project.type_id)
            if pt is None:
                actions.append({
                    "project_id": project.id,
                    "slug": project.slug,
                    "action": "warn",
                    "reason": "broken type_id",
                })
                continue

            if not project.local_path:
                actions.append({
                    "project_id": project.id,
                    "slug": project.slug,
                    "current": None,
                    "expected": None,
                    "action": "skip",
                })
                continue

            try:
                expected = expected_project_path(
                    root, pt.slug, project.slug,
                    archived=project.archived_at is not None,
                    archived_group=project.archived_group,
                )
            except ValueError:
                actions.append({
                    "project_id": project.id,
                    "slug": project.slug,
                    "action": "warn",
                    "reason": f"unknown type_slug '{pt.slug}'",
                })
                continue

            current = Path(project.local_path)
            current_exists = current.exists()
            expected_exists = expected.exists()
            same_path = (
                current.resolve() == expected.resolve()
                if (current_exists or expected_exists)
                else str(current) == str(expected)
            )

            row: dict[str, Any] = {
                "project_id": project.id,
                "slug": project.slug,
                "current": str(current),
                "expected": str(expected),
            }

            if same_path and expected_exists:
                row["action"] = "ok"
            elif same_path and not expected_exists and not current_exists:
                row["action"] = "warn"
                row["reason"] = "ни current, ни expected не существуют"
            elif not same_path and expected_exists:
                # DB drift: в БД записан не тот путь, но expected есть физически.
                row["action"] = "db-fix"
            elif current_exists and not expected_exists:
                # Physical drift: нужно сделать mv.
                row["action"] = "move"
            else:
                row["action"] = "warn"
                row["reason"] = "неясно состояние"
            actions.append(row)

        # Сводка
        counts = {
            "ok": 0, "db-fix": 0, "move": 0, "skip": 0, "warn": 0,
        }
        for a in actions:
            counts[a.get("action", "warn")] = counts.get(a.get("action", "warn"), 0) + 1

        # Вывод таблицы
        if actions:
            table = Table(title=f"Reorganize plan ({len(actions)} projects)")
            table.add_column("slug", style="cyan")
            table.add_column("current_path", style="dim")
            table.add_column("expected_path", style="bold")
            table.add_column("action", style="magenta")
            for a in actions:
                if a.get("action") == "skip":
                    cur = "—"
                    exp = "—"
                else:
                    cur = a.get("current") or "—"
                    exp = a.get("expected") or "—"
                act = a.get("action", "?")
                if a.get("reason"):
                    act = f"{act} ({a['reason']})"
                table.add_row(a["slug"], cur, exp, act)
            console.print(table)

        console.print(
            f"\nScanned {len(actions)} projects:\n"
            f"  ✓ In sync:      {counts['ok']}\n"
            f"  ⚠ DB drift:     {counts['db-fix']} (will update path in DB)\n"
            f"  🔀 Physical:    {counts['move']} (will move folder)\n"
            f"  • Skipped:      {counts['skip']} (no local_path)\n"
            f"  ⚠ Broken:       {counts['warn']}"
        )

        if dry_run:
            console.print(
                "\n[yellow]Dry run. Use --apply to execute.[/yellow]"
            )
            return

        # --apply: выполняем изменения.
        any_changed = False
        for a in actions:
            action = a.get("action")
            project_id = a.get("project_id")
            if action == "db-fix":
                proj = session.get(Project, project_id)
                if proj is None:
                    continue
                old = proj.local_path
                proj.local_path = a["expected"]
                _log_action(
                    session,
                    action="project_reorganized",
                    entity_id=proj.id,
                    details={
                        "kind": "db-fix",
                        "old_path": old,
                        "new_path": a["expected"],
                    },
                )
                any_changed = True
            elif action == "move":
                proj = session.get(Project, project_id)
                if proj is None:
                    continue
                src = Path(a["current"])
                dst = Path(a["expected"])
                try:
                    moved = _move_folder(src, dst)
                except FileExistsError as exc:
                    console.print(f"[red]{exc}[/red]")
                    continue
                if moved:
                    proj.local_path = str(dst)
                    _log_action(
                        session,
                        action="project_reorganized",
                        entity_id=proj.id,
                        details={
                            "kind": "move",
                            "old_path": str(src),
                            "new_path": str(dst),
                        },
                    )
                    any_changed = True

        if any_changed:
            session.commit()
            console.print("[green]✓ Applied.[/green]")
        else:
            console.print("[dim]Нечего применять.[/dim]")


# --------------------------------------------------------------------------- #
# Note: справочники types/statuses вынесены в отдельные top-level subapp:    #
# `atlas types ...` (src/atlas/pm/commands/types.py)                         #
# `atlas statuses ...` (src/atlas/pm/commands/statuses.py)                   #
# --------------------------------------------------------------------------- #
