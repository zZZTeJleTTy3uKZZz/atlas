"""Тесты для CLI `atlas tags ...` (PM-слой, NP-005).

TDD: эти тесты пишутся ДО реализации src/atlas/pm/commands/tags.py.
Покрытие: add / list / get / update / delete + edge cases + action_log.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def fresh_engine(tmp_path, monkeypatch):
    """Чистая SQLite БД на диске + ATLAS_DB_URL в env."""
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
    """Чистая БД + полный seed (project_types, project_statuses, participants)."""
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
    """CLI-приложение tags."""
    from atlas.pm.commands.tags import app as tags_app
    return tags_app


@pytest.fixture()
def projects_app():
    from atlas.pm.commands.projects import projects_app
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


def _add(runner, app, *args):
    return runner.invoke(app, ["add", *args])


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


class TestAdd:
    def test_add_minimal(self, runner, app, seeded_engine):
        """name + category → slug auto."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Tag

        result = _add(
            runner, app,
            "--name", "Bitrix24",
            "--category", "stack",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            t = session.execute(
                select(Tag).where(Tag.name == "Bitrix24")
            ).scalar_one()
            assert t.category == "stack"
            assert t.slug  # auto
            # ожидаем slug из slugify_text("Bitrix24") = 'bitrix24'
            assert t.slug == "bitrix24"

    def test_add_explicit_slug(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Tag

        result = _add(
            runner, app,
            "--name", "Bitrix24",
            "--category", "stack",
            "--slug", "b24",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            t = session.execute(
                select(Tag).where(Tag.slug == "b24")
            ).scalar_one()
            assert t.name == "Bitrix24"
            assert t.category == "stack"

    def test_add_slug_collision_explicit(self, runner, app, seeded_engine):
        """Если --slug явный и занят → error, без auto-suffix."""
        r1 = _add(
            runner, app,
            "--name", "First", "--category", "stack", "--slug", "b24",
        )
        assert r1.exit_code == 0, _combined(r1)

        r2 = _add(
            runner, app,
            "--name", "Second", "--category", "stack", "--slug", "b24",
        )
        assert r2.exit_code != 0
        combined = _combined(r2).lower()
        assert "b24" in combined

    def test_add_slug_collision_auto(self, runner, app, seeded_engine):
        """Если slug автогенерируется и занят → suffix -2."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Tag

        r1 = _add(
            runner, app,
            "--name", "Bitrix24", "--category", "stack", "--slug", "bitrix24",
        )
        assert r1.exit_code == 0, _combined(r1)

        # Второй: name="Bitrix24" → slug auto = 'bitrix24', занят → 'bitrix24-2'
        r2 = _add(
            runner, app,
            "--name", "Bitrix24", "--category", "other",
        )
        assert r2.exit_code == 0, _combined(r2)

        with make_session(seeded_engine) as session:
            t = session.execute(
                select(Tag).where(Tag.slug == "bitrix24-2")
            ).scalar_one()
            assert t.category == "other"

    def test_add_invalid_category(self, runner, app, seeded_engine):
        result = _add(
            runner, app,
            "--name", "X", "--category", "robot",  # invalid
        )
        assert result.exit_code != 0
        combined = _combined(result).lower()
        assert "categor" in combined or "robot" in combined

    def test_add_with_color_and_description(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Tag

        result = _add(
            runner, app,
            "--name", "Bitrix24",
            "--category", "stack",
            "--slug", "b24",
            "--color", "#00ACED",
            "--description", "CRM on Bitrix24",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            t = session.execute(
                select(Tag).where(Tag.slug == "b24")
            ).scalar_one()
            assert t.color == "#00ACED"
            assert t.description == "CRM on Bitrix24"

    def test_add_creates_action_log(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        result = _add(
            runner, app,
            "--name", "Bitrix24", "--category", "stack", "--slug", "b24",
        )
        assert result.exit_code == 0

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "tag_created")
            ).scalar_one()
            assert entry.entity_type == "tag"
            details = json.loads(entry.details_json)
            assert details["slug"] == "b24"
            assert details["name"] == "Bitrix24"
            assert details["category"] == "stack"


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


class TestList:
    def test_list_empty(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        # Не падаем, может вывести "empty" / "no tags"
        assert result.exit_code == 0

    def test_list_all(self, runner, app, seeded_engine):
        _add(runner, app, "--name", "Bitrix24", "--category", "stack", "--slug", "b24")
        _add(runner, app, "--name", "Notion", "--category", "stack", "--slug", "notion")
        _add(runner, app, "--name", "Dmitry", "--category", "owner", "--slug", "dmitry-tag")

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        out = result.stdout.lower()
        assert "b24" in out
        assert "notion" in out
        assert "dmitry-tag" in out

    def test_list_filter_category(self, runner, app, seeded_engine):
        _add(runner, app, "--name", "Bitrix24", "--category", "stack", "--slug", "b24")
        _add(runner, app, "--name", "Dmitry", "--category", "owner", "--slug", "dmitry-tag")

        result = runner.invoke(app, ["list", "--category", "stack"])
        assert result.exit_code == 0, _combined(result)
        out = result.stdout.lower()
        assert "b24" in out
        assert "dmitry-tag" not in out

    def test_list_shows_project_count(self, runner, app, seeded_engine, projects_app):
        """Колонка Projects в list показывает COUNT от project_tags."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Project, ProjectTag, Tag

        _add(runner, app, "--name", "Bitrix24", "--category", "stack", "--slug", "b24")
        runner.invoke(
            projects_app,
            ["add", "--name", "A", "--type", "client-project", "--slug", "a-cifro",
             "--prefix", "acf"],
        )
        runner.invoke(
            projects_app,
            ["add", "--name", "B", "--type", "client-project", "--slug", "b-cifro",
             "--prefix", "bcf"],
        )
        with make_session(seeded_engine) as session:
            tag = session.execute(
                select(Tag).where(Tag.slug == "b24")
            ).scalar_one()
            for slug in ("a-cifro", "b-cifro"):
                p = session.execute(
                    select(Project).where(Project.slug == slug)
                ).scalar_one()
                session.add(ProjectTag(project_id=p.id, tag_id=tag.id))
            session.commit()

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0, _combined(result)
        # счётчик 2 где-то есть в таблице
        assert "2" in result.stdout


# --------------------------------------------------------------------------- #
# get                                                                         #
# --------------------------------------------------------------------------- #


class TestGet:
    def test_get_by_category_slug(self, runner, app, seeded_engine):
        _add(runner, app, "--name", "Dmitry", "--category", "owner", "--slug", "dmitry-tag")

        result = runner.invoke(app, ["get", "owner:dmitry-tag"])
        assert result.exit_code == 0, _combined(result)
        assert "dmitry-tag" in result.stdout.lower()

    def test_get_by_bare_slug(self, runner, app, seeded_engine):
        _add(runner, app, "--name", "Dmitry", "--category", "owner", "--slug", "dmitry-tag")

        result = runner.invoke(app, ["get", "dmitry-tag"])
        assert result.exit_code == 0, _combined(result)
        assert "dmitry-tag" in result.stdout.lower()

    def test_get_by_uuid_full(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Tag

        _add(runner, app, "--name", "Dmitry", "--category", "owner", "--slug", "dmitry-tag")
        with make_session(seeded_engine) as session:
            t = session.execute(
                select(Tag).where(Tag.slug == "dmitry-tag")
            ).scalar_one()
            full_uuid = t.id

        result = runner.invoke(app, ["get", full_uuid])
        assert result.exit_code == 0, _combined(result)
        assert "dmitry-tag" in result.stdout.lower()

    def test_get_by_uuid_short(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Tag

        _add(runner, app, "--name", "Dmitry", "--category", "owner", "--slug", "dmitry-tag")
        with make_session(seeded_engine) as session:
            t = session.execute(
                select(Tag).where(Tag.slug == "dmitry-tag")
            ).scalar_one()
            short = t.id[:8]

        result = runner.invoke(app, ["get", short])
        assert result.exit_code == 0, _combined(result)
        assert "dmitry-tag" in result.stdout.lower()

    def test_get_not_found(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["get", "no-such-tag"])
        assert result.exit_code != 0

    def test_get_shows_projects_list(self, runner, app, seeded_engine, projects_app):
        """get отображает список проектов, которым прикреплён этот тег."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Project, ProjectTag, Tag

        _add(runner, app, "--name", "Bitrix24", "--category", "stack", "--slug", "b24")
        runner.invoke(
            projects_app,
            ["add", "--name", "Cifro", "--type", "client-project", "--slug", "cifro"],
        )
        with make_session(seeded_engine) as session:
            tag = session.execute(
                select(Tag).where(Tag.slug == "b24")
            ).scalar_one()
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            session.add(ProjectTag(project_id=proj.id, tag_id=tag.id))
            session.commit()

        result = runner.invoke(app, ["get", "b24"])
        assert result.exit_code == 0, _combined(result)
        assert "cifro" in result.stdout.lower()


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #


class TestUpdate:
    def test_update_single_field(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Tag

        _add(runner, app, "--name", "Old", "--category", "stack", "--slug", "upd1")
        result = runner.invoke(app, ["update", "upd1", "--name", "New"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            t = session.execute(
                select(Tag).where(Tag.slug == "upd1")
            ).scalar_one()
            assert t.name == "New"

    def test_update_category(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Tag

        _add(runner, app, "--name", "X", "--category", "stack", "--slug", "upd2")
        result = runner.invoke(app, ["update", "upd2", "--category", "domain"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            t = session.execute(
                select(Tag).where(Tag.slug == "upd2")
            ).scalar_one()
            assert t.category == "domain"

    def test_update_slug_forbidden(self, runner, app, seeded_engine):
        _add(runner, app, "--name", "X", "--category", "stack", "--slug", "upd3")
        result = runner.invoke(app, ["update", "upd3", "--slug", "new-slug"])
        assert result.exit_code != 0
        assert "slug" in _combined(result).lower()

    def test_update_nonexistent(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["update", "nope-xx", "--name", "X"])
        assert result.exit_code != 0

    def test_update_creates_action_log_with_diff(self, runner, app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        _add(runner, app, "--name", "Old", "--category", "stack", "--slug", "upd4")
        runner.invoke(app, ["update", "upd4", "--name", "New"])

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "tag_updated")
            ).scalar_one()
            details = json.loads(entry.details_json)
            assert "name" in details
            assert details["name"]["old"] == "Old"
            assert details["name"]["new"] == "New"


# --------------------------------------------------------------------------- #
# delete                                                                      #
# --------------------------------------------------------------------------- #


class TestDelete:
    def test_delete_unused(self, runner, app, seeded_engine):
        """Тег не прикреплён ни к одному проекту → hard delete без подтверждения."""
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog, Tag

        _add(runner, app, "--name", "X", "--category", "stack", "--slug", "del1")
        result = runner.invoke(app, ["delete", "del1"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            t = session.execute(
                select(Tag).where(Tag.slug == "del1")
            ).scalar_one_or_none()
            assert t is None
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "tag_deleted")
            ).scalar_one()
            assert entry is not None

    def test_delete_attached_without_force(self, runner, app, seeded_engine, projects_app):
        """Тег прикреплён к проекту → без --force → error."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Project, ProjectTag, Tag

        _add(runner, app, "--name", "B24", "--category", "stack", "--slug", "del-b24")
        runner.invoke(
            projects_app,
            ["add", "--name", "A", "--type", "client-project", "--slug", "pa"],
        )
        with make_session(seeded_engine) as session:
            t = session.execute(select(Tag).where(Tag.slug == "del-b24")).scalar_one()
            p = session.execute(select(Project).where(Project.slug == "pa")).scalar_one()
            session.add(ProjectTag(project_id=p.id, tag_id=t.id))
            session.commit()

        result = runner.invoke(app, ["delete", "del-b24"])
        assert result.exit_code != 0
        combined = _combined(result).lower()
        assert "force" in combined or "attached" in combined

    def test_delete_attached_with_force(self, runner, app, seeded_engine, projects_app):
        """--force каскадно удаляет project_tags + удаляет тег + action_log."""
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog, Project, ProjectTag, Tag

        _add(runner, app, "--name", "B24", "--category", "stack", "--slug", "del-b24")
        runner.invoke(
            projects_app,
            ["add", "--name", "A", "--type", "client-project", "--slug", "pa",
             "--prefix", "pa"],
        )
        runner.invoke(
            projects_app,
            ["add", "--name", "B", "--type", "client-project", "--slug", "pb",
             "--prefix", "pb"],
        )
        with make_session(seeded_engine) as session:
            t = session.execute(select(Tag).where(Tag.slug == "del-b24")).scalar_one()
            for slug in ("pa", "pb"):
                p = session.execute(select(Project).where(Project.slug == slug)).scalar_one()
                session.add(ProjectTag(project_id=p.id, tag_id=t.id))
            session.commit()

        result = runner.invoke(app, ["delete", "del-b24", "--force"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            t2 = session.execute(
                select(Tag).where(Tag.slug == "del-b24")
            ).scalar_one_or_none()
            assert t2 is None
            # все project_tags удалены
            remaining = session.execute(
                select(func.count()).select_from(ProjectTag)
            ).scalar()
            assert remaining == 0
            # action_log с detached_projects_count
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "tag_deleted")
            ).scalar_one()
            details = json.loads(entry.details_json)
            assert details.get("detached_projects_count") == 2

    def test_delete_nonexistent(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["delete", "nope-xxx"])
        assert result.exit_code != 0
