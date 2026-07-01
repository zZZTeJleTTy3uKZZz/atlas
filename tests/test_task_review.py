"""Review-workflow + комментарии: гейт reviewer, submit/approve/reject/reopen, передача."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from atlas import lease as L
from atlas import task_review as TR
from atlas import task_status as TS
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

runner = CliRunner()


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'review.db'}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    with make_session(eng) as s:
        seed_all(s)
        s.commit()
    return eng


def _project(session, slug="rev") -> Project:
    pt = session.execute(select(ProjectType)).scalars().first()
    ps = session.execute(select(ProjectStatus)).scalars().first()
    p = Project(slug=slug, name=slug.title(), type_id=pt.id, status_id=ps.id,
                priority="P2", one_line_summary="x", prefix=slug[:3].upper())
    session.add(p); session.flush()
    return p


def _task(session, *, status="todo", reviewer_id=None) -> Task:
    p = session.execute(select(Project)).scalars().first() or _project(session)
    t = Task(project_id=p.id, title="T", cpp_description="c", priority="P2",
             status=status, reviewer_id=reviewer_id)
    session.add(t); session.flush()
    return t


def _actor(session, slug) -> Participant:
    return session.execute(select(Participant).where(Participant.slug == slug)).scalar_one()


# --------------------------------------------------------------------------- #
# домен                                                                        #
# --------------------------------------------------------------------------- #


def test_finish_gate_blocks_non_reviewer(engine):
    with make_session(engine) as s:
        owner, ex = _actor(s, "owner"), _actor(s, "claude-code")
        t = _task(s, reviewer_id=owner.id)
        L.claim_task(s, t, ex)
        s.commit()
        with pytest.raises(TS.ReviewGateError):
            TS.finish_task(s, t, ex)             # исполнитель ≠ reviewer
        TS.finish_task(s, t, owner, force=True)  # reviewer (force мимо чужого lease)
        assert t.status == "done"


def test_finish_no_reviewer_no_gate(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        t = _task(s)  # reviewer_id None
        L.claim_task(s, t, owner)
        s.commit()
        TS.finish_task(s, t, owner)
        assert t.status == "done"


def test_submit_releases_lease_then_reviewer_approves(engine):
    with make_session(engine) as s:
        owner, ex = _actor(s, "owner"), _actor(s, "claude-code")
        t = _task(s, reviewer_id=owner.id)
        L.claim_task(s, t, ex)
        s.commit()
        TR.submit_task(s, t, ex, comment="сделал X, зарелизил Y")
        assert t.status == "review"
        assert t.lease_owner is None             # handoff снял lease
        with pytest.raises(TS.ReviewGateError):
            TR.approve_task(s, t, ex)            # исполнитель закрыть не может
        TR.approve_task(s, t, owner, comment="принято")
        assert t.status == "done"
        kinds = [c.kind for c in TR.list_comments(s, t)]
        assert "submit" in kinds and "approve" in kinds


def test_reject_returns_to_work_with_reason(engine):
    with make_session(engine) as s:
        owner, ex = _actor(s, "owner"), _actor(s, "claude-code")
        t = _task(s, reviewer_id=owner.id)
        L.claim_task(s, t, ex)
        TR.submit_task(s, t, ex)
        s.commit()
        with pytest.raises(TS.ReviewGateError):
            TR.reject_task(s, t, ex, comment="x")   # не reviewer
        TR.reject_task(s, t, owner, comment="не покрыт кейс Z")
        assert t.status == "in_progress"
        assert any(c.kind == "reject" for c in TR.list_comments(s, t))


def test_reopen_reviewer_gated(engine):
    with make_session(engine) as s:
        owner, ex = _actor(s, "owner"), _actor(s, "claude-code")
        t = _task(s, status="done", reviewer_id=owner.id)
        s.commit()
        with pytest.raises(TS.ReviewGateError):
            TR.reopen_task(s, t, ex)
        TR.reopen_task(s, t, owner, comment="нашёл баг, переоткрыл")
        assert t.status == "todo"
        assert t.completed_at is None


def test_comments_chronological(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        t = _task(s)
        TR.add_comment(s, t, owner, "первый")
        TR.add_comment(s, t, owner, "второй")
        s.commit()
        assert [c.body for c in TR.list_comments(s, t)] == ["первый", "второй"]


# --------------------------------------------------------------------------- #
# CLI: полный мультиагентный сценарий приёмки                                  #
# --------------------------------------------------------------------------- #


def test_cli_full_review_scenario(engine):
    from atlas.cli import app

    with make_session(engine) as s:
        _project(s, "rev")
        s.commit()

    r = runner.invoke(app, ["task", "add", "--project", "rev", "--title", "Фича",
                            "--cpp", "работает", "--reviewer", "owner"])
    assert r.exit_code == 0, r.stdout
    num = str(json.loads(r.stdout)["number"])

    # agent2 (claude-code) берёт и пытается закрыть — гейт не пускает
    assert runner.invoke(app, ["task", "start", num, "--actor", "claude-code"]).exit_code == 0
    assert runner.invoke(app, ["task", "done", num, "--actor", "claude-code"]).exit_code != 0

    # сдаёт на проверку
    assert runner.invoke(app, ["task", "submit", num, "--actor", "claude-code",
                               "-m", "сделал, зарелизил"]).exit_code == 0
    # reviewer возвращает
    assert runner.invoke(app, ["task", "reject", num, "--actor", "owner",
                               "-m", "доработать кейс"]).exit_code == 0
    # доделал, сдал снова
    runner.invoke(app, ["task", "start", num, "--actor", "claude-code"])
    runner.invoke(app, ["task", "submit", num, "--actor", "claude-code", "-m", "доделал"])
    # reviewer одобряет
    ra = runner.invoke(app, ["task", "approve", num, "--actor", "owner", "-m", "ок"])
    assert ra.exit_code == 0, ra.stdout
    assert json.loads(ra.stdout)["status"] == "done"

    # вся история комментов на месте (передача контекста)
    rc = runner.invoke(app, ["task", "comments", num])
    kinds = [c["kind"] for c in json.loads(rc.stdout)]
    assert "submit" in kinds and "reject" in kinds and "approve" in kinds

    # task get отдаёт reviewer + комменты
    rg = runner.invoke(app, ["task", "get", num])
    card = json.loads(rg.stdout)
    assert card["reviewer"] == "owner"
    assert len(card["comments"]) >= 3
