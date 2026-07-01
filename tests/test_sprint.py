"""Тесты спринтов: домен (velocity/переходы/assign) + CLI (add/start/close/assign)."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from atlas import sprint as S
from atlas.db import make_engine, make_session
from atlas.models import (
    Base,
    Project,
    ProjectStatus,
    ProjectType,
    Sprint,
    Task,
)
from atlas.seeds import seed_all

runner = CliRunner()


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'sprint.db'}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    eng = make_engine(url)
    Base.metadata.create_all(eng)  # включает sprints + tasks.sprint_id
    with make_session(eng) as s:
        seed_all(s)
        s.commit()
    return eng


def _project(session, slug="alpha") -> Project:
    pt = session.execute(select(ProjectType)).scalars().first()
    ps = session.execute(select(ProjectStatus)).scalars().first()
    p = Project(slug=slug, name=slug.title(), type_id=pt.id, status_id=ps.id,
                priority="P2", one_line_summary="x", prefix=slug[:3].upper())
    session.add(p); session.flush()
    return p


def _sprint(session, proj, **kw) -> Sprint:
    sp = Sprint(slug=kw.get("slug", "s1"), project_id=proj.id, name=kw.get("name", "Sprint 1"),
                status=kw.get("status", "planning"), planned_velocity=kw.get("planned_velocity"))
    session.add(sp); session.flush()
    return sp


def _task(session, proj, *, status="todo", points=None, sprint_id=None, slug=None) -> Task:
    t = Task(project_id=proj.id, title="T", cpp_description="c", priority="P2",
             status=status, story_points=points, sprint_id=sprint_id, slug=slug)
    session.add(t); session.flush()
    return t


# --------------------------------------------------------------------------- #
# домен                                                                        #
# --------------------------------------------------------------------------- #


def test_velocity_sums_done_points(engine):
    with make_session(engine) as s:
        p = _project(s)
        sp = _sprint(s, p, planned_velocity=10)
        _task(s, p, status="done", points=3, sprint_id=sp.id)
        _task(s, p, status="done", points=5, sprint_id=sp.id)
        _task(s, p, status="in_progress", points=8, sprint_id=sp.id)  # не done → не в actual
        s.commit()
        v = S.sprint_velocity(s, sp)
        assert v["actual_velocity"] == 8       # 3+5
        assert v["committed_points"] == 16     # 3+5+8
        assert v["done_tasks"] == 2
        assert v["total_tasks"] == 3
        assert v["planned_velocity"] == 10


def test_lifecycle_transitions(engine):
    with make_session(engine) as s:
        p = _project(s)
        sp = _sprint(s, p)
        assert S.start_sprint(s, sp).status == "active"
        assert S.close_sprint(s, sp, retro="ок").status == "closed"
        assert sp.retro_notes == "ок"


def test_start_from_closed_rejected(engine):
    with make_session(engine) as s:
        p = _project(s)
        sp = _sprint(s, p, status="closed")
        with pytest.raises(S.SprintTransitionError):
            S.start_sprint(s, sp)


def test_assign_and_clear(engine):
    with make_session(engine) as s:
        p = _project(s)
        sp = _sprint(s, p)
        t1, t2 = _task(s, p), _task(s, p)
        assert S.assign_tasks(s, sp, [t1, t2]) == 2
        assert t1.sprint_id == sp.id
        assert S.assign_tasks(s, None, [t1]) == 1  # отвязка
        assert t1.sprint_id is None


def test_board_groups_by_status(engine):
    with make_session(engine) as s:
        p = _project(s)
        sp = _sprint(s, p)
        _task(s, p, status="todo", points=2, sprint_id=sp.id)
        _task(s, p, status="done", points=5, sprint_id=sp.id)
        s.commit()
        board = S.sprint_board(s, sp)
        assert len(board["columns"]["todo"]["tasks"]) == 1
        assert board["columns"]["done"]["points"] == 5


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _app():
    from atlas.cli import app
    return app


def test_cli_add_start_close_velocity(engine):
    app = _app()
    # проект для спринта
    with make_session(engine) as s:
        _project(s, "beta"); s.commit()
    r = runner.invoke(app, ["sprint", "add", "--project", "beta", "--name", "Sprint 1",
                            "--planned-velocity", "10", "--slug", "spr1"])
    assert r.exit_code == 0, r.stdout
    assert json.loads(r.stdout)["status"] == "planning"
    assert runner.invoke(app, ["sprint", "start", "spr1"]).exit_code == 0
    assert json.loads(runner.invoke(app, ["sprint", "get", "spr1"]).stdout)["status"] == "active"
    rc = runner.invoke(app, ["sprint", "close", "spr1", "--retro", "хорошо"])
    assert rc.exit_code == 0
    assert json.loads(rc.stdout)["status"] == "closed"
    # velocity-список закрытых
    rv = runner.invoke(app, ["sprint", "velocity", "--project", "beta"])
    assert rv.exit_code == 0
    assert any(sp["sprint"] == "spr1" for sp in json.loads(rv.stdout)["sprints"])


def test_cli_assign_and_task_filter(engine):
    app = _app()
    with make_session(engine) as s:
        p = _project(s, "gamma")
        sp = _sprint(s, p, slug="gspr")
        _task(s, p, slug="gt1")  # резолв по slug (number в обход CLI не присваивается)
        s.commit()
    r = runner.invoke(app, ["sprint", "assign", "gspr", "gt1"])
    assert r.exit_code == 0, r.stdout
    assert json.loads(r.stdout)["changed"] == 1
    # task list --sprint фильтр
    rl = runner.invoke(app, ["task", "list", "--sprint", "gspr"])
    assert rl.exit_code == 0, rl.stdout
    assert len(json.loads(rl.stdout)) == 1
