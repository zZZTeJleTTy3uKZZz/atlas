"""CLI-команды `atlas types ...` — справочник project_types.

Команды:
- ``add``   — добавить новый тип проекта.
- ``list``  — список типов (по умолчанию без архивных).
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.models import ActionLog, Participant, ProjectType

app = typer.Typer(
    no_args_is_help=True,
    help="Project types: справочник типов проектов (PM-БД).",
)
console = Console()

SLUG_RE = re.compile(r"^[a-z0-9-]{2,50}$")
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


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


@app.command("add")
def add_cmd(
    slug: str = typer.Option(..., "--slug", help="Уникальный slug ([a-z0-9-], 2-50)"),
    name: str = typer.Option(..., "--name"),
    description: Optional[str] = typer.Option(None, "--description"),
    color: Optional[str] = typer.Option(None, "--color", help='Hex, напр. "#FF5733"'),
) -> None:
    """Создать новый тип проекта."""
    _validate_slug(slug)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
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
            },
        )
        session.commit()

        console.print(
            f"[green]✓ Project type '{slug}' created[/green] · {name}"
        )


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


@app.command("list")
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

    if not rows:
        console.print("[yellow]Типов не найдено.[/yellow]")
        return

    table = Table(title=f"Project Types ({len(rows)})")
    table.add_column("Slug", style="cyan")
    table.add_column("Name")
    table.add_column("Description", overflow="fold")
    table.add_column("Color", style="dim")
    table.add_column("Archived", justify="center")
    for t in rows:
        table.add_row(
            t.slug, t.name, t.description or "",
            t.color or "—",
            "✓" if t.is_archived else "—",
        )
    console.print(table)
