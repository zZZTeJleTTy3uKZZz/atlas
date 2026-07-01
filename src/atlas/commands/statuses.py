"""CLI-команды `atlas statuses ...` — справочник project_statuses.

Команды:
- ``add``   — добавить новый lifecycle-статус.
- ``list``  — список статусов (сортирован по order_idx).
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
from atlas.models import ActionLog, Participant, ProjectStatus

app = typer.Typer(
    no_args_is_help=True,
    help="Project statuses: справочник lifecycle-статусов (PM-БД).",
)
console = Console()

SLUG_RE = re.compile(r"^[a-z0-9-]{2,50}$")
DEFAULT_ACTOR_SLUG = default_actor()


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
        entity_type="project_status",
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
@command
def add_cmd(
    slug: str = typer.Option(..., "--slug", help="Уникальный slug"),
    name: str = typer.Option(..., "--name"),
    order_idx: int = typer.Option(..., "--order-idx", help="Порядковый индекс"),
    description: Optional[str] = typer.Option(None, "--description"),
) -> None:
    """Создать новый lifecycle-статус."""
    _validate_slug(slug)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        existing = session.execute(
            select(ProjectStatus).where(ProjectStatus.slug == slug)
        ).scalar_one_or_none()
        if existing is not None:
            console.print(
                f"[red]Project status '{slug}' уже существует.[/red]"
            )
            raise typer.Exit(code=1)

        ps = ProjectStatus(
            slug=slug,
            name=name,
            order_idx=order_idx,
            description=description,
        )
        session.add(ps)
        session.flush()

        _log_action(
            session,
            action="project_status_created",
            entity_id=ps.id,
            details={
                "slug": slug,
                "name": name,
                "order_idx": order_idx,
                "description": description,
            },
        )
        session.commit()

        emit_data(
            {
                "slug": slug,
                "name": name,
                "order_idx": order_idx,
                "description": description,
            },
            text_renderer=lambda d: console.print(
                f"[green]✓ Project status '{d['slug']}' created[/green] · "
                f"{d['name']} (#{d['order_idx']})"
            ),
        )


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


@app.command("list")
@command
def list_cmd() -> None:
    """Список lifecycle-статусов (сортирован по order_idx)."""
    url = _db_url()
    engine = make_engine(url)
    with make_session(engine) as session:
        rows = session.execute(
            select(ProjectStatus).order_by(ProjectStatus.order_idx)
        ).scalars().all()

    data = [
        {
            "slug": s.slug,
            "name": s.name,
            "description": s.description,
            "order_idx": s.order_idx,
        }
        for s in rows
    ]
    emit_table(
        data,
        columns=[
            {"key": "slug", "header": "Slug", "style": "cyan"},
            {"key": "name", "header": "Name"},
            {"key": "description", "header": "Description",
             "format": lambda v: v or ""},
            {"key": "order_idx", "header": "Order", "justify": "right", "style": "dim"},
        ],
        title=f"Project Statuses ({len(data)})",
        empty_message="[yellow]Статусов не найдено.[/yellow]",
    )
