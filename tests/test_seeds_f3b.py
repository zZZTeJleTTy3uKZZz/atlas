"""F3b: сиды политик/контрагентов идемпотентны, дефолты проставлены типам."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from atlas.models import Base, Counterparty, ProjectType, SyncPolicy
from atlas.seeds import seed_all


def _fresh_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_seed_all_includes_f3b():
    s = _fresh_session()
    seed_all(s)
    assert s.get(SyncPolicy, "full").sync_checklist == 1
    cp = s.execute(select(Counterparty).where(Counterparty.slug == "cifro-pro")).scalar_one()
    assert cp.git_namespace == "cifropro1"
    ct = s.execute(select(ProjectType).where(ProjectType.slug == "client-project")).scalar_one()
    assert ct.default_sync_policy == "full"


def test_seed_all_idempotent():
    s = _fresh_session()
    seed_all(s)
    seed_all(s)  # повторный вызов не должен падать/дублировать
    n = s.execute(select(SyncPolicy)).scalars().all()
    assert len(n) == 4
