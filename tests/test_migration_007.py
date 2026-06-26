"""Тесты миграции 007 (add entity_kind to projects + simplify statuses).

W45-39: добавляем колонку `projects.entity_kind` и new status `cancelled`.

Проверяют:
- upgrade head: колонка `entity_kind` существует с CHECK constraint.
- Backfill: проекты type=inbox получают entity_kind='inbox', все остальные
  → 'project' (default).
- Backfill: проекты status=idea получают entity_kind='idea', status=active.
- Конверсия legacy-статусов research/maintained/planned/graduating → active,
  dormant/frozen → paused.
- Status `cancelled` добавлен в project_statuses.
- CHECK ck_projects_entity_kind — допустимые значения 'project', 'idea',
  'inbox'.
- Roundtrip up→down→up — clean.
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


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Временная SQLite БД для миграционных тестов."""
    db_path = tmp_path / "atlas_mig_007_test.db"
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


def _seed_minimal_projects_at_006(url: str) -> dict[str, str]:
    """Создать минимальный seed на ревизии 006: типы, статусы, 3 проекта.

    Возвращает {slug → project_id} для проверок после миграции.
    """
    # Get type ids: миграция 005 уже создала project_type 'inbox'.
    # client-project и business-product создаём руками (insert if not exists).
    type_ids: dict[str, str] = {}
    now_ts = datetime.utcnow().isoformat(sep=" ")
    # Получить существующие.
    existing = {
        row[1]: row[0]
        for row in _query(url, "SELECT id, slug FROM project_types")
    }
    for slug in ("client-project", "business-product", "inbox"):
        if slug in existing:
            type_ids[slug] = existing[slug]
            continue
        tid = str(uuid.uuid4())
        type_ids[slug] = tid
        _exec(
            url,
            "INSERT INTO project_types (id, slug, name, is_archived, created_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (tid, slug, slug.replace("-", " ").title(), now_ts),
        )
    # Statuses: idea, active, dormant, archived (нужны для конверсий).
    status_ids: dict[str, str] = {}
    existing_st = {
        row[1]: row[0]
        for row in _query(url, "SELECT id, slug FROM project_statuses")
    }
    for i, slug in enumerate(("idea", "active", "dormant", "archived"), start=100):
        if slug in existing_st:
            status_ids[slug] = existing_st[slug]
            continue
        sid = str(uuid.uuid4())
        status_ids[slug] = sid
        _exec(
            url,
            "INSERT INTO project_statuses (id, slug, name, order_idx, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (sid, slug, slug.title(), i, datetime.utcnow().isoformat(sep=" ")),
        )

    # 3 проекта:
    #   - p1: type=client-project, status=idea     → должен стать
    #         entity_kind=idea, status=active.
    #   - p2: type=inbox,          status=active   → entity_kind=inbox.
    #   - p3: type=business-product, status=dormant → entity_kind=project,
    #         status=paused.
    proj_ids: dict[str, str] = {}
    cases = [
        ("p1", "client-project", "idea"),
        ("p2", "inbox", "active"),
        ("p3", "business-product", "dormant"),
    ]
    now = datetime.utcnow().isoformat(sep=" ")
    for slug, tslug, sslug in cases:
        pid = str(uuid.uuid4())
        proj_ids[slug] = pid
        _exec(
            url,
            "INSERT INTO projects "
            "(id, slug, name, type_id, status_id, priority, one_line_summary, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'P2', '-', ?, ?)",
            (
                pid, slug, f"Project {slug}",
                type_ids[tslug], status_ids[sslug],
                now, now,
            ),
        )
    return proj_ids


def test_007_upgrade_adds_entity_kind_column(tmp_db):
    """После upgrade head — projects содержит entity_kind."""
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")
    cols = _columns(tmp_db, "projects")
    assert "entity_kind" in cols, "entity_kind колонка должна быть после 007"
    # NOT NULL + default 'project'
    assert cols["entity_kind"]["notnull"] == 1
    assert "project" in str(cols["entity_kind"]["dflt_value"])


def test_007_backfill_inbox_type_becomes_inbox_kind(tmp_db):
    """Проекты с type=inbox после миграции имеют entity_kind='inbox'."""
    cfg = _cfg(tmp_db)
    # upgrade до 006 → seed проекты → upgrade до 007.
    command.upgrade(cfg, "237c08c450f6")
    proj_ids = _seed_minimal_projects_at_006(tmp_db)
    command.upgrade(cfg, "head")

    rows = _query(
        tmp_db,
        "SELECT slug, entity_kind FROM projects WHERE id IN (?, ?, ?)",
        tuple(proj_ids.values()),
    )
    by_slug = {r[0]: r[1] for r in rows}
    assert by_slug["p1"] == "idea", "p1 (status=idea) → entity_kind=idea"
    assert by_slug["p2"] == "inbox", "p2 (type=inbox) → entity_kind=inbox"
    assert by_slug["p3"] == "project", "p3 → entity_kind=project (default)"


def test_007_backfill_idea_status_becomes_active(tmp_db):
    """Проекты status=idea после миграции имеют status=active (idea → kind)."""
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "237c08c450f6")
    proj_ids = _seed_minimal_projects_at_006(tmp_db)
    command.upgrade(cfg, "head")

    rows = _query(
        tmp_db,
        "SELECT p.slug, s.slug FROM projects p "
        "JOIN project_statuses s ON s.id = p.status_id "
        "WHERE p.id = ?",
        (proj_ids["p1"],),
    )
    assert rows[0][1] == "active", "p1 status=idea должен стать active"


def test_007_backfill_dormant_status_becomes_paused(tmp_db):
    """Проекты status=dormant → status=paused."""
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "237c08c450f6")

    # paused должен быть в project_statuses ДО миграции 007 для конверсии.
    # Проверим — может уже добавлен ранней миграцией.
    existing_paused = _query(
        tmp_db, "SELECT id FROM project_statuses WHERE slug='paused'"
    )
    if not existing_paused:
        paused_id = str(uuid.uuid4())
        _exec(
            tmp_db,
            "INSERT INTO project_statuses (id, slug, name, order_idx, created_at) "
            "VALUES (?, 'paused', 'Paused', 99, ?)",
            (paused_id, datetime.utcnow().isoformat(sep=" ")),
        )

    proj_ids = _seed_minimal_projects_at_006(tmp_db)
    command.upgrade(cfg, "head")

    rows = _query(
        tmp_db,
        "SELECT s.slug FROM projects p "
        "JOIN project_statuses s ON s.id = p.status_id "
        "WHERE p.id = ?",
        (proj_ids["p3"],),
    )
    assert rows[0][0] == "paused", "p3 status=dormant должен стать paused"


def test_007_adds_cancelled_status(tmp_db):
    """После 007 статус `cancelled` присутствует в project_statuses."""
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")
    rows = _query(
        tmp_db,
        "SELECT slug, name FROM project_statuses WHERE slug = 'cancelled'",
    )
    assert len(rows) == 1
    assert rows[0][1] == "Отменено"


def test_007_check_constraint_rejects_invalid_kind(tmp_db):
    """CHECK ck_projects_entity_kind отвергает не-канонические значения."""
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")

    # Минимальные FK: добавим тип + статус.
    type_id = str(uuid.uuid4())
    status_id = str(uuid.uuid4())
    now_ts = datetime.utcnow().isoformat(sep=" ")
    _exec(
        tmp_db,
        "INSERT INTO project_types (id, slug, name, is_archived, created_at) "
        "VALUES (?, 't', 'T', 0, ?)",
        (type_id, now_ts),
    )
    _exec(
        tmp_db,
        "INSERT INTO project_statuses (id, slug, name, order_idx, created_at) "
        "VALUES (?, 'st', 'St', 99, ?)",
        (status_id, datetime.utcnow().isoformat(sep=" ")),
    )
    pid = str(uuid.uuid4())
    now = datetime.utcnow().isoformat(sep=" ")
    with pytest.raises(sqlite3.IntegrityError):
        _exec(
            tmp_db,
            "INSERT INTO projects "
            "(id, slug, name, type_id, status_id, priority, one_line_summary, "
            "entity_kind, created_at, updated_at) "
            "VALUES (?, 'bad', 'Bad', ?, ?, 'P2', '-', 'invalid', ?, ?)",
            (pid, type_id, status_id, now, now),
        )


def test_007_roundtrip_upgrade_downgrade_upgrade(tmp_db):
    """Roundtrip миграции 007: up→down→up — clean.

    Используем абсолютные ревизии 007 (ca84c1d9b54e) и её предшественника
    (237c08c450f6) вместо относительных head/-1 — чтобы тест проверял именно
    007 и оставался устойчивым к новым миграциям поверх head (напр. F3b).
    """
    rev_007 = "ca84c1d9b54e"
    rev_before_007 = "237c08c450f6"
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, rev_007)
    cols_after_up = _columns(tmp_db, "projects")
    assert "entity_kind" in cols_after_up

    command.downgrade(cfg, rev_before_007)
    cols_after_down = _columns(tmp_db, "projects")
    assert "entity_kind" not in cols_after_down

    command.upgrade(cfg, rev_007)
    cols_after_up2 = _columns(tmp_db, "projects")
    assert "entity_kind" in cols_after_up2


def test_007_paths_entity_kind_to_root(tmp_path, monkeypatch):
    """paths.entity_kind_to_root() — routing для каждого kind."""
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
    from atlas.paths import entity_kind_to_root, entity_logical_path

    # idea → _Ideas
    assert entity_kind_to_root("idea").name == "_Ideas"
    # inbox → _Inbox
    assert entity_kind_to_root("inbox").name == "_Inbox"
    # project + type=client-project → Clients
    assert entity_kind_to_root("project", "client-project").name == "Clients"
    # project + type=business-product → Products
    assert entity_kind_to_root("project", "business-product").name == "Products"
    # project + type=test → Tests
    assert entity_kind_to_root("project", "test").name == "Tests"

    # Logical path: idea — это .md файл!
    p = entity_logical_path("idea", "my-idea")
    assert p.name == "my-idea.md"
    # Logical path: project — папка
    p2 = entity_logical_path("project", "my-proj", "client-project")
    assert p2.name == "my-proj"
    # Logical path: inbox — папка
    p3 = entity_logical_path("inbox", "raw-stuff")
    assert p3.name == "raw-stuff"


def test_007_paths_entity_kind_to_root_invalid(tmp_path, monkeypatch):
    """Неизвестный entity_kind → ValueError."""
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
    from atlas.paths import entity_kind_to_root

    with pytest.raises(ValueError, match="entity_kind"):
        entity_kind_to_root("client")  # не canonical kind
    # project без type_slug — тоже ошибка.
    with pytest.raises(ValueError, match="type_slug"):
        entity_kind_to_root("project")
