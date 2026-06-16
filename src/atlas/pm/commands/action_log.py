"""CLI-команды `atlas action-log ...` — append-only audit log.

Только ``list``. ActionLog — append-only, никаких add/update/delete.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.models import ActionLog, Participant, Task
from atlas.pm.slugs import AmbiguousRefError, resolve_project_ref

app = typer.Typer(
    no_args_is_help=True,
    help="Action log: append-only audit для PM-БД (NP-005).",
)
console = Console()


@app.callback()
def _callback() -> None:
    """Force Typer to treat sub-commands as nested even when only one exists."""
    return


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _db_url() -> str:
    return resolve_db_url()


def _resolve_actor_id(session: Session, slug: str) -> Optional[str]:
    p = session.execute(
        select(Participant).where(Participant.slug == slug)
    ).scalar_one_or_none()
    if p is None:
        console.print(f"[red]Actor '{slug}' не найден.[/red]")
        raise typer.Exit(code=1)
    return p.id


def _truncate_details(details_json: Optional[str], max_len: int = 60) -> str:
    if not details_json:
        return ""
    try:
        data = json.loads(details_json)
        compact = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except json.JSONDecodeError:
        compact = details_json
    if len(compact) > max_len:
        return compact[: max_len - 1] + "…"
    return compact


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


@app.command("list")
def list_cmd(
    project: Optional[str] = typer.Option(
        None, "--project",
        help="Project ref. Фильтрует по project + всем его tasks.",
    ),
    actor: Optional[str] = typer.Option(
        None, "--actor", help="Slug участника (actor)",
    ),
    entity_type: Optional[str] = typer.Option(
        None, "--entity-type",
        help="project | task | sprint | participant | project_type | ...",
    ),
    action: Optional[str] = typer.Option(
        None, "--action", help="Точное имя action: created / updated / ...",
    ),
    since: Optional[str] = typer.Option(
        None, "--since", help="ISO-дата YYYY-MM-DD: только события >= этой даты",
    ),
    limit: int = typer.Option(50, "--limit", help="Макс. строк (default 50)"),
) -> None:
    """Показать записи action_log с фильтрами."""
    since_dt: Optional[datetime] = None
    if since is not None:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            console.print(
                f"[red]Невалидный --since '{since}': ожидаю YYYY-MM-DD.[/red]"
            )
            raise typer.Exit(code=1)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        stmt = select(ActionLog).order_by(ActionLog.timestamp.desc())

        if project is not None:
            try:
                proj = resolve_project_ref(session, project)
            except AmbiguousRefError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            if proj is None:
                console.print(f"[red]Project '{project}' не найден.[/red]")
                raise typer.Exit(code=1)
            task_ids = [
                tid for (tid,) in session.execute(
                    select(Task.id).where(Task.project_id == proj.id)
                ).all()
            ]
            from sqlalchemy import and_, or_
            conditions = [
                and_(
                    ActionLog.entity_type == "project",
                    ActionLog.entity_id == proj.id,
                ),
            ]
            if task_ids:
                conditions.append(
                    and_(
                        ActionLog.entity_type == "task",
                        ActionLog.entity_id.in_(task_ids),
                    )
                )
            stmt = stmt.where(or_(*conditions))

        if actor is not None:
            actor_id = _resolve_actor_id(session, actor)
            stmt = stmt.where(ActionLog.actor_id == actor_id)
        if entity_type is not None:
            stmt = stmt.where(ActionLog.entity_type == entity_type)
        if action is not None:
            stmt = stmt.where(ActionLog.action == action)
        if since_dt is not None:
            stmt = stmt.where(ActionLog.timestamp >= since_dt)

        stmt = stmt.limit(limit)
        rows = session.execute(stmt).scalars().all()

        # Для отображения — соберём slug-ы actor'ов одним запросом
        actor_ids = {r.actor_id for r in rows if r.actor_id}
        actor_map: dict[str, str] = {}
        if actor_ids:
            for p in session.execute(
                select(Participant).where(Participant.id.in_(actor_ids))
            ).scalars().all():
                actor_map[p.id] = p.slug

    if not rows:
        console.print("[yellow]Нет записей action_log.[/yellow]")
        return

    table = Table(title=f"Action Log ({len(rows)})")
    table.add_column("Timestamp", style="cyan", no_wrap=True)
    table.add_column("Actor", style="green")
    table.add_column("Entity", style="magenta")
    table.add_column("Action", style="bold", no_wrap=True)
    table.add_column("Details", overflow="fold")

    for r in rows:
        ts = r.timestamp.strftime("%Y-%m-%d %H:%M") if r.timestamp else "—"
        actor_slug = actor_map.get(r.actor_id, "—") if r.actor_id else "—"
        entity = (
            f"{r.entity_type}:{(r.entity_id or '')[:8]}"
            if r.entity_id else r.entity_type
        )
        table.add_row(ts, actor_slug, entity, r.action, _truncate_details(r.details_json))
    console.print(table)
