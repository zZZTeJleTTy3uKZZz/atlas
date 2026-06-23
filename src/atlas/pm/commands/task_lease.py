"""CLI lease/claim для `atlas task` (Волна 8): claim / release / renew / take / stale.

Команды декорируют существующий ``pm_tasks_app`` и регистрируются импортом этого
модуля (см. ``atlas/cli.py``). Вся логика — в ``pm/lease.py`` (pure-logic);
здесь только резолв ref/actor, парсинг TTL, вывод и маппинг ошибок в exit-коды.
"""
from __future__ import annotations

from typing import Any, Optional

import typer
from clikit import command, emit_data
from rich.console import Console
from sqlalchemy import select

from atlas.pm import lease as L
from atlas.pm._time import msk_now
from atlas.pm.commands.pm_tasks import _resolve_task_or_die, pm_tasks_app
from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.models import Task

console = Console()


# --------------------------------------------------------------------------- #
# Хелперы                                                                     #
# --------------------------------------------------------------------------- #


def _resolve_actor_or_die(session, actor_slug: Optional[str]):
    try:
        return L.resolve_actor(session, actor_slug)
    except L.LeaseError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)


def _parse_ttl_or_die(raw: str):
    try:
        return L.parse_ttl(raw)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)


def _lease_data(session, task: Task, action: str) -> dict[str, Any]:
    return {
        "action": action,
        "task": task.slug or task.id,
        "number": task.number,
        "title": task.title,
        "status": task.status,
        "lease_owner": L._holder_slug(session, task.lease_owner),
        "lease_session_id": task.lease_session_id,
        "lease_origin": task.lease_origin,
        "claimed_at": task.claimed_at.isoformat() if task.claimed_at else None,
        "lease_expires_at": (
            task.lease_expires_at.isoformat() if task.lease_expires_at else None
        ),
    }


def _render_lease(d: dict[str, Any]) -> None:
    icons = {"claimed": "🔒", "released": "🔓", "renewed": "♻", "taken": "⚡"}
    console.print(f"{icons.get(d['action'], '')} [bold cyan]{d['task']}[/bold cyan] — {d['action']}")
    console.print(f"  Status:  {d['status']}")
    if d["lease_owner"]:
        line = f"  Lease:   {d['lease_owner']}"
        if d["lease_session_id"]:
            line += f" · сессия {d['lease_session_id']}"
        if d["lease_origin"]:
            line += f" · откуда {d['lease_origin']}"
        console.print(line)
    if d["lease_expires_at"]:
        console.print(f"  До:      {d['lease_expires_at']}")


def _render_stale(d: dict[str, Any]) -> None:
    if "reaped" in d:
        console.print(f"[green]Освобождено протухших lease: {d['count']}[/green]")
        for slug in d["reaped"]:
            console.print(f"  🔓 {slug}")
        return
    console.print(f"Протухших lease: {d['count']}")
    for row in d["stale"]:
        console.print(f"  ⏰ {row['task']} — {row['owner']} (истёк {row['expired_at']})")


# --------------------------------------------------------------------------- #
# Команды                                                                     #
# --------------------------------------------------------------------------- #


@pm_tasks_app.command("claim")
@command
def claim_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    ttl: str = typer.Option("2h", "--ttl", help="TTL аренды: 2h / 30m / 90s / 1d"),
    actor: Optional[str] = typer.Option(
        None, "--actor", help="participant slug (default: env ATLAS_ACTOR / dmitry)"
    ),
    session_id: Optional[str] = typer.Option(
        None, "--session", help="id сессии Claude Code (default: env ATLAS_SESSION)"
    ),
    origin: Optional[str] = typer.Option(
        None, "--from", help="откуда взято (default: env ATLAS_FROM / cwd)"
    ),
) -> None:
    """Взять задачу в работу: lease + status=in_progress + assignee=actor."""
    ttl_td = _parse_ttl_or_die(ttl)
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        L.expire_stale_leases(session)  # ленивый reaper
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            L.claim_task(
                session, task, actor_p,
                session_id=L.resolve_session_id(session_id),
                origin=L.resolve_origin(origin), ttl=ttl_td,
            )
        except L.LeaseHeldError as exc:
            console.print(f"[red]✗ {exc}[/red]")
            raise typer.Exit(code=1)
        session.commit()
        data = _lease_data(session, task, "claimed")
    emit_data(data, text_renderer=_render_lease)


@pm_tasks_app.command("release")
@command
def release_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Отпустить lease (только держатель). Статус не меняется."""
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            L.release_task(session, task, actor_p)
        except L.LeaseNotOwnedError as exc:
            console.print(f"[red]✗ {exc}[/red]")
            raise typer.Exit(code=1)
        session.commit()
        data = _lease_data(session, task, "released")
    emit_data(data, text_renderer=_render_lease)


@pm_tasks_app.command("renew")
@command
def renew_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    ttl: str = typer.Option("2h", "--ttl", help="новый TTL: 2h / 30m / 1d"),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Продлить lease (heartbeat; только держатель)."""
    ttl_td = _parse_ttl_or_die(ttl)
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            L.renew_lease(session, task, actor_p, ttl=ttl_td)
        except L.LeaseNotOwnedError as exc:
            console.print(f"[red]✗ {exc}[/red]")
            raise typer.Exit(code=1)
        session.commit()
        data = _lease_data(session, task, "renewed")
    emit_data(data, text_renderer=_render_lease)


@pm_tasks_app.command("take")
@command
def take_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    force: bool = typer.Option(
        False, "--force", help="подтвердить принудительный отбор lease"
    ),
    ttl: str = typer.Option("2h", "--ttl"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    session_id: Optional[str] = typer.Option(None, "--session"),
    origin: Optional[str] = typer.Option(None, "--from"),
) -> None:
    """Принудительно отобрать задачу (даже занятую/протухшую). Требует --force."""
    if not force:
        console.print("[red]✗ take требует --force (принудительный отбор lease)[/red]")
        raise typer.Exit(code=1)
    ttl_td = _parse_ttl_or_die(ttl)
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            L.take_task(
                session, task, actor_p,
                session_id=L.resolve_session_id(session_id),
                origin=L.resolve_origin(origin), ttl=ttl_td,
            )
        except L.OptimisticLockError as exc:
            console.print(f"[red]✗ {exc}[/red]")
            raise typer.Exit(code=1)
        session.commit()
        data = _lease_data(session, task, "taken")
    emit_data(data, text_renderer=_render_lease)


@pm_tasks_app.command("stale")
@command
def stale_cmd(
    reap: bool = typer.Option(
        False, "--reap", help="освободить протухшие lease (иначе только отчёт)"
    ),
) -> None:
    """Протухшие lease: отчёт (по умолчанию) или освобождение (--reap)."""
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        now = msk_now()
        if reap:
            freed = L.expire_stale_leases(session, now=now)
            session.commit()
            data: dict[str, Any] = {
                "reaped": [t.slug or t.id for t in freed],
                "count": len(freed),
            }
        else:
            stale = (
                session.execute(
                    select(Task).where(
                        Task.lease_owner.is_not(None),
                        Task.lease_expires_at.is_not(None),
                        Task.lease_expires_at < now,
                    )
                )
                .scalars()
                .all()
            )
            data = {
                "stale": [
                    {
                        "task": t.slug or t.id,
                        "owner": L._holder_slug(session, t.lease_owner),
                        "expired_at": t.lease_expires_at.isoformat(),
                    }
                    for t in stale
                ],
                "count": len(stale),
            }
    emit_data(data, text_renderer=_render_stale)
