"""Тесты миграции 005 (add inbox project_type + allow archived_group inbox).

Проверяют:
- upgrade до head: seed нового project_type 'inbox' попадает в БД.
- upgrade до head: CHECK constraint projects.archived_group принимает 'inbox'.
- downgrade -1: inbox удалён, CHECK вернул только clients/products/tests.
- Roundtrip upgrade → downgrade → upgrade: clean, без дубликатов.

TDD: пишется ДО миграции 005 (сначала падает — ревизии ещё нет, потом зелёная).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Временная SQLite БД для миграционных тестов."""
    db_path = tmp_path / "atlas_mig_005_test.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    return url


def _cfg(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _query(url: str, sql: str, params=()):
    import sqlite3

    path = url.replace("sqlite:///", "")
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _exec(url: str, sql: str, params=()):
    import sqlite3

    path = url.replace("sqlite:///", "")
    conn = sqlite3.connect(path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def test_005_upgrade_seeds_inbox_type(tmp_db):
    """После upgrade head — project_type со slug='inbox' существует."""
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")

    rows = _query(tmp_db, "SELECT slug, name, color FROM project_types WHERE slug = 'inbox'")
    assert len(rows) == 1
    slug, name, color = rows[0]
    assert slug == "inbox"
    # name — что-то осмысленное, не пустое
    assert name
    # color — hex (проверим что начинается с #)
    assert color and color.startswith("#")


def test_005_upgrade_allows_inbox_archived_group(tmp_db):
    """После upgrade — можно INSERT в projects с archived_group='inbox'.

    До миграции 005 CHECK запрещал inbox. Проверяем что CHECK обновлён:
    raw SQL INSERT с archived_group='inbox' должен пройти без IntegrityError.
    """
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")

    # Нужны валидные type_id и status_id из сидов, плюс проверим что inbox тоже присутствует.
    type_rows = _query(tmp_db, "SELECT id FROM project_types WHERE slug = 'inbox'")
    assert type_rows, "inbox project_type должен быть после upgrade"
    type_id = type_rows[0][0]

    status_rows = _query(tmp_db, "SELECT id FROM project_statuses LIMIT 1")
    assert status_rows
    status_id = status_rows[0][0]

    now = datetime.utcnow().isoformat(sep=" ")
    project_id = str(uuid.uuid4())
    # raw INSERT — если CHECK не обновлён, будет IntegrityError.
    _exec(
        tmp_db,
        """
        INSERT INTO projects (
            id, slug, name, type_id, status_id, priority, one_line_summary,
            renewal_count, archived_group, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id, "raw-inbox-test", "Raw inbox test", type_id, status_id,
            "P2", "smoke test", 0, "inbox", now, now,
        ),
    )

    rows = _query(
        tmp_db,
        "SELECT archived_group FROM projects WHERE slug = 'raw-inbox-test'",
    )
    assert rows == [("inbox",)]


def test_005_roundtrip_upgrade_downgrade_upgrade(tmp_db):
    """Roundtrip: up→down→up — clean, inbox seed идемпотентен."""
    cfg = _cfg(tmp_db)
    # up
    command.upgrade(cfg, "head")
    assert _query(tmp_db, "SELECT slug FROM project_types WHERE slug='inbox'") == [("inbox",)]

    # down -1
    command.downgrade(cfg, "-1")
    # inbox seed удалён.
    assert _query(tmp_db, "SELECT slug FROM project_types WHERE slug='inbox'") == []

    # up снова — без ошибок дубликатов.
    command.upgrade(cfg, "head")
    assert _query(tmp_db, "SELECT slug FROM project_types WHERE slug='inbox'") == [("inbox",)]


def test_005_downgrade_restores_old_check_constraint(tmp_db):
    """После downgrade -1 — archived_group='inbox' снова запрещён (CHECK возвращён)."""
    import sqlite3

    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")

    # Попытка вставить с archived_group='inbox' должна провалиться.
    # Нужны валидный type_id / status_id.
    type_rows = _query(tmp_db, "SELECT id FROM project_types LIMIT 1")
    status_rows = _query(tmp_db, "SELECT id FROM project_statuses LIMIT 1")
    if not (type_rows and status_rows):
        pytest.skip("Нет базовых seed-данных после downgrade")

    type_id = type_rows[0][0]
    status_id = status_rows[0][0]
    now = datetime.utcnow().isoformat(sep=" ")

    with pytest.raises(sqlite3.IntegrityError):
        _exec(
            tmp_db,
            """
            INSERT INTO projects (
                id, slug, name, type_id, status_id, priority, one_line_summary,
                renewal_count, archived_group, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), "inbox-forbidden", "X", type_id, status_id,
                "P2", "s", 0, "inbox", now, now,
            ),
        )
