"""F3d: SyncCursor get/set по каналу."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import Base
from atlas.pm.sync import cursor


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_get_none_initially(session):
    assert cursor.get_cursor(session, "atlas") is None


def test_set_then_get(session):
    cursor.set_cursor(session, "atlas", "2026-06-14T10:00:00")
    session.commit()
    assert cursor.get_cursor(session, "atlas") == "2026-06-14T10:00:00"


def test_set_overwrites(session):
    cursor.set_cursor(session, "atlas", "2026-06-14T10:00:00")
    cursor.set_cursor(session, "atlas", "2026-06-14T11:00:00")
    session.commit()
    assert cursor.get_cursor(session, "atlas") == "2026-06-14T11:00:00"
