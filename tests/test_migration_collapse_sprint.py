"""Тесты миграции collapse sprint→epic (e5f6a7b8c9d0).

Схлопывание tasks.sprint_id в tasks.epic_id.

Проверяют:
- upgrade head: колонки sprint_id больше нет, epic_id есть.
- Индекс idx_tasks_sprint удалён, idx_tasks_epic создан.
- Backfill: task.sprint_id = epics.slug → epic_id заполняется id эпика.
- Несуществующий slug в sprint_id → epic_id остаётся NULL.
- downgrade: sprint_id + idx_tasks_sprint восстановлены, обратный backfill
  epic_id → slug.
- Roundtrip up→down→up — clean.

Стиль — как test_pm_migration_canon.py: Config + alembic.command, sqlite3 для
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

REV_COLLAPSE = "e5f6a7b8c9d0"
REV_BEFORE = "d4e5f6a7b8c9"


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "atlas_mig_collapse_test.db"
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


def _seed_project_epic_tasks(url: str) -> str:
    """Заселить project + epic(slug='sprint-1') + 2 задачи ДО collapse.

    task1.sprint_id = 'sprint-1' (валидный slug эпика) → ожидаем backfill.
    task2.sprint_id = 'ghost' (нет такого эпика) → epic_id остаётся NULL.

    Возвращает id эпика.
    """
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
        (epic_id, "sprint-1", proj, "Sprint 1", "active", "native", now, now),
    )
    _exec(
        url,
        "INSERT INTO tasks (id, number, slug, project_id, sprint_id, epic_id, "
        "title, cpp_description, status, priority, origin, created_at, "
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), 1, "cp-t1", proj, "sprint-1", None, "T1", "cpp",
         "todo", "P2", "native", now, now),
    )
    _exec(
        url,
        "INSERT INTO tasks (id, number, slug, project_id, sprint_id, epic_id, "
        "title, cpp_description, status, priority, origin, created_at, "
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), 2, "cp-t2", proj, "ghost", None, "T2", "cpp",
         "todo", "P2", "native", now, now),
    )
    return epic_id


def test_collapse_drops_sprint_keeps_epic(tmp_db):
    cfg = _cfg(tmp_db)
    # Проверяем эффект collapse на ЕГО ревизии: позднее (13f6db0144ed) sprint_id
    # вернулся как ОТДЕЛЬНЫЙ Scrum-тайм-бокс (FK→sprints), это другая семантика.
    command.upgrade(cfg, REV_COLLAPSE)
    cols = _columns(tmp_db, "tasks")
    assert "sprint_id" not in cols
    assert "epic_id" in cols


def test_collapse_index_swap(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")
    idx = _indexes(tmp_db, "tasks")
    assert "idx_tasks_sprint" not in idx
    assert "idx_tasks_epic" in idx


def test_collapse_backfill_slug_to_epic_id(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, REV_BEFORE)
    epic_id = _seed_project_epic_tasks(tmp_db)
    # До ревизии collapse (на head sprint_id вернулся как отдельный тайм-бокс).
    command.upgrade(cfg, REV_COLLAPSE)

    rows = _query(tmp_db, "SELECT number, epic_id FROM tasks ORDER BY number")
    by_number = {r[0]: r[1] for r in rows}
    # task1: sprint_id='sprint-1' → epic_id = id эпика
    assert by_number[1] == epic_id
    # task2: sprint_id='ghost' (нет эпика) → epic_id остался NULL
    assert by_number[2] is None
    # после collapse колонки sprint_id нет (на ЭТОЙ ревизии)
    assert "sprint_id" not in _columns(tmp_db, "tasks")


def test_collapse_downgrade_restores_sprint_and_backfills(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, REV_BEFORE)
    epic_id = _seed_project_epic_tasks(tmp_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, REV_BEFORE)

    cols = _columns(tmp_db, "tasks")
    assert "sprint_id" in cols
    idx = _indexes(tmp_db, "tasks")
    assert "idx_tasks_sprint" in idx
    assert "idx_tasks_epic" not in idx

    # Обратный backfill: epic_id → slug эпика обратно в sprint_id.
    rows = _query(tmp_db, "SELECT number, sprint_id FROM tasks ORDER BY number")
    by_number = {r[0]: r[1] for r in rows}
    assert by_number[1] == "sprint-1"
    # task2 не имел epic_id → sprint_id остался NULL
    assert by_number[2] is None
    assert epic_id  # sanity


def test_collapse_roundtrip(tmp_db):
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, REV_COLLAPSE)
    assert "sprint_id" not in _columns(tmp_db, "tasks")

    command.downgrade(cfg, REV_BEFORE)
    assert "sprint_id" in _columns(tmp_db, "tasks")

    command.upgrade(cfg, REV_COLLAPSE)
    assert "sprint_id" not in _columns(tmp_db, "tasks")
    assert "idx_tasks_epic" in _indexes(tmp_db, "tasks")
