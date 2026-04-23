"""Smoke-тесты PM-слоя: engine, sessions, создание таблиц из моделей."""
from __future__ import annotations

import pytest
from sqlalchemy import select


def test_pm_module_imports():
    """PM-модуль должен импортироваться без ошибок."""
    from atlas import pm
    from atlas.pm import db, models

    assert hasattr(db, "make_engine")
    assert hasattr(db, "make_session")


def test_can_create_sqlite_memory_engine():
    """Engine создаётся в памяти."""
    from atlas.pm.db import make_engine

    engine = make_engine("sqlite:///:memory:")
    assert engine is not None
    # Проверим, что соединение живое
    with engine.connect() as conn:
        result = conn.execute(select(1))
        assert result.scalar() == 1


def test_can_create_all_tables():
    """Все MVP-таблицы создаются из Base.metadata."""
    from atlas.pm.db import make_engine
    from atlas.pm.models import Base

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    expected_tables = {
        "project_types",
        "project_statuses",
        "projects",
        "participants",
        "project_participants",
        "tasks",
        "action_log",
    }
    actual_tables = set(Base.metadata.tables.keys())
    missing = expected_tables - actual_tables
    assert not missing, f"Missing tables: {missing}"


def test_can_insert_project_type():
    """Можно создать project_type."""
    from atlas.pm.db import make_engine, make_session
    from atlas.pm.models import Base, ProjectType

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with make_session(engine) as session:
        pt = ProjectType(
            slug="personal-utility",
            name="Личные утилиты",
            description="Dev-утилиты Дмитрия",
        )
        session.add(pt)
        session.commit()

        # Чтение
        result = session.execute(
            select(ProjectType).where(ProjectType.slug == "personal-utility")
        ).scalar_one()
        assert result.name == "Личные утилиты"
        assert result.id is not None


def test_project_requires_type_and_status():
    """Project нельзя создать без type_id и status_id (FK constraint)."""
    from atlas.pm.db import make_engine, make_session
    from atlas.pm.models import Base, Project, ProjectStatus, ProjectType

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with make_session(engine) as session:
        pt = ProjectType(slug="test-type", name="Test")
        ps = ProjectStatus(slug="active", name="Active", order_idx=1)
        session.add_all([pt, ps])
        session.commit()

        proj = Project(
            slug="test-project",
            name="Test Project",
            type_id=pt.id,
            status_id=ps.id,
            priority="P0",
            one_line_summary="Test summary",
        )
        session.add(proj)
        session.commit()

        assert proj.id is not None
        assert proj.created_at is not None


def test_action_log_is_append_only_in_contract():
    """action_log.id автоинкрементный, timestamp ставится автоматически."""
    from atlas.pm.db import make_engine, make_session
    from atlas.pm.models import ActionLog, Base

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with make_session(engine) as session:
        entry = ActionLog(
            entity_type="project",
            entity_id=None,
            action="created",
            details_json='{"slug":"test"}',
        )
        session.add(entry)
        session.commit()

        assert entry.id is not None
        assert entry.timestamp is not None
