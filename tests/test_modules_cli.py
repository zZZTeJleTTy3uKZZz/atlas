"""CLI-тесты физики контейнеров-модулей (#163 add --parent + #127 gitignore).

#163: `atlas project add --parent <container>` с --setup-layout БЕЗ ручной
работы создаёт `_storage/<module_slug>/` + junction
`<container_logical>/modules/<module_slug>/` → `_storage/<module_slug>/`
(а не в type-группе Products/...).

#127: у контейнера (проекта с модулями) в `.gitignore` идемпотентно добавляется
`modules/`; каждый модуль — отдельный git-репо в своём `_storage/<module>/`.

Все junction/robocopy/git мокаются — реальную ФС вне tmp_path не трогаем.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from typer.testing import CliRunner


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def projects_root(tmp_path, monkeypatch):
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
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _combined(result) -> str:
    out = result.stdout or ""
    try:
        err = result.stderr or ""
    except (ValueError, AttributeError):
        err = ""
    return out + err


def _fake_create_junction(link, target):
    """Подмена create_junction: создаёт пустую папку как «junction»."""
    link = Path(link)
    target = Path(target)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.mkdir(exist_ok=True)


# --------------------------------------------------------------------------- #
# #163 — add --parent физика: junction в container/modules/<slug>            #
# --------------------------------------------------------------------------- #


class TestAddParentPhysics:
    def _add_container(self, runner, app, slug="cont", type_slug="business-product"):
        result = runner.invoke(app, [
            "add", "--name", "Container", "--slug", slug,
            "--type", type_slug, "--no-setup-layout", "--no-canonical", "--no-sync",
        ])
        assert result.exit_code == 0, _combined(result)

    def test_add_module_creates_storage_and_junction_under_container_modules(
        self, runner, app, seeded_engine, projects_root
    ):
        self._add_container(runner, app)
        # Контейнерная логическая папка должна существовать (junction-родитель).
        container_logical = projects_root / "Products" / "cont"
        container_logical.mkdir(parents=True, exist_ok=True)

        from atlas.commands import projects as projects_mod

        with patch.object(
            projects_mod, "create_junction", side_effect=_fake_create_junction
        ):
            result = runner.invoke(app, [
                "add", "--name", "Module", "--slug", "mod",
                "--type", "business-product", "--parent", "cont",
                "--setup-layout", "--no-canonical", "--no-sync",
            ])
        assert result.exit_code == 0, _combined(result)

        storage = projects_root / "_storage" / "mod"
        assert storage.exists(), "storage модуля должен быть создан"

        junction = container_logical / "modules" / "mod"
        assert junction.exists(), f"junction в container/modules/ нет: {junction}"

        # Junction НЕ должен лежать в type-группе Products/mod.
        assert not (projects_root / "Products" / "mod").exists()

    def test_module_local_path_points_to_container_modules(
        self, runner, app, seeded_engine, projects_root
    ):
        self._add_container(runner, app)
        container_logical = projects_root / "Products" / "cont"
        container_logical.mkdir(parents=True, exist_ok=True)

        from atlas.commands import projects as projects_mod
        from atlas.db import make_session
        from atlas.models import Project

        with patch.object(
            projects_mod, "create_junction", side_effect=_fake_create_junction
        ):
            result = runner.invoke(app, [
                "add", "--name", "Module", "--slug", "mod",
                "--type", "business-product", "--parent", "cont",
                "--setup-layout", "--no-canonical", "--no-sync",
            ])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            mod = session.execute(
                select(Project).where(Project.slug == "mod")
            ).scalar_one()
        assert mod.local_path is not None
        expected = container_logical / "modules" / "mod"
        assert Path(mod.local_path) == expected

    def test_standalone_without_parent_keeps_type_group(
        self, runner, app, seeded_engine, projects_root
    ):
        """Без --parent — прежнее поведение (junction в type-группе)."""
        from atlas.commands import projects as projects_mod

        with patch.object(
            projects_mod, "create_junction", side_effect=_fake_create_junction
        ):
            result = runner.invoke(app, [
                "add", "--name", "Solo", "--slug", "solo",
                "--type", "business-product",
                "--setup-layout", "--no-canonical", "--no-sync",
            ])
        assert result.exit_code == 0, _combined(result)
        # junction в Products/solo, не в каком-либо modules/.
        assert (projects_root / "Products" / "solo").exists()
        assert (projects_root / "_storage" / "solo").exists()


# --------------------------------------------------------------------------- #
# #127 — контейнер .gitignore содержит modules/ (идемпотентно)               #
# --------------------------------------------------------------------------- #


class TestContainerGitignore:
    def test_ensure_gitignore_modules_adds_entry(self, tmp_path):
        from atlas.commands.projects import _ensure_gitignore_modules

        d = tmp_path / "cont"
        d.mkdir()
        changed = _ensure_gitignore_modules(d)
        assert changed is True
        text = (d / ".gitignore").read_text(encoding="utf-8")
        assert "modules/" in text

    def test_ensure_gitignore_modules_idempotent(self, tmp_path):
        from atlas.commands.projects import _ensure_gitignore_modules

        d = tmp_path / "cont"
        d.mkdir()
        _ensure_gitignore_modules(d)
        # Второй вызов — ничего не меняет, modules/ не дублируется.
        changed2 = _ensure_gitignore_modules(d)
        assert changed2 is False
        text = (d / ".gitignore").read_text(encoding="utf-8")
        assert text.count("modules/") == 1

    def test_ensure_gitignore_appends_to_existing(self, tmp_path):
        from atlas.commands.projects import _ensure_gitignore_modules

        d = tmp_path / "cont"
        d.mkdir()
        (d / ".gitignore").write_text("*.log\n.venv/\n", encoding="utf-8")
        changed = _ensure_gitignore_modules(d)
        assert changed is True
        text = (d / ".gitignore").read_text(encoding="utf-8")
        assert "*.log" in text
        assert ".venv/" in text
        assert "modules/" in text
