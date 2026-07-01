"""Тесты машины состояний задачи (src/atlas/task_status.py).

Покрытие: finish/cancel/review/block/unblock — happy-переходы, идемпотентность,
машина состояний (невалидный from → TransitionError), уважение чужого lease
(LeaseHeldError без --force, успех с force), release lease на done/cancel,
сохранение lease на review/block, unblock только держателем.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from atlas import lease as L
from atlas import task_status as TS
from atlas.db import make_engine, make_session
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


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'status.db'}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    with make_session(eng) as s:
        seed_all(s)
        s.commit()
    return eng


def _make_task(session, status: str = "todo") -> Task:
    pt = session.execute(select(ProjectType)).scalars().first()
    ps = session.execute(select(ProjectStatus)).scalars().first()
    proj = Project(
        slug="p1", name="P1", type_id=pt.id, status_id=ps.id,
        priority="P2", one_line_summary="x",
    )
    session.add(proj)
    session.flush()
    task = Task(
        project_id=proj.id, title="T", cpp_description="cpp",
        priority="P2", status=status,
    )
    session.add(task)
    session.commit()
    return task


def _actor(session, slug: str) -> Participant:
    return session.execute(
        select(Participant).where(Participant.slug == slug)
    ).scalar_one()


def _actions(session, task_id: str) -> list[str]:
    return list(
        session.execute(
            select(ActionLog.action).where(
                ActionLog.entity_type == "task", ActionLog.entity_id == task_id
            )
        ).scalars().all()
    )


# --------------------------------------------------------------------------- #
# finish (→ done)                                                             #
# --------------------------------------------------------------------------- #


def test_finish_from_in_progress_releases_lease(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        task = _make_task(s, "todo")
        L.claim_task(s, task, owner)  # → in_progress + lease
        s.commit()
        TS.finish_task(s, task, owner)
        s.commit()
        assert task.status == "done"
        assert task.completed_at is not None
        assert task.started_at is not None
        assert task.lease_owner is None  # lease снят
        assert "task_done" in _actions(s, task.id)


def test_finish_is_idempotent(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        task = _make_task(s, "todo")
        L.claim_task(s, task, owner)
        TS.finish_task(s, task, owner)
        s.commit()
        first_completed = task.completed_at
        TS.finish_task(s, task, owner)  # повторно — no-op
        assert task.completed_at == first_completed


def test_finish_quick_close_from_todo_sets_started(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        task = _make_task(s, "todo")
        TS.finish_task(s, task, owner)  # быстрое закрытие, минуя in_progress
        s.commit()
        assert task.status == "done"
        assert task.started_at is not None


# --------------------------------------------------------------------------- #
# уважение чужого lease                                                       #
# --------------------------------------------------------------------------- #


def test_finish_others_live_lease_blocked(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        other = _actor(s, "claude-code")
        task = _make_task(s, "todo")
        L.claim_task(s, task, owner)  # держит owner
        s.commit()
        with pytest.raises(L.LeaseHeldError):
            TS.finish_task(s, task, other)  # other не может закрыть чужую


def test_finish_force_overrides_others_lease(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        other = _actor(s, "claude-code")
        task = _make_task(s, "todo")
        L.claim_task(s, task, owner)
        s.commit()
        TS.finish_task(s, task, other, force=True)  # --force отбирает
        s.commit()
        assert task.status == "done"
        assert task.lease_owner is None


# --------------------------------------------------------------------------- #
# cancel                                                                      #
# --------------------------------------------------------------------------- #


def test_cancel_releases_lease_no_completed(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        task = _make_task(s, "todo")
        L.claim_task(s, task, owner)
        s.commit()
        TS.cancel_task(s, task, owner)
        s.commit()
        assert task.status == "cancelled"
        assert task.lease_owner is None
        assert task.completed_at is None  # отмена ≠ завершение
        assert "task_cancelled" in _actions(s, task.id)


# --------------------------------------------------------------------------- #
# review / block / unblock                                                    #
# --------------------------------------------------------------------------- #


def test_review_keeps_lease(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        task = _make_task(s, "todo")
        L.claim_task(s, task, owner)  # in_progress
        s.commit()
        TS.review_task(s, task, owner)
        s.commit()
        assert task.status == "review"
        assert task.lease_owner == owner.id  # lease СОХРАНЁН


def test_review_from_todo_rejected(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        task = _make_task(s, "todo")
        with pytest.raises(TS.TransitionError):
            TS.review_task(s, task, owner)


def test_block_keeps_lease_logs_reason(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        task = _make_task(s, "todo")
        L.claim_task(s, task, owner)
        s.commit()
        TS.block_task(s, task, owner, reason="ждём API-ключ")
        s.commit()
        assert task.status == "blocked"
        assert task.lease_owner == owner.id
        assert "task_blocked" in _actions(s, task.id)


def test_unblock_by_holder(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        task = _make_task(s, "todo")
        L.claim_task(s, task, owner)
        TS.block_task(s, task, owner)
        s.commit()
        TS.unblock_task(s, task, owner)
        s.commit()
        assert task.status == "in_progress"


def test_unblock_requires_lease(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        other = _actor(s, "claude-code")
        task = _make_task(s, "todo")
        L.claim_task(s, task, owner)
        TS.block_task(s, task, owner)
        s.commit()
        with pytest.raises(L.LeaseNotOwnedError):
            TS.unblock_task(s, task, other)  # не держатель


def test_unblock_wrong_status(engine):
    with make_session(engine) as s:
        owner = _actor(s, "owner")
        task = _make_task(s, "todo")
        L.claim_task(s, task, owner)  # in_progress, не blocked
        s.commit()
        with pytest.raises(TS.TransitionError):
            TS.unblock_task(s, task, owner)
