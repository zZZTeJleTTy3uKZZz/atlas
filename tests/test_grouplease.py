"""Тесты pure-logic группового lease (эпик «Групповой lease»).

Покрытие:
- claim_epic: каскадит на незавершённые задачи; завершённые не трогает;
  идемпотентен для того же actor; guard (задача занята другим → LeaseHeldError).
- release_epic: каскадит release незавершённых задач держателя; только держатель.
- claim_tasks (multi): все свободны; одна занята + all-or-nothing → откат;
  best-effort → свободные взяты, занятая в skipped.
- guard мьютекса #195: claim_task отдельной задачи, чей эпик взят другим →
  LeaseHeldError. claim своей задачи при своём же epic-lease — ok.
- expire_stale_leases: протухший epic-lease реапается.
- claim_epic НЕ пишет в outbox.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import attributes

from atlas import lease as L
from atlas._time import local_now
from atlas.db import make_engine, make_session
from atlas.models import (
    ActionLog,
    Base,
    Epic,
    Outbox,
    Participant,
    Project,
    ProjectStatus,
    ProjectType,
    Task,
)
from atlas.seeds import seed_all


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'grouplease.db'}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    with make_session(eng) as s:
        seed_all(s)
        s.commit()
    return eng


def _project(session) -> Project:
    pt = session.execute(select(ProjectType)).scalars().first()
    ps = session.execute(select(ProjectStatus)).scalars().first()
    proj = Project(
        slug="p1", name="P1", type_id=pt.id, status_id=ps.id,
        priority="P2", one_line_summary="x",
    )
    session.add(proj)
    session.flush()
    return proj


def _epic(session, proj) -> Epic:
    e = Epic(project_id=proj.id, title="E1", slug="e1", status="active")
    session.add(e)
    session.flush()
    return e


def _task(session, proj, *, epic=None, status="todo", slug=None) -> Task:
    t = Task(
        project_id=proj.id, title="T", cpp_description="cpp",
        priority="P2", status=status, slug=slug,
        epic_id=epic.id if epic else None,
    )
    session.add(t)
    session.flush()
    return t


def _actor(session, slug: str) -> Participant:
    return session.execute(
        select(Participant).where(Participant.slug == slug)
    ).scalar_one()


def _actions(session, entity_type, entity_id):
    return session.execute(
        select(ActionLog.action).where(
            ActionLog.entity_type == entity_type,
            ActionLog.entity_id == entity_id,
        )
    ).scalars().all()


# --------------------------------------------------------------------------- #
# claim_epic                                                                  #
# --------------------------------------------------------------------------- #


def test_claim_epic_cascades_unfinished_only(engine):
    with make_session(engine) as s:
        proj = _project(s)
        epic = _epic(s, proj)
        t_todo = _task(s, proj, epic=epic, status="todo", slug="p-t1")
        t_prog = _task(s, proj, epic=epic, status="in_progress", slug="p-t2")
        t_done = _task(s, proj, epic=epic, status="done", slug="p-t3")
        t_cancel = _task(s, proj, epic=epic, status="cancelled", slug="p-t4")
        a = _actor(s, "claude-code")
        s.commit()

        L.claim_epic(s, epic, a, ttl=timedelta(hours=1))
        s.commit()

        assert epic.lease_owner == a.id
        assert t_todo.lease_owner == a.id
        assert t_prog.lease_owner == a.id
        # завершённые НЕ трогаются
        assert t_done.lease_owner is None
        assert t_cancel.lease_owner is None
        assert "epic_claimed" in _actions(s, "epic", epic.id)


def test_claim_epic_idempotent_same_actor(engine):
    with make_session(engine) as s:
        proj = _project(s)
        epic = _epic(s, proj)
        t = _task(s, proj, epic=epic, status="todo", slug="p-t1")
        a = _actor(s, "claude-code")
        s.commit()
        now = local_now()
        L.claim_epic(s, epic, a, ttl=timedelta(hours=1), now=now)
        s.commit()
        # повторно тем же актором — ok, без ошибки
        L.claim_epic(s, epic, a, ttl=timedelta(hours=1), now=now + timedelta(minutes=5))
        s.commit()
        assert epic.lease_owner == a.id
        assert t.lease_owner == a.id


def test_claim_epic_rejected_when_task_held_by_other(engine):
    """#195 guard: задача эпика занята другим живым lease → epic claim LeaseHeldError."""
    with make_session(engine) as s:
        proj = _project(s)
        epic = _epic(s, proj)
        t1 = _task(s, proj, epic=epic, status="todo", slug="p-t1")
        _task(s, proj, epic=epic, status="todo", slug="p-t2")
        a = _actor(s, "claude-code")
        b = _actor(s, "owner")
        s.commit()
        # b держит t1
        L.claim_task(s, t1, b)
        s.commit()
        with pytest.raises(L.LeaseHeldError):
            L.claim_epic(s, epic, a)
        s.rollback()
        # эпик не взят
        s.refresh(epic)
        assert epic.lease_owner is None


def test_claim_epic_cascade_savepoint_survives_stale_task(engine):
    """Регресс (review #1/#7): version-конфликт каскадной задачи НЕ должен
    через полный rollback снести уже зафлашенный epic-lease и сиблингов.

    Подделываем committed lock_version второй задачи каскада → её первый flush
    ловит StaleDataError. Раньше claim_task делал session.rollback() (полный
    откат всей транзакции) — эпик и t1 теряли lease, состояние half-locked.
    Теперь — SAVEPOINT: откатывается только сбойная задача, она перечитывается
    и ретраится; итог консистентен: epic + ОБЕ задачи под actor.
    """
    with make_session(engine) as s:
        proj = _project(s)
        epic = _epic(s, proj)
        t1 = _task(s, proj, epic=epic, status="todo", slug="p-t1")
        t2 = _task(s, proj, epic=epic, status="todo", slug="p-t2")
        a = _actor(s, "claude-code")
        s.commit()
        # Форсим один StaleDataError на t2 в середине каскада.
        attributes.set_committed_value(t2, "lock_version", t2.lock_version + 5)
        res = L.claim_epic(s, epic, a, ttl=timedelta(hours=1))
        s.commit()
        s.refresh(epic); s.refresh(t1); s.refresh(t2)
        assert epic.lease_owner == a.id
        assert t1.lease_owner == a.id          # сиблинг НЕ снесён
        assert t2.lease_owner == a.id          # сбойная задача переретраена
        assert {t.slug for t in res.claimed_tasks} == {"p-t1", "p-t2"}


def test_claim_tasks_all_or_nothing_savepoint_survives_stale(engine):
    """Регресс (review #1): all-or-nothing батч — version-конфликт одной задачи
    не должен снести уже взятые сиблинги (контракт all-locked-or-error)."""
    with make_session(engine) as s:
        proj = _project(s)
        t0 = _task(s, proj, status="todo", slug="p-t0")
        t1 = _task(s, proj, status="todo", slug="p-t1")
        t2 = _task(s, proj, status="todo", slug="p-t2")
        a = _actor(s, "claude-code")
        s.commit()
        attributes.set_committed_value(t1, "lock_version", t1.lock_version + 5)
        res = L.claim_tasks(s, [t0, t1, t2], a, all_or_nothing=True)
        s.commit()
        s.refresh(t0); s.refresh(t1); s.refresh(t2)
        assert t0.lease_owner == a.id
        assert t1.lease_owner == a.id
        assert t2.lease_owner == a.id
        assert {t.slug for t in res.claimed} == {"p-t0", "p-t1", "p-t2"}


def test_claim_epic_no_outbox(engine):
    with make_session(engine) as s:
        proj = _project(s)
        epic = _epic(s, proj)
        _task(s, proj, epic=epic, status="todo", slug="p-t1")
        a = _actor(s, "claude-code")
        s.commit()
        L.claim_epic(s, epic, a)
        s.commit()
        assert s.execute(select(Outbox)).scalars().all() == []


# --------------------------------------------------------------------------- #
# release_epic                                                                #
# --------------------------------------------------------------------------- #


def test_release_epic_cascades(engine):
    with make_session(engine) as s:
        proj = _project(s)
        epic = _epic(s, proj)
        t1 = _task(s, proj, epic=epic, status="todo", slug="p-t1")
        t2 = _task(s, proj, epic=epic, status="in_progress", slug="p-t2")
        a = _actor(s, "claude-code")
        s.commit()
        L.claim_epic(s, epic, a)
        s.commit()
        L.release_epic(s, epic, a)
        s.commit()
        assert epic.lease_owner is None
        assert t1.lease_owner is None
        assert t2.lease_owner is None
        assert "epic_released" in _actions(s, "epic", epic.id)


def test_release_epic_not_owner(engine):
    with make_session(engine) as s:
        proj = _project(s)
        epic = _epic(s, proj)
        _task(s, proj, epic=epic, status="todo", slug="p-t1")
        a = _actor(s, "claude-code")
        b = _actor(s, "owner")
        s.commit()
        L.claim_epic(s, epic, a)
        s.commit()
        with pytest.raises(L.LeaseNotOwnedError):
            L.release_epic(s, epic, b)


# --------------------------------------------------------------------------- #
# claim_tasks (multi)                                                         #
# --------------------------------------------------------------------------- #


def test_claim_tasks_all_free(engine):
    with make_session(engine) as s:
        proj = _project(s)
        t1 = _task(s, proj, status="todo", slug="p-t1")
        t2 = _task(s, proj, status="todo", slug="p-t2")
        t3 = _task(s, proj, status="todo", slug="p-t3")
        a = _actor(s, "claude-code")
        s.commit()
        res = L.claim_tasks(s, [t1, t2, t3], a)
        s.commit()
        assert {t.id for t in res.claimed} == {t1.id, t2.id, t3.id}
        assert res.skipped == []
        assert t1.lease_owner == a.id
        assert t2.lease_owner == a.id
        assert t3.lease_owner == a.id


def test_claim_tasks_all_or_nothing_rollback(engine):
    with make_session(engine) as s:
        proj = _project(s)
        t1 = _task(s, proj, status="todo", slug="p-t1")
        t2 = _task(s, proj, status="todo", slug="p-t2")
        a = _actor(s, "claude-code")
        b = _actor(s, "owner")
        s.commit()
        # b держит t2
        L.claim_task(s, t2, b)
        s.commit()
        with pytest.raises(L.LeaseHeldError):
            L.claim_tasks(s, [t1, t2], a, all_or_nothing=True)
        s.rollback()
        s.refresh(t1)
        # НИ ОДНА не взята: t1 остался свободен
        assert t1.lease_owner is None


def test_claim_tasks_best_effort(engine):
    with make_session(engine) as s:
        proj = _project(s)
        t1 = _task(s, proj, status="todo", slug="p-t1")
        t2 = _task(s, proj, status="todo", slug="p-t2")
        t3 = _task(s, proj, status="todo", slug="p-t3")
        a = _actor(s, "claude-code")
        b = _actor(s, "owner")
        s.commit()
        L.claim_task(s, t2, b)
        s.commit()
        res = L.claim_tasks(s, [t1, t2, t3], a, all_or_nothing=False)
        s.commit()
        assert {t.id for t in res.claimed} == {t1.id, t3.id}
        assert [t.id for t in res.skipped] == [t2.id]
        assert t1.lease_owner == a.id
        assert t3.lease_owner == a.id
        assert t2.lease_owner == b.id


# --------------------------------------------------------------------------- #
# guard мьютекса #195 на claim_task (single)                                  #
# --------------------------------------------------------------------------- #


def test_claim_task_rejected_when_epic_held_by_other(engine):
    with make_session(engine) as s:
        proj = _project(s)
        epic = _epic(s, proj)
        t = _task(s, proj, epic=epic, status="todo", slug="p-t1")
        a = _actor(s, "claude-code")
        b = _actor(s, "owner")
        s.commit()
        # a держит epic (каскадит на t)
        L.claim_epic(s, epic, a)
        s.commit()
        # b пытается взять отдельную задачу эпика → отклонено
        with pytest.raises(L.LeaseHeldError):
            L.claim_task(s, t, b)


def test_claim_task_ok_when_epic_held_by_self(engine):
    with make_session(engine) as s:
        proj = _project(s)
        epic = _epic(s, proj)
        t = _task(s, proj, epic=epic, status="todo", slug="p-t1")
        a = _actor(s, "claude-code")
        s.commit()
        L.claim_epic(s, epic, a)
        s.commit()
        # тот же actor берёт задачу — ok (идемпотентно)
        L.claim_task(s, t, a)
        s.commit()
        assert t.lease_owner == a.id


def test_claim_task_ok_when_no_epic(engine):
    """Регресс: задача без эпика — guard не мешает."""
    with make_session(engine) as s:
        proj = _project(s)
        t = _task(s, proj, status="todo", slug="p-t1")
        a = _actor(s, "claude-code")
        s.commit()
        L.claim_task(s, t, a)
        s.commit()
        assert t.lease_owner == a.id


def test_claim_task_ok_when_epic_free(engine):
    """Регресс: эпик существует, но свободен — claim_task проходит."""
    with make_session(engine) as s:
        proj = _project(s)
        epic = _epic(s, proj)
        t = _task(s, proj, epic=epic, status="todo", slug="p-t1")
        a = _actor(s, "claude-code")
        s.commit()
        L.claim_task(s, t, a)
        s.commit()
        assert t.lease_owner == a.id
        assert epic.lease_owner is None  # epic не лочится одиночным claim_task


# --------------------------------------------------------------------------- #
# expire stale epics                                                          #
# --------------------------------------------------------------------------- #


def test_expire_stale_epic_lease(engine):
    with make_session(engine) as s:
        proj = _project(s)
        epic = _epic(s, proj)
        _task(s, proj, epic=epic, status="todo", slug="p-t1")
        a = _actor(s, "claude-code")
        s.commit()
        t0 = local_now()
        L.claim_epic(s, epic, a, ttl=timedelta(minutes=10), now=t0)
        s.commit()
        freed = L.expire_stale_leases(s, now=t0 + timedelta(minutes=20))
        s.commit()
        assert epic.lease_owner is None
        assert epic.id in [getattr(x, "id", None) for x in freed]
