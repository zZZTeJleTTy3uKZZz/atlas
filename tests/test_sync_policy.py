"""F3c: policy.should_sync — потолок синка по SyncPolicy с дефолтом от типа."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import (
    Base, Project, ProjectStatus, ProjectType, SyncPolicy,
)
from atlas.pm.sync import policy


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _setup(s, *, type_default=None, project_policy=None):
    s.add_all([
        SyncPolicy(slug="local", name="l", sync_epic=0, sync_task=0, sync_checklist=0),
        SyncPolicy(slug="epics", name="e", sync_epic=1, sync_task=0, sync_checklist=0),
        SyncPolicy(slug="full", name="f", sync_epic=1, sync_task=1, sync_checklist=1),
    ])
    t = ProjectType(slug="t", name="t", default_sync_policy=type_default)
    st = ProjectStatus(slug="a", name="a", order_idx=1)
    s.add_all([t, st]); s.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2",
                one_line_summary="x", sync_policy=project_policy)
    s.add(p); s.flush()
    return p


def test_project_policy_wins(session):
    p = _setup(session, type_default="local", project_policy="full")
    assert policy.should_sync(session, "task", p) is True
    assert policy.should_sync(session, "checklist", p) is True


def test_falls_back_to_type_default(session):
    p = _setup(session, type_default="epics", project_policy=None)
    assert policy.should_sync(session, "epic", p) is True
    assert policy.should_sync(session, "task", p) is False


def test_no_policy_no_sync(session):
    p = _setup(session, type_default=None, project_policy=None)
    assert policy.should_sync(session, "epic", p) is False


def test_unknown_level(session):
    p = _setup(session, type_default="full", project_policy=None)
    assert policy.should_sync(session, "bogus", p) is False
