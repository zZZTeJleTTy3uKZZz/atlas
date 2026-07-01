"""CLI `atlas checklist ...` — чек-листы задач (шаги). На clikit."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import typer
from clikit import CliError, command, emit_data
from sqlalchemy import func, select

from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import ChecklistItem, Project, Task
from atlas.slugs import resolve_task_ref
from atlas.sync import outbox as _outbox

checklist_app = typer.Typer(no_args_is_help=True, help="Чек-листы задач (шаги ИИ-агента).")


def _sync_portal_id() -> str:
    """Slug портала-стора этого Atlas для ``source_portal_id`` событий синка —
    из активного конфига (``cfg.portal_id``), а НЕ хардкод. Ядро резолвит
    slug→portal по этому значению; неправильный slug → событие зависает pending."""
    from atlas.appconfig import load_config
    return load_config().portal_id


def _db_url() -> str:
    return resolve_db_url()


def _enqueue(session, op, obj, project):
    try:
        _outbox.enqueue(
            session, op, "checklist", obj, project=project,
            portal_id=_sync_portal_id(),
        )
    except Exception:
        pass


@checklist_app.command("add")
@command
def add_cmd(
    task: str = typer.Option(..., "--task", help="Task ref (number | slug | UUID)"),
    text: str = typer.Option(..., "--text"),
    due: Optional[str] = typer.Option(
        None, "--due", help="Срок пункта (YYYY-MM-DD или полный ISO)"
    ),
) -> None:
    """Добавить пункт чек-листа к задаче."""
    due_dt: Optional[datetime] = None
    if due:
        try:
            due_dt = datetime.fromisoformat(due)
        except ValueError:
            raise CliError("invalid_date", f"Невалидный --due '{due}': ожидаю YYYY-MM-DD.")
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        t = resolve_task_ref(session, task)
        if t is None:
            raise typer.Exit(1)
        next_pos = session.execute(
            select(func.count()).select_from(ChecklistItem).where(ChecklistItem.task_id == t.id)
        ).scalar_one()
        ci = ChecklistItem(task_id=t.id, text=text, position=next_pos, due_date=due_dt)
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


@checklist_app.command("delete")
@command
def delete_cmd(
    item_id: str = typer.Argument(..., help="UUID пункта"),
) -> None:
    """Удалить пункт чек-листа (локально + enqueue delete наружу)."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ci = session.get(ChecklistItem, item_id)
        if ci is None:
            raise typer.Exit(1)
        # enqueue ДО удаления: mapper читает поля пункта и backend_id родителя.
        task = session.get(Task, ci.task_id)
        proj = session.get(Project, task.project_id) if task else None
        if proj is not None:
            _enqueue(session, "delete", ci, proj)
        ci_id = ci.id
        session.delete(ci)
        session.commit()
        emit_data(
            {"id": ci_id, "deleted": True},
            text_renderer=lambda d: print(f"✗ {d['id']} удалён"),
        )
