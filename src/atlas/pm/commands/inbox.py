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
from clikit import command, emit_data, emit_table
from rich.console import Console
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.models import (
    ActionLog,
    Participant,
    Project,
    ProjectStatus,
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
@command
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

    def _render(d: dict[str, Any]) -> None:
        console.print(f"[green]✓ Inbox '{d['slug']}' created[/green]")
        console.print(f"  Name:     {d['name']}")
        console.print(f"  Priority: {d['priority']}")
        console.print(f"  Path:     {d['path']}")

    emit_data(
        {
            "slug": final_slug,
            "name": name,
            "priority": priority,
            "status": status_slug,
            "path": str(item_path),
        },
        text_renderer=_render,
    )


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


@inbox_app.command("list")
@command
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

        data: list[dict[str, Any]] = []
        for p in items:
            ps = session.get(ProjectStatus, p.status_id)
            data.append({
                "slug": p.slug,
                "name": p.name,
                "status": ps.slug if ps else "?",
                "priority": p.priority,
                "path": p.local_path,
            })

        emit_table(
            data,
            columns=[
                ("slug", "Slug"),
                ("name", "Name"),
                ("status", "Status"),
                ("priority", "P"),
                ("path", "Path"),
            ],
            title=f"Inbox ({len(data)})",
            empty_message="[dim]Inbox пуст.[/dim]",
        )


# --------------------------------------------------------------------------- #
# show                                                                        #
# --------------------------------------------------------------------------- #


@inbox_app.command("show")
@command
def show_cmd(ref: str = typer.Argument(...)) -> None:
    """Карточка inbox-записи."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        proj = _resolve_inbox_or_die(session, ref)
        ps = session.get(ProjectStatus, proj.status_id)
        tags = list_project_tags(session, proj.id)

        # ----- содержимое директории / файла -----
        contents: Optional[list[str]] = None
        contents_more = 0
        single_file: Optional[dict[str, Any]] = None
        if proj.local_path and Path(proj.local_path).exists():
            path = Path(proj.local_path)
            if path.is_dir():
                children = list(path.iterdir())
                contents = [c.name for c in children[:20]]
                contents_more = max(0, len(children) - 20)
            else:
                single_file = {
                    "name": path.name,
                    "size": path.stat().st_size,
                }

        data = {
            "slug": proj.slug,
            "name": proj.name,
            "kind": "inbox",
            "status": ps.slug if ps else None,
            "priority": proj.priority,
            "created": f"{proj.created_at:%Y-%m-%d}",
            "path": proj.local_path,
            "tags": [t.slug for t in tags],
            "contents": contents,
            "contents_more": contents_more,
            "single_file": single_file,
        }

    def _render(d: dict[str, Any]) -> None:
        console.print(f"[bold]{d['slug']}[/bold]  — {d['name']}")
        console.print(f"  Kind:     inbox")
        console.print(f"  Status:   {d['status'] or '—'}")
        console.print(f"  Priority: {d['priority']}")
        console.print(f"  Created:  {d['created']}")
        console.print(f"  Path:     {d['path']}")
        if d["tags"]:
            console.print(f"  Tags:     {', '.join(d['tags'])}")

        path_name = Path(d["path"]).name if d["path"] else ""
        if d["contents"] is not None:
            console.print(f"\n[bold]--- {path_name}/ contents ---[/bold]")
            if d["contents"]:
                for name in d["contents"]:
                    console.print(f"  • {name}")
                if d["contents_more"]:
                    console.print(f"  ... ({d['contents_more']} more)")
            else:
                console.print("  [dim](пусто)[/dim]")
        elif d["single_file"] is not None:
            console.print(f"\n[bold]--- {path_name}/ contents ---[/bold]")
            console.print(
                f"  (one file: {d['single_file']['name']}, "
                f"{d['single_file']['size']} bytes)"
            )

    emit_data(data, text_renderer=_render)
