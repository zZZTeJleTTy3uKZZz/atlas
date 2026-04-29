"""Тесты миграции 006 (add git fields to projects).

Проверяют:
- upgrade head: новые колонки `git_remote_url`, `git_default_branch`,
  `git_provider`, `git_initialized_at`, `git_last_pushed_at` существуют.
- `git_default_branch` имеет server_default='main'.
- CHECK ck_projects_git_provider — допустимые значения 'gitlab', 'github' и NULL.
- Roundtrip upgrade → downgrade → upgrade — clean.

TDD: пишется ДО миграции 006 (RED), потом реализация → GREEN.
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
    db_path = tmp_path / "atlas_mig_006_test.db"
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
    """Вернуть mapping column_name → {dflt_value, notnull, type}."""
    rows = _query(url, f"PRAGMA table_info({table})")
    # rows: cid, name, type, notnull, dflt_value, pk
    return {
        r[1]: {"type": r[2], "notnull": r[3], "dflt_value": r[4], "pk": r[5]}
        for r in rows
    }


def test_006_upgrade_adds_git_columns(tmp_db):
    """После upgrade head — projects содержит 5 git-колонок."""
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")

    cols = _columns(tmp_db, "projects")
    expected = {
        "git_remote_url",
        "git_default_branch",
        "git_provider",
        "git_initialized_at",
        "git_last_pushed_at",
    }
    missing = expected - set(cols.keys())
    assert not missing, f"После upgrade отсутствуют колонки: {missing}"


def test_006_git_default_branch_has_server_default_main(tmp_db):
    """git_default_branch имеет server_default='main'."""
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")

    cols = _columns(tmp_db, "projects")
    assert "git_default_branch" in cols
    dflt = cols["git_default_branch"]["dflt_value"]
    # SQLite хранит server_default как строку, обычно с кавычками: "'main'"
    assert dflt is not None, "git_default_branch должен иметь server_default"
    assert "main" in str(dflt), f"Ожидал 'main' в server_default, получил {dflt!r}"


def test_006_git_provider_check_constraint_allows_gitlab_github_null(tmp_db):
    """CHECK ck_projects_git_provider пропускает 'gitlab', 'github' и NULL."""
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")

    # Возьмём валидные type_id и status_id (миграция 005 уже сидит inbox).
    type_rows = _query(tmp_db, "SELECT id FROM project_types LIMIT 1")
    status_rows = _query(tmp_db, "SELECT id FROM project_statuses LIMIT 1")
    assert type_rows and status_rows
    type_id, status_id = type_rows[0][0], status_rows[0][0]

    now = datetime.utcnow().isoformat(sep=" ")

    # 1) NULL provider — должен пройти
    _exec(
        tmp_db,
        """
        INSERT INTO projects (
            id, slug, name, type_id, status_id, priority, one_line_summary,
            renewal_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'P2', 's', 0, ?, ?)
        """,
        (str(uuid.uuid4()), "git-null-provider", "X", type_id, status_id, now, now),
    )

    # 2) provider='gitlab' — должен пройти
    _exec(
        tmp_db,
        """
        INSERT INTO projects (
            id, slug, name, type_id, status_id, priority, one_line_summary,
            renewal_count, git_provider, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'P2', 's', 0, 'gitlab', ?, ?)
        """,
        (str(uuid.uuid4()), "git-gitlab", "X", type_id, status_id, now, now),
    )

    # 3) provider='github' — должен пройти
    _exec(
        tmp_db,
        """
        INSERT INTO projects (
            id, slug, name, type_id, status_id, priority, one_line_summary,
            renewal_count, git_provider, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'P2', 's', 0, 'github', ?, ?)
        """,
        (str(uuid.uuid4()), "git-github", "X", type_id, status_id, now, now),
    )

    # 4) provider='bitbucket' — должен упасть
    with pytest.raises(sqlite3.IntegrityError):
        _exec(
            tmp_db,
            """
            INSERT INTO projects (
                id, slug, name, type_id, status_id, priority, one_line_summary,
                renewal_count, git_provider, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'P2', 's', 0, 'bitbucket', ?, ?)
            """,
            (str(uuid.uuid4()), "git-bb", "X", type_id, status_id, now, now),
        )


def test_006_git_default_branch_value_main_for_new_project(tmp_db):
    """server_default 'main' применяется при INSERT без явного значения."""
    cfg = _cfg(tmp_db)
    command.upgrade(cfg, "head")

    type_rows = _query(tmp_db, "SELECT id FROM project_types LIMIT 1")
    status_rows = _query(tmp_db, "SELECT id FROM project_statuses LIMIT 1")
    type_id, status_id = type_rows[0][0], status_rows[0][0]
    now = datetime.utcnow().isoformat(sep=" ")

    _exec(
        tmp_db,
        """
        INSERT INTO projects (
            id, slug, name, type_id, status_id, priority, one_line_summary,
            renewal_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'P2', 's', 0, ?, ?)
        """,
        (str(uuid.uuid4()), "git-default-branch", "X", type_id, status_id, now, now),
    )

    rows = _query(
        tmp_db,
        "SELECT git_default_branch FROM projects WHERE slug = 'git-default-branch'",
    )
    assert rows == [("main",)]


def test_006_roundtrip_upgrade_downgrade_upgrade(tmp_db):
    """Roundtrip миграции 006: up до 006 → down -1 (= 005) → up до head — clean.

    NOTE: после миграции 007 (entity_kind) head — это `ca84c1d9b54e`. Чтобы
    тестировать конкретно 006, upgrade'имся явно до её ревизии, потом
    откатываемся на 1, потом до head обратно.
    """
    cfg = _cfg(tmp_db)

    # up до миграции 006 (rev 237c08c450f6).
    command.upgrade(cfg, "237c08c450f6")
    cols_after_up = _columns(tmp_db, "projects")
    assert "git_remote_url" in cols_after_up

    # down -1 (006 → 005)
    command.downgrade(cfg, "-1")
    cols_after_down = _columns(tmp_db, "projects")
    # Колонки 006 должны исчезнуть.
    assert "git_remote_url" not in cols_after_down
    assert "git_default_branch" not in cols_after_down
    assert "git_provider" not in cols_after_down
    assert "git_initialized_at" not in cols_after_down
    assert "git_last_pushed_at" not in cols_after_down

    # up до head — без ошибок (включая 006 + 007).
    command.upgrade(cfg, "head")
    cols_after_up2 = _columns(tmp_db, "projects")
    assert "git_remote_url" in cols_after_up2
    assert "git_default_branch" in cols_after_up2
    # 007 тоже применилась.
    assert "entity_kind" in cols_after_up2
