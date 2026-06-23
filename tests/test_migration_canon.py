"""Тесты миграции canon (d4e5f6a7b8c9).

Канон типов: project_types.storage_group + projects.parent_id.

Проверяют:
- upgrade head: колонки storage_group и parent_id существуют.
- Индекс idx_projects_parent создан.
- Backfill storage_group по текущей карте TYPE_TO_GROUP: client-project→clients,
  test→tests, inbox→inbox, business-product/прочее→products.
- Roundtrip up→down→up — clean.

Стиль — как в test_pm_migration_007.py: Config + alembic.command, sqlite3 для
PRAGMA / прямых запросов, monkeypatch ATLAS_DB_URL.
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

REV_CANON = "d4e5f6a7b8c9"
REV_BEFORE_CANON = "c1d2e3f4a5b6"


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "atlas_mig_canon_test.db"
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


def _seed_types_before_canon(url: str) -> None:
    """Заселить project_types различных slug ДО миграции canon.

    Покрывает все ветки backfill: client-project, test, inbox,
    business-product (→products) и custom-type (не в карте → products).
    """
    existing = {row[1] for row in _query(url, "SELECT id, slug FROM project_types")}
    now_ts = datetime.utcnow().isoformat(sep=" ")
    for slug in ("client-project", "business-product", "test", "inbox", "custom-type"):
        if slug in existing:
            continue
        _exec(
            url,
            "INSERT INTO project_types (id, slug, name, is_archived, created_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (str(uuid.uuid4()), slug, slug.replace("-", " ").title(), now_ts),
        )


def test_canon_upgrade_adds_columns(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")
    pt_cols = _columns(tmp_db, "project_types")
    proj_cols = _columns(tmp_db, "projects")
    assert "storage_group" in pt_cols
    assert "parent_id" in proj_cols


def test_canon_creates_parent_index(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")
    idx = _query(
        tmp_db,
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='idx_projects_parent'",
    )
    assert len(idx) == 1


def test_canon_backfill_storage_group(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, REV_BEFORE_CANON)
    _seed_types_before_canon(tmp_db)
    command.upgrade(cfg, "head")

    rows = _query(tmp_db, "SELECT slug, storage_group FROM project_types")
    by_slug = {r[0]: r[1] for r in rows}
    assert by_slug["client-project"] == "clients"
    assert by_slug["test"] == "tests"
    assert by_slug["inbox"] == "inbox"
    assert by_slug["business-product"] == "products"
    # тип не в явной карте → products
    assert by_slug["custom-type"] == "products"


def test_canon_roundtrip_upgrade_downgrade_upgrade(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, REV_CANON)
    assert "storage_group" in _columns(tmp_db, "project_types")
    assert "parent_id" in _columns(tmp_db, "projects")

    command.downgrade(cfg, REV_BEFORE_CANON)
    assert "storage_group" not in _columns(tmp_db, "project_types")
    assert "parent_id" not in _columns(tmp_db, "projects")

    command.upgrade(cfg, REV_CANON)
    assert "storage_group" in _columns(tmp_db, "project_types")
    assert "parent_id" in _columns(tmp_db, "projects")
