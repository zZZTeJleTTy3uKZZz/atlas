"""F4f: `atlas projects member-add / member-list / member-remove`.

Управление участием члена в проекте с ролью (lead/member) поверх модели
ProjectParticipant. PK=(project_id, participant_id) → одна роль на участника
в проекте; повторный add ОБНОВЛЯЕТ role_in_project (не плодит дубль).

TDD: тесты написаны ДО реализации команд в projects.py.
Стиль зеркалит test_pm_projects_tags_cli.py (CliRunner на tmp sqlite + seed).
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
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
def projects_app():
    from atlas.commands.projects import projects_app
    return projects_app


@pytest.fixture()
def participants_app():
    from atlas.commands.participants import app as participants_app
    return participants_app


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


def _add_project(runner, projects_app, *args):
    return runner.invoke(
        projects_app,
        ["add", "--no-setup-layout", "--no-canonical", *args],
    )


def _add_participant(runner, participants_app, slug, name="Person"):
    return runner.invoke(
        participants_app,
        ["add", "--name", name, "--kind", "human", "--slug", slug],
    )


def _setup_project_and_member(runner, projects_app, participants_app,
                              project_slug="cifro", member_slug="ivan"):
    r1 = _add_project(
        runner, projects_app,
        "--name", "Cifro", "--type", "client-project", "--slug", project_slug,
    )
    assert r1.exit_code == 0, _combined(r1)
    r2 = _add_participant(runner, participants_app, member_slug)
    assert r2.exit_code == 0, _combined(r2)


# --------------------------------------------------------------------------- #
# member-add                                                                  #
# --------------------------------------------------------------------------- #


class TestProjectsMemberAdd:
    def test_member_add_creates_link_with_role(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        from atlas.db import make_session
        from atlas.models import Participant, Project, ProjectParticipant

        _setup_project_and_member(runner, projects_app, participants_app)
        result = runner.invoke(
            projects_app,
            ["member-add", "cifro", "--member", "ivan", "--role", "lead"],
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            part = session.execute(
                select(Participant).where(Participant.slug == "ivan")
            ).scalar_one()
            link = session.get(ProjectParticipant, (proj.id, part.id))
            assert link is not None
            assert link.role_in_project == "lead"

    def test_member_add_default_role_is_member(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        """--role не задан → role_in_project='member' (из константы-дефолта)."""
        from atlas.db import make_session
        from atlas.models import Participant, Project, ProjectParticipant

        _setup_project_and_member(runner, projects_app, participants_app)
        result = runner.invoke(
            projects_app,
            ["member-add", "cifro", "--member", "ivan"],
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            part = session.execute(
                select(Participant).where(Participant.slug == "ivan")
            ).scalar_one()
            link = session.get(ProjectParticipant, (proj.id, part.id))
            assert link.role_in_project == "member"

    def test_member_add_repeat_updates_role_no_dup(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        """Повторный add с другой ролью ОБНОВЛЯЕТ role_in_project, не плодит дубль."""
        from atlas.db import make_session
        from atlas.models import Participant, Project, ProjectParticipant

        _setup_project_and_member(runner, projects_app, participants_app)
        runner.invoke(
            projects_app,
            ["member-add", "cifro", "--member", "ivan", "--role", "lead"],
        )
        result = runner.invoke(
            projects_app,
            ["member-add", "cifro", "--member", "ivan", "--role", "member"],
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            count = session.execute(
                select(func.count()).select_from(ProjectParticipant)
                .where(ProjectParticipant.project_id == proj.id)
            ).scalar()
            assert count == 2  # owner (авто-lead) + ivan; дубля ivan нет
            part = session.execute(
                select(Participant).where(Participant.slug == "ivan")
            ).scalar_one()
            link = session.get(ProjectParticipant, (proj.id, part.id))
            assert link.role_in_project == "member"  # роль обновилась

    def test_member_add_invalid_role_exits_1(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        _setup_project_and_member(runner, projects_app, participants_app)
        result = runner.invoke(
            projects_app,
            ["member-add", "cifro", "--member", "ivan", "--role", "boss"],
        )
        assert result.exit_code == 1
        assert "boss" in _combined(result).lower()

    def test_member_add_unknown_project_exits_1(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        _add_participant(runner, participants_app, "ivan")
        result = runner.invoke(
            projects_app,
            ["member-add", "nope", "--member", "ivan", "--role", "lead"],
        )
        assert result.exit_code == 1

    def test_member_add_unknown_member_exits_1(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        _add_project(
            runner, projects_app,
            "--name", "Cifro", "--type", "client-project", "--slug", "cifro",
        )
        result = runner.invoke(
            projects_app,
            ["member-add", "cifro", "--member", "ghost", "--role", "lead"],
        )
        assert result.exit_code == 1

    def test_member_add_logs_action(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        from atlas.db import make_session
        from atlas.models import ActionLog

        _setup_project_and_member(runner, projects_app, participants_app)
        runner.invoke(
            projects_app,
            ["member-add", "cifro", "--member", "ivan", "--role", "lead"],
        )

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog)
                .where(ActionLog.action == "project_member_added")
                .order_by(ActionLog.timestamp.desc())
            ).scalars().first()
            assert entry is not None
            details = json.loads(entry.details_json)
            assert details.get("role") == "lead"
            assert details.get("participant") in ("ivan", None) or "ivan" in str(details)

    def test_member_add_accepts_participant_alias(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        """--participant — алиас к --member (план архитектора)."""
        from atlas.db import make_session
        from atlas.models import Participant, Project, ProjectParticipant

        _setup_project_and_member(runner, projects_app, participants_app)
        result = runner.invoke(
            projects_app,
            ["member-add", "cifro", "--participant", "ivan", "--role", "lead"],
        )
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            part = session.execute(
                select(Participant).where(Participant.slug == "ivan")
            ).scalar_one()
            assert session.get(ProjectParticipant, (proj.id, part.id)) is not None


# --------------------------------------------------------------------------- #
# member-list                                                                 #
# --------------------------------------------------------------------------- #


class TestProjectsMemberList:
    def test_member_list_shows_members(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        _add_project(
            runner, projects_app,
            "--name", "Cifro", "--type", "client-project", "--slug", "cifro",
        )
        _add_participant(runner, participants_app, "ivan", name="Иван")
        _add_participant(runner, participants_app, "petr", name="Пётр")
        runner.invoke(
            projects_app,
            ["member-add", "cifro", "--member", "ivan", "--role", "lead"],
        )
        runner.invoke(
            projects_app,
            ["member-add", "cifro", "--member", "petr", "--role", "member"],
        )

        result = runner.invoke(projects_app, ["member-list", "cifro"])
        assert result.exit_code == 0, _combined(result)
        out = result.stdout.lower()
        assert "ivan" in out
        assert "petr" in out
        assert "lead" in out
        assert "member" in out

    def test_member_list_empty(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        _add_project(
            runner, projects_app,
            "--name", "Cifro", "--type", "client-project", "--slug", "cifro",
        )
        result = runner.invoke(projects_app, ["member-list", "cifro"])
        assert result.exit_code == 0, _combined(result)

    def test_member_list_unknown_project_exits_1(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        result = runner.invoke(projects_app, ["member-list", "nope"])
        assert result.exit_code == 1


# --------------------------------------------------------------------------- #
# member-remove                                                               #
# --------------------------------------------------------------------------- #


class TestProjectsMemberRemove:
    def test_member_remove_drops_link(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        from atlas.db import make_session
        from atlas.models import Participant, Project, ProjectParticipant

        _setup_project_and_member(runner, projects_app, participants_app)
        runner.invoke(
            projects_app,
            ["member-add", "cifro", "--member", "ivan", "--role", "lead"],
        )
        result = runner.invoke(
            projects_app,
            ["member-remove", "cifro", "--member", "ivan"],
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            count = session.execute(
                select(func.count()).select_from(ProjectParticipant)
                .where(ProjectParticipant.project_id == proj.id)
            ).scalar()
            # ivan снят; остаётся только owner (авто-lead владельца)
            assert count == 1
            ivan = session.execute(
                select(Participant).where(Participant.slug == "ivan")
            ).scalar_one()
            assert session.get(ProjectParticipant, (proj.id, ivan.id)) is None

    def test_member_remove_absent_graceful(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        """Снятие отсутствующей связи — graceful (exit 0, warning)."""
        _setup_project_and_member(runner, projects_app, participants_app)
        result = runner.invoke(
            projects_app,
            ["member-remove", "cifro", "--member", "ivan"],
        )
        assert result.exit_code == 0

    def test_member_remove_logs_action(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        from atlas.db import make_session
        from atlas.models import ActionLog

        _setup_project_and_member(runner, projects_app, participants_app)
        runner.invoke(
            projects_app,
            ["member-add", "cifro", "--member", "ivan", "--role", "lead"],
        )
        runner.invoke(
            projects_app,
            ["member-remove", "cifro", "--member", "ivan"],
        )

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog)
                .where(ActionLog.action == "project_member_removed")
                .order_by(ActionLog.timestamp.desc())
            ).scalars().first()
            assert entry is not None

    def test_member_remove_unknown_project_exits_1(
        self, runner, projects_app, participants_app, seeded_engine
    ):
        result = runner.invoke(
            projects_app,
            ["member-remove", "nope", "--member", "ivan"],
        )
        assert result.exit_code == 1
