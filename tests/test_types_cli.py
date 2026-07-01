"""Тесты для CLI `atlas types ...` (PM-слой).

TDD: пишутся ДО реализации src/atlas/pm/commands/types.py.
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
    from atlas.db import make_engine
    from atlas.models import Base

    db_path = tmp_path / "atlas.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def seeded_engine(fresh_engine):
    from atlas.db import make_session
    from atlas.seeds import seed_all

    with make_session(fresh_engine) as session:
        seed_all(session)
    return fresh_engine


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def app():
    from atlas.commands.types import app as types_app
    return types_app


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
    def test_list_default(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        # 5 типов из seed
        assert "client-project" in result.stdout
        assert "business-product" in result.stdout

    def test_list_archived_flag(self, runner, app, seeded_engine):
        """С --archived показывает архивные тоже."""
        from atlas.db import make_session
        from atlas.models import ProjectType

        # архивируем один тип в БД напрямую
        with make_session(seeded_engine) as session:
            t = session.execute(
                select(ProjectType).where(ProjectType.slug == "client-project")
            ).scalar_one()
            t.is_archived = True
            session.commit()

        # default — скрывает архивные
        r1 = runner.invoke(app, ["list"])
        assert r1.exit_code == 0
        assert "client-project" not in r1.stdout

        # --archived — показывает все
        r2 = runner.invoke(app, ["list", "--archived"])
        assert r2.exit_code == 0
        assert "client-project" in r2.stdout


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


class TestAdd:
    def test_add_valid(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ProjectType

        result = runner.invoke(app, [
            "add",
            "--slug", "research-partner",
            "--name", "Research-партнёр",
            "--description", "Партнёрские исследования",
            "--color", "#FF5733",
        ])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            t = session.execute(
                select(ProjectType).where(ProjectType.slug == "research-partner")
            ).scalar_one()
            assert t.name == "Research-партнёр"
            assert t.description == "Партнёрские исследования"
            assert t.color == "#FF5733"

    def test_add_slug_collision(self, runner, app, seeded_engine):
        result = runner.invoke(app, [
            "add", "--slug", "client-project", "--name", "Other",
        ])
        assert result.exit_code != 0
        assert "client-project" in _combined(result).lower()

    def test_add_invalid_slug_format(self, runner, app, seeded_engine):
        result = runner.invoke(app, [
            "add", "--slug", "Invalid Slug!", "--name", "X",
        ])
        assert result.exit_code != 0

    def test_add_creates_action_log(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ActionLog

        result = runner.invoke(app, [
            "add", "--slug", "logged-type", "--name", "Logged",
        ])
        assert result.exit_code == 0

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "project_type_created")
            ).scalar_one()
            assert entry.entity_type == "project_type"
            details = json.loads(entry.details_json)
            assert details["slug"] == "logged-type"

    def test_add_with_group_and_policy(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ProjectType

        result = runner.invoke(app, [
            "add", "--slug", "worker-kit", "--name", "Worker Kit",
            "--group", "products", "--default-sync-policy", "epics",
        ])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            t = session.execute(
                select(ProjectType).where(ProjectType.slug == "worker-kit")
            ).scalar_one()
            assert t.storage_group == "products"
            assert t.default_sync_policy == "epics"

    def test_add_defaults_group_products_policy_local(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ProjectType

        result = runner.invoke(app, [
            "add", "--slug", "bare-type", "--name", "Bare",
        ])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            t = session.execute(
                select(ProjectType).where(ProjectType.slug == "bare-type")
            ).scalar_one()
            assert t.storage_group == "products"
            assert t.default_sync_policy == "local"

    def test_add_invalid_group_rejected(self, runner, app, seeded_engine):
        result = runner.invoke(app, [
            "add", "--slug", "x-type", "--name", "X", "--group", "nonsense",
        ])
        assert result.exit_code != 0

    def test_add_invalid_policy_rejected(self, runner, app, seeded_engine):
        result = runner.invoke(app, [
            "add", "--slug", "y-type", "--name", "Y",
            "--default-sync-policy", "nonexistent-policy",
        ])
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# edit                                                                        #
# --------------------------------------------------------------------------- #


class TestEdit:
    def test_edit_changes_fields(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ProjectType

        result = runner.invoke(app, [
            "edit", "client-project",
            "--name", "Клиентские (ред.)",
            "--group", "products",
            "--default-sync-policy", "epics",
        ])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            t = session.execute(
                select(ProjectType).where(ProjectType.slug == "client-project")
            ).scalar_one()
            assert t.name == "Клиентские (ред.)"
            assert t.storage_group == "products"
            assert t.default_sync_policy == "epics"

    def test_edit_unknown_ref_fails(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["edit", "no-such-type", "--name", "Z"])
        assert result.exit_code != 0

    def test_edit_invalid_policy_rejected(self, runner, app, seeded_engine):
        result = runner.invoke(app, [
            "edit", "client-project", "--default-sync-policy", "bogus",
        ])
        assert result.exit_code != 0
