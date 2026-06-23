"""CLI lease/claim для `atlas task` (Волна 8): claim / release / renew / take / stale.

Команды декорируют существующий ``task_app`` и регистрируются импортом этого
модуля (см. ``atlas/cli.py``). Вся логика — в ``pm/lease.py`` (pure-logic);
здесь только резолв ref/actor, парсинг TTL, вывод и маппинг ошибок в exit-коды.
"""
from __future__ import annotations

from typing import Any, Optional

import typer
from clikit import CliError, command, emit_data
from rich.console import Console
from sqlalchemy import select

from atlas import lease as L
from atlas._time import local_now
from atlas.commands.epic import epic_app
from atlas.commands.task import _resolve_task_or_die, task_app
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import Epic, Participant, Task

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


def _render_multi(d: dict[str, Any]) -> None:
    console.print(f"🔒 multi-claim: взято {len(d['claimed'])}, пропущено {len(d['skipped'])}")
    for c in d["claimed"]:
        console.print(f"  🔒 {c['task']} — {c['lease_owner']}")
    for sk in d["skipped"]:
        console.print(f"  ⏭ {sk['task']} — занято {sk['lease_owner']}")


def _epic_lease_data(session, epic: Epic, action: str) -> dict[str, Any]:
    return {
        "action": action,
        "epic": epic.slug or epic.id,
        "title": epic.title,
        "status": epic.status,
        "lease_owner": L._holder_slug(session, epic.lease_owner),
        "lease_session_id": epic.lease_session_id,
        "lease_origin": epic.lease_origin,
        "claimed_at": epic.claimed_at.isoformat() if epic.claimed_at else None,
        "lease_expires_at": (
            epic.lease_expires_at.isoformat() if epic.lease_expires_at else None
        ),
        "cascaded": [],
    }


def _render_epic_lease(d: dict[str, Any]) -> None:
    icons = {"claimed": "🔒", "released": "🔓"}
    console.print(
        f"{icons.get(d['action'], '')} [bold magenta]{d['epic']}[/bold magenta] — эпик {d['action']}"
    )
    if d["lease_owner"]:
        console.print(f"  Lease:   {d['lease_owner']}")
    if d["lease_expires_at"]:
        console.print(f"  До:      {d['lease_expires_at']}")
    if d["cascaded"]:
        console.print(f"  Каскад:  {', '.join(d['cascaded'])}")


def _resolve_epic_or_die(session, ref: str) -> Epic:
    epic = session.execute(
        select(Epic).where((Epic.slug == ref) | (Epic.id == ref))
    ).scalar_one_or_none()
    if epic is None:
        raise CliError("epic_not_found", f"Эпик '{ref}' не найден.")
    return epic


def _render_stale(d: dict[str, Any]) -> None:
    if "reaped" in d:
        console.print(f"[green]Освобождено протухших lease: {d['count']}[/green]")
        for slug in d["reaped"]:
            console.print(f"  🔓 {slug}")
        return
    console.print(f"Протухших lease: {d['count']}")
    for row in d["stale"]:
        ent = row.get("entity", "task")
        mark = "📦" if ent == "epic" else "⏰"
        console.print(
            f"  {mark} {row['ref']} ({ent}) — {row['owner']} "
            f"(истёк {row['expired_at']})"
        )


# --------------------------------------------------------------------------- #
# Команды                                                                     #
# --------------------------------------------------------------------------- #


@task_app.command("claim")
@command
def claim_cmd(
    refs: list[str] = typer.Argument(..., help="одна или НЕСКОЛЬКО задач: number | slug | UUID"),
    ttl: str = typer.Option("2h", "--ttl", help="TTL аренды: 2h / 30m / 90s / 1d"),
    best_effort: bool = typer.Option(
        False, "--best-effort/--all-or-nothing",
        help="best-effort: брать свободные, занятые пропустить (по умолчанию "
             "all-or-nothing: хоть одна занята → не берём ни одной)",
    ),
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
    """Взять задачу(и) в работу: lease + status=in_progress + assignee=actor.

    Один ref — прежнее поведение (карточка задачи). Несколько ref — multi-claim
    (#193): all-or-nothing (по умолчанию) или --best-effort.
    """
    ttl_td = _parse_ttl_or_die(ttl)
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        L.expire_stale_leases(session)  # ленивый reaper
        actor_p = _resolve_actor_or_die(session, actor)
        tasks = [_resolve_task_or_die(session, r) for r in refs]
        sess = L.resolve_session_id(session_id)
        orig = L.resolve_origin(origin)

        # Один ref — карточка задачи (обратная совместимость).
        if len(tasks) == 1:
            try:
                L.claim_task(
                    session, tasks[0], actor_p,
                    session_id=sess, origin=orig, ttl=ttl_td,
                )
            except L.LeaseHeldError as exc:
                raise CliError("lease_held", str(exc))
            session.commit()
            data = _lease_data(session, tasks[0], "claimed")
            emit_data(data, text_renderer=_render_lease)
            return

        # Несколько ref — multi-claim.
        try:
            res = L.claim_tasks(
                session, tasks, actor_p,
                all_or_nothing=not best_effort,
                session_id=sess, origin=orig, ttl=ttl_td,
            )
        except L.LeaseHeldError as exc:
            session.rollback()
            raise CliError("lease_held", str(exc))
        session.commit()
        data = {
            "action": "claimed",
            "claimed": [_lease_data(session, t, "claimed") for t in res.claimed],
            "skipped": [
                {"task": t.slug or t.id, "number": t.number,
                 "lease_owner": L._holder_slug(session, t.lease_owner)}
                for t in res.skipped
            ],
        }
    emit_data(data, text_renderer=_render_multi)


@task_app.command("release")
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


@task_app.command("renew")
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


@task_app.command("take")
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


@task_app.command("stale")
@command
def stale_cmd(
    reap: bool = typer.Option(
        False, "--reap", help="освободить протухшие lease (иначе только отчёт)"
    ),
) -> None:
    """Протухшие lease: отчёт (по умолчанию) или освобождение (--reap)."""
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        now = local_now()
        if reap:
            freed = L.expire_stale_leases(session, now=now)
            session.commit()
            data: dict[str, Any] = {
                "reaped": [t.slug or t.id for t in freed],
                "count": len(freed),
            }
        else:
            # Зеркалим обе ветки expire_stale_leases: report-превью должен
            # совпадать с тем, что освободит --reap (и задачи, И эпики).
            stale_tasks = (
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
            stale_epics = (
                session.execute(
                    select(Epic).where(
                        Epic.lease_owner.is_not(None),
                        Epic.lease_expires_at.is_not(None),
                        Epic.lease_expires_at < now,
                    )
                )
                .scalars()
                .all()
            )
            stale_rows = [
                {
                    "entity": "task",
                    "ref": t.slug or t.id,
                    "owner": L._holder_slug(session, t.lease_owner),
                    "expired_at": t.lease_expires_at.isoformat(),
                }
                for t in stale_tasks
            ] + [
                {
                    "entity": "epic",
                    "ref": e.slug or e.id,
                    "owner": L._holder_slug(session, e.lease_owner),
                    "expired_at": e.lease_expires_at.isoformat(),
                }
                for e in stale_epics
            ]
            data = {
                "stale": stale_rows,
                "count": len(stale_rows),
            }
    emit_data(data, text_renderer=_render_stale)


# --------------------------------------------------------------------------- #
# Групповой lease на эпик (#194): epic claim / epic release                   #
# --------------------------------------------------------------------------- #


@epic_app.command("claim")
@command
def epic_claim_cmd(
    ref: str = typer.Argument(..., help="slug | UUID эпика"),
    ttl: str = typer.Option("2h", "--ttl", help="TTL аренды: 2h / 30m / 1d"),
    actor: Optional[str] = typer.Option(
        None, "--actor", help="participant slug (default: env ATLAS_ACTOR / dmitry)"
    ),
    session_id: Optional[str] = typer.Option(None, "--session"),
    origin: Optional[str] = typer.Option(None, "--from"),
) -> None:
    """Взять эпик в работу: lease на эпик + каскад на незавершённые задачи."""
    ttl_td = _parse_ttl_or_die(ttl)
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        L.expire_stale_leases(session)  # ленивый reaper
        epic = _resolve_epic_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            res = L.claim_epic(
                session, epic, actor_p,
                session_id=L.resolve_session_id(session_id),
                origin=L.resolve_origin(origin), ttl=ttl_td,
            )
        except L.LeaseHeldError as exc:
            session.rollback()
            raise CliError("lease_held", str(exc))
        except L.OptimisticLockError as exc:
            session.rollback()
            raise CliError("lease_conflict", str(exc))
        session.commit()
        data = _epic_lease_data(session, epic, "claimed")
        data["cascaded"] = [t.slug or t.id for t in res.claimed_tasks]
    emit_data(data, text_renderer=_render_epic_lease)


@epic_app.command("release")
@command
def epic_release_cmd(
    ref: str = typer.Argument(..., help="slug | UUID эпика"),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Снять lease эпика + каскадно отпустить незавершённые задачи держателя."""
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        epic = _resolve_epic_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            res = L.release_epic(session, epic, actor_p)
        except L.LeaseNotOwnedError as exc:
            raise CliError("lease_not_owned", str(exc))
        session.commit()
        data = _epic_lease_data(session, epic, "released")
        data["cascaded"] = [t.slug or t.id for t in res.claimed_tasks]
    emit_data(data, text_renderer=_render_epic_lease)
