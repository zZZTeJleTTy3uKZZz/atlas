"""Тесты для CLI `atlas participants ...` (PM-слой).

TDD: эти тесты пишутся ДО реализации src/atlas/pm/commands/participants.py.

Покрытие: add / list / get / update / delete + edge cases.
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
    """Чистая SQLite БД на диске + ATLAS_DB_URL в env."""
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
    """Чистая БД + полный seed (project_types, project_statuses, participants)."""
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
    """CLI-приложение participants."""
    from atlas.commands.participants import app as participants_app
    return participants_app


@pytest.fixture()
def projects_app():
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


def _add(runner, app, *args):
    return runner.invoke(app, ["add", *args])


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


class TestAdd:
    def test_add_minimal(self, runner, app, seeded_engine):
        """name + kind → slug auto."""
        from atlas.db import make_session
        from atlas.models import Participant

        result = _add(
            runner, app,
            "--name", "Артём Маркетолог",
            "--kind", "contractor",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            p = session.execute(
                select(Participant).where(Participant.name == "Артём Маркетолог")
            ).scalar_one()
            assert p.kind == "contractor"
            assert p.slug  # auto-сгенерирован
            assert p.is_active is True or p.is_active == 1

    def test_add_explicit_slug(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Participant

        result = _add(
            runner, app,
            "--name", "Artyom",
            "--kind", "contractor",
            "--slug", "artyom",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            p = session.execute(
                select(Participant).where(Participant.slug == "artyom")
            ).scalar_one()
            assert p.name == "Artyom"

    def test_add_slug_collision(self, runner, app, seeded_engine):
        """seed уже содержит 'owner' → попытка занятого slug → error."""
        result = _add(
            runner, app,
            "--name", "Other Dmitry",
            "--kind", "human",
            "--slug", "owner",
        )
        assert result.exit_code != 0
        combined = _combined(result)
        assert "owner" in combined.lower()

    def test_add_invalid_kind(self, runner, app, seeded_engine):
        result = _add(
            runner, app,
            "--name", "X",
            "--kind", "robot",  # invalid
        )
        assert result.exit_code != 0

    def test_add_with_metadata(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Participant

        result = _add(
            runner, app,
            "--name", "Контрактор",
            "--kind", "contractor",
            "--slug", "kontraktor",
            "--metadata-json", '{"rate": 2000, "currency": "RUB"}',
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            p = session.execute(
                select(Participant).where(Participant.slug == "kontraktor")
            ).scalar_one()
            data = json.loads(p.metadata_json)
            assert data["rate"] == 2000

    def test_add_with_role_and_email(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Participant

        result = _add(
            runner, app,
            "--name", "Маркер",
            "--kind", "contractor",
            "--slug", "marker",
            "--role", "Marketing",
            "--email", "marker@example.com",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            p = session.execute(
                select(Participant).where(Participant.slug == "marker")
            ).scalar_one()
            assert p.role_default == "Marketing"
            assert p.email == "marker@example.com"

    def test_add_creates_action_log(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ActionLog

        result = _add(
            runner, app,
            "--name", "Logged",
            "--kind", "contractor",
            "--slug", "logged-p",
        )
        assert result.exit_code == 0

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "participant_created")
            ).scalar_one()
            assert entry.entity_type == "participant"
            details = json.loads(entry.details_json)
            assert details["slug"] == "logged-p"


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


class TestList:
    def test_list_all_default(self, runner, app, seeded_engine):
        """Seed имеет 2 активных participants — owner и claude-code."""
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0, _combined(result)
        assert "owner" in result.stdout.lower()
        assert "claude" in result.stdout.lower()

    def test_list_filter_kind(self, runner, app, seeded_engine):
        _add(runner, app, "--name", "C1", "--kind", "contractor", "--slug", "c1")

        result = runner.invoke(app, ["list", "--kind", "contractor"])
        assert result.exit_code == 0
        assert "c1" in result.stdout.lower()
        assert "owner" not in result.stdout.lower()

    def test_list_hides_inactive_by_default(self, runner, app, seeded_engine):
        _add(runner, app, "--name", "Dead", "--kind", "contractor", "--slug", "dead-p")
        runner.invoke(app, ["delete", "dead-p", "--soft"])

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "dead-p" not in result.stdout.lower()

    def test_list_shows_inactive_with_flag(self, runner, app, seeded_engine):
        _add(runner, app, "--name", "Dead", "--kind", "contractor", "--slug", "dead-p")
        runner.invoke(app, ["delete", "dead-p", "--soft"])

        result = runner.invoke(app, ["list", "--inactive"])
        assert result.exit_code == 0
        assert "dead-p" in result.stdout.lower()


# --------------------------------------------------------------------------- #
# get                                                                         #
# --------------------------------------------------------------------------- #


class TestGet:
    def test_get_by_slug(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["get", "owner"])
        assert result.exit_code == 0
        assert "дмитрий" in result.stdout.lower() or "owner" in result.stdout.lower()

    def test_get_by_uuid(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Participant

        with make_session(seeded_engine) as session:
            p = session.execute(
                select(Participant).where(Participant.slug == "owner")
            ).scalar_one()
            full_uuid = p.id

        result = runner.invoke(app, ["get", full_uuid])
        assert result.exit_code == 0
        assert "owner" in result.stdout.lower()

    def test_get_not_found(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["get", "nope-no-such"])
        assert result.exit_code == 1

    def test_get_shows_projects(self, runner, app, seeded_engine, projects_app):
        """get показывает проекты участника. add авто-добавляет владельца
        как lead → он сразу участник созданного проекта (без ручного member-add)."""
        runner.invoke(
            projects_app,
            ["add", "--name", "Cifro", "--type", "client-project", "--slug", "cifro"],
        )
        result = runner.invoke(app, ["get", "owner"])
        assert result.exit_code == 0
        assert "cifro" in result.stdout.lower()


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #


class TestUpdate:
    def test_update_single_field(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Participant

        _add(runner, app, "--name", "Old", "--kind", "contractor", "--slug", "upd1")
        result = runner.invoke(app, ["update", "upd1", "--name", "New"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            p = session.execute(
                select(Participant).where(Participant.slug == "upd1")
            ).scalar_one()
            assert p.name == "New"

    def test_update_multiple_fields(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Participant

        _add(runner, app, "--name", "Old", "--kind", "contractor", "--slug", "upd2")
        result = runner.invoke(app, [
            "update", "upd2",
            "--name", "New",
            "--role", "QA",
            "--email", "qa@example.com",
        ])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            p = session.execute(
                select(Participant).where(Participant.slug == "upd2")
            ).scalar_one()
            assert p.name == "New"
            assert p.role_default == "QA"
            assert p.email == "qa@example.com"

    def test_update_inactive_flag(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Participant

        _add(runner, app, "--name", "X", "--kind", "contractor", "--slug", "upd3")
        result = runner.invoke(app, ["update", "upd3", "--inactive"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            p = session.execute(
                select(Participant).where(Participant.slug == "upd3")
            ).scalar_one()
            assert not p.is_active

    def test_update_slug_forbidden(self, runner, app, seeded_engine):
        _add(runner, app, "--name", "X", "--kind", "contractor", "--slug", "upd4")
        result = runner.invoke(app, ["update", "upd4", "--slug", "new-slug"])
        assert result.exit_code != 0
        assert "slug" in _combined(result).lower()

    def test_update_nonexistent(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["update", "nope-xx", "--name", "X"])
        assert result.exit_code != 0

    def test_update_creates_action_log_with_diff(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ActionLog

        _add(runner, app, "--name", "Old", "--kind", "contractor", "--slug", "upd5")
        runner.invoke(app, ["update", "upd5", "--name", "New"])

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "participant_updated")
            ).scalar_one()
            details = json.loads(entry.details_json)
            assert "name" in details
            assert details["name"]["old"] == "Old"
            assert details["name"]["new"] == "New"


# --------------------------------------------------------------------------- #
# delete                                                                      #
# --------------------------------------------------------------------------- #


class TestDelete:
    def test_delete_hard_no_attachments(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ActionLog, Participant

        _add(runner, app, "--name", "X", "--kind", "contractor", "--slug", "del1")
        result = runner.invoke(app, ["delete", "del1"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            p = session.execute(
                select(Participant).where(Participant.slug == "del1")
            ).scalar_one_or_none()
            assert p is None
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "participant_deleted")
            ).scalar_one()
            assert entry.entity_type == "participant"

    def test_delete_with_attachments_blocks(self, runner, app, seeded_engine, projects_app):
        """Если participant используется в project_participants → нужен --force."""
        from atlas.db import make_session
        from atlas.models import Participant, Project, ProjectParticipant

        _add(runner, app, "--name", "Worker", "--kind", "contractor", "--slug", "worker1")
        runner.invoke(
            projects_app,
            ["add", "--name", "Cifro", "--type", "client-project", "--slug", "cifro"],
        )
        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            worker = session.execute(
                select(Participant).where(Participant.slug == "worker1")
            ).scalar_one()
            session.add(ProjectParticipant(
                project_id=proj.id, participant_id=worker.id,
                role_in_project="Dev",
            ))
            session.commit()

        result = runner.invoke(app, ["delete", "worker1"])
        assert result.exit_code != 0
        combined = _combined(result)
        assert "force" in combined.lower() or "used" in combined.lower()

    def test_delete_force_cascades(self, runner, app, seeded_engine, projects_app):
        """--force удаляет связи и обнуляет assignee у tasks."""
        from atlas.db import make_session
        from atlas.models import (
            Participant, Project, ProjectParticipant, Task,
        )

        _add(runner, app, "--name", "Worker", "--kind", "contractor", "--slug", "worker2")
        runner.invoke(
            projects_app,
            ["add", "--name", "Cf2", "--type", "client-project",
             "--slug", "cf2", "--prefix", "cf2"],
        )
        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cf2")
            ).scalar_one()
            worker = session.execute(
                select(Participant).where(Participant.slug == "worker2")
            ).scalar_one()
            session.add(ProjectParticipant(
                project_id=proj.id, participant_id=worker.id,
                role_in_project="Dev",
            ))
            # task с assignee
            task = Task(
                number=1, slug="cf2-t1", project_id=proj.id,
                assignee_id=worker.id,
                title="T", cpp_description="C", status="todo", priority="P2",
            )
            session.add(task)
            session.commit()
            task_id = task.id

        result = runner.invoke(app, ["delete", "worker2", "--force"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            assert session.execute(
                select(Participant).where(Participant.slug == "worker2")
            ).scalar_one_or_none() is None
            # link удалён
            link = session.execute(
                select(ProjectParticipant).where(
                    ProjectParticipant.participant_id == worker.id
                )
            ).scalar_one_or_none()
            assert link is None
            # task assignee = NULL
            t = session.get(Task, task_id)
            assert t is not None
            assert t.assignee_id is None

    def test_delete_soft_deactivates(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ActionLog, Participant

        _add(runner, app, "--name", "X", "--kind", "contractor", "--slug", "del2")
        result = runner.invoke(app, ["delete", "del2", "--soft"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            p = session.execute(
                select(Participant).where(Participant.slug == "del2")
            ).scalar_one()
            assert not p.is_active
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "participant_deactivated")
            ).scalar_one()
            assert entry is not None

    def test_delete_nonexistent(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["delete", "nope-xxx"])
        assert result.exit_code != 0
