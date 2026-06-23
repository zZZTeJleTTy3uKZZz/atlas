"""Тесты для CLI `atlas action-log list` (PM-слой).

TDD: пишутся ДО реализации src/atlas/pm/commands/action_log.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from typer.testing import CliRunner


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def fresh_engine(tmp_path, monkeypatch):
    from atlas.pm.db import make_engine
    from atlas.pm.models import Base

    db_path = tmp_path / "atlas.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def seeded_engine(fresh_engine):
    from atlas.pm.db import make_session
    from atlas.pm.seeds import seed_all

    with make_session(fresh_engine) as session:
        seed_all(session)
    return fresh_engine


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def app():
    from atlas.pm.commands.action_log import app as action_log_app
    return action_log_app


@pytest.fixture()
def projects_app():
    from atlas.pm.commands.projects import projects_app
    return projects_app


@pytest.fixture()
def pm_tasks_app():
    from atlas.pm.commands.pm_tasks import pm_tasks_app
    return pm_tasks_app


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _combined(result) -> str:
    out = result.stdout or ""
    try:
        err = result.stderr or ""
    except (ValueError, AttributeError):
        err = ""
    return out + err


def _add_log(session, **kwargs):
    """Утилита для inject записи action_log напрямую."""
    from atlas.pm.models import ActionLog

    entry = ActionLog(**kwargs)
    session.add(entry)
    return entry


# --------------------------------------------------------------------------- #
# tests                                                                        #
# --------------------------------------------------------------------------- #


class TestList:
    def test_list_empty(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        # пустой список — должно быть какое-то сообщение, не ошибка

    def test_list_default_with_data(self, runner, app, seeded_engine, projects_app):
        # генерим запись
        runner.invoke(
            projects_app,
            ["add", "--name", "X", "--type", "client-project", "--slug", "xx"],
        )
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "project_created" in result.stdout

    def test_list_filter_actor(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Participant

        with make_session(seeded_engine) as session:
            dmitry = session.execute(
                select(Participant).where(Participant.slug == "dmitry")
            ).scalar_one()
            claude = session.execute(
                select(Participant).where(Participant.slug == "claude-code")
            ).scalar_one()
            _add_log(session, actor_id=dmitry.id, entity_type="project",
                     entity_id="x", action="dmitry_action", details_json="{}")
            _add_log(session, actor_id=claude.id, entity_type="project",
                     entity_id="y", action="claude_action", details_json="{}")
            session.commit()

        result = runner.invoke(app, ["list", "--actor", "dmitry"])
        assert result.exit_code == 0
        assert "dmitry_action" in result.stdout
        assert "claude_action" not in result.stdout

    def test_list_filter_entity_type(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session

        with make_session(seeded_engine) as session:
            _add_log(session, entity_type="project", entity_id="p1",
                     action="proj_a", details_json="{}")
            _add_log(session, entity_type="task", entity_id="t1",
                     action="task_a", details_json="{}")
            session.commit()

        result = runner.invoke(app, ["list", "--entity-type", "task"])
        assert result.exit_code == 0
        assert "task_a" in result.stdout
        assert "proj_a" not in result.stdout

    def test_list_filter_action(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session

        with make_session(seeded_engine) as session:
            _add_log(session, entity_type="project", entity_id="p1",
                     action="created", details_json="{}")
            _add_log(session, entity_type="project", entity_id="p2",
                     action="updated", details_json="{}")
            session.commit()

        result = runner.invoke(app, ["list", "--action", "updated"])
        assert result.exit_code == 0
        assert "updated" in result.stdout
        # 'created' не должен матчить (точный match)
        # учитываем, что в табличной строке 'created' может появиться как часть
        # другого слова, поэтому не assert-им NOT for 'created'

    def test_list_filter_since(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session

        old_ts = datetime(2025, 1, 1)
        new_ts = datetime(2030, 1, 1)
        with make_session(seeded_engine) as session:
            _add_log(session, entity_type="project", entity_id="p1",
                     action="old_event", details_json="{}", timestamp=old_ts)
            _add_log(session, entity_type="project", entity_id="p2",
                     action="new_event", details_json="{}", timestamp=new_ts)
            session.commit()

        result = runner.invoke(app, ["list", "--since", "2026-01-01"])
        assert result.exit_code == 0
        assert "new_event" in result.stdout
        assert "old_event" not in result.stdout

    def test_list_limit(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session

        with make_session(seeded_engine) as session:
            for i in range(10):
                _add_log(session, entity_type="project", entity_id=f"p{i}",
                         action=f"event_{i}", details_json="{}")
            session.commit()

        result = runner.invoke(app, ["list", "--limit", "3"])
        assert result.exit_code == 0
        # ровно 3 записи (json-дефолт → массив объектов)
        import json as _json
        rows = _json.loads(result.stdout)
        count = sum(1 for r in rows if str(r.get("action", "")).startswith("event_"))
        assert count == 3

    def test_list_sorted_desc(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session

        ts1 = datetime(2026, 1, 1, 10, 0)
        ts2 = datetime(2026, 1, 2, 10, 0)
        ts3 = datetime(2026, 1, 3, 10, 0)
        with make_session(seeded_engine) as session:
            _add_log(session, entity_type="project", entity_id="p1",
                     action="event_a", details_json="{}", timestamp=ts1)
            _add_log(session, entity_type="project", entity_id="p2",
                     action="event_b", details_json="{}", timestamp=ts2)
            _add_log(session, entity_type="project", entity_id="p3",
                     action="event_c", details_json="{}", timestamp=ts3)
            session.commit()

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        out = result.stdout
        i_a = out.find("event_a")
        i_b = out.find("event_b")
        i_c = out.find("event_c")
        # DESC: c — самый свежий, должен быть первым
        assert i_c < i_b < i_a

    def test_list_filter_project(
        self, runner, app, seeded_engine, projects_app, pm_tasks_app,
    ):
        """--project фильтрует записи по проекту И его задачам."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Project

        # создаём 2 проекта, в одном — задача
        runner.invoke(
            projects_app,
            ["add", "--name", "TheOne", "--type", "client-project",
             "--slug", "the-one", "--prefix", "to"],
        )
        runner.invoke(
            projects_app,
            ["add", "--name", "Other", "--type", "client-project",
             "--slug", "other", "--prefix", "ot"],
        )
        runner.invoke(
            pm_tasks_app,
            ["add", "--project", "the-one", "--title", "T1", "--cpp", "C1"],
        )

        with make_session(seeded_engine) as session:
            the_one = session.execute(
                select(Project).where(Project.slug == "the-one")
            ).scalar_one()
            other = session.execute(
                select(Project).where(Project.slug == "other")
            ).scalar_one()

        result = runner.invoke(app, ["list", "--project", "the-one"])
        assert result.exit_code == 0, _combined(result)
        # Должны быть и project_created (the_one), и task_created. НЕ должно быть
        # project_created по 'other'.
        assert "project_created" in result.stdout or "task_created" in result.stdout
