"""F3d: pull_once применяет события из long-poll и двигает курсор."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from atlas.pm.models import (
    Base, Project, ProjectStatus, ProjectType, Task,
)
from atlas.pm.sync import cursor, pull


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def poll_events(self, since=None, *, timeout=25.0):
        self.calls.append({"since": since, "timeout": timeout})
        return self._response

    async def aclose(self):
        pass


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _task(s, backend_id):
    t = ProjectType(slug="t", name="t"); st = ProjectStatus(slug="a", name="a", order_idx=1)
    s.add_all([t, st]); s.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2", one_line_summary="x")
    s.add(p); s.flush()
    task = Task(project_id=p.id, title="old", cpp_description="ц", priority="P2", backend_id=backend_id)
    s.add(task); s.commit()


async def test_pull_applies_and_advances_cursor(session):
    _task(session, "be-1")
    client = _FakeClient({
        "events": [
            {"entity_kind": "task", "op": "update", "entity_id": "be-1",
             "payload_json": {"status": "done"}, "occurred_at": "2026-06-14T12:00:00"},
        ],
        "cursor": "2026-06-14T12:00:00",
    })
    result = await pull.pull_once(session, client, channel="atlas", timeout=1.0)
    assert result["applied"] == 1
    got = session.execute(select(Task).where(Task.backend_id == "be-1")).scalar_one()
    assert got.status == "done"
    assert cursor.get_cursor(session, "atlas") == "2026-06-14T12:00:00"


async def test_pull_empty_keeps_cursor(session):
    cursor.set_cursor(session, "atlas", "2026-06-14T00:00:00")
    session.commit()
    client = _FakeClient({"events": [], "cursor": None})
    result = await pull.pull_once(session, client, channel="atlas", timeout=1.0)
    assert result["applied"] == 0
    # курсор не сбрасывается в None при пустом ответе
    assert cursor.get_cursor(session, "atlas") == "2026-06-14T00:00:00"


async def test_pull_passes_since_from_cursor(session):
    cursor.set_cursor(session, "atlas", "2026-06-14T09:00:00")
    session.commit()
    client = _FakeClient({"events": [], "cursor": None})
    await pull.pull_once(session, client, channel="atlas", timeout=2.0)
    assert client.calls[0]["since"] == "2026-06-14T09:00:00"
    assert client.calls[0]["timeout"] == 2.0


def test_sync_pull_and_watch_in_cli_help():
    from typer.testing import CliRunner
    from atlas.cli import app
    result = CliRunner().invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "pull" in result.stdout
    assert "watch" in result.stdout
