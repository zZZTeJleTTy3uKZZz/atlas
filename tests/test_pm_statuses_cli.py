"""Тесты для CLI `atlas statuses ...` (PM-слой).

TDD: пишутся ДО реализации src/atlas/pm/commands/statuses.py.
"""
from __future__ import annotations

import json

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
    from atlas.pm.commands.statuses import app as statuses_app
    return statuses_app


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


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


class TestList:
    def test_list_default_sorted_by_order(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        # 6 статусов, ожидаем порядок: experiment(1), active(2), maintained(3),
        # dormant(4), graduating(5), archived(6).
        out = result.stdout
        i_exp = out.find("experiment")
        i_act = out.find("active")
        i_arc = out.find("archived")
        assert i_exp != -1 and i_act != -1 and i_arc != -1
        assert i_exp < i_act < i_arc

    def test_list_empty(self, runner, app, fresh_engine):
        """Без seed — пустой список."""
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


class TestAdd:
    def test_add_valid(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import ProjectStatus

        result = runner.invoke(app, [
            "add",
            "--slug", "in-progress",
            "--name", "В работе",
            "--order-idx", "10",
            "--description", "Активная работа",
        ])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            s = session.execute(
                select(ProjectStatus).where(ProjectStatus.slug == "in-progress")
            ).scalar_one()
            assert s.name == "В работе"
            assert s.order_idx == 10

    def test_add_slug_collision(self, runner, app, seeded_engine):
        """Нельзя добавить статус с уже существующим slug."""
        result = runner.invoke(app, [
            "add", "--slug", "active", "--name", "Other", "--order-idx", "100",
        ])
        assert result.exit_code != 0

    def test_add_creates_action_log(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        result = runner.invoke(app, [
            "add", "--slug", "logged-st", "--name", "Logged", "--order-idx", "99",
        ])
        assert result.exit_code == 0

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "project_status_created")
            ).scalar_one()
            assert entry.entity_type == "project_status"
            details = json.loads(entry.details_json)
            assert details["slug"] == "logged-st"
            assert details["order_idx"] == 99
