"""CLI-тесты lease/claim: `atlas task claim/release/renew/take/stale`."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from typer.testing import CliRunner


@pytest.fixture()
def fresh_engine(tmp_path, monkeypatch):
    from atlas.db import make_engine
    from atlas.models import Base

    url = f"sqlite:///{tmp_path / 'atlas.db'}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def seeded_engine(fresh_engine):
    from atlas.db import make_session
    from atlas.seeds import seed_all

    with make_session(fresh_engine) as s:
        seed_all(s)
        s.commit()
    return fresh_engine


@pytest.fixture(autouse=True)
def _reset_output_mode():
    """Сброс clikit module-global ``_mode`` на json: другие CLI-тесты форсят
    ``--text`` и не возвращают режим, из-за чего наши json.loads падали при
    общем прогоне (изоляция от утечки глобального состояния)."""
    from clikit import output as _out

    _out._mode = "json"
    yield


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def app():
    import atlas.commands.task_lease  # noqa: F401  (регистрирует lease-команды)
    from atlas.commands.task import task_app

    return task_app


@pytest.fixture()
def projects_app():
    from atlas.commands.projects import projects_app

    return projects_app


@pytest.fixture()
def task_ref(runner, app, projects_app, seeded_engine):
    """Создать проект + задачу, вернуть ref (number) задачи."""
    r = runner.invoke(
        projects_app,
        ["add", "--name", "Cifro", "--type", "client-project",
         "--slug", "cifro", "--prefix", "cf"],
    )
    assert r.exit_code == 0, r.stdout
    r = runner.invoke(
        app, ["add", "--project", "cifro", "--title", "T1", "--cpp", "ЦКП"]
    )
    assert r.exit_code == 0, r.stdout
    from atlas.db import make_session
    from atlas.models import Task

    with make_session(seeded_engine) as s:
        t = s.execute(select(Task)).scalars().first()
        return str(t.number)


def test_claim_sets_in_progress(runner, app, task_ref):
    r = runner.invoke(app, ["claim", task_ref, "--actor", "claude-code",
                            "--session", "sess-1", "--from", "atlas"])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    assert d["status"] == "in_progress"
    assert d["lease_owner"] == "claude-code"
    assert d["lease_session_id"] == "sess-1"
    assert d["lease_origin"] == "atlas"
    assert d["lease_expires_at"] is not None


def test_claim_held_by_other_exits_nonzero(runner, app, task_ref):
    assert runner.invoke(app, ["claim", task_ref, "--actor", "claude-code"]).exit_code == 0
    r = runner.invoke(app, ["claim", task_ref, "--actor", "owner"])
    assert r.exit_code != 0
    # CliError("lease_held", ...) → emit_error в stderr (json-режим). Сообщение
    # "занята" — в error-записи. CliRunner разделяет потоки: смотрим оба.
    combined = (r.stdout or "") + (r.stderr or "")
    assert "занята" in combined or "lease_held" in combined


def test_release(runner, app, task_ref):
    runner.invoke(app, ["claim", task_ref, "--actor", "claude-code"])
    r = runner.invoke(app, ["release", task_ref, "--actor", "claude-code"])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    assert d["lease_owner"] is None


def test_release_not_owner_exits_nonzero(runner, app, task_ref):
    runner.invoke(app, ["claim", task_ref, "--actor", "claude-code"])
    r = runner.invoke(app, ["release", task_ref, "--actor", "owner"])
    assert r.exit_code != 0


def test_renew(runner, app, task_ref):
    runner.invoke(app, ["claim", task_ref, "--actor", "claude-code", "--ttl", "30m"])
    r = runner.invoke(app, ["renew", task_ref, "--actor", "claude-code", "--ttl", "2h"])
    assert r.exit_code == 0, r.stdout


def test_take_requires_force(runner, app, task_ref):
    runner.invoke(app, ["claim", task_ref, "--actor", "claude-code"])
    # без --force — отказ
    assert runner.invoke(app, ["take", task_ref, "--actor", "owner"]).exit_code != 0
    # с --force — отбирает
    r = runner.invoke(app, ["take", task_ref, "--actor", "owner", "--force"])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    assert d["lease_owner"] == "owner"


def test_claim_uses_env_actor(runner, app, task_ref, monkeypatch):
    monkeypatch.setenv("ATLAS_ACTOR", "claude-code")
    r = runner.invoke(app, ["claim", task_ref])
    assert r.exit_code == 0, r.stdout
    assert json.loads(r.stdout)["lease_owner"] == "claude-code"


def test_bad_ttl_exits_nonzero(runner, app, task_ref):
    r = runner.invoke(app, ["claim", task_ref, "--actor", "claude-code", "--ttl", "xyz"])
    assert r.exit_code != 0


def test_update_done_releases_lease(runner, app, task_ref):
    runner.invoke(app, ["claim", task_ref, "--actor", "claude-code"])
    r = runner.invoke(app, ["update", task_ref, "--status", "done"])
    assert r.exit_code == 0, r.stdout
    g = json.loads(runner.invoke(app, ["get", task_ref]).stdout)
    assert g["status"] == "done"
    assert g["lease_owner"] is None


def test_get_shows_lease(runner, app, task_ref):
    runner.invoke(app, ["claim", task_ref, "--actor", "claude-code", "--from", "atlas"])
    r = runner.invoke(app, ["get", task_ref])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    assert d["lease_owner"] == "claude-code"
    assert d["lease_origin"] == "atlas"
    assert d["lease_expires_at"] is not None


def test_list_shows_lease(runner, app, task_ref):
    runner.invoke(app, ["claim", task_ref, "--actor", "claude-code"])
    r = runner.invoke(app, ["list"])
    assert r.exit_code == 0, r.stdout
    rows = json.loads(r.stdout)
    assert rows[0]["lease_owner"] == "claude-code"


def test_stale_report_and_reap_empty(runner, app, task_ref):
    runner.invoke(app, ["claim", task_ref, "--actor", "claude-code"])
    r = runner.invoke(app, ["stale"])
    assert r.exit_code == 0, r.stdout
    assert json.loads(r.stdout)["count"] == 0      # свежий lease не протух
    r = runner.invoke(app, ["stale", "--reap"])
    assert r.exit_code == 0
    assert json.loads(r.stdout)["count"] == 0
