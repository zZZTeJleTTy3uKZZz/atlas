"""F3b: Outbox/SyncCursor + backend_id на Task."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.models import (
    Base, Outbox, Project, ProjectStatus, ProjectType, SyncCursor, Task,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_outbox_defaults(session):
    o = Outbox(op="create", entity_kind="task", entity_id="x", payload_json="{}")
    session.add(o); session.commit()
    got = session.get(Outbox, o.id)
    assert got.status == "pending"
    assert got.attempts == 0


def test_sync_cursor(session):
    session.add(SyncCursor(channel="atlas", cursor="2026-06-14T00:00:00"))
    session.commit()
    assert session.get(SyncCursor, "atlas").cursor.startswith("2026")


def test_task_backend_id(session):
    t = ProjectType(slug="t", name="t"); st = ProjectStatus(slug="a", name="a", order_idx=1)
    session.add_all([t, st]); session.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2", one_line_summary="x")
    session.add(p); session.flush()
    task = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2", backend_id="be-1")
    session.add(task); session.commit()
    assert session.get(Task, task.id).backend_id == "be-1"


def test_outbox_op_constraint(session):
    from sqlalchemy.exc import IntegrityError
    session.add(Outbox(op="explode", entity_kind="task", entity_id="x", payload_json="{}"))
    with pytest.raises(IntegrityError):
        session.commit()
