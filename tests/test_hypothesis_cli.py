"""Тесты для CLI `atlas hypothesis ...` (Atlas Hypothesis Ledger).

Покрытие: add / list / get / update / close / delete + slug/number-генерация,
status transitions (tested_at/closed_at), verdict, action_log, edge cases.
По образцу tests/test_pm_tasks_cli.py.
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
    return CliRunner()


@pytest.fixture()
def projects_app():
    from atlas.pm.commands.projects import projects_app
    return projects_app


@pytest.fixture()
def tasks_app():
    from atlas.pm.commands.pm_tasks import pm_tasks_app
    return pm_tasks_app


@pytest.fixture()
def app():
    """CLI-приложение hypothesis."""
    from atlas.pm.commands.hypothesis import hypothesis_app
    return hypothesis_app


@pytest.fixture()
def project_cifro(runner, projects_app, seeded_engine):
    """Создать проект 'cifro' с prefix 'cf' для тестов гипотез."""
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


def _add(runner, app, *args):
    return runner.invoke(app, ["add", *args])


# --------------------------------------------------------------------------- #
# add                                                                          #
# --------------------------------------------------------------------------- #


class TestAdd:
    def test_add_minimal(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        result = _add(
            runner, app,
            "--project", "cifro",
            "--title", "Snappier onboarding lifts activation",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            h = session.execute(select(Hypothesis)).scalar_one()
            assert h.slug is not None
            assert h.slug.startswith("cf-")
            assert h.number == 1
            assert h.status == "draft"
            assert h.confidence == "M"
            assert h.verdict is None

    def test_add_explicit_slug(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        result = _add(
            runner, app,
            "--project", "cifro", "--title", "X",
            "--slug", "faster-onboarding",
        )
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            h = session.execute(
                select(Hypothesis).where(Hypothesis.slug == "cf-faster-onboarding")
            ).scalar_one()
            assert h.title == "X"

    def test_add_full_fields(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        result = _add(
            runner, app,
            "--project", "cifro", "--title", "Pricing test",
            "--statement", "если показать цену раньше, то конверсия +",
            "--metric", "conversion", "--baseline", "3%", "--target", "4%",
            "--method", "A/B 2 недели", "--confidence", "H",
        )
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            h = session.execute(select(Hypothesis)).scalar_one()
            assert h.metric == "conversion"
            assert h.baseline == "3%"
            assert h.target == "4%"
            assert h.confidence == "H"

    def test_add_with_task(self, runner, app, tasks_app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis, Task

        r = runner.invoke(tasks_app, [
            "add", "--project", "cifro", "--title", "Build onboarding", "--cpp", "x",
        ])
        assert r.exit_code == 0, _combined(r)

        result = _add(
            runner, app,
            "--project", "cifro", "--title", "Linked", "--task", "1",
        )
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            h = session.execute(select(Hypothesis)).scalar_one()
            task = session.execute(select(Task)).scalar_one()
            assert h.task_id == task.id

    def test_add_task_other_project_fails(
        self, runner, app, tasks_app, projects_app, project_cifro, seeded_engine,
    ):
        runner.invoke(projects_app, [
            "add", "--name", "Other", "--type", "client-project",
            "--slug", "other", "--prefix", "ot",
        ])
        runner.invoke(tasks_app, [
            "add", "--project", "other", "--title", "Foreign", "--cpp", "x",
        ])
        result = _add(
            runner, app,
            "--project", "cifro", "--title", "Wrong link", "--task", "1",
        )
        assert result.exit_code != 0

    def test_add_slug_collision_auto(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        r1 = _add(runner, app, "--project", "cifro", "--title", "Dup", "--slug", "dup")
        assert r1.exit_code == 0
        r2 = _add(runner, app, "--project", "cifro", "--title", "Dup")
        assert r2.exit_code == 0, _combined(r2)
        with make_session(seeded_engine) as session:
            h = session.execute(
                select(Hypothesis).where(Hypothesis.slug == "cf-dup-2")
            ).scalar_one()
            assert h.title == "Dup"

    def test_add_number_autoincrement(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        for t in ["A", "B", "C"]:
            r = _add(runner, app, "--project", "cifro", "--title", t)
            assert r.exit_code == 0, _combined(r)
        with make_session(seeded_engine) as session:
            numbers = sorted(session.execute(select(Hypothesis.number)).scalars().all())
            assert numbers == [1, 2, 3]

    def test_add_status_testing_sets_tested_at(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        r = _add(runner, app, "--project", "cifro", "--title", "T", "--status", "testing")
        assert r.exit_code == 0, _combined(r)
        with make_session(seeded_engine) as session:
            h = session.execute(select(Hypothesis)).scalar_one()
            assert h.status == "testing"
            assert h.tested_at is not None

    def test_add_invalid_status(self, runner, app, project_cifro, seeded_engine):
        r = _add(runner, app, "--project", "cifro", "--title", "X", "--status", "garbage")
        assert r.exit_code != 0

    def test_add_invalid_confidence(self, runner, app, project_cifro, seeded_engine):
        r = _add(runner, app, "--project", "cifro", "--title", "X", "--confidence", "Z")
        assert r.exit_code != 0

    def test_add_nonexistent_project(self, runner, app, seeded_engine):
        r = _add(runner, app, "--project", "nope", "--title", "X")
        assert r.exit_code != 0

    def test_add_creates_action_log(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        r = _add(runner, app, "--project", "cifro", "--title", "Logged")
        assert r.exit_code == 0, _combined(r)
        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "hypothesis_created")
            ).scalar_one()
            assert entry.entity_type == "hypothesis"
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
        _add(runner, app, "--project", "cifro", "--title", "Alpha")
        _add(runner, app, "--project", "cifro", "--title", "Bravo")
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "alpha" in result.stdout.lower()
        assert "bravo" in result.stdout.lower()

    def test_list_filter_status(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "Draftone")
        _add(runner, app, "--project", "cifro", "--title", "Testone", "--status", "testing")
        result = runner.invoke(app, ["list", "--status", "testing"])
        assert result.exit_code == 0
        assert "testone" in result.stdout.lower()
        assert "draftone" not in result.stdout.lower()

    def test_list_filter_confidence(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "Highone", "--confidence", "H")
        _add(runner, app, "--project", "cifro", "--title", "Lowone", "--confidence", "L")
        result = runner.invoke(app, ["list", "--confidence", "H"])
        assert result.exit_code == 0
        assert "highone" in result.stdout.lower()
        assert "lowone" not in result.stdout.lower()

    def test_list_filter_verdict(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "Accepted")
        _add(runner, app, "--project", "cifro", "--title", "Rejected")
        runner.invoke(app, ["close", "1", "--verdict", "accept"])
        runner.invoke(app, ["close", "2", "--verdict", "reject"])
        result = runner.invoke(app, ["list", "--verdict", "accept"])
        assert result.exit_code == 0
        assert "accepted" in result.stdout.lower()
        assert "rejected" not in result.stdout.lower()

    def test_list_filter_project(self, runner, app, projects_app, seeded_engine):
        runner.invoke(projects_app, [
            "add", "--name", "Cifro", "--type", "client-project",
            "--slug", "cifro", "--prefix", "cf",
        ])
        runner.invoke(projects_app, [
            "add", "--name", "Other", "--type", "client-project",
            "--slug", "other", "--prefix", "ot",
        ])
        _add(runner, app, "--project", "cifro", "--title", "Cif")
        _add(runner, app, "--project", "other", "--title", "Oth")
        result = runner.invoke(app, ["list", "--project", "cifro"])
        assert result.exit_code == 0
        # Проверяем по slug-токенам (cf-cif / ot-oth), а не по сырым "cif"/"oth":
        # подстрока "oth" встречается в слове "Hypotheses" в заголовке таблицы.
        out = result.stdout.lower()
        assert "cf-cif" in out
        assert "ot-oth" not in out

    def test_list_hides_archived(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "Live")
        _add(runner, app, "--project", "cifro", "--title", "Dead")
        runner.invoke(app, ["delete", "2"])
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "live" in result.stdout.lower()
        assert "dead" not in result.stdout.lower()

    def test_list_shows_archived_with_flag(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "Live")
        _add(runner, app, "--project", "cifro", "--title", "Dead")
        runner.invoke(app, ["delete", "2"])
        result = runner.invoke(app, ["list", "--archived"])
        assert result.exit_code == 0
        assert "dead" in result.stdout.lower()


# --------------------------------------------------------------------------- #
# get                                                                          #
# --------------------------------------------------------------------------- #


class TestGet:
    def test_get_by_number(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "Firstone")
        result = runner.invoke(app, ["get", "1"])
        assert result.exit_code == 0
        assert "firstone" in result.stdout.lower()

    def test_get_by_slug(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "X", "--slug", "login")
        result = runner.invoke(app, ["get", "cf-login"])
        assert result.exit_code == 0

    def test_get_by_uuid_full(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        _add(runner, app, "--project", "cifro", "--title", "X")
        with make_session(seeded_engine) as session:
            full = session.execute(select(Hypothesis)).scalar_one().id
        result = runner.invoke(app, ["get", full])
        assert result.exit_code == 0

    def test_get_by_uuid_short(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        _add(runner, app, "--project", "cifro", "--title", "X")
        with make_session(seeded_engine) as session:
            short = session.execute(select(Hypothesis)).scalar_one().id[:8]
        result = runner.invoke(app, ["get", short])
        assert result.exit_code == 0

    def test_get_not_found(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["get", "nonexistent"])
        assert result.exit_code != 0

    def test_get_shows_project_and_log(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "X")
        result = runner.invoke(app, ["get", "1"])
        assert result.exit_code == 0
        assert "cifro" in result.stdout.lower()
        assert "hypothesis_created" in result.stdout


# --------------------------------------------------------------------------- #
# update                                                                       #
# --------------------------------------------------------------------------- #


class TestUpdate:
    def test_update_single_field(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        _add(runner, app, "--project", "cifro", "--title", "Old")
        result = runner.invoke(app, ["update", "1", "--title", "New"])
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            h = session.execute(select(Hypothesis)).scalar_one()
            assert h.title == "New"

    def test_update_measurement_fields(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        _add(runner, app, "--project", "cifro", "--title", "X")
        result = runner.invoke(app, [
            "update", "1", "--result-value", "4.2%", "--delta", "+18%",
            "--lesson", "раннее раскрытие цены работает",
            "--consolidated-into", "skills/pricing/SKILL.md",
        ])
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            h = session.execute(select(Hypothesis)).scalar_one()
            assert h.result_value == "4.2%"
            assert h.delta == "+18%"
            assert h.lesson == "раннее раскрытие цены работает"
            assert h.consolidated_into == "skills/pricing/SKILL.md"

    def test_update_status_testing_sets_tested_at(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        _add(runner, app, "--project", "cifro", "--title", "X")
        result = runner.invoke(app, ["update", "1", "--status", "testing"])
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            h = session.execute(select(Hypothesis)).scalar_one()
            assert h.status == "testing"
            assert h.tested_at is not None

    def test_update_status_closed_sets_closed_at(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        _add(runner, app, "--project", "cifro", "--title", "X")
        result = runner.invoke(app, ["update", "1", "--status", "closed"])
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            h = session.execute(select(Hypothesis)).scalar_one()
            assert h.status == "closed"
            assert h.closed_at is not None

    def test_update_slug_forbidden(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "X")
        result = runner.invoke(app, ["update", "1", "--slug", "new"])
        assert result.exit_code != 0
        assert "slug" in _combined(result).lower()

    def test_update_number_forbidden(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "X")
        result = runner.invoke(app, ["update", "1", "--number", "99"])
        assert result.exit_code != 0

    def test_update_creates_action_log_with_diff(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        _add(runner, app, "--project", "cifro", "--title", "Old")
        runner.invoke(app, ["update", "1", "--title", "New"])
        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "hypothesis_updated")
            ).scalar_one()
            details = json.loads(entry.details_json)
            assert details["title"]["old"] == "Old"
            assert details["title"]["new"] == "New"

    def test_update_nonexistent(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["update", "999", "--title", "X"])
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# close                                                                        #
# --------------------------------------------------------------------------- #


class TestClose:
    def test_close_sets_verdict_status_closed_at(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        _add(runner, app, "--project", "cifro", "--title", "X")
        result = runner.invoke(app, [
            "close", "1", "--verdict", "accept", "--delta", "+18%",
            "--result-value", "4.2%", "--lesson", "урок",
        ])
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            h = session.execute(select(Hypothesis)).scalar_one()
            assert h.status == "closed"
            assert h.verdict == "accept"
            assert h.closed_at is not None
            assert h.delta == "+18%"
            assert h.result_value == "4.2%"
            assert h.lesson == "урок"

    def test_close_invalid_verdict(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "X")
        result = runner.invoke(app, ["close", "1", "--verdict", "maybe"])
        assert result.exit_code != 0

    def test_close_requires_verdict(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "X")
        result = runner.invoke(app, ["close", "1"])
        assert result.exit_code != 0

    def test_close_creates_action_log(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        _add(runner, app, "--project", "cifro", "--title", "X")
        runner.invoke(app, ["close", "1", "--verdict", "reject"])
        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "hypothesis_closed")
            ).scalar_one()
            assert entry.entity_type == "hypothesis"
            details = json.loads(entry.details_json)
            assert details["verdict"]["new"] == "reject"


# --------------------------------------------------------------------------- #
# delete                                                                       #
# --------------------------------------------------------------------------- #


class TestDelete:
    def test_delete_soft_sets_archived_at(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog, Hypothesis

        _add(runner, app, "--project", "cifro", "--title", "X")
        result = runner.invoke(app, ["delete", "1"])
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            h = session.execute(select(Hypothesis)).scalar_one()
            assert h.archived_at is not None
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "hypothesis_archived")
            ).scalar_one()
            assert entry.entity_id == h.id

    def test_delete_soft_hidden_from_list(self, runner, app, project_cifro, seeded_engine):
        _add(runner, app, "--project", "cifro", "--title", "Gone")
        runner.invoke(app, ["delete", "1"])
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "gone" not in result.stdout.lower()

    def test_delete_hard_with_confirm(self, runner, app, project_cifro, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Hypothesis

        _add(runner, app, "--project", "cifro", "--title", "X")
        result = runner.invoke(app, ["delete", "1", "--hard"], input="y\n")
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            cnt = session.execute(select(func.count()).select_from(Hypothesis)).scalar()
            assert cnt == 0

    def test_delete_nonexistent(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["delete", "999"])
        assert result.exit_code != 0
