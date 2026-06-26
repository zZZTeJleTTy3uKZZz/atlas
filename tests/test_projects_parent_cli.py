"""Тесты для иерархии проектов (parent_id) в CLI `atlas projects ...`.

Лёгкий контейнер: parent_id как метаданные + CLI + отображение.
НИКАКОЙ физики (junction/git/layout вне scope) — только БД + CLI.

Покрытие:
- add --parent делает модуль (проставляет parent_id);
- add --parent на сам себя / несуществующий → ошибка;
- get показывает Parent у модуля и Modules у контейнера (text + json);
- list --parent фильтрует только модули контейнера; --standalone → parent IS NULL;
- update --parent / --no-parent (взаимоисключающие);
- update с циклом A→B→A → ошибка.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from typer.testing import CliRunner


# --------------------------------------------------------------------------- #
# Fixtures (как в test_pm_projects_cli.py)                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def isolated_projects_root(tmp_path, monkeypatch):
    root = tmp_path / "PROJECT"
    root.mkdir()
    for sub in ("Clients", "Products", "Tests", "_Inbox", "_Archive", "_storage"):
        (root / sub).mkdir(exist_ok=True)
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(root))
    return root


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
    from atlas.commands.projects import projects_app
    return projects_app


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _combined(result) -> str:
    out = result.stdout or ""
    try:
        err = result.stderr or ""
    except (ValueError, AttributeError):
        err = ""
    return out + err


def _add_project(runner, app, *args):
    base = ["add", "--no-setup-layout", "--no-canonical", "--no-sync"]
    return runner.invoke(app, [*base, *args])


def _parent_id_of(session, slug: str):
    from atlas.models import Project

    return session.execute(
        select(Project.parent_id).where(Project.slug == slug)
    ).scalar_one()


# --------------------------------------------------------------------------- #
# add --parent                                                                #
# --------------------------------------------------------------------------- #


class TestAddParent:
    def test_add_with_parent_sets_parent_id(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Project

        _add_project(runner, app, "--name", "Container", "--slug", "cont")
        with make_session(seeded_engine) as session:
            cont_id = session.execute(
                select(Project.id).where(Project.slug == "cont")
            ).scalar_one()

        result = _add_project(
            runner, app, "--name", "Module", "--slug", "mod", "--parent", "cont"
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            assert _parent_id_of(session, "mod") == cont_id

    def test_add_parent_can_resolve_by_uuid(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Project

        _add_project(runner, app, "--name", "Container", "--slug", "cont")
        with make_session(seeded_engine) as session:
            cont_id = session.execute(
                select(Project.id).where(Project.slug == "cont")
            ).scalar_one()

        result = _add_project(
            runner, app, "--name", "Module", "--slug", "mod", "--parent", cont_id
        )
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            assert _parent_id_of(session, "mod") == cont_id

    def test_add_parent_nonexistent_errors(self, runner, app, seeded_engine):
        result = _add_project(
            runner, app, "--name", "Module", "--slug", "mod", "--parent", "ghost"
        )
        assert result.exit_code != 0
        assert "ghost" in _combined(result).lower() or "parent" in _combined(result).lower()

    def test_add_parent_self_errors(self, runner, app, seeded_engine):
        """--parent на собственный (ещё не созданный) slug → ошибка резолва.

        На add цикл невозможен, но --parent на сам себя резолвится в
        несуществующий проект (он ещё не создан) → ошибка.
        """
        result = _add_project(
            runner, app, "--name", "Selfy", "--slug", "selfy", "--parent", "selfy"
        )
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# get: Parent / Modules                                                       #
# --------------------------------------------------------------------------- #


class TestGetHierarchy:
    def _setup(self, runner, app):
        _add_project(runner, app, "--name", "Контейнер", "--slug", "cont")
        _add_project(runner, app, "--name", "Модуль А", "--slug", "moda", "--parent", "cont")
        _add_project(runner, app, "--name", "Модуль Б", "--slug", "modb", "--parent", "cont")

    def test_get_module_shows_parent_text(self, runner, app, seeded_engine):
        self._setup(runner, app)
        result = runner.invoke(app, ["get", "moda"])
        assert result.exit_code == 0, _combined(result)
        out = result.stdout.lower()
        assert "parent" in out
        assert "cont" in out

    def test_get_container_shows_modules_text(self, runner, app, seeded_engine):
        self._setup(runner, app)
        result = runner.invoke(app, ["get", "cont"])
        assert result.exit_code == 0, _combined(result)
        out = result.stdout.lower()
        assert "modules" in out or "модул" in out
        assert "moda" in out
        assert "modb" in out

    def test_get_json_module_has_parent(self, runner, app, seeded_engine):
        self._setup(runner, app)
        result = runner.invoke(app, ["get", "moda"])
        assert result.exit_code == 0, _combined(result)
        data = json.loads(result.stdout)
        assert data["parent"] is not None
        assert data["parent"]["slug"] == "cont"

    def test_get_json_container_has_modules(self, runner, app, seeded_engine):
        self._setup(runner, app)
        result = runner.invoke(app, ["get", "cont"])
        assert result.exit_code == 0, _combined(result)
        data = json.loads(result.stdout)
        slugs = {m["slug"] for m in data["modules"]}
        assert slugs == {"moda", "modb"}
        # тип присутствует в описании модуля
        assert all("type" in m for m in data["modules"])

    def test_get_json_standalone_no_parent_no_modules(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "Одинокий", "--slug", "solo")
        result = runner.invoke(app, ["get", "solo"])
        assert result.exit_code == 0, _combined(result)
        data = json.loads(result.stdout)
        assert data["parent"] is None
        assert data["modules"] == []


# --------------------------------------------------------------------------- #
# list --parent / --standalone                                                #
# --------------------------------------------------------------------------- #


class TestListHierarchy:
    def _setup(self, runner, app):
        _add_project(runner, app, "--name", "Контейнер", "--slug", "cont")
        _add_project(runner, app, "--name", "Модуль А", "--slug", "moda", "--parent", "cont")
        _add_project(runner, app, "--name", "Модуль Б", "--slug", "modb", "--parent", "cont")
        _add_project(runner, app, "--name", "Соло", "--slug", "solo")

    def test_list_parent_filters_modules(self, runner, app, seeded_engine):
        self._setup(runner, app)
        result = runner.invoke(app, ["list", "--parent", "cont"])
        assert result.exit_code == 0, _combined(result)
        out = result.stdout.lower()
        assert "moda" in out
        assert "modb" in out
        assert "solo" not in out

    def test_list_standalone_excludes_modules(self, runner, app, seeded_engine):
        self._setup(runner, app)
        result = runner.invoke(app, ["list", "--standalone"])
        assert result.exit_code == 0, _combined(result)
        out = result.stdout.lower()
        assert "solo" in out
        assert "cont" in out
        assert "moda" not in out
        assert "modb" not in out

    def test_list_parent_combines_with_type(self, runner, app, seeded_engine):
        """--parent совместим с --type."""
        _add_project(runner, app, "--name", "Контейнер", "--slug", "cont")
        _add_project(
            runner, app, "--name", "Модуль клиент", "--slug", "modc",
            "--type", "client-project", "--parent", "cont",
        )
        _add_project(
            runner, app, "--name", "Модуль бизнес", "--slug", "modbz",
            "--type", "business-product", "--parent", "cont",
        )
        result = runner.invoke(
            app, ["list", "--parent", "cont", "--type", "client-project"]
        )
        assert result.exit_code == 0, _combined(result)
        out = result.stdout.lower()
        assert "modc" in out
        assert "modbz" not in out


# --------------------------------------------------------------------------- #
# update --parent / --no-parent                                               #
# --------------------------------------------------------------------------- #


class TestUpdateParent:
    def test_update_set_parent(self, runner, app, seeded_engine):
        from atlas.db import make_session

        _add_project(runner, app, "--name", "Контейнер", "--slug", "cont")
        _add_project(runner, app, "--name", "Модуль", "--slug", "mod")

        result = runner.invoke(app, ["update", "mod", "--parent", "cont"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            assert _parent_id_of(session, "mod") is not None

    def test_update_no_parent_clears(self, runner, app, seeded_engine):
        from atlas.db import make_session

        _add_project(runner, app, "--name", "Контейнер", "--slug", "cont")
        _add_project(runner, app, "--name", "Модуль", "--slug", "mod", "--parent", "cont")

        result = runner.invoke(app, ["update", "mod", "--no-parent"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            assert _parent_id_of(session, "mod") is None

    def test_update_parent_and_no_parent_mutually_exclusive(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "Контейнер", "--slug", "cont")
        _add_project(runner, app, "--name", "Модуль", "--slug", "mod")
        result = runner.invoke(
            app, ["update", "mod", "--parent", "cont", "--no-parent"]
        )
        assert result.exit_code != 0

    def test_update_self_parent_errors(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "Модуль", "--slug", "mod")
        result = runner.invoke(app, ["update", "mod", "--parent", "mod"])
        assert result.exit_code != 0
        assert "сам" in _combined(result).lower() or "self" in _combined(result).lower() \
            or "цикл" in _combined(result).lower()

    def test_update_cycle_errors(self, runner, app, seeded_engine):
        """A→B→A: B имеет parent=A, попытка дать A parent=B → цикл → ошибка."""
        _add_project(runner, app, "--name", "A", "--slug", "aaa")
        _add_project(runner, app, "--name", "B", "--slug", "bbb", "--parent", "aaa")

        result = runner.invoke(app, ["update", "aaa", "--parent", "bbb"])
        assert result.exit_code != 0
        assert "цикл" in _combined(result).lower() or "cycle" in _combined(result).lower()

    def test_update_long_cycle_errors(self, runner, app, seeded_engine):
        """A→B→C→A: длинная цепочка тоже ловится."""
        _add_project(runner, app, "--name", "A", "--slug", "aaa")
        _add_project(runner, app, "--name", "B", "--slug", "bbb", "--parent", "aaa")
        _add_project(runner, app, "--name", "C", "--slug", "ccc", "--parent", "bbb")

        # дать A родителя C → A→C→B→A цикл
        result = runner.invoke(app, ["update", "aaa", "--parent", "ccc"])
        assert result.exit_code != 0
        assert "цикл" in _combined(result).lower() or "cycle" in _combined(result).lower()

    def test_update_parent_logs_action(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ActionLog, Project

        _add_project(runner, app, "--name", "Контейнер", "--slug", "cont")
        _add_project(runner, app, "--name", "Модуль", "--slug", "mod")
        runner.invoke(app, ["update", "mod", "--parent", "cont"])

        with make_session(seeded_engine) as session:
            mod_id = session.execute(
                select(Project.id).where(Project.slug == "mod")
            ).scalar_one()
            logs = session.execute(
                select(ActionLog)
                .where(ActionLog.entity_id == mod_id)
                .where(ActionLog.action == "project_updated")
            ).scalars().all()
        assert logs, "ожидался project_updated в action_log"
        assert any("parent" in (log.details_json or "") for log in logs)
