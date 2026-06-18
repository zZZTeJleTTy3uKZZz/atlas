"""CLI `atlas member ...` — участники задачи (TaskMember: responsible/executor/watcher)."""
from __future__ import annotations

import typer
from clikit import command, emit_data
from sqlalchemy import select

from atlas.pm.commands.pm_tasks import _sync_portal_id
from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.models import Participant, Project, TaskMember
from atlas.pm.slugs import resolve_task_ref
from atlas.pm.sync import outbox as _outbox

member_app = typer.Typer(no_args_is_help=True, help="Участники задачи (роли).")
_ROLES = {"responsible", "executor", "watcher"}


def _db_url() -> str:
    return resolve_db_url()


def _participant(session, slug):
    return session.execute(
        select(Participant).where(Participant.slug == slug)
    ).scalar_one_or_none()


def _enqueue_task_update(session, task) -> None:
    """Best-effort: выгрузить новый состав задачи сразу (enqueue update task).

    Смена участников = смена task-payload (ключ ``assignees``), поэтому ставим
    op=update этой задачи в outbox — как делают pm_tasks add/update. Резолв
    проекта и portal_id из конфига (ноль хардкода). try/except, чтобы сбой синка
    не валил CLI: участник всё равно изменён."""
    try:
        proj = session.get(Project, task.project_id)
        if proj is not None:
            _outbox.enqueue(
                session, "update", "task", task,
                project=proj, portal_id=_sync_portal_id(),
            )
    except Exception:
        pass


@member_app.command("add")
@command
def add_cmd(
    task: str = typer.Option(..., "--task"),
    participant: str = typer.Option(..., "--participant", help="participant slug"),
    role: str = typer.Option("executor", "--role", help="responsible|executor|watcher"),
) -> None:
    """Назначить участника на задачу с ролью."""
    if role not in _ROLES:
        raise typer.Exit(1)
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        t = resolve_task_ref(session, task)
        p = _participant(session, participant)
        if t is None or p is None:
            raise typer.Exit(1)
        exists = session.get(TaskMember, (t.id, p.id, role))
        if exists is None:
            session.add(TaskMember(task_id=t.id, participant_id=p.id, role=role))
            session.flush()
            _enqueue_task_update(session, t)
            session.commit()
        emit_data(
            {"task_id": t.id, "participant": participant, "role": role},
            text_renderer=lambda d: print(f"✓ {d['participant']} → {d['role']}"),
        )


@member_app.command("list")
@command
def list_cmd(task: str = typer.Option(..., "--task")) -> None:
    """Список участников задачи."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        t = resolve_task_ref(session, task)
        if t is None:
            raise typer.Exit(1)
        rows = session.execute(
            select(TaskMember, Participant)
            .join(Participant, TaskMember.participant_id == Participant.id)
            .where(TaskMember.task_id == t.id)
        ).all()
        data = [{"participant": p.slug, "role": tm.role} for tm, p in rows]
        emit_data(
            data,
            text_renderer=lambda items: [print(f"{i['participant']}: {i['role']}") for i in items],
        )


@member_app.command("rm")
@command
def rm_cmd(
    task: str = typer.Option(..., "--task"),
    participant: str = typer.Option(..., "--participant"),
    role: str = typer.Option(..., "--role"),
) -> None:
    """Снять участника с роли на задаче."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        t = resolve_task_ref(session, task)
        p = _participant(session, participant)
        if t is None or p is None:
            raise typer.Exit(1)
        tm = session.get(TaskMember, (t.id, p.id, role))
        if tm is not None:
            session.delete(tm)
            session.flush()
            _enqueue_task_update(session, t)
            session.commit()
        emit_data({"removed": tm is not None}, text_renderer=lambda d: print("✓ removed" if d["removed"] else "— нет такого"))
