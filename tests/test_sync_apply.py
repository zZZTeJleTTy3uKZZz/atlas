"""F3d: apply_event — идемпотентный upsert/delete по backend_id."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from atlas.pm.models import (
    Base, Project, ProjectStatus, ProjectType, Task,
)
from atlas.pm.sync import apply


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _project(s, backend_id="proj-be"):
    t = ProjectType(slug="t", name="t"); st = ProjectStatus(slug="a", name="a", order_idx=1)
    s.add_all([t, st]); s.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2",
                one_line_summary="x", backend_id=backend_id)
    s.add(p); s.flush()
    return p


def test_update_existing_task_by_backend_id(session):
    p = _project(session)
    task = Task(project_id=p.id, title="old", cpp_description="ц", priority="P2", backend_id="task-be")
    session.add(task); session.commit()
    ev = {"entity_kind": "task", "op": "update", "entity_id": "task-be",
          "payload_json": {"title": "new", "status": "done"}}
    res = apply.apply_event(session, ev)
    session.commit()
    assert res["updated"] == "task"
    got = session.execute(select(Task).where(Task.backend_id == "task-be")).scalar_one()
    assert got.title == "new"
    assert got.status == "done"


def test_create_task_when_project_resolved(session):
    _project(session, backend_id="proj-be")
    ev = {"entity_kind": "task", "op": "create", "entity_id": "task-be2",
          "payload_json": {"title": "T", "project_backend_id": "proj-be", "cpp": "ЦКП"}}
    res = apply.apply_event(session, ev)
    session.commit()
    assert res["created"] == "task"
    got = session.execute(select(Task).where(Task.backend_id == "task-be2")).scalar_one()
    assert got.title == "T"


def test_create_skipped_without_project(session):
    ev = {"entity_kind": "task", "op": "create", "entity_id": "x",
          "payload_json": {"title": "T"}}
    res = apply.apply_event(session, ev)
    assert "skipped" in res


def test_idempotent_update_twice(session):
    p = _project(session)
    task = Task(project_id=p.id, title="a", cpp_description="ц", priority="P2", backend_id="be")
    session.add(task); session.commit()
    ev = {"entity_kind": "task", "op": "update", "entity_id": "be",
          "payload_json": {"status": "done"}}
    apply.apply_event(session, ev); session.commit()
    apply.apply_event(session, ev); session.commit()
    rows = session.execute(select(Task).where(Task.backend_id == "be")).scalars().all()
    assert len(rows) == 1  # без дублей


def test_delete_soft_archives(session):
    p = _project(session)
    task = Task(project_id=p.id, title="a", cpp_description="ц", priority="P2", backend_id="be")
    session.add(task); session.commit()
    ev = {"entity_kind": "task", "op": "delete", "entity_id": "be", "payload_json": {}}
    res = apply.apply_event(session, ev); session.commit()
    assert res["deleted"] == "task"
    got = session.execute(select(Task).where(Task.backend_id == "be")).scalar_one()
    assert got.archived_at is not None


def test_unknown_kind_skipped(session):
    ev = {"entity_kind": "widget", "op": "update", "entity_id": "x", "payload_json": {}}
    assert "skipped" in apply.apply_event(session, ev)
