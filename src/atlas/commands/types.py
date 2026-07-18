"""CLI-команды `atlas types ...` — справочник project_types.

Команды:
- ``add``   — добавить новый тип проекта.
- ``list``  — список типов (по умолчанию без архивных).
"""
from __future__ import annotations

from atlas.appconfig import default_actor

import json
import re
from typing import Any, Optional

import typer
from clikit import command, emit_data, emit_table
from rich.console import Console
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import ActionLog, Participant, ProjectType, SyncPolicy

app = typer.Typer(
    no_args_is_help=True,
    help="Project types: справочник типов проектов (PM-БД).",
)
console = Console()

SLUG_RE = re.compile(r"^[a-z0-9-]{2,50}$")
DEFAULT_ACTOR_SLUG = default_actor()
VALID_GROUPS = ("clients", "products", "tests", "inbox")
DEFAULT_GROUP = "products"
DEFAULT_POLICY = "local"


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
    session: Session,
    *,
    action: str,
    entity_id: str,
    details: dict[str, Any],
) -> None:
    entry = ActionLog(
        actor_id=_actor_id(session),
        entity_type="project_type",
        entity_id=entity_id,
        action=action,
        details_json=json.dumps(details, ensure_ascii=False, default=str),
    )
    session.add(entry)


def _validate_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        console.print(
            f"[red]Невалидный slug '{slug}': допустимы [a-z0-9-], длина 2-50.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_group(group: str) -> None:
    if group not in VALID_GROUPS:
        console.print(
            f"[red]Невалидная группа '{group}'. Допустимо: {', '.join(VALID_GROUPS)}.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_policy(session: Session, policy: str) -> None:
    exists = session.execute(
        select(SyncPolicy).where(SyncPolicy.slug == policy)
    ).scalar_one_or_none()
    if exists is None:
        known = session.execute(select(SyncPolicy.slug).order_by(SyncPolicy.slug)).scalars().all()
        console.print(
            f"[red]Неизвестная sync-policy '{policy}'. Известные: {', '.join(known)}.[/red]"
        )
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


@app.command("add")
@command
def add_cmd(
    slug: str = typer.Option(..., "--slug", help="Уникальный slug ([a-z0-9-], 2-50)"),
    name: str = typer.Option(..., "--name"),
    description: Optional[str] = typer.Option(None, "--description"),
    color: Optional[str] = typer.Option(None, "--color", help='Hex, напр. "#FF5733"'),
    group: str = typer.Option(
        DEFAULT_GROUP, "--group",
        help="Физическая группа: clients|products|tests|inbox (дефолт products)",
    ),
    default_sync_policy: str = typer.Option(
        DEFAULT_POLICY, "--default-sync-policy",
        help="slug политики синка (sync_policies); дефолт local",
    ),
) -> None:
    """Создать новый тип проекта."""
    _validate_slug(slug)
    _validate_group(group)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        _validate_policy(session, default_sync_policy)

        existing = session.execute(
            select(ProjectType).where(ProjectType.slug == slug)
        ).scalar_one_or_none()
        if existing is not None:
            console.print(
                f"[red]Project type '{slug}' уже существует.[/red]"
            )
            raise typer.Exit(code=1)

        pt = ProjectType(
            slug=slug,
            name=name,
            description=description,
            color=color,
            storage_group=group,
            default_sync_policy=default_sync_policy,
        )
        session.add(pt)
        session.flush()

        _log_action(
            session,
            action="project_type_created",
            entity_id=pt.id,
            details={
                "slug": slug,
                "name": name,
                "description": description,
                "color": color,
                "storage_group": group,
                "default_sync_policy": default_sync_policy,
            },
        )
        session.commit()

        emit_data(
            {
                "id": pt.id,
                "slug": slug,
                "name": name,
                "storage_group": group,
                "default_sync_policy": default_sync_policy,
            },
            text_renderer=lambda d: console.print(
                f"[green]✓ Project type '{d['slug']}' created[/green] · {d['name']} "
                f"· group={d['storage_group']} · policy={d['default_sync_policy']}"
            ),
        )


# --------------------------------------------------------------------------- #
# edit                                                                        #
# --------------------------------------------------------------------------- #


@app.command("update")
@command
def edit_cmd(
    ref: str = typer.Argument(..., help="slug типа (неизменяемый идентификатор)"),
    name: Optional[str] = typer.Option(None, "--name"),
    description: Optional[str] = typer.Option(None, "--description"),
    color: Optional[str] = typer.Option(None, "--color"),
    group: Optional[str] = typer.Option(
        None, "--group", help="clients|products|tests|inbox"
    ),
    default_sync_policy: Optional[str] = typer.Option(
        None, "--default-sync-policy", help="slug политики синка (sync_policies)"
    ),
) -> None:
    """Отредактировать существующий тип (slug не меняется)."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        pt = session.execute(
            select(ProjectType).where(ProjectType.slug == ref)
        ).scalar_one_or_none()
        if pt is None:
            console.print(f"[red]Project type '{ref}' не найден.[/red]")
            raise typer.Exit(code=1)

        if group is not None:
            _validate_group(group)
        if default_sync_policy is not None:
            _validate_policy(session, default_sync_policy)

        changes: dict[str, Any] = {}
        if name is not None:
            pt.name = name
            changes["name"] = name
        if description is not None:
            pt.description = description
            changes["description"] = description
        if color is not None:
            pt.color = color
            changes["color"] = color
        if group is not None:
            pt.storage_group = group
            changes["storage_group"] = group
        if default_sync_policy is not None:
            pt.default_sync_policy = default_sync_policy
            changes["default_sync_policy"] = default_sync_policy

        if not changes:
            console.print("[yellow]Нечего менять (не передано ни одно поле).[/yellow]")
            raise typer.Exit(code=0)

        _log_action(
            session,
            action="project_type_updated",
            entity_id=pt.id,
            details={"slug": ref, **changes},
        )
        session.commit()

        emit_data(
            {"slug": ref, **changes},
            text_renderer=lambda d: console.print(
                f"[green]✓ Project type '{ref}' updated[/green] · "
                + ", ".join(f"{k}={v}" for k, v in changes.items())
            ),
        )


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


@app.command("list")
@command
def list_cmd(
    archived: bool = typer.Option(
        False, "--archived",
        help="Включать архивные (по умолчанию скрыты)",
    ),
) -> None:
    """Список типов проектов."""
    url = _db_url()
    engine = make_engine(url)
    with make_session(engine) as session:
        stmt = select(ProjectType).order_by(ProjectType.name)
        if not archived:
            stmt = stmt.where(ProjectType.is_archived == False)  # noqa: E712
        rows = session.execute(stmt).scalars().all()

    data = [
        {
            "slug": t.slug,
            "name": t.name,
            "storage_group": t.storage_group,
            "default_sync_policy": t.default_sync_policy,
            "description": t.description,
            "is_archived": t.is_archived,
        }
        for t in rows
    ]
    emit_table(
        data,
        columns=[
            {"key": "slug", "header": "Slug", "style": "cyan", "no_wrap": True},
            {"key": "name", "header": "Name", "no_wrap": True},
            {"key": "storage_group", "header": "Group", "style": "green", "no_wrap": True},
            {"key": "default_sync_policy", "header": "Sync policy",
             "style": "magenta", "no_wrap": True},
            {"key": "description", "header": "Description",
             "format": lambda v: v or ""},
            {"key": "is_archived", "header": "Archived", "justify": "center"},
        ],
        title=f"Project Types ({len(data)})",
        empty_message="[yellow]Типов не найдено.[/yellow]",
    )
