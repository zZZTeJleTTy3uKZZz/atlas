"""CLI `atlas checklist ...` — чек-листы задач (шаги). На clikit."""
from __future__ import annotations

import typer
from clikit import command, emit_data
from sqlalchemy import func, select

from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.models import ChecklistItem, Project, Task
from atlas.pm.slugs import resolve_task_ref
from atlas.pm.sync import outbox as _outbox

checklist_app = typer.Typer(no_args_is_help=True, help="Чек-листы задач (шаги ИИ-агента).")
_PORTAL = "atlas-local"


def _db_url() -> str:
    return resolve_db_url()


def _enqueue(session, op, obj, project):
    try:
        _outbox.enqueue(session, op, "checklist", obj, project=project, portal_id=_PORTAL)
    except Exception:
        pass


@checklist_app.command("add")
@command
def add_cmd(
    task: str = typer.Option(..., "--task", help="Task ref (number | slug | UUID)"),
    text: str = typer.Option(..., "--text"),
) -> None:
    """Добавить пункт чек-листа к задаче."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        t = resolve_task_ref(session, task)
        if t is None:
            raise typer.Exit(1)
        next_pos = session.execute(
            select(func.count()).select_from(ChecklistItem).where(ChecklistItem.task_id == t.id)
        ).scalar_one()
        ci = ChecklistItem(task_id=t.id, text=text, position=next_pos)
        session.add(ci)
        session.flush()
        proj = session.get(Project, t.project_id)
        _enqueue(session, "create", ci, proj)
        session.commit()
        emit_data(
            {"id": ci.id, "text": ci.text, "is_done": ci.is_done, "position": ci.position},
            text_renderer=lambda d: print(f"☐ [{d['position']}] {d['text']}"),
        )


@checklist_app.command("list")
@command
def list_cmd(task: str = typer.Option(..., "--task")) -> None:
    """Список пунктов чек-листа задачи."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        t = resolve_task_ref(session, task)
        if t is None:
            raise typer.Exit(1)
        rows = session.execute(
            select(ChecklistItem).where(ChecklistItem.task_id == t.id).order_by(ChecklistItem.position)
        ).scalars().all()
        data = [{"id": c.id, "text": c.text, "is_done": c.is_done, "position": c.position} for c in rows]
        emit_data(
            data,
            text_renderer=lambda items: [print(f"{'☑' if i['is_done'] else '☐'} {i['text']}") for i in items],
        )


@checklist_app.command("check")
@command
def check_cmd(
    item_id: str = typer.Argument(..., help="UUID пункта"),
    uncheck: bool = typer.Option(False, "--uncheck", help="Снять отметку"),
) -> None:
    """Отметить пункт выполненным (или снять --uncheck)."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ci = session.get(ChecklistItem, item_id)
        if ci is None:
            raise typer.Exit(1)
        ci.is_done = 0 if uncheck else 1
        task = session.get(Task, ci.task_id)
        proj = session.get(Project, task.project_id) if task else None
        if proj is not None:
            _enqueue(session, "update", ci, proj)
        session.commit()
        emit_data(
            {"id": ci.id, "is_done": ci.is_done},
            text_renderer=lambda d: print(f"{'☑' if d['is_done'] else '☐'} {d['id']}"),
        )
