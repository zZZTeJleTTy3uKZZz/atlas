"""SQLAlchemy engine и session-factory для PM-системы.

Дефолтное расположение БД: `~/.atlas/atlas.db` (не коммитится).
Для тестов используется `sqlite:///:memory:`.
"""
from __future__ import annotations

import os
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


def resolve_db_url() -> str:
    """SQLite URL БД с учётом активного профиля (F4e): профиль = отдельный стор.

    Приоритет:
    1. env ``ATLAS_DB_URL`` — явный override (тесты/спец-случаи), всегда побеждает;
    2. активный профиль (env ``ATLAS_PROFILE``, ставится корневым ``--profile``):
       путь = ``<config_dir профиля>/atlas.db`` — clikit ``_config_dir('atlas')``
       при заданном профиле уводит в ``profiles/<p>/``, так у каждого профиля
       СВОЯ atlas.db (два профиля → две независимые БД → два стора);
    3. без профиля — ``~/.atlas/atlas.db`` (``DEFAULT_DB_PATH``, обратная
       совместимость с однопрофильным режимом Дмитрия).

    Единая точка правды: команды зовут ``resolve_db_url()`` вместо локальных
    ``_db_url()`` (которые игнорировали профиль).
    """
    override = os.environ.get("ATLAS_DB_URL")
    if override:
        return override
    if os.environ.get("ATLAS_PROFILE"):
        # clikit сам читает ATLAS_PROFILE/ATLAS_CONFIG_DIR и уводит в profiles/<p>/.
        from clikit.config import _config_dir

        db_path = _config_dir("atlas") / "atlas.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{db_path}"
    return _default_url()


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
