"""CLI-тесты для `atlas ideas ...` (W45-38)."""
from __future__ import annotations

from pathlib import Path

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
    (root / "Clients").mkdir()
    (root / "Products").mkdir()
    (root / "Tests").mkdir()
    (root / "_Inbox").mkdir()
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(root))
    return root


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
def ideas_app(seeded_engine, projects_root):
    from atlas.pm.commands.ideas import ideas_app

    return ideas_app


def _combined(result) -> str:
    out = result.stdout or ""
    try:
        err = result.stderr or ""
    except (ValueError, AttributeError):
        err = ""
    return out + err


def _get_project(engine, slug):
    from atlas.pm.db import make_session
    from atlas.pm.models import Project

    with make_session(engine) as session:
        return session.execute(
            select(Project).where(Project.slug == slug)
        ).scalar_one()


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


class TestIdeasAdd:
    def test_add_creates_db_record_and_md_file(
        self, runner, ideas_app, seeded_engine, projects_root
    ):
        result = runner.invoke(
            ideas_app,
            [
                "add", "--name", "My idea", "--slug", "my-idea",
                "--type", "business-product", "--priority", "P1",
                "--one-line", "Solving X for Y",
            ],
        )
        assert result.exit_code == 0, _combined(result)
        # БД
        proj = _get_project(seeded_engine, "my-idea")
        assert proj.entity_kind == "idea"
        assert proj.priority == "P1"
        # MD-файл
        md = projects_root / "_Ideas" / "my-idea.md"
        assert md.exists()
        assert "My idea" in md.read_text(encoding="utf-8")
        assert "Solving X for Y" in md.read_text(encoding="utf-8")

    def test_add_creates_ideas_root_with_readme_and_backlog(
        self, runner, ideas_app, seeded_engine, projects_root
    ):
        runner.invoke(
            ideas_app, ["add", "--name", "X", "--slug", "x",
                        "--type", "business-product"],
        )
        ideas = projects_root / "_Ideas"
        assert (ideas / "README.md").exists()
        assert (ideas / "BACKLOG.md").exists()

    def test_add_rejects_duplicate_slug(
        self, runner, ideas_app, seeded_engine, projects_root
    ):
        runner.invoke(
            ideas_app, ["add", "--name", "X", "--slug", "dup",
                        "--type", "business-product"],
        )
        result = runner.invoke(
            ideas_app, ["add", "--name", "X2", "--slug", "dup",
                        "--type", "business-product"],
        )
        assert result.exit_code != 0
        assert "занят" in _combined(result).lower() or "dup" in _combined(result)

    def test_add_invalid_type_errors(
        self, runner, ideas_app, seeded_engine, projects_root
    ):
        result = runner.invoke(
            ideas_app, ["add", "--name", "X", "--slug", "x",
                        "--type", "nonexistent"],
        )
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


class TestIdeasList:
    def test_list_shows_only_idea_kind(
        self, runner, ideas_app, seeded_engine, projects_root
    ):
        # Создать идею через ideas, и project через projects.add.
        runner.invoke(
            ideas_app,
            ["add", "--name", "Idea1", "--slug", "i1",
             "--type", "business-product"],
        )
        # А просто Project (не idea) — через atlas projects.
        from atlas.pm.commands.projects import projects_app

        runner.invoke(
            projects_app,
            [
                "add", "--name", "P1", "--slug", "p1",
                "--type", "business-product",
                "--no-setup-layout", "--no-canonical",
            ],
        )

        result = runner.invoke(ideas_app, ["list"])
        assert result.exit_code == 0
        out = _combined(result)
        assert "i1" in out
        assert "p1" not in out  # project НЕ должен попасть в `ideas list`


# --------------------------------------------------------------------------- #
# show                                                                        #
# --------------------------------------------------------------------------- #


class TestIdeasShow:
    def test_show_idea_card_and_md_content(
        self, runner, ideas_app, seeded_engine, projects_root
    ):
        runner.invoke(
            ideas_app,
            ["add", "--name", "ShowMe", "--slug", "show-me",
             "--type", "business-product",
             "--one-line", "what it does"],
        )
        result = runner.invoke(ideas_app, ["show", "show-me"])
        assert result.exit_code == 0, _combined(result)
        out = _combined(result)
        assert "ShowMe" in out
        assert "show-me" in out
        # содержимое MD
        assert "what it does" in out


# --------------------------------------------------------------------------- #
# promote                                                                     #
# --------------------------------------------------------------------------- #


class TestIdeasPromote:
    def test_promote_moves_md_extracts_backlog_creates_storage(
        self, runner, ideas_app, seeded_engine, projects_root
    ):
        runner.invoke(
            ideas_app,
            ["add", "--name", "P", "--slug", "promotee",
             "--type", "business-product"],
        )
        # Добавим секцию backlog для этой идеи.
        backlog = projects_root / "_Ideas" / "BACKLOG.md"
        backlog_text = backlog.read_text(encoding="utf-8")
        backlog.write_text(
            backlog_text + "\n### #promotee\n- [ ] **P0** task A\n",
            encoding="utf-8",
        )

        result = runner.invoke(
            ideas_app,
            ["promote", "promotee", "--no-canonical", "--no-init-git"],
        )
        assert result.exit_code == 0, _combined(result)

        # БД: entity_kind стал project.
        proj = _get_project(seeded_engine, "promotee")
        assert proj.entity_kind == "project"

        # _storage создан + IDEA.md внутри.
        storage = projects_root / "_storage" / "promotee"
        assert storage.exists()
        assert (storage / "IDEA.md").exists()

        # _Ideas/promotee.md удалён.
        assert not (projects_root / "_Ideas" / "promotee.md").exists()

        # _storage/promotee/BACKLOG.md содержит вытащенную секцию.
        assert (storage / "BACKLOG.md").exists()
        assert "task A" in (storage / "BACKLOG.md").read_text(encoding="utf-8")

        # Из _Ideas/BACKLOG.md секция удалена.
        new_backlog = backlog.read_text(encoding="utf-8")
        assert "### #promotee" not in new_backlog


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #


class TestIdeasUpdate:
    def test_update_priority_and_status(
        self, runner, ideas_app, seeded_engine, projects_root
    ):
        runner.invoke(
            ideas_app,
            ["add", "--name", "U", "--slug", "u",
             "--type", "business-product", "--priority", "P3"],
        )
        result = runner.invoke(
            ideas_app,
            ["update", "u", "--priority", "P1", "--status", "cancelled"],
        )
        assert result.exit_code == 0, _combined(result)
        proj = _get_project(seeded_engine, "u")
        assert proj.priority == "P1"
        # status сменился.
        from atlas.pm.db import make_session
        from atlas.pm.models import ProjectStatus

        with make_session(seeded_engine) as s:
            ps = s.get(ProjectStatus, proj.status_id)
            assert ps.slug == "cancelled"
