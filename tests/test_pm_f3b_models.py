"""F3b: новые модели создаются и связываются в in-memory БД."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import (
    Base, Counterparty, Project, ProjectStatus, ProjectType, SyncPolicy,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _mk_type_status(s):
    t = ProjectType(slug="client-project", name="Кл", default_sync_policy="full")
    st = ProjectStatus(slug="active", name="A", order_idx=1)
    s.add_all([t, st])
    s.flush()
    return t, st


def test_sync_policy_crud(session):
    session.add(SyncPolicy(slug="full", name="Full", sync_epic=1, sync_task=1, sync_checklist=1))
    session.commit()
    p = session.get(SyncPolicy, "full")
    assert (p.sync_epic, p.sync_task, p.sync_checklist) == (1, 1, 1)


def test_counterparty_and_project_owner(session):
    session.add(SyncPolicy(slug="full", name="Full", sync_epic=1, sync_task=1, sync_checklist=1))
    t, st = _mk_type_status(session)
    owner = Counterparty(slug="cifro-pro", kind="company", name="Cifro.pro", git_namespace="cifropro1")
    session.add(owner)
    session.flush()
    proj = Project(
        slug="acme", name="Acme", type_id=t.id, status_id=st.id, priority="P2",
        one_line_summary="x", owner_id=owner.id, sync_policy="full",
    )
    session.add(proj)
    session.commit()
    got = session.get(Project, proj.id)
    assert got.owner_id == owner.id
    assert got.sync_policy == "full"


def test_counterparty_kind_constraint(session):
    from sqlalchemy.exc import IntegrityError
    session.add(Counterparty(slug="bad", kind="alien", name="X"))
    with pytest.raises(IntegrityError):
        session.commit()
