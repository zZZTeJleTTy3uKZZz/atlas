"""F3c: push_pending шлёт pending-события и помечает sent."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import (
    Base, Project, ProjectStatus, ProjectType, SyncPolicy, Task,
)
from atlas.pm.sync import outbox, push


class _FakeClient:
    def __init__(self):
        self.sent = []

    async def push_events(self, events):
        self.sent.append(events)
        return {"accepted": len(events)}

    async def aclose(self):
        pass


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _task_in_outbox(s):
    s.add(SyncPolicy(slug="full", name="f", sync_epic=1, sync_task=1, sync_checklist=1))
    t = ProjectType(slug="t", name="t"); st = ProjectStatus(slug="a", name="a", order_idx=1)
    s.add_all([t, st]); s.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2",
                one_line_summary="x", sync_policy="full")
    s.add(p); s.flush()
    task = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2", slug="p-t1")
    s.add(task); s.flush()
    outbox.enqueue(s, "create", "task", task, project=p, portal_id="atlas-local")
    s.commit()


async def test_push_pending_sends_and_marks(session):
    _task_in_outbox(session)
    client = _FakeClient()
    result = await push.push_pending(session, client)
    assert result["sent"] == 1
    assert len(client.sent) == 1
    assert client.sent[0][0]["entity_kind"] == "task"
    assert outbox.pending(session) == []


async def test_push_pending_empty(session):
    client = _FakeClient()
    result = await push.push_pending(session, client)
    assert result["sent"] == 0


def test_sync_push_in_cli_help():
    from typer.testing import CliRunner
    from atlas.cli import app
    result = CliRunner().invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "push" in result.stdout
