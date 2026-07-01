"""CLI-тесты группового lease: `atlas task claim a b c` + `atlas epic claim/release`."""
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
    """clikit держит режим вывода в module-global ``_mode``; другие CLI-тесты
    форсят ``--text`` и не возвращают его → наши json.loads падают при общем
    прогоне. Сбрасываем на json перед каждым тестом (изоляция от утечки)."""
    from clikit import output as _out

    _out._mode = "json"
    yield


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def task_app():
    import atlas.commands.task_lease  # noqa: F401  (регистрирует lease-команды)
    from atlas.commands.task import task_app

    return task_app


@pytest.fixture()
def epic_app():
    import atlas.commands.task_lease  # noqa: F401  (регистрирует epic claim/release)
    from atlas.commands.epic import epic_app

    return epic_app


@pytest.fixture()
def projects_app():
    from atlas.commands.projects import projects_app

    return projects_app


@pytest.fixture()
def setup(runner, task_app, epic_app, projects_app, seeded_engine):
    """Проект + эпик + 3 задачи (две в эпике, одна вне). Вернуть refs."""
    r = runner.invoke(
        projects_app,
        ["add", "--name", "Cifro", "--type", "client-project",
         "--slug", "cifro", "--prefix", "cf"],
    )
    assert r.exit_code == 0, r.stdout
    r = runner.invoke(epic_app, ["add", "--project", "cifro", "--title", "E1",
                                 "--slug", "e1"])
    assert r.exit_code == 0, r.stdout
    for i in range(1, 4):
        r = runner.invoke(
            task_app, ["add", "--project", "cifro", "--title", f"T{i}", "--cpp", "ЦКП"]
        )
        assert r.exit_code == 0, r.stdout

    from atlas.db import make_session
    from atlas.models import Epic, Task

    with make_session(seeded_engine) as s:
        epic = s.execute(select(Epic)).scalars().first()
        tasks = s.execute(select(Task).order_by(Task.number)).scalars().all()
        # привязать t1,t2 к эпику
        tasks[0].epic_id = epic.id
        tasks[1].epic_id = epic.id
        s.commit()
        return {
            "epic": epic.slug,
            "t1": str(tasks[0].number),
            "t2": str(tasks[1].number),
            "t3": str(tasks[2].number),
        }


# --------------------------------------------------------------------------- #
# multi-claim (#193)                                                          #
# --------------------------------------------------------------------------- #


def test_multi_claim_all_free(runner, task_app, setup):
    r = runner.invoke(task_app, ["claim", setup["t1"], setup["t2"], setup["t3"],
                                 "--actor", "claude-code"])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    assert len(d["claimed"]) == 3
    assert d["skipped"] == []


def test_single_claim_still_works(runner, task_app, setup):
    r = runner.invoke(task_app, ["claim", setup["t3"], "--actor", "claude-code"])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    assert d["status"] == "in_progress"
    assert d["lease_owner"] == "claude-code"


def test_multi_claim_all_or_nothing_rollback(runner, task_app, setup):
    # owner держит t2
    assert runner.invoke(task_app, ["claim", setup["t2"], "--actor", "owner"]).exit_code == 0
    r = runner.invoke(task_app, ["claim", setup["t1"], setup["t2"], setup["t3"],
                                 "--actor", "claude-code"])
    assert r.exit_code != 0
    # t1 НЕ взят (откат) — claude-code может взять его отдельно
    r2 = runner.invoke(task_app, ["claim", setup["t1"], "--actor", "claude-code"])
    assert r2.exit_code == 0, r2.stdout


def test_multi_claim_best_effort(runner, task_app, setup):
    assert runner.invoke(task_app, ["claim", setup["t2"], "--actor", "owner"]).exit_code == 0
    r = runner.invoke(task_app, ["claim", setup["t1"], setup["t2"], setup["t3"],
                                 "--actor", "claude-code", "--best-effort"])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    assert len(d["claimed"]) == 2
    assert len(d["skipped"]) == 1


# --------------------------------------------------------------------------- #
# epic claim/release (#194)                                                   #
# --------------------------------------------------------------------------- #


def test_epic_claim_cascades(runner, task_app, epic_app, setup):
    r = runner.invoke(epic_app, ["claim", setup["epic"], "--actor", "claude-code"])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    assert d["lease_owner"] == "claude-code"
    # задачи эпика залочены
    g1 = json.loads(runner.invoke(task_app, ["get", setup["t1"]]).stdout)
    g2 = json.loads(runner.invoke(task_app, ["get", setup["t2"]]).stdout)
    assert g1["lease_owner"] == "claude-code"
    assert g2["lease_owner"] == "claude-code"


def test_epic_release_cascades(runner, task_app, epic_app, setup):
    runner.invoke(epic_app, ["claim", setup["epic"], "--actor", "claude-code"])
    r = runner.invoke(epic_app, ["release", setup["epic"], "--actor", "claude-code"])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    assert d["lease_owner"] is None
    g1 = json.loads(runner.invoke(task_app, ["get", setup["t1"]]).stdout)
    assert g1["lease_owner"] is None


def test_epic_claim_rejected_when_task_held_by_other(runner, task_app, epic_app, setup):
    # owner держит t1 (задача эпика)
    runner.invoke(task_app, ["claim", setup["t1"], "--actor", "owner"])
    r = runner.invoke(epic_app, ["claim", setup["epic"], "--actor", "claude-code"])
    assert r.exit_code != 0


def test_task_claim_rejected_when_epic_held_by_other(runner, task_app, epic_app, setup):
    runner.invoke(epic_app, ["claim", setup["epic"], "--actor", "claude-code"])
    # owner пытается взять отдельную задачу эпика
    r = runner.invoke(task_app, ["claim", setup["t1"], "--actor", "owner"])
    assert r.exit_code != 0


def test_epic_get_shows_lease(runner, epic_app, setup):
    runner.invoke(epic_app, ["claim", setup["epic"], "--actor", "claude-code"])
    r = runner.invoke(epic_app, ["get", setup["epic"]])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    assert d["lease_owner"] == "claude-code"
    assert d["lease_expires_at"] is not None


def test_epic_list_shows_lease(runner, epic_app, setup):
    runner.invoke(epic_app, ["claim", setup["epic"], "--actor", "claude-code"])
    r = runner.invoke(epic_app, ["list"])
    assert r.exit_code == 0, r.stdout
    rows = json.loads(r.stdout)
    assert rows[0]["lease_owner"] == "claude-code"


def test_stale_report_and_reap_surface_epic(runner, task_app, epic_app, setup, seeded_engine):
    """Регресс (review #3/#4): `task stale` (отчёт без --reap) обязан показывать
    протухший epic-lease — иначе превью расходится с тем, что освободит --reap.

    Берём эпик, backdate'им lease_expires_at в прошлое → и dry-run-отчёт, и
    --reap должны увидеть эпик.
    """
    from datetime import timedelta

    from atlas._time import local_now
    from atlas.db import make_session
    from atlas.models import Epic, Task

    assert runner.invoke(
        epic_app, ["claim", setup["epic"], "--actor", "claude-code"]
    ).exit_code == 0

    # Протухаем lease эпика И его каскадных задач (backdate в прошлое).
    past = local_now() - timedelta(hours=1)
    with make_session(seeded_engine) as s:
        for e in s.execute(select(Epic)).scalars():
            if e.lease_owner is not None:
                e.lease_expires_at = past
        for t in s.execute(select(Task)).scalars():
            if t.lease_owner is not None:
                t.lease_expires_at = past
        s.commit()

    # Dry-run отчёт: эпик ДОЛЖЕН быть среди stale (раньше показывались только задачи).
    r = runner.invoke(task_app, ["stale"])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    entities = {row["entity"] for row in d["stale"]}
    refs = {row["ref"] for row in d["stale"]}
    assert "epic" in entities, d
    assert setup["epic"] in refs, d
    report_count = d["count"]

    # --reap освобождает И задачи, И эпик; число совпадает с превью.
    r = runner.invoke(task_app, ["stale", "--reap"])
    assert r.exit_code == 0, r.stdout
    d2 = json.loads(r.stdout)
    assert d2["count"] == report_count
    with make_session(seeded_engine) as s:
        e = s.execute(select(Epic)).scalars().first()
        assert e.lease_owner is None  # эпик реально освобождён


def test_epic_claim_no_outbox(runner, epic_app, setup, seeded_engine):
    """epic claim — локальная координация: НЕ добавляет записей в outbox.

    (Записи от `epic add`/`task add` в фикстуре допустимы — проверяем дельту.)
    """
    from atlas.db import make_session
    from atlas.models import Outbox

    with make_session(seeded_engine) as s:
        before = len(s.execute(select(Outbox)).scalars().all())
    runner.invoke(epic_app, ["claim", setup["epic"], "--actor", "claude-code"])
    with make_session(seeded_engine) as s:
        after = len(s.execute(select(Outbox)).scalars().all())
    assert after == before
