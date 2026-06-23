"""CLI-команды `atlas inbox ...` (W45-38).

Inbox = `Project` с `entity_kind='inbox'`. Свалка сырых материалов
(PDF, голосовые, дампы, заметки) для разбора AI-агентом.

Команды (минимум на старте):
- ``add``  — зарегистрировать inbox-материал (создать запись в БД +
             папку `_Inbox/<slug>/`).
- ``list`` — список всех inbox-материалов.
- ``show`` — карточка inbox-записи.

Будущее (TODO для W45-38+):
- ``triage`` — AI-команда: читает содержимое inbox-папки, предлагает куда
               распределить (idea / project / archive / specific project).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm._time import local_now
from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.models import (
    ActionLog,
    Participant,
    Project,
    ProjectStatus,
    ProjectTag,
    ProjectType,
    Tag,
)
from atlas.pm.paths import INBOX_FOLDER_NAME, get_projects_root
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
    list_project_tags,
    resolve_tag_ref,
)


inbox_app = typer.Typer(
    no_args_is_help=True,
    help="Inbox: свалка сырого материала (entity_kind=inbox) для AI-разбора.",
)
console = Console()

DEFAULT_ACTOR_SLUG = "dmitry"


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _db_url() -> str:
    return resolve_db_url()


def _actor_id(session: Session) -> Optional[str]:
    actor = session.execute(
        select(Participant).where(Participant.slug == DEFAULT_ACTOR_SLUG)
    ).scalar_one_or_none()
    return actor.id if actor else None


def _log_action(
    session: Session, *, action: str, entity_id: str, details: dict[str, Any]
) -> None:
    entry = ActionLog(
        actor_id=_actor_id(session),
        entity_type="project",
        entity_id=entity_id,
        action=action,
        details_json=json.dumps(details, ensure_ascii=False, default=str),
    )
    session.add(entry)


def _resolve_tags_or_die(session: Session, tag_refs: list[str]) -> list[Tag]:
    resolved: list[Tag] = []
    for ref in tag_refs:
        try:
            tag = resolve_tag_ref(session, ref)
        except (AmbiguousTagRefError, InvalidTagCategoryError) as exc:
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


def _slug_exists(session: Session, candidate: str) -> bool:
    return session.execute(
        select(Project.id).where(Project.slug == candidate)
    ).scalar_one_or_none() is not None


def _resolve_inbox_or_die(session: Session, ref: str) -> Project:
    try:
        proj = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if proj is None:
        console.print(f"[red]Inbox-запись '{ref}' не найдена.[/red]")
        raise typer.Exit(code=1)
    if proj.entity_kind != "inbox":
        console.print(
            f"[red]Project '{proj.slug}' не inbox (entity_kind={proj.entity_kind}).[/red]"
        )
        raise typer.Exit(code=1)
    return proj


def _resolve_inbox_type(session: Session) -> ProjectType:
    """Получить ProjectType slug='inbox' (или создать-зашикакать ошибкой).

    Inbox-проекты в БД хранят type_id=inbox-тип (исторический атрибут с
    миграции 005). entity_kind='inbox' — отдельный новый атрибут.
    """
    pt = session.execute(
        select(ProjectType).where(ProjectType.slug == "inbox")
    ).scalar_one_or_none()
    if pt is None:
        console.print(
            "[red]Тип 'inbox' не найден в project_types. "
            "Запустите `atlas projects init`.[/red]"
        )
        raise typer.Exit(code=1)
    return pt


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


@inbox_app.command("add")
def add_cmd(
    name: str = typer.Option(..., "--name", help="Название inbox-материала"),
    slug: Optional[str] = typer.Option(None, "--slug"),
    one_line: Optional[str] = typer.Option(
        None, "--one-line",
        help="Краткое описание (что это / откуда взяли / куда планируем)",
    ),
    priority: str = typer.Option("P3", "--priority", help="P0|P1|P2|P3"),
    status_slug: str = typer.Option(
        "active", "--status",
        help="Статус (default: active = ждёт разбора).",
    ),
    tags: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Теги (например, owner:dmitry, source:notion)"
    ),
    create_dir: bool = typer.Option(
        True, "--create-dir/--no-create-dir",
        help="Создать `_Inbox/<slug>/` директорию для материалов.",
    ),
) -> None:
    """Зарегистрировать inbox-материал."""
    if priority not in {"P0", "P1", "P2", "P3"}:
        console.print(f"[red]Невалидный priority '{priority}'.[/red]")
        raise typer.Exit(code=1)

    engine = make_engine(_db_url())
    with make_session(engine) as session:
        pt = _resolve_inbox_type(session)
        ps = session.execute(
            select(ProjectStatus).where(ProjectStatus.slug == status_slug)
        ).scalar_one_or_none()
        if ps is None:
            console.print(f"[red]Статус '{status_slug}' не найден.[/red]")
            raise typer.Exit(code=1)

        # ----- slug -----
        if slug:
            if _slug_exists(session, slug):
                console.print(f"[red]Slug '{slug}' занят.[/red]")
                raise typer.Exit(code=1)
            final_slug = slug
        else:
            base = slugify_text(name)
            try:
                final_slug = generate_unique_slug(
                    base, lambda s: _slug_exists(session, s)
                )
            except SlugGenerationError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)

        # ----- prefix (auto) -----
        from atlas.pm.commands.projects import _generate_unique_prefix

        base_prefix = generate_prefix_from_slug(final_slug)
        try:
            final_prefix = _generate_unique_prefix(session, base_prefix)
        except SlugGenerationError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        # ----- physical path -----
        root = get_projects_root()
        inbox_dir = root / INBOX_FOLDER_NAME
        inbox_dir.mkdir(parents=True, exist_ok=True)
        item_path = inbox_dir / final_slug
        if create_dir and not item_path.exists():
            item_path.mkdir(parents=True, exist_ok=True)

        # ----- create -----
        project = Project(
            slug=final_slug,
            prefix=final_prefix,
            name=name,
            type_id=pt.id,
            status_id=ps.id,
            priority=priority,
            one_line_summary=one_line or "",
            entity_kind="inbox",
            local_path=str(item_path),
        )
        session.add(project)
        session.flush()

        # ----- tags -----
        tag_slugs_for_log: list[str] = []
        if tags:
            resolved_tags = _resolve_tags_or_die(session, tags)
            tag_slugs_for_log = [t.slug for t in resolved_tags]
            attach_tags(session, project.id, [t.id for t in resolved_tags])

        _log_action(
            session,
            action="inbox_created",
            entity_id=project.id,
            details={
                "slug": final_slug,
                "priority": priority,
                "status": status_slug,
                "tags": tag_slugs_for_log,
            },
        )
        session.commit()

    console.print(f"[green]✓ Inbox '{final_slug}' created[/green]")
    console.print(f"  Name:     {name}")
    console.print(f"  Priority: {priority}")
    console.print(f"  Path:     {item_path}")


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


@inbox_app.command("list")
def list_cmd(
    status_slug: Optional[str] = typer.Option(None, "--status"),
    tags: Optional[list[str]] = typer.Option(None, "--tag", "-t"),
) -> None:
    """Список inbox-материалов (entity_kind='inbox')."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        stmt = select(Project).where(Project.entity_kind == "inbox")
        if status_slug:
            ps = session.execute(
                select(ProjectStatus).where(ProjectStatus.slug == status_slug)
            ).scalar_one_or_none()
            if ps:
                stmt = stmt.where(Project.status_id == ps.id)

        items = list(session.execute(stmt).scalars().all())

        if tags:
            from atlas.pm.tags import filter_projects_by_tags

            resolved = _resolve_tags_or_die(session, tags)
            tag_slugs = [t.slug for t in resolved]
            allowed_set = {p.id for p in filter_projects_by_tags(
                session, tag_slugs, archived=False
            )}
            items = [p for p in items if p.id in allowed_set]

        if not items:
            console.print("[dim]Inbox пуст.[/dim]")
            return

        table = Table(title=f"Inbox ({len(items)})")
        table.add_column("Slug")
        table.add_column("Name")
        table.add_column("Status")
        table.add_column("P")
        table.add_column("Path")
        for p in items:
            ps = session.get(ProjectStatus, p.status_id)
            table.add_row(
                p.slug,
                p.name,
                ps.slug if ps else "?",
                p.priority,
                p.local_path or "—",
            )
        console.print(table)


# --------------------------------------------------------------------------- #
# show                                                                        #
# --------------------------------------------------------------------------- #


@inbox_app.command("show")
def show_cmd(ref: str = typer.Argument(...)) -> None:
    """Карточка inbox-записи."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        proj = _resolve_inbox_or_die(session, ref)
        ps = session.get(ProjectStatus, proj.status_id)
        tags = list_project_tags(session, proj.id)

        console.print(f"[bold]{proj.slug}[/bold]  — {proj.name}")
        console.print(f"  Kind:     inbox")
        console.print(f"  Status:   {ps.slug if ps else '—'}")
        console.print(f"  Priority: {proj.priority}")
        console.print(f"  Created:  {proj.created_at:%Y-%m-%d}")
        console.print(f"  Path:     {proj.local_path}")
        if tags:
            console.print(f"  Tags:     {', '.join(t.slug for t in tags)}")

    if proj.local_path and Path(proj.local_path).exists():
        path = Path(proj.local_path)
        console.print(f"\n[bold]--- {path.name}/ contents ---[/bold]")
        if path.is_dir():
            children = list(path.iterdir())
            if children:
                for c in children[:20]:
                    console.print(f"  • {c.name}")
                if len(children) > 20:
                    console.print(f"  ... ({len(children) - 20} more)")
            else:
                console.print("  [dim](пусто)[/dim]")
        else:
            console.print(f"  (one file: {path.name}, "
                          f"{path.stat().st_size} bytes)")
