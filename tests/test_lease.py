"""Тесты pure-logic lease-движка (src/atlas/pm/lease.py).

Покрытие: parse_ttl, _lease_is_free, claim (happy/held/idempotent/expired/race),
release, renew, take --force, expire_stale_leases, ActionLog-события.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from atlas import lease as L
from atlas._time import local_now
from atlas.db import make_engine, make_session
from atlas.models import (
    ActionLog,
    Base,
    Outbox,
    Participant,
    Project,
    ProjectStatus,
    ProjectType,
    Task,
)
from atlas.seeds import seed_all


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'lease.db'}"
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
    rows = session.execute(
        select(ActionLog.action).where(
            ActionLog.entity_type == "task", ActionLog.entity_id == task_id
        )
    ).scalars().all()
    return list(rows)


# --------------------------------------------------------------------------- #
# parse_ttl                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2h", timedelta(hours=2)),
        ("30m", timedelta(minutes=30)),
        ("90s", timedelta(seconds=90)),
        ("1d", timedelta(days=1)),
        (" 3H ", timedelta(hours=3)),
    ],
)
def test_parse_ttl_ok(raw, expected):
    assert L.parse_ttl(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "10", "2hh", "h2", "-5m"])
def test_parse_ttl_invalid(raw):
    with pytest.raises(ValueError):
        L.parse_ttl(raw)


# --------------------------------------------------------------------------- #
# _lease_is_free                                                              #
# --------------------------------------------------------------------------- #


def test_lease_is_free_transitions(engine):
    with make_session(engine) as s:
        t = _make_task(s)
        a = _actor(s, "claude-code")
        b = _actor(s, "dmitry")
        now = local_now()
        assert L._lease_is_free(t, a.id, now) is True
        L.claim_task(s, t, a, ttl=timedelta(hours=1), now=now)
        s.commit()
        assert L._lease_is_free(t, b.id, now) is False          # занято A
        assert L._lease_is_free(t, a.id, now) is True           # мой
        later = now + timedelta(hours=2)
        assert L._lease_is_free(t, b.id, later) is True         # протух → свободен B


# --------------------------------------------------------------------------- #
# claim                                                                       #
# --------------------------------------------------------------------------- #


def test_claim_happy(engine):
    with make_session(engine) as s:
        t = _make_task(s)
        a = _actor(s, "claude-code")
        now = local_now()
        res = L.claim_task(
            s, t, a, session_id="sess-1", origin="atlas",
            ttl=timedelta(hours=2), now=now,
        )
        s.commit()
        assert t.status == "in_progress"
        assert t.assignee_id == a.id
        assert t.lease_owner == a.id
        assert t.lease_session_id == "sess-1"
        assert t.lease_origin == "atlas"
        assert t.started_at is not None
        assert t.claimed_at == now
        assert t.lease_expires_at == now + timedelta(hours=2)
        assert res.previous_holder is None
        assert "task_claimed" in _actions(s, t.id)


def test_claim_held_by_other(engine):
    with make_session(engine) as s:
        t = _make_task(s)
        a = _actor(s, "claude-code")
        b = _actor(s, "dmitry")
        L.claim_task(s, t, a)
        s.commit()
        with pytest.raises(L.LeaseHeldError) as ei:
            L.claim_task(s, t, b)
        assert ei.value.holder == "claude-code"


def test_claim_idempotent_same_actor(engine):
    with make_session(engine) as s:
        t = _make_task(s)
        a = _actor(s, "claude-code")
        now = local_now()
        L.claim_task(s, t, a, ttl=timedelta(hours=2), now=now)
        s.commit()
        first_claimed = t.claimed_at
        # повторный claim тем же actor (lease жив) → success, claimed_at сохраняется
        res = L.claim_task(s, t, a, ttl=timedelta(hours=2), now=now + timedelta(minutes=5))
        s.commit()
        assert res.previous_holder is None
        assert t.claimed_at == first_claimed
        assert t.lease_owner == a.id


def test_claim_expired_lease_reclaim_by_other(engine):
    with make_session(engine) as s:
        t = _make_task(s)
        a = _actor(s, "claude-code")
        b = _actor(s, "dmitry")
        t0 = local_now()
        L.claim_task(s, t, a, ttl=timedelta(minutes=10), now=t0)
        s.commit()
        res = L.claim_task(s, t, b, now=t0 + timedelta(minutes=20))  # A протух
        s.commit()
        assert t.lease_owner == b.id
        assert res.previous_holder == "claude-code"
        assert _actions(s, t.id).count("task_claimed") == 2


def test_claim_race_two_sessions(engine):
    """Гонка: оба читают v0; первый клеймит, второй ловит StaleData→retry→held."""
    with make_session(engine) as s0:
        t = _make_task(s0)
        tid = t.id
    with make_session(engine) as s1, make_session(engine) as s2:
        t1 = s1.get(Task, tid)
        t2 = s2.get(Task, tid)
        a = _actor(s1, "claude-code")
        b = _actor(s2, "dmitry")
        L.claim_task(s1, t1, a)
        s1.commit()
        with pytest.raises(L.LeaseHeldError):
            L.claim_task(s2, t2, b)
            s2.commit()


# --------------------------------------------------------------------------- #
# release / renew                                                             #
# --------------------------------------------------------------------------- #


def test_release_owner(engine):
    with make_session(engine) as s:
        t = _make_task(s)
        a = _actor(s, "claude-code")
        L.claim_task(s, t, a)
        s.commit()
        L.release_task(s, t, a)
        s.commit()
        assert t.lease_owner is None
        assert t.lease_expires_at is None
        assert "task_released" in _actions(s, t.id)


def test_release_not_owner(engine):
    with make_session(engine) as s:
        t = _make_task(s)
        a = _actor(s, "claude-code")
        b = _actor(s, "dmitry")
        L.claim_task(s, t, a)
        s.commit()
        with pytest.raises(L.LeaseNotOwnedError):
            L.release_task(s, t, b)


def test_renew_extends(engine):
    with make_session(engine) as s:
        t = _make_task(s)
        a = _actor(s, "claude-code")
        t0 = local_now()
        L.claim_task(s, t, a, ttl=timedelta(minutes=10), now=t0)
        s.commit()
        L.renew_lease(s, t, a, ttl=timedelta(hours=1), now=t0 + timedelta(minutes=5))
        s.commit()
        assert t.lease_expires_at == t0 + timedelta(minutes=5) + timedelta(hours=1)
        assert "lease_renewed" in _actions(s, t.id)


def test_renew_not_owner(engine):
    with make_session(engine) as s:
        t = _make_task(s)
        a = _actor(s, "claude-code")
        b = _actor(s, "dmitry")
        L.claim_task(s, t, a)
        s.commit()
        with pytest.raises(L.LeaseNotOwnedError):
            L.renew_lease(s, t, b)


# --------------------------------------------------------------------------- #
# take --force                                                                #
# --------------------------------------------------------------------------- #


def test_take_force_steals(engine):
    with make_session(engine) as s:
        t = _make_task(s)
        a = _actor(s, "claude-code")
        b = _actor(s, "dmitry")
        L.claim_task(s, t, a)
        s.commit()
        res = L.take_task(s, t, b, session_id="sess-b", origin="other")
        s.commit()
        assert t.lease_owner == b.id
        assert t.lease_session_id == "sess-b"
        assert res.previous_holder == "claude-code"
        assert "task_taken" in _actions(s, t.id)


# --------------------------------------------------------------------------- #
# expire_stale_leases                                                         #
# --------------------------------------------------------------------------- #


def test_claim_does_not_enqueue_outbox(engine):
    """Инвариант: claim — локальная координация, в ядро (outbox) не уходит."""
    with make_session(engine) as s:
        t = _make_task(s)
        a = _actor(s, "claude-code")
        L.claim_task(s, t, a)
        s.commit()
        assert s.execute(select(Outbox)).scalars().all() == []


def test_optimistic_lock_on_plain_update(engine):
    """version_id_col защищает ЛЮБОЙ апдейт задачи, не только lease."""
    from sqlalchemy.orm.exc import StaleDataError

    with make_session(engine) as s0:
        t = _make_task(s0)
        tid = t.id
    with make_session(engine) as s1, make_session(engine) as s2:
        t1 = s1.get(Task, tid)
        t2 = s2.get(Task, tid)
        t1.title = "changed-by-1"
        s1.commit()
        t2.title = "changed-by-2"
        with pytest.raises(StaleDataError):
            s2.commit()


def test_expire_stale_frees_only_expired(engine):
    with make_session(engine) as s:
        t = _make_task(s)
        a = _actor(s, "claude-code")
        t0 = local_now()
        L.claim_task(s, t, a, ttl=timedelta(minutes=10), now=t0)
        s.commit()
        # свежий lease не трогается
        freed_none = L.expire_stale_leases(s, now=t0 + timedelta(minutes=5))
        assert freed_none == []
        assert t.lease_owner == a.id
        # протухший — освобождается
        freed = L.expire_stale_leases(s, now=t0 + timedelta(minutes=20))
        s.commit()
        assert [x.id for x in freed] == [t.id]
        assert t.lease_owner is None
        assert "lease_expired" in _actions(s, t.id)
