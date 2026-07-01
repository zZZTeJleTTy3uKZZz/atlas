"""Тесты агрегации дашборда (src/atlas/dashboard.py)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from atlas import lease as L
from atlas import task_status as TS
from atlas._time import local_now
from atlas.dashboard import build_dashboard
from atlas.db import make_engine, make_session
from atlas.models import (
    Base,
    Participant,
    Project,
    ProjectStatus,
    ProjectType,
    Task,
)
from atlas.seeds import seed_all


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'dash.db'}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    with make_session(eng) as s:
        seed_all(s)
        s.commit()
    return eng


def _project(session, slug: str, status_slug: str = "active") -> Project:
    pt = session.execute(select(ProjectType)).scalars().first()
    ps = session.execute(
        select(ProjectStatus).where(ProjectStatus.slug == status_slug)
    ).scalar_one_or_none() or session.execute(select(ProjectStatus)).scalars().first()
    p = Project(slug=slug, name=slug.title(), type_id=pt.id, status_id=ps.id,
                priority="P2", one_line_summary="x", prefix=slug[:3].upper())
    session.add(p)
    session.flush()
    return p


def _task(session, proj, *, status="todo", priority="P2", due=None) -> Task:
    t = Task(project_id=proj.id, title=f"T-{status}", cpp_description="c",
             priority=priority, status=status, due_date=due)
    session.add(t)
    session.flush()
    return t


def test_dashboard_status_and_priority_distribution(engine):
    with make_session(engine) as s:
        p = _project(s, "alpha")
        _task(s, p, status="review", priority="P1")
        _task(s, p, status="todo", priority="P2")
        _task(s, p, status="done", priority="P3")
        s.commit()
        d = build_dashboard(s)
        assert d["scope"] == "portfolio"
        assert d["tasks"]["by_status"]["review"] == 1
        assert d["tasks"]["by_status"]["todo"] == 1
        assert d["tasks"]["by_status"]["done"] == 1
        # open = не терминальные (review+todo), done не считается
        assert d["tasks"]["open"] == 2
        assert d["tasks"]["by_priority"]["P1"] == 1
        assert d["tasks"]["by_priority"]["P3"] == 0  # done не в открытых приоритетах


def test_dashboard_in_progress_and_lease(engine):
    with make_session(engine) as s:
        owner = s.execute(select(Participant).where(Participant.slug == "owner")).scalar_one()
        p = _project(s, "beta")
        t = _task(s, p, status="todo")
        L.claim_task(s, t, owner)  # → in_progress + lease
        s.commit()
        d = build_dashboard(s)
        assert len(d["tasks"]["in_progress"]) == 1
        row = d["tasks"]["in_progress"][0]
        assert row["lease_owner"] == "owner"
        assert row["project"] == "beta"
        assert d["leases"]["active"] == 1


def test_dashboard_blocked_and_overdue(engine):
    now = local_now()
    with make_session(engine) as s:
        owner = s.execute(select(Participant).where(Participant.slug == "owner")).scalar_one()
        p = _project(s, "gamma")
        tb = _task(s, p, status="todo")
        L.claim_task(s, tb, owner)
        TS.block_task(s, tb, owner, reason="ждём ключ")
        _task(s, p, status="todo", due=now - timedelta(days=2))  # просрочена
        s.commit()
        d = build_dashboard(s)
        assert len(d["tasks"]["blocked"]) == 1
        assert len(d["tasks"]["overdue"]) == 1
        assert d["tasks"]["overdue"][0]["overdue"] is True


def test_dashboard_by_project_and_filter(engine):
    with make_session(engine) as s:
        a = _project(s, "alpha")
        b = _project(s, "beta")
        _task(s, a, status="todo")
        _task(s, a, status="review")
        _task(s, b, status="todo")
        s.commit()
        d = build_dashboard(s)
        bp = {r["project"]: r for r in d["by_project"]}
        assert bp["alpha"]["open"] == 2
        assert bp["beta"]["open"] == 1
        # фильтр по проекту
        d2 = build_dashboard(s, project_ref="alpha")
        assert d2["scope"] == "alpha"
        assert d2["tasks"]["open"] == 2


def test_dashboard_unknown_project_raises(engine):
    with make_session(engine) as s:
        with pytest.raises(ValueError):
            build_dashboard(s, project_ref="no-such-project")
