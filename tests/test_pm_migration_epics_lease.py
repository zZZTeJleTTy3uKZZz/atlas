"""Тесты миграции epics-lease (b8c9d0e1f2a3).

Добавляет на epics lease/version-поля (симметрично tasks из a7b8c9d0e1f2):
- lease_owner / lease_session_id / lease_origin (TEXT NULL),
- claimed_at / lease_expires_at (DATETIME NULL),
- lock_version (INTEGER NOT NULL, server_default '0') — optimistic-lock.

Индексы: idx_epics_lease(lease_owner, lease_expires_at),
idx_epics_lease_expires(lease_expires_at).

Проверяют:
- upgrade head: 6 lease-колонок есть; lock_version NOT NULL с default 0.
- Индексы idx_epics_lease / idx_epics_lease_expires созданы.
- Backfill не нужен: существующая строка epics получает lock_version=0.
- downgrade: lease-колонки + индексы убраны.
- Roundtrip up→down→up — clean.

Стиль — как test_pm_migration_collapse_sprint.py.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"

REV_EPICS_LEASE = "b8c9d0e1f2a3"
REV_BEFORE = "a7b8c9d0e1f2"

_LEASE_COLS = {
    "lease_owner",
    "lease_session_id",
    "lease_origin",
    "claimed_at",
    "lease_expires_at",
    "lock_version",
}


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "atlas_mig_epics_lease_test.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    return url


def _cfg(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _query(url: str, sql: str, params=()):
    path = url.replace("sqlite:///", "")
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _exec(url: str, sql: str, params=()):
    path = url.replace("sqlite:///", "")
    conn = sqlite3.connect(path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _columns(url: str, table: str) -> dict[str, dict]:
    rows = _query(url, f"PRAGMA table_info({table})")
    return {
        r[1]: {"type": r[2], "notnull": r[3], "dflt_value": r[4], "pk": r[5]}
        for r in rows
    }


def _indexes(url: str, table: str) -> set[str]:
    rows = _query(
        url,
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (table,),
    )
    return {r[0] for r in rows}


def _seed_project_epic(url: str) -> str:
    """Заселить project + epic ДО epics-lease миграции. Возвращает id эпика."""
    now = datetime.now().isoformat(sep=" ")
    pt, st, proj = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    _exec(
        url,
        "INSERT INTO project_types (id, slug, name, is_archived, created_at) "
        "VALUES (?,?,?,0,?)",
        (pt, "cp", "CP", now),
    )
    _exec(
        url,
        "INSERT INTO project_statuses (id, slug, name, order_idx, created_at) "
        "VALUES (?,?,?,?,?)",
        (st, "act", "Active", 10, now),
    )
    _exec(
        url,
        "INSERT INTO projects (id, slug, name, type_id, status_id, priority, "
        "one_line_summary, renewal_count, git_default_branch, entity_kind, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,0,'main','project',?,?)",
        (proj, "acme", "Acme", pt, st, "P2", "x", now, now),
    )
    epic_id = str(uuid.uuid4())
    _exec(
        url,
        "INSERT INTO epics (id, slug, project_id, title, status, origin, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (epic_id, "e1", proj, "Epic 1", "active", "native", now, now),
    )
    return epic_id


def test_upgrade_adds_lease_columns(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")
    cols = _columns(tmp_db, "epics")
    assert _LEASE_COLS.issubset(cols.keys())
    # lock_version NOT NULL c default 0
    assert cols["lock_version"]["notnull"] == 1
    assert cols["lock_version"]["dflt_value"] in ("0", "'0'")
    # lease_owner nullable
    assert cols["lease_owner"]["notnull"] == 0


def test_upgrade_creates_indexes(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")
    idx = _indexes(tmp_db, "epics")
    assert "idx_epics_lease" in idx
    assert "idx_epics_lease_expires" in idx


def test_existing_epic_gets_lock_version_zero(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, REV_BEFORE)
    epic_id = _seed_project_epic(tmp_db)
    command.upgrade(cfg, "head")
    rows = _query(tmp_db, "SELECT lock_version FROM epics WHERE id=?", (epic_id,))
    assert rows[0][0] == 0


def test_downgrade_drops_lease(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, REV_BEFORE)
    cols = _columns(tmp_db, "epics")
    assert _LEASE_COLS.isdisjoint(cols.keys())
    idx = _indexes(tmp_db, "epics")
    assert "idx_epics_lease" not in idx
    assert "idx_epics_lease_expires" not in idx


def test_roundtrip(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, REV_EPICS_LEASE)
    assert _LEASE_COLS.issubset(_columns(tmp_db, "epics").keys())
    command.downgrade(cfg, REV_BEFORE)
    assert _LEASE_COLS.isdisjoint(_columns(tmp_db, "epics").keys())
    command.upgrade(cfg, REV_EPICS_LEASE)
    assert _LEASE_COLS.issubset(_columns(tmp_db, "epics").keys())
    assert "idx_epics_lease" in _indexes(tmp_db, "epics")
