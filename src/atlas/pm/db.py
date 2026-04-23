"""SQLAlchemy engine и session-factory для PM-системы.

Дефолтное расположение БД: `~/.atlas/atlas.db` (не коммитится).
Для тестов используется `sqlite:///:memory:`.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DB_PATH = Path.home() / ".atlas" / "atlas.db"


def _default_url() -> str:
    """Сформировать SQLite URL для дефолтной БД Дмитрия."""
    DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_DB_PATH}"


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Создать SQLAlchemy engine.

    Параметры:
        url: SQLAlchemy URL. Если None — дефолтная локальная SQLite.
        echo: логировать SQL (для debug).
    """
    effective_url = url or _default_url()
    engine = create_engine(effective_url, echo=echo, future=True)

    # Включаем FK-constraints в SQLite (по умолчанию отключены)
    if effective_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


@contextmanager
def make_session(engine: Engine) -> Iterator[Session]:
    """Контекст-менеджер сессии SQLAlchemy.

    Коммит вручную (session.commit()). Rollback автоматически при исключении.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
