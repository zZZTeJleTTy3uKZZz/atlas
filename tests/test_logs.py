"""Тесты обогащённого журнала (atlas.logs.build_logs + CLI atlas logs)."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from atlas.db import make_engine, make_session
from atlas.logs import build_logs
from atlas.models import (
    ActionLog,
    Base,
    Participant,
    Project,
    ProjectStatus,
    ProjectType,
    Task,
)
from atlas.seeds import seed_all

runner = CliRunner()


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'logs.db'}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    eng = make_engine(url)
    Base.metadata.create_all(eng)
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


def _task(session, proj, *, title="Сверстать лендинг", priority="P1") -> Task:
    t = Task(project_id=proj.id, title=title, cpp_description="c", priority=priority,
             status="todo", slug="t1")
    session.add(t); session.flush()
    return t


def _log(session, actor, action, task):
    session.add(ActionLog(actor_id=actor.id, entity_type="task", entity_id=task.id, action=action))
    session.flush()


def test_build_logs_enriches_task_event(engine):
    with make_session(engine) as s:
        owner = s.execute(select(Participant).where(Participant.slug == "owner")).scalar_one()
        p = _project(s)
        t = _task(s, p, title="Сверстать лендинг", priority="P1")
        _log(s, owner, "task_created", t)
        s.commit()
        rows = build_logs(s, limit=10)
        assert len(rows) == 1
        r = rows[0]
        assert r["actor"] == "owner"
        assert r["action"] == "task_created"
        assert r["title"] == "Сверстать лендинг"   # заголовок, а не голый id
        assert r["project"] == "alpha"             # проект резолвится
        assert r["priority"] == "P1"               # приоритет


def test_build_logs_filter_by_project(engine):
    with make_session(engine) as s:
        owner = s.execute(select(Participant).where(Participant.slug == "owner")).scalar_one()
        a = _project(s, "alpha")
        b = _project(s, "beta")
        ta = _task(s, a)
        tb = Task(project_id=b.id, title="B", cpp_description="c", priority="P2",
                  status="todo", slug="t2")
        s.add(tb); s.flush()
        _log(s, owner, "task_created", ta)
        _log(s, owner, "task_done", tb)
        s.commit()
        rows = build_logs(s, project_ref="alpha")
        assert all(r["project"] == "alpha" for r in rows)
        assert len(rows) == 1


def test_build_logs_filter_by_action(engine):
    with make_session(engine) as s:
        owner = s.execute(select(Participant).where(Participant.slug == "owner")).scalar_one()
        p = _project(s)
        t = _task(s, p)
        _log(s, owner, "task_created", t)
        _log(s, owner, "task_done", t)
        s.commit()
        rows = build_logs(s, action="task_done")
        assert len(rows) == 1 and rows[0]["action"] == "task_done"


def test_cli_logs_json(engine):
    from atlas.cli import app

    with make_session(engine) as s:
        owner = s.execute(select(Participant).where(Participant.slug == "owner")).scalar_one()
        p = _project(s)
        t = _task(s, p)
        _log(s, owner, "task_created", t)
        s.commit()
    res = runner.invoke(app, ["logs", "--json"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert data["count"] == 1
    assert data["logs"][0]["title"] == "Сверстать лендинг"
