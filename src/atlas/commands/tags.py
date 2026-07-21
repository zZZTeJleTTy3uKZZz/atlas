"""CLI-команды `atlas tags ...`.

CRUD по тегам PM-БД (Atlas).

Команды:
- ``add``    — создать тег (slug auto или явно).
- ``list``   — список тегов (фильтр по category, колонка Projects=COUNT).
- ``get``    — карточка тега (category:slug | bare slug | UUID full | short).
- ``update`` — изменить поля (любые, кроме slug: стабильный ID для агентов).
- ``delete`` — hard delete (--force для каскада по project_tags).
"""
from __future__ import annotations

from atlas.appconfig import default_actor

import json
import re
from typing import Any, Optional

import typer
from clikit import command, emit_data, emit_table
from rich.console import Console
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import ActionLog, Participant, Project, ProjectTag, Tag
from atlas.tags import (
    VALID_CATEGORIES,
    AmbiguousTagRefError,
    InvalidTagCategoryError,
    generate_tag_slug,
    resolve_tag_ref,
)

app = typer.Typer(
    no_args_is_help=True,
    help="Tags management: теги проектов (PM-БД), CRUD.",
)
console = Console()

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

TAG_SLUG_RE = re.compile(r"^[a-z0-9-]{2,50}$")
DEFAULT_ACTOR_SLUG = default_actor()


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
        entity_type="tag",
        entity_id=entity_id,
        action=action,
        details_json=json.dumps(details, ensure_ascii=False, default=str),
    )
    session.add(entry)


def _slug_exists_fn(session: Session):
    def _check(candidate: str) -> bool:
        return session.execute(
            select(Tag.id).where(Tag.slug == candidate)
        ).scalar_one_or_none() is not None
    return _check


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def _validate_category(category: str) -> None:
    if category not in VALID_CATEGORIES:
        console.print(
            f"[red]Невалидная category '{category}': "
            f"допустимы {sorted(VALID_CATEGORIES)}.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_slug(slug: str) -> None:
    if not TAG_SLUG_RE.match(slug):
        console.print(
            f"[red]Невалидный slug '{slug}': допустимы [a-z0-9-], длина 2-50.[/red]"
        )
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------- #
# Resolve helper                                                              #
# --------------------------------------------------------------------------- #


def _resolve_or_die(session: Session, ref: str) -> Tag:
    try:
        tag = resolve_tag_ref(session, ref)
    except AmbiguousTagRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    except InvalidTagCategoryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    if tag is None:
        console.print(f"[red]Tag '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return tag


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


@app.command("add")
@command
def add_cmd(
    name: str = typer.Option(..., "--name", help="Человекочитаемое название тега"),
    category: str = typer.Option(
        ..., "--category",
        help="owner | stack | domain | other",
    ),
    slug: Optional[str] = typer.Option(
        None, "--slug",
        help="Уникальный slug ([a-z0-9-], 2-50). Если не задан — авто из --name.",
    ),
    color: Optional[str] = typer.Option(None, "--color", help="HEX-цвет, напр. #00ACED"),
    description: Optional[str] = typer.Option(None, "--description"),
) -> None:
    """Создать новый тег."""
    _validate_category(category)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        # ----- slug -----
        slug_auto = False
        if slug:
            _validate_slug(slug)
            if _slug_exists_fn(session)(slug):
                console.print(
                    f"[red]Slug '{slug}' занят. Выберите другой.[/red]"
                )
                raise typer.Exit(code=1)
            final_slug = slug
        else:
            final_slug = generate_tag_slug(name, category, _slug_exists_fn(session))
            slug_auto = True

        tag = Tag(
            slug=final_slug,
            name=name,
            category=category,
            color=color,
            description=description,
        )
        session.add(tag)
        session.flush()

        _log_action(
            session,
            action="tag_created",
            entity_id=tag.id,
            details={
                "slug": final_slug,
                "name": name,
                "category": category,
            },
        )
        session.commit()

        def _render(d: dict[str, Any]) -> None:
            if d["slug_auto"]:
                console.print(f"[dim]slug auto-generated: {d['slug']}[/dim]")
            console.print("[green]✓ Tag created[/green]")
            console.print(f"  ID:          {d['id']}")
            console.print(f"  Slug:        {d['slug']}")
            console.print(f"  Name:        {d['name']}")
            console.print(f"  Category:    {d['category']}")
            if d["color"]:
                console.print(f"  Color:       {d['color']}")
            if d["description"]:
                console.print(f"  Description: {d['description']}")

        emit_data(
            {
                "id": tag.id,
                "slug": final_slug,
                "name": name,
                "category": category,
                "color": color,
                "description": description,
                "slug_auto": slug_auto,
            },
            text_renderer=_render,
        )


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


@app.command("list")
@command
def list_cmd(
    category: Optional[str] = typer.Option(
        None, "--category", help="Фильтр: owner | stack | domain | other"
    ),
) -> None:
    """Список тегов с счётчиком проектов на каждый."""
    if category is not None:
        _validate_category(category)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        stmt = (
            select(
                Tag.id,
                Tag.slug,
                Tag.name,
                Tag.category,
                Tag.color,
                Tag.description,
                func.count(ProjectTag.project_id).label("projects_count"),
            )
            .select_from(Tag)
            .outerjoin(ProjectTag, ProjectTag.tag_id == Tag.id)
            .group_by(
                Tag.id,
                Tag.slug,
                Tag.name,
                Tag.category,
                Tag.color,
                Tag.description,
            )
            .order_by(Tag.category, Tag.slug)
        )
        if category is not None:
            stmt = stmt.where(Tag.category == category)

        rows = session.execute(stmt).all()

    data = [
        {
            "id": row.id,
            "slug": row.slug,
            "name": row.name,
            "category": row.category,
            "color": row.color,
            "description": row.description,
            "projects_count": row.projects_count,
        }
        for row in rows
    ]
    emit_table(
        data,
        columns=[
            {"key": "slug", "header": "Slug", "style": "cyan", "no_wrap": True},
            {"key": "name", "header": "Name"},
            {"key": "category", "header": "Category", "style": "magenta"},
            {"key": "color", "header": "Color", "style": "dim"},
            {"key": "description", "header": "Description", "style": "dim"},
            {"key": "projects_count", "header": "Projects", "justify": "right"},
        ],
        title=f"Tags ({len(data)})",
        empty_message="[yellow]Тегов не найдено.[/yellow]",
    )


# --------------------------------------------------------------------------- #
# get                                                                         #
# --------------------------------------------------------------------------- #


@app.command("get")
@command
def get_cmd(
    ref: str = typer.Argument(
        ..., help="'category:slug' | slug | UUID full | UUID short prefix (≥ 7 chars)"
    ),
) -> None:
    """Карточка тега."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        tag = _resolve_or_die(session, ref)

        # проекты, которым прикреплён этот тег
        proj_rows = session.execute(
            select(Project.slug, Project.name)
            .join(ProjectTag, ProjectTag.project_id == Project.id)
            .where(ProjectTag.tag_id == tag.id)
            .order_by(Project.slug)
        ).all()

        # 5 последних записей action_log для этого тега
        log_rows = session.execute(
            select(ActionLog)
            .where(ActionLog.entity_type == "tag")
            .where(ActionLog.entity_id == tag.id)
            .order_by(ActionLog.timestamp.desc())
            .limit(5)
        ).scalars().all()

        data = {
            "id": tag.id,
            "slug": tag.slug,
            "name": tag.name,
            "category": tag.category,
            "color": tag.color,
            "description": tag.description,
            "created_at": tag.created_at.isoformat() if tag.created_at else None,
            "projects": [
                {"slug": row.slug, "name": row.name} for row in proj_rows
            ],
            "recent_activity": [
                {
                    "timestamp": entry.timestamp.strftime("%Y-%m-%d %H:%M"),
                    "action": entry.action,
                }
                for entry in log_rows
            ],
        }

    def _render(d: dict[str, Any]) -> None:
        console.print(f"[bold cyan]{d['slug']}[/bold cyan] — {d['name']}")
        console.print(f"  ID:          {d['id']}")
        console.print(f"  Slug:        {d['slug']}")
        console.print(f"  Name:        {d['name']}")
        console.print(f"  Category:    {d['category']}")
        if d["color"]:
            console.print(f"  Color:       {d['color']}")
        if d["description"]:
            console.print(f"  Description: {d['description']}")
        console.print(f"  Created:     {d['created_at']}")

        if d["projects"]:
            console.print("\n[bold]Projects:[/bold]")
            for row in d["projects"]:
                console.print(f"  • {row['slug']} ({row['name']})")
        else:
            console.print("\n[dim]Projects: —[/dim]")

        if d["recent_activity"]:
            console.print("\n[bold]Recent activity:[/bold]")
            for entry in d["recent_activity"]:
                console.print(f"  • {entry['timestamp']} — {entry['action']}")

    emit_data(data, text_renderer=_render)


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #


@app.command("update")
@command
def update_cmd(
    ref: str = typer.Argument(..., help="ref тега"),
    name: Optional[str] = typer.Option(None, "--name"),
    category: Optional[str] = typer.Option(None, "--category"),
    color: Optional[str] = typer.Option(None, "--color"),
    description: Optional[str] = typer.Option(None, "--description"),
    slug: Optional[str] = typer.Option(
        None, "--slug",
        help="ЗАПРЕЩЕНО менять slug — стабильный ID для агентов.",
    ),
) -> None:
    """Обновить поля тега (любые, кроме slug)."""
    if slug is not None:
        console.print(
            "[red]Изменение slug запрещено: slug — стабильный идентификатор "
            "для агентов/скриптов.[/red]"
        )
        raise typer.Exit(code=1)

    if category is not None:
        _validate_category(category)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        tag = _resolve_or_die(session, ref)

        diffs: dict[str, dict[str, Any]] = {}

        def _maybe_update(field: str, new_value: Any) -> None:
            if new_value is None:
                return
            old_value = getattr(tag, field)
            if old_value != new_value:
                diffs[field] = {"old": old_value, "new": new_value}
                setattr(tag, field, new_value)

        _maybe_update("name", name)
        _maybe_update("category", category)
        _maybe_update("color", color)
        _maybe_update("description", description)

        if not diffs:
            console.print("[yellow]Нечего обновлять.[/yellow]")
            return

        _log_action(
            session,
            action="tag_updated",
            entity_id=tag.id,
            details=diffs,
        )
        session.commit()

        tag_slug = tag.slug

        def _render(d: dict[str, Any]) -> None:
            console.print(
                f"[green]✓ Tag '{d['slug']}' updated[/green] "
                f"({len(d['diffs'])} field(s))"
            )
            for field, diff in d["diffs"].items():
                console.print(
                    f"  {field}: [dim]{diff['old']}[/dim] → [bold]{diff['new']}[/bold]"
                )

        emit_data(
            {"slug": tag_slug, "diffs": diffs},
            text_renderer=_render,
        )


# --------------------------------------------------------------------------- #
# delete                                                                      #
# --------------------------------------------------------------------------- #


@app.command("delete")
@command
def delete_cmd(
    ref: str = typer.Argument(..., help="ref тега"),
    force: bool = typer.Option(
        False, "--force",
        help="Cascade: отвязать от всех проектов и удалить тег.",
    ),
) -> None:
    """Удалить тег.

    - Если ни к одному проекту не прикреплён → hard delete без подтверждения.
    - Если прикреплён к 1+ проектам и без --force → error.
    - С --force → удалить все project_tags + сам тег.
    """
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        tag = _resolve_or_die(session, ref)
        slug_for_msg = tag.slug
        tag_id = tag.id

        attached_count = session.execute(
            select(func.count()).select_from(ProjectTag)
            .where(ProjectTag.tag_id == tag_id)
        ).scalar() or 0

        if attached_count > 0 and not force:
            console.print(
                f"[red]Tag '{slug_for_msg}' attached to {attached_count} project(s). "
                "Use --force to delete tag and detach from all.[/red]"
            )
            raise typer.Exit(code=1)

        detached = 0
        if attached_count > 0 and force:
            result = session.execute(
                delete(ProjectTag).where(ProjectTag.tag_id == tag_id)
            )
            detached = result.rowcount or attached_count

        # Лог ДО удаления (flush позже)
        details: dict[str, Any] = {
            "slug": slug_for_msg,
            "name": tag.name,
            "category": tag.category,
        }
        if force:
            details["detached_projects_count"] = detached

        _log_action(
            session,
            action="tag_deleted",
            entity_id=tag_id,
            details=details,
        )
        session.flush()
        session.delete(tag)
        session.commit()

        def _render(d: dict[str, Any]) -> None:
            cascade_msg = ""
            if d["forced"] and d["detached_projects_count"] > 0:
                cascade_msg = (
                    f" (detached from {d['detached_projects_count']} project(s))"
                )
            console.print(
                f"[red]✗ Tag '{d['slug']}' deleted[/red]{cascade_msg}"
            )

        emit_data(
            {
                "slug": slug_for_msg,
                "deleted": True,
                "forced": force,
                "detached_projects_count": detached,
            },
            text_renderer=_render,
        )
