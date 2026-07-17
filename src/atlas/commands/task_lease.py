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
from atlas import task_review as TR
from atlas import task_status as TS
from atlas._time import local_now
from atlas.commands.epic import epic_app
from atlas.commands.task import _resolve_task_or_die, task_app
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import Epic, Task

console = Console()


# --------------------------------------------------------------------------- #
# Хелперы                                                                     #
# --------------------------------------------------------------------------- #


def _enqueue_task_update(session, task: Task) -> None:
    """best-effort: update-событие задачи в outbox.

    Статус — синкаемое поле, а смена статуса теперь идёт через глаголы (start/done/
    ...), не через update. Чтобы переход доезжал до ядра/Notion, ставим update-
    событие (как делал update_cmd). Lease-поля в payload не попадают (mapper их
    исключает — см. test_lease_sync_invariant)."""
    try:
        from atlas.commands.task import _sync_portal_id
        from atlas.models import Project
        from atlas.sync import outbox as _outbox

        proj = session.get(Project, task.project_id)
        if proj is not None:
            _outbox.enqueue(
                session, "update", "task", task,
                project=proj, portal_id=_sync_portal_id(),
            )
    except Exception:
        pass


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


def _run_claim(
    refs: list[str], ttl: str, best_effort: bool,
    actor: Optional[str], session_id: Optional[str], origin: Optional[str],
) -> None:
    """Общее тело claim/start: взять задачу(и) в работу (lease + in_progress)."""
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
            _enqueue_task_update(session, tasks[0])  # статус→in_progress в ядро
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
        for _t in res.claimed:
            _enqueue_task_update(session, _t)
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
        None, "--actor", help="participant slug (default: env ATLAS_ACTOR / owner)"
    ),
    session_id: Optional[str] = typer.Option(
        None, "--session", help="id сессии Claude Code (default: env ATLAS_SESSION)"
    ),
    origin: Optional[str] = typer.Option(
        None, "--from", help="откуда взято (default: env ATLAS_FROM / cwd)"
    ),
) -> None:
    """Взять задачу(и) в работу: lease + status=in_progress + assignee=actor.

    Синоним — ``task start``. Один ref — карточка задачи. Несколько ref —
    multi-claim (#193): all-or-nothing (по умолчанию) или --best-effort.
    """
    _run_claim(refs, ttl, best_effort, actor, session_id, origin)


@task_app.command("start")
@command
def start_cmd(
    refs: list[str] = typer.Argument(..., help="одна или НЕСКОЛЬКО задач: number | slug | UUID"),
    ttl: str = typer.Option("2h", "--ttl", help="TTL аренды: 2h / 30m / 90s / 1d"),
    best_effort: bool = typer.Option(False, "--best-effort/--all-or-nothing"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    session_id: Optional[str] = typer.Option(None, "--session"),
    origin: Optional[str] = typer.Option(None, "--from"),
) -> None:
    """Начать работу над задачей (= claim): lease + status=in_progress + assignee.

    Человеческий синоним ``task claim``. Именно так берут задачу в работу —
    «голый» ``update --status in_progress`` запрещён (обошёл бы lease)."""
    _run_claim(refs, ttl, best_effort, actor, session_id, origin)


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
        _enqueue_task_update(session, task)  # статус→in_progress в ядро
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
# Lifecycle-глаголы: done / review / block / unblock / cancel                  #
# --------------------------------------------------------------------------- #


def _status_data(session, task: Task, action: str) -> dict[str, Any]:
    return {
        "action": action,
        "task": task.slug or task.id,
        "number": task.number,
        "title": task.title,
        "status": task.status,
        "lease_owner": L._holder_slug(session, task.lease_owner),
        "reviewer": L._holder_slug(session, task.reviewer_id),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def _render_status(d: dict[str, Any]) -> None:
    icons = {"done": "✅", "cancelled": "🚫", "review": "👀",
             "blocked": "⛔", "in_progress": "▶"}
    console.print(
        f"{icons.get(d['status'], '·')} [bold cyan]{d['task']}[/bold cyan] → {d['status']}"
    )
    if d.get("lease_owner"):
        console.print(f"  Lease:   {d['lease_owner']}")
    if d.get("completed_at"):
        console.print(f"  Завершено: {d['completed_at']}")


def _lifecycle_engine():
    return make_engine(resolve_db_url())


@task_app.command("done")
@command
def done_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    force: bool = typer.Option(False, "--force", help="закрыть, даже если задачу держит другой"),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Завершить задачу → done (снимает lease, ставит completed_at)."""
    with make_session(_lifecycle_engine()) as session:
        L.expire_stale_leases(session)
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            TS.finish_task(session, task, actor_p, force=force)
        except TS.ReviewGateError as exc:
            raise CliError("review_gate", f"{exc}; либо approve от reviewer, либо --force")
        except L.LeaseHeldError as exc:
            raise CliError("lease_held", f"{exc}; чтобы закрыть чужую — --force")
        except TS.TransitionError as exc:
            raise CliError("invalid_transition", str(exc))
        _enqueue_task_update(session, task)
        session.commit()
        data = _status_data(session, task, "done")
    emit_data(data, text_renderer=_render_status)


@task_app.command("cancel")
@command
def cancel_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    force: bool = typer.Option(False, "--force"),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Отменить задачу → cancelled (снимает lease; не ставит completed_at)."""
    with make_session(_lifecycle_engine()) as session:
        L.expire_stale_leases(session)
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            TS.cancel_task(session, task, actor_p, force=force)
        except L.LeaseHeldError as exc:
            raise CliError("lease_held", f"{exc}; чтобы отменить чужую — --force")
        except TS.TransitionError as exc:
            raise CliError("invalid_transition", str(exc))
        _enqueue_task_update(session, task)
        session.commit()
        data = _status_data(session, task, "cancelled")
    emit_data(data, text_renderer=_render_status)


@task_app.command("review")
@command
def review_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    force: bool = typer.Option(False, "--force"),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Отправить задачу на ревью → review (lease сохраняется)."""
    with make_session(_lifecycle_engine()) as session:
        L.expire_stale_leases(session)
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            TS.review_task(session, task, actor_p, force=force)
        except L.LeaseHeldError as exc:
            raise CliError("lease_held", f"{exc}; --force чтобы перевести чужую")
        except TS.TransitionError as exc:
            raise CliError("invalid_transition", str(exc))
        _enqueue_task_update(session, task)
        session.commit()
        data = _status_data(session, task, "review")
    emit_data(data, text_renderer=_render_status)


@task_app.command("block")
@command
def block_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    reason: Optional[str] = typer.Option(None, "--reason", help="причина блокировки (в action-log)"),
    force: bool = typer.Option(False, "--force"),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Пометить задачу заблокированной → blocked (lease сохраняется)."""
    with make_session(_lifecycle_engine()) as session:
        L.expire_stale_leases(session)
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            TS.block_task(session, task, actor_p, reason=reason, force=force)
        except L.LeaseHeldError as exc:
            raise CliError("lease_held", f"{exc}; --force чтобы заблокировать чужую")
        except TS.TransitionError as exc:
            raise CliError("invalid_transition", str(exc))
        _enqueue_task_update(session, task)
        session.commit()
        data = _status_data(session, task, "blocked")
    emit_data(data, text_renderer=_render_status)


@task_app.command("unblock")
@command
def unblock_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Снять блокировку → in_progress (нужно держать lease; иначе task start)."""
    with make_session(_lifecycle_engine()) as session:
        L.expire_stale_leases(session)
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            TS.unblock_task(session, task, actor_p)
        except L.LeaseNotOwnedError as exc:
            raise CliError("lease_not_owned", f"{exc}; возьми заново — task start {ref}")
        except TS.TransitionError as exc:
            raise CliError("invalid_transition", str(exc))
        _enqueue_task_update(session, task)
        session.commit()
        data = _status_data(session, task, "in_progress")
    emit_data(data, text_renderer=_render_status)


# --------------------------------------------------------------------------- #
# Review-workflow: submit / approve / reject / reopen + комментарии            #
# --------------------------------------------------------------------------- #


@task_app.command("submit")
@command
def submit_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    comment: Optional[str] = typer.Option(None, "--comment", "-m", help="Что сделано/что дальше (передача reviewer'у)."),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Отправить задачу на проверку → review (исполнитель). Опц. комментарий-передача."""
    with make_session(_lifecycle_engine()) as session:
        L.expire_stale_leases(session)
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            TR.submit_task(session, task, actor_p, comment=comment)
        except L.LeaseHeldError as exc:
            raise CliError("lease_held", f"{exc}; --force чтобы перевести чужую")
        except TS.TransitionError as exc:
            raise CliError("invalid_transition", str(exc))
        _enqueue_task_update(session, task)
        session.commit()
        data = _status_data(session, task, "review")
    emit_data(data, text_renderer=_render_status)


@task_app.command("approve")
@command
def approve_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    comment: Optional[str] = typer.Option(None, "--comment", "-m", help="Резолюция приёмки."),
    force: bool = typer.Option(False, "--force"),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Одобрить и закрыть → done (только reviewer). Синоним reviewer-варианта `done`."""
    with make_session(_lifecycle_engine()) as session:
        L.expire_stale_leases(session)
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            TR.approve_task(session, task, actor_p, comment=comment, force=force)
        except TS.ReviewGateError as exc:
            raise CliError("review_gate", str(exc))
        except L.LeaseHeldError as exc:
            raise CliError("lease_held", f"{exc}; --force")
        except TS.TransitionError as exc:
            raise CliError("invalid_transition", str(exc))
        _enqueue_task_update(session, task)
        session.commit()
        data = _status_data(session, task, "done")
    emit_data(data, text_renderer=_render_status)


@task_app.command("reject")
@command
def reject_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    comment: str = typer.Option(..., "--comment", "-m", help="Причина возврата (обязательно)."),
    force: bool = typer.Option(False, "--force"),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Вернуть задачу в работу: review → in_progress (только reviewer). Причина обязательна."""
    with make_session(_lifecycle_engine()) as session:
        L.expire_stale_leases(session)
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            TR.reject_task(session, task, actor_p, comment=comment, force=force)
        except TS.ReviewGateError as exc:
            raise CliError("review_gate", str(exc))
        except TS.TransitionError as exc:
            raise CliError("invalid_transition", str(exc))
        _enqueue_task_update(session, task)
        session.commit()
        data = _status_data(session, task, "in_progress")
    emit_data(data, text_renderer=_render_status)


@task_app.command("reopen")
@command
def reopen_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    comment: Optional[str] = typer.Option(None, "--comment", "-m", help="Почему переоткрыта."),
    force: bool = typer.Option(False, "--force"),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Переоткрыть закрытую задачу: done/cancelled → todo (только reviewer)."""
    with make_session(_lifecycle_engine()) as session:
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        try:
            TR.reopen_task(session, task, actor_p, comment=comment, force=force)
        except TS.ReviewGateError as exc:
            raise CliError("review_gate", str(exc))
        except TS.TransitionError as exc:
            raise CliError("invalid_transition", str(exc))
        _enqueue_task_update(session, task)
        session.commit()
        data = _status_data(session, task, "todo")
    emit_data(data, text_renderer=_render_status)


@task_app.command("comment")
@command
def comment_cmd(
    ref: str = typer.Argument(..., help="number | slug | UUID"),
    body: str = typer.Argument(..., help="Текст комментария."),
    actor: Optional[str] = typer.Option(None, "--actor"),
) -> None:
    """Добавить комментарий к задаче (передача контекста между агентами)."""
    with make_session(_lifecycle_engine()) as session:
        task = _resolve_task_or_die(session, ref)
        actor_p = _resolve_actor_or_die(session, actor)
        c = TR.add_comment(session, task, actor_p, body, kind="comment")
        session.commit()
        data = {"task": task.slug or str(task.number), "comment_id": c.id,
                "author": L._holder_slug(session, c.author_id), "body": body}
    emit_data(
        data,
        text_renderer=lambda d: console.print(f"💬 [{d['author'] or '?'}] {d['body']}"),
    )


@task_app.command("comments")
@command
def comments_cmd(ref: str = typer.Argument(..., help="number | slug | UUID")) -> None:
    """Показать комментарии задачи (хронологически)."""
    with make_session(_lifecycle_engine()) as session:
        task = _resolve_task_or_die(session, ref)
        rows = [
            {
                "kind": c.kind,
                "author": L._holder_slug(session, c.author_id),
                "body": c.body,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in TR.list_comments(session, task)
        ]

    def _render(items: list[dict]) -> None:
        if not items:
            console.print("[dim]Комментариев нет.[/dim]")
            return
        marks = {"submit": "📤", "approve": "✅", "reject": "↩", "reopen": "🔄", "comment": "💬"}
        for c in items:
            ts = (c["created_at"] or "")[:16].replace("T", " ")
            console.print(f"{marks.get(c['kind'], '💬')} [grey50]{ts}[/grey50] "
                          f"[cyan]{c['author'] or '?'}[/cyan]: {c['body']}")

    emit_data(rows, text_renderer=_render)


# --------------------------------------------------------------------------- #
# Триаж: что в работе / застряло / ЗАБЫТО                                     #
# --------------------------------------------------------------------------- #


#: Scheduled Task для ежедневного триажа (зеркалит atlas-daily-backup).
TRIAGE_TASK_NAME = "atlas-daily-triage"
TRIAGE_DEFAULT_TIME = "09:00"


def _find_triage_register_script():
    """Найти scripts/triage/register_triage_task.ps1 относительно пакета."""
    from pathlib import Path

    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "scripts" / "triage" / "register_triage_task.ps1"
        if candidate.exists():
            return candidate
    raise CliError(
        "script_not_found",
        "register_triage_task.ps1 не найден (ожидался в scripts/triage/).",
    )


def _install_triage_task(time: str) -> dict[str, Any]:
    """Зарегистрировать Windows Scheduled Task ``atlas-daily-triage``."""
    import subprocess

    script = _find_triage_register_script()
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(script), "-Time", time,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise CliError(
            "install_failed",
            (proc.stderr or proc.stdout or "").strip()
            or f"powershell вернул код {proc.returncode}",
        )
    return {"task": TRIAGE_TASK_NAME, "time": time, "installed": True,
            "stdout": proc.stdout or ""}


def _uninstall_triage_task() -> dict[str, Any]:
    """Снять Scheduled Task ``atlas-daily-triage``."""
    import subprocess

    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-Command",
        f"Unregister-ScheduledTask -TaskName '{TRIAGE_TASK_NAME}' -Confirm:$false",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise CliError(
            "uninstall_failed",
            (proc.stderr or proc.stdout or "").strip()
            or f"powershell вернул код {proc.returncode}",
        )
    return {"task": TRIAGE_TASK_NAME, "uninstalled": True,
            "stdout": proc.stdout or ""}


@task_app.command("triage")
@command
def triage_cmd(
    days: int = typer.Option(7, "--days", help="Порог «забытости»: активная задача не тронута N дней."),
    project: Optional[str] = typer.Option(None, "--project", help="Только этот проект."),
    assignee: Optional[str] = typer.Option(None, "--assignee", help="Только этот исполнитель."),
    install: bool = typer.Option(
        False, "--install",
        help="Зарегистрировать ежедневный Scheduled Task (Windows) автозапуска триажа.",
    ),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Снять Scheduled Task автозапуска триажа.",
    ),
    at_time: str = typer.Option(
        TRIAGE_DEFAULT_TIME, "--time", help="HH:MM — время ежедневного триажа (с --install).",
    ),
) -> None:
    """Триаж задач: активная работа + STALE (забытые, не тронуты > N дней).

    Смотри В НАЧАЛЕ сессии: что в работе, что застряло (blocked/review), и
    главное — что ЗАБЫТО (active, давно не трогали). --json для агента.

    ``--install`` ставит ежедневный Windows Scheduled Task (отчёт triage → лог,
    как `atlas backup install`); ``--uninstall`` снимает его."""
    from rich.box import ROUNDED
    from rich.table import Table

    from atlas.triage import build_triage

    # Управление автозапуском (Scheduled Task) — раньше построения отчёта.
    if install and uninstall:
        raise CliError("bad_args", "--install и --uninstall взаимоисключающи.")
    if install:
        data = _install_triage_task(at_time)
        emit_data(data, text_renderer=lambda d: (
            console.print(d["stdout"]) if d.get("stdout") else None,
            console.print(
                f"[green]✓ Task '{d['task']}' установлен на {d['time']} "
                f"(ежедневно).[/green]"),
        ))
        return
    if uninstall:
        data = _uninstall_triage_task()
        emit_data(data, text_renderer=lambda d: (
            console.print(d["stdout"]) if d.get("stdout") else None,
            console.print(f"[green]✓ Task '{d['task']}' удалён.[/green]"),
        ))
        return

    with make_session(_lifecycle_engine()) as session:
        try:
            data = build_triage(session, project_ref=project, assignee=assignee, stale_days=days)
        except ValueError as exc:
            raise CliError("not_found", str(exc))

    def _tbl(title: str, rows: list[dict], *, age: bool = False) -> Table:
        t = Table(title=f"[bold]{title}[/bold]", box=ROUNDED, border_style="grey37",
                  title_justify="left", expand=True, padding=(0, 1))
        t.add_column("Задача", style="cyan", no_wrap=True, max_width=18)
        t.add_column("P", justify="center", max_width=3)
        t.add_column("Заголовок", style="white", ratio=1)
        t.add_column("Проект", style="grey62", no_wrap=True, max_width=16)
        t.add_column("Кто", style="green", no_wrap=True, max_width=12)
        if age:
            t.add_column("Дней", justify="right", style="red", max_width=5)
        if not rows:
            t.add_row("[dim]—[/dim]", "", "", "", "", *(["", ] if age else []))
            return t
        for r in rows[:15]:
            cells = [r["ref"], r["priority"], (r["title"] or "")[:50],
                     r["project"] or "—", r.get("assignee") or r.get("reviewer") or "[dim]?[/dim]"]
            if age:
                cells.append(str(r.get("age_days", "?")))
            t.add_row(*cells)
        return t

    def _render(d: dict[str, Any]) -> None:
        c = d["counts"]
        console.print(
            f"\n[bold magenta]Триаж — {d['scope']}[/bold magenta]  "
            f"открытых [bold]{d['total_open']}[/bold] · в работе [cyan]{c['in_progress']}[/cyan] · "
            f"ревью [yellow]{c['review']}[/yellow] · заблок [red]{c['blocked']}[/red] · "
            f"todo {c['todo']}"
        )
        if d["stale"]:
            console.print(f"[bold red]⚠ ЗАБЫТЫЕ (active, не тронуты > {d['stale_days']} дн):[/bold red]")
            console.print(_tbl("", d["stale"], age=True))
        else:
            console.print(f"[green]✓ забытых нет (порог {d['stale_days']} дн)[/green]")
        console.print(_tbl("▶ В работе", d["in_progress"]))
        if d["blocked"]:
            console.print(_tbl("⛔ Заблокированные", d["blocked"]))
        if d["review"]:
            console.print(_tbl("👀 На ревью", d["review"]))

    emit_data(data, text_renderer=_render)


# --------------------------------------------------------------------------- #
# Групповой lease на эпик (#194): epic claim / epic release                   #
# --------------------------------------------------------------------------- #


@epic_app.command("claim")
@command
def epic_claim_cmd(
    ref: str = typer.Argument(..., help="slug | UUID эпика"),
    ttl: str = typer.Option("2h", "--ttl", help="TTL аренды: 2h / 30m / 1d"),
    actor: Optional[str] = typer.Option(
        None, "--actor", help="participant slug (default: env ATLAS_ACTOR / owner)"
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
