"""F3c: outbox.enqueue консультируется с policy; pending/mark работают."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.models import (
    Base, Outbox, Project, ProjectStatus, ProjectType, SyncPolicy, Task,
)
from atlas.sync import outbox


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _project(s, policy_slug):
    s.add_all([
        SyncPolicy(slug="local", name="l", sync_epic=0, sync_task=0, sync_checklist=0),
        SyncPolicy(slug="full", name="f", sync_epic=1, sync_task=1, sync_checklist=1),
    ])
    t = ProjectType(slug="t", name="t")
    st = ProjectStatus(slug="a", name="a", order_idx=1)
    s.add_all([t, st]); s.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2",
                one_line_summary="x", sync_policy=policy_slug)
    s.add(p); s.flush()
    return p


def _task(s, p):
    t = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2", slug="p-t1")
    s.add(t); s.flush()
    return t


def test_enqueue_when_policy_allows(session):
    p = _project(session, "full")
    t = _task(session, p)
    ob = outbox.enqueue(session, "create", "task", t, project=p, portal_id="atlas-local")
    session.commit()
    assert ob is not None
    payload = json.loads(ob.payload_json)
    assert payload["entity_kind"] == "task"
    assert payload["source_portal_id"] == "atlas-local"


def test_enqueue_skipped_when_policy_forbids(session):
    p = _project(session, "local")
    t = _task(session, p)
    ob = outbox.enqueue(session, "create", "task", t, project=p, portal_id="atlas-local")
    session.commit()
    assert ob is None
    assert outbox.pending(session) == []


def test_pending_and_mark(session):
    p = _project(session, "full")
    t = _task(session, p)
    ob = outbox.enqueue(session, "create", "task", t, project=p, portal_id="atlas-local")
    session.commit()
    pend = outbox.pending(session)
    assert len(pend) == 1
    outbox.mark_sent(session, ob.id)
    session.commit()
    assert outbox.pending(session) == []
    assert session.get(Outbox, ob.id).status == "sent"
