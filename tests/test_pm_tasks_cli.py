"""Тесты для CLI `atlas pm-tasks ...` (PM-слой, локальная БД).

TDD: сначала RED — тесты пишутся ДО реализации
src/atlas/pm/commands/pm_tasks.py.

Покрытие: add / list / get / update / delete + slug/number-генерация,
status transitions (started_at/completed_at), action_log, edge cases.
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
    """Чистая SQLite БД на диске + ATLAS_DB_URL в env, чтобы CLI её увидел."""
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
    """Typer CliRunner."""
    return CliRunner()


@pytest.fixture()
def projects_app():
    from atlas.pm.commands.projects import projects_app
    return projects_app


@pytest.fixture()
def app():
    """CLI-приложение pm-tasks."""
    from atlas.pm.commands.pm_tasks import pm_tasks_app
    return pm_tasks_app


@pytest.fixture()
def project_cifro(runner, projects_app, seeded_engine):
    """Создать проект 'cifro' с prefix 'cf' для тестов задач."""
    result = runner.invoke(
        projects_app,
        [
            "add", "--name", "Cifro", "--type", "client-project",
            "--slug", "cifro", "--prefix", "cf",
        ],
    )
    assert result.exit_code == 0, _combined(result)
    return "cifro"


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


def _add_task(runner, app, *args):
    return runner.invoke(app, ["add", *args])


# --------------------------------------------------------------------------- #
# add                                                                          #
# --------------------------------------------------------------------------- #


class TestAdd:
    def test_add_minimal(self, runner, app, project_cifro, seeded_engine):
        """project/title/cpp → slug и number генерятся."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Task

        result = _add_task(
            runner, app,
            "--project", "cifro",
            "--title", "Fix login",
            "--cpp", "Пользователь может войти",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            task = session.execute(
                select(Task).where(Task.title == "Fix login")
            ).scalar_one()
            assert task.slug is not None
            assert task.slug.startswith("cf-")
            assert task.number is not None
            assert task.number == 1
            assert task.cpp_description == "Пользователь может войти"
            assert task.status == "backlog"
            assert task.priority == "P2"

    def test_add_explicit_slug(self, runner, app, project_cifro, seeded_engine):
        """--slug fix-login + prefix cf → cf-fix-login."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Task

        result = _add_task(
            runner, app,
            "--project", "cifro",
            "--title", "Fix login",
            "--cpp", "ЦКП",
            "--slug", "fix-login",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            task = session.execute(
                select(Task).where(Task.slug == "cf-fix-login")
            ).scalar_one()
            assert task.title == "Fix login"

    def test_add_slug_collision_explicit(self, runner, app, project_cifro, seeded_engine):
        """Если --slug задан и занят → error без auto-suffix."""
        r1 = _add_task(
            runner, app,
            "--project", "cifro", "--title", "First", "--cpp", "x",
            "--slug", "fix-login",
        )
        assert r1.exit_code == 0, _combined(r1)

        r2 = _add_task(
            runner, app,
            "--project", "cifro", "--title", "Second", "--cpp", "x",
            "--slug", "fix-login",
        )
        assert r2.exit_code != 0
        assert "fix-login" in _combined(r2).lower() or "cf-fix-login" in _combined(r2).lower()

    def test_add_slug_collision_auto(self, runner, app, project_cifro, seeded_engine):
        """Auto-slug занят → suffix -2."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Task

        r1 = _add_task(
            runner, app,
            "--project", "cifro", "--title", "Fix login", "--cpp", "x",
            "--slug", "fix-login",
        )
        assert r1.exit_code == 0

        # Второй: title тот же → slugify даст 'fix-login' → cf-fix-login занят
        r2 = _add_task(
            runner, app,
            "--project", "cifro", "--title", "Fix login", "--cpp", "x",
        )
        assert r2.exit_code == 0, _combined(r2)

        with make_session(seeded_engine) as session:
            task = session.execute(
                select(Task).where(Task.slug == "cf-fix-login-2")
            ).scalar_one()
            assert task.title == "Fix login"

    def test_add_number_autoincrement(self, runner, app, project_cifro, seeded_engine):
        """Три задачи подряд → номера 1, 2, 3."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Task

        for i, t in enumerate(["A", "B", "C"], start=1):
            r = _add_task(
                runner, app,
                "--project", "cifro", "--title", t, "--cpp", "x",
            )
            assert r.exit_code == 0, _combined(r)

        with make_session(seeded_engine) as session:
            numbers = sorted(
                session.execute(select(Task.number)).scalars().all()
            )
            assert numbers == [1, 2, 3]

    def test_add_nonexistent_project(self, runner, app, seeded_engine):
        result = _add_task(
            runner, app,
            "--project", "nope", "--title", "X", "--cpp", "x",
        )
        assert result.exit_code != 0

    def test_add_cpp_required(self, runner, app, project_cifro, seeded_engine):
        """Без --cpp → error (NOT NULL constraint в модели)."""
        result = runner.invoke(app, [
            "add", "--project", "cifro", "--title", "X",
        ])
        assert result.exit_code != 0

    def test_add_with_assignee(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Participant, Task

        result = _add_task(
            runner, app,
            "--project", "cifro", "--title", "X", "--cpp", "y",
            "--assignee", "dmitry",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            task = session.execute(
                select(Task).where(Task.title == "X")
            ).scalar_one()
            dmitry = session.execute(
                select(Participant).where(Participant.slug == "dmitry")
            ).scalar_one()
            assert task.assignee_id == dmitry.id

    def test_add_invalid_priority(self, runner, app, project_cifro, seeded_engine):
        result = _add_task(
            runner, app,
            "--project", "cifro", "--title", "X", "--cpp", "x",
            "--priority", "P9",
        )
        assert result.exit_code != 0

    def test_add_invalid_status(self, runner, app, project_cifro, seeded_engine):
        result = _add_task(
            runner, app,
            "--project", "cifro", "--title", "X", "--cpp", "x",
            "--status", "garbage",
        )
        assert result.exit_code != 0

    def test_add_creates_action_log(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        result = _add_task(
            runner, app,
            "--project", "cifro", "--title", "Logged", "--cpp", "x",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "task_created")
            ).scalar_one()
            assert entry.entity_type == "task"
            assert entry.entity_id is not None
            assert entry.actor_id is not None
            details = json.loads(entry.details_json)
            assert details["title"] == "Logged"
            assert details["slug"].startswith("cf-")
            assert details["number"] == 1


# --------------------------------------------------------------------------- #
# list                                                                         #
# --------------------------------------------------------------------------- #


class TestList:
    def test_list_empty(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0

    def test_list_all(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Alpha", "--cpp", "x")
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Bravo", "--cpp", "x")

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "alpha" in result.stdout.lower()
        assert "bravo" in result.stdout.lower()

    def test_list_filter_project(self, runner, app, projects_app, seeded_engine):
        # 2 проекта, по одной задаче в каждом
        runner.invoke(projects_app, [
            "add", "--name", "Cifro", "--type", "client-project",
            "--slug", "cifro", "--prefix", "cf",
        ])
        runner.invoke(projects_app, [
            "add", "--name", "Other", "--type", "client-project",
            "--slug", "other", "--prefix", "ot",
        ])
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Alpha", "--cpp", "x")
        _add_task(runner, app, "--project", "other",
                  "--title", "Bravo", "--cpp", "x")

        result = runner.invoke(app, ["list", "--project", "cifro"])
        assert result.exit_code == 0
        assert "alpha" in result.stdout.lower()
        assert "bravo" not in result.stdout.lower()

    def test_list_filter_status(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Inb", "--cpp", "x", "--status", "backlog")
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Wip", "--cpp", "x", "--status", "in_progress")

        result = runner.invoke(app, ["list", "--status", "in_progress"])
        assert result.exit_code == 0
        assert "wip" in result.stdout.lower()
        assert "inb" not in result.stdout.lower()

    def test_list_filter_assignee(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Mine", "--cpp", "x", "--assignee", "dmitry")
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Free", "--cpp", "x")

        result = runner.invoke(app, ["list", "--assignee", "dmitry"])
        assert result.exit_code == 0
        assert "mine" in result.stdout.lower()
        assert "free" not in result.stdout.lower()

    def test_list_hides_archived(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Live", "--cpp", "x")
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Dead", "--cpp", "x")
        # Удалим Dead (которая получит number=2)
        runner.invoke(app, ["delete", "2"])

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "live" in result.stdout.lower()
        assert "dead" not in result.stdout.lower()

    def test_list_shows_archived_with_flag(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Live", "--cpp", "x")
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Dead", "--cpp", "x")
        runner.invoke(app, ["delete", "2"])

        result = runner.invoke(app, ["list", "--archived"])
        assert result.exit_code == 0
        assert "dead" in result.stdout.lower()
        assert "live" in result.stdout.lower()

    def test_list_sort_by_priority_then_number(self, runner, app, project_cifro, seeded_engine):
        # 3 задачи: P2 #1, P0 #2, P1 #3 → ожидаем порядок P0, P1, P2
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Low", "--cpp", "x", "--priority", "P2")
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Critical", "--cpp", "x", "--priority", "P0")
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Mid", "--cpp", "x", "--priority", "P1")

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        out = result.stdout.lower()
        # Critical (P0) идёт первым
        assert out.index("critical") < out.index("mid") < out.index("low")


# --------------------------------------------------------------------------- #
# get                                                                          #
# --------------------------------------------------------------------------- #


class TestGet:
    def test_get_by_number(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "First", "--cpp", "ЦКП первый")
        result = runner.invoke(app, ["get", "1"])
        assert result.exit_code == 0
        assert "first" in result.stdout.lower()

    def test_get_by_slug(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Login", "--cpp", "x", "--slug", "login")
        result = runner.invoke(app, ["get", "cf-login"])
        assert result.exit_code == 0
        assert "login" in result.stdout.lower()

    def test_get_by_uuid_full(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Task

        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y")
        with make_session(seeded_engine) as session:
            task = session.execute(select(Task)).scalar_one()
            full = task.id

        result = runner.invoke(app, ["get", full])
        assert result.exit_code == 0
        assert "x" in result.stdout.lower()

    def test_get_by_uuid_short(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Task

        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y")
        with make_session(seeded_engine) as session:
            task = session.execute(select(Task)).scalar_one()
            short = task.id[:8]

        result = runner.invoke(app, ["get", short])
        assert result.exit_code == 0
        assert "x" in result.stdout.lower()

    def test_get_not_found(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["get", "nonexistent"])
        assert result.exit_code != 0

    def test_get_shows_project_info(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y")
        result = runner.invoke(app, ["get", "1"])
        assert result.exit_code == 0
        assert "cifro" in result.stdout.lower()

    def test_get_shows_assignee(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y", "--assignee", "dmitry")
        result = runner.invoke(app, ["get", "1"])
        assert result.exit_code == 0
        assert "dmitry" in result.stdout.lower() or "дмитрий" in result.stdout.lower()

    def test_get_shows_action_log(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y")
        result = runner.invoke(app, ["get", "1"])
        assert result.exit_code == 0
        assert "task_created" in result.stdout


# --------------------------------------------------------------------------- #
# update                                                                       #
# --------------------------------------------------------------------------- #


class TestUpdate:
    def test_update_single_field(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Task

        _add_task(runner, app, "--project", "cifro",
                  "--title", "Old", "--cpp", "x")
        result = runner.invoke(app, ["update", "1", "--title", "New"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            task = session.execute(select(Task)).scalar_one()
            assert task.title == "New"

    def test_update_multiple(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Task

        _add_task(runner, app, "--project", "cifro",
                  "--title", "Old", "--cpp", "x")
        result = runner.invoke(app, [
            "update", "1",
            "--title", "New",
            "--priority", "P0",
            "--story-points", "5",
        ])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            task = session.execute(select(Task)).scalar_one()
            assert task.title == "New"
            assert task.priority == "P0"
            assert task.story_points == 5

    def test_update_slug_forbidden(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y")
        result = runner.invoke(app, ["update", "1", "--slug", "new-slug"])
        assert result.exit_code != 0
        assert "slug" in _combined(result).lower()

    def test_update_number_forbidden(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y")
        result = runner.invoke(app, ["update", "1", "--number", "99"])
        assert result.exit_code != 0

    def test_update_project_forbidden(self, runner, app, projects_app, project_cifro, seeded_engine):
        runner.invoke(projects_app, [
            "add", "--name", "Other", "--type", "client-project",
            "--slug", "other", "--prefix", "ot",
        ])
        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y")
        result = runner.invoke(app, ["update", "1", "--project", "other"])
        assert result.exit_code != 0

    def test_update_status_to_in_progress_sets_started_at(
        self, runner, app, project_cifro, seeded_engine,
    ):
        from atlas.pm.db import make_session
        from atlas.pm.models import Task

        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y")
        result = runner.invoke(app, ["update", "1", "--status", "in_progress"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            task = session.execute(select(Task)).scalar_one()
            assert task.status == "in_progress"
            assert task.started_at is not None

    def test_update_status_to_done_sets_completed_at(
        self, runner, app, project_cifro, seeded_engine,
    ):
        from atlas.pm.db import make_session
        from atlas.pm.models import Task

        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y")
        result = runner.invoke(app, ["update", "1", "--status", "done"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            task = session.execute(select(Task)).scalar_one()
            assert task.status == "done"
            assert task.completed_at is not None

    def test_update_status_from_done_clears_completed_at(
        self, runner, app, project_cifro, seeded_engine,
    ):
        from atlas.pm.db import make_session
        from atlas.pm.models import Task

        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y")
        runner.invoke(app, ["update", "1", "--status", "done"])
        result = runner.invoke(app, ["update", "1", "--status", "todo"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            task = session.execute(select(Task)).scalar_one()
            assert task.status == "todo"
            assert task.completed_at is None

    def test_update_creates_action_log_with_diff(
        self, runner, app, project_cifro, seeded_engine,
    ):
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        _add_task(runner, app, "--project", "cifro",
                  "--title", "Old", "--cpp", "y")
        runner.invoke(app, ["update", "1", "--title", "New"])

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "task_updated")
            ).scalar_one()
            details = json.loads(entry.details_json)
            assert "title" in details
            assert details["title"]["old"] == "Old"
            assert details["title"]["new"] == "New"

    def test_update_nonexistent(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["update", "999", "--title", "X"])
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# delete                                                                       #
# --------------------------------------------------------------------------- #


class TestDelete:
    def test_delete_soft_sets_archived_at(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog, Task

        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y")
        result = runner.invoke(app, ["delete", "1"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            task = session.execute(select(Task)).scalar_one()
            assert task.archived_at is not None
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "task_archived")
            ).scalar_one()
            assert entry.entity_id == task.id

    def test_delete_soft_hidden_from_list(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Gone", "--cpp", "y")
        runner.invoke(app, ["delete", "1"])

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "gone" not in result.stdout.lower()

    def test_delete_soft_visible_in_get(self, runner, app, project_cifro, seeded_engine):
        _add_task(runner, app, "--project", "cifro",
                  "--title", "Gone", "--cpp", "y")
        runner.invoke(app, ["delete", "1"])

        result = runner.invoke(app, ["get", "1"])
        assert result.exit_code == 0
        assert "archived" in result.stdout.lower()

    def test_delete_hard_with_confirm(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Task

        _add_task(runner, app, "--project", "cifro",
                  "--title", "X", "--cpp", "y")
        result = runner.invoke(app, ["delete", "1", "--hard"], input="y\n")
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            assert session.execute(select(func.count()).select_from(Task)).scalar() == 0

    def test_delete_nonexistent(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["delete", "999"])
        assert result.exit_code != 0
