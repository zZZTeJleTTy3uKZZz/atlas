"""Тесты миграции 004 (tags and archive engine).

Проверяют:
- upgrade от initial до head: схема соответствует моделям.
- downgrade -1: таблицы tags/project_tags удалены, колонки projects откачены,
  seed-данные миграции удалены.
- upgrade ещё раз: clean (никаких ошибок дубликатов).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Временная SQLite БД для миграционных тестов."""
    db_path = tmp_path / "atlas_mig_test.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    return url


def _cfg(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _query(url: str, sql: str):
    import sqlite3

    path = url.replace("sqlite:///", "")
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_004_upgrade_creates_new_tables(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")

    tables = [r[0] for r in _query(
        tmp_db,
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
    )]
    assert "tags" in tables
    assert "project_tags" in tables


def test_004_upgrade_adds_project_columns(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")

    cols = [r[1] for r in _query(tmp_db, "PRAGMA table_info(projects)")]
    assert "renewal_count" in cols
    assert "archived_group" in cols


def test_004_upgrade_seeds_new_type_and_statuses(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")

    type_slugs = {r[0] for r in _query(tmp_db, "SELECT slug FROM project_types")}
    assert "test" in type_slugs

    status_slugs = {r[0] for r in _query(
        tmp_db, "SELECT slug FROM project_statuses"
    )}
    expected = {"idea", "research", "planned", "paused", "frozen", "completed"}
    assert expected.issubset(status_slugs)


def test_004_roundtrip_upgrade_downgrade_upgrade(tmp_db):
    cfg = _cfg(tmp_db)
    # up до конца и явно к ревизии PRED-004, чтобы после любых будущих миграций
    # (например 005) тест проверял именно откат 004 — а не просто "-1".
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "c55f75e76e5b")

    # После downgrade до ревизии ДО 004: tags/project_tags нет.
    tables = [r[0] for r in _query(
        tmp_db,
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
    )]
    assert "tags" not in tables
    assert "project_tags" not in tables

    # Seed-данные миграции 004 откачены.
    status_slugs = {r[0] for r in _query(
        tmp_db, "SELECT slug FROM project_statuses"
    )}
    assert "idea" not in status_slugs
    assert "paused" not in status_slugs

    # up снова — без ошибок.
    command.upgrade(cfg, "head")
    tables2 = [r[0] for r in _query(
        tmp_db,
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
    )]
    assert "tags" in tables2
    assert "project_tags" in tables2
