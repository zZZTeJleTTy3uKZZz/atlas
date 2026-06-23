"""Lease/Claim движок: блокировка задач для мультиагентности (Волна 8).

Pure-logic, без typer. Атомарность — через SQLAlchemy ``version_id_col`` на
``Task`` (optimistic-lock): claim/take/release/renew мутируют ORM-объект и
``flush``; рассинхрон версии → ``StaleDataError``, который claim/take ловят,
откатывают, перечитывают задачу и ретраят (compare-and-swap-семантика, как
``ClaimIssueInTx`` в beads, но через version_id_col).

Lease — ЛОКАЛЬНАЯ координация (кто/откуда/когда взял задачу). В ядро НЕ
синкается (см. ``sync/mapper._task_payload``): иначе протухание lease на одной
машине затрёт состояние на другой через LWW.

Контекст держателя:
- ``lease_owner``       — кто (participant; роль/агент);
- ``lease_session_id``  — кто конкретно (id сессии Claude Code);
- ``lease_origin``      — откуда (проект/cwd);
- ``claimed_at``        — когда взял;
- ``lease_expires_at``  — TTL-дедлайн (протухание).
«Когда закончил» — событие в ``ActionLog`` (task_released/lease_expired) +
``completed_at`` при переходе в done.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from atlas.pm._time import local_now
from atlas.pm.models import ActionLog, Participant, Task

DEFAULT_TTL = timedelta(hours=2)
DEFAULT_ACTOR_SLUG = "dmitry"
MAX_CLAIM_RETRIES = 3

_TTL_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_TTL_UNIT = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


# --------------------------------------------------------------------------- #
# Ошибки                                                                      #
# --------------------------------------------------------------------------- #


class LeaseError(Exception):
    """База для lease-ошибок."""


class LeaseHeldError(LeaseError):
    """Задача занята другим живым lease — взять нельзя."""

    def __init__(self, holder: Optional[str], expires_at: Optional[datetime]):
        self.holder = holder
        self.expires_at = expires_at
        tail = f", до {expires_at:%Y-%m-%d %H:%M}" if expires_at else ""
        super().__init__(f"задача занята ({holder or 'неизвестно'}{tail})")


class LeaseNotOwnedError(LeaseError):
    """release/renew lease, принадлежащего другому (или отсутствующего)."""

    def __init__(self, holder: Optional[str]):
        self.holder = holder
        super().__init__(
            f"lease принадлежит другому ({holder or 'никому'}) — release/renew нельзя"
        )


class OptimisticLockError(LeaseError):
    """Задача изменена параллельно (version mismatch не разрешился ретраями)."""


@dataclass
class LeaseResult:
    task: Task
    previous_holder: Optional[str]


# --------------------------------------------------------------------------- #
# Резолв контекста держателя                                                  #
# --------------------------------------------------------------------------- #


def parse_ttl(value: str) -> timedelta:
    """'2h' | '30m' | '90s' | '1d' → timedelta. Невалидное → ValueError."""
    m = _TTL_RE.match(value or "")
    if not m:
        raise ValueError(f"неверный TTL: {value!r} (примеры: 2h, 30m, 90s, 1d)")
    return timedelta(**{_TTL_UNIT[m.group(2).lower()]: int(m.group(1))})


def resolve_actor(session: Session, actor_slug: Optional[str] = None) -> Participant:
    """actor = --actor › env ATLAS_ACTOR › DEFAULT_ACTOR_SLUG. Не найден → LeaseError."""
    slug = actor_slug or os.environ.get("ATLAS_ACTOR") or DEFAULT_ACTOR_SLUG
    actor = session.execute(
        select(Participant).where(Participant.slug == slug)
    ).scalar_one_or_none()
    if actor is None:
        raise LeaseError(
            f"участник '{slug}' не найден — создайте participant или укажите --actor"
        )
    return actor


def resolve_session_id(explicit: Optional[str] = None) -> Optional[str]:
    """session-id = --session › env ATLAS_SESSION › None."""
    return explicit or os.environ.get("ATLAS_SESSION") or None


def resolve_origin(explicit: Optional[str] = None) -> Optional[str]:
    """origin = --from › env ATLAS_FROM › basename(cwd)."""
    return explicit or os.environ.get("ATLAS_FROM") or Path.cwd().name


# --------------------------------------------------------------------------- #
# Внутренние хелперы                                                          #
# --------------------------------------------------------------------------- #


def _holder_slug(session: Session, participant_id: Optional[str]) -> Optional[str]:
    if not participant_id:
        return None
    p = session.get(Participant, participant_id)
    return p.slug if p else participant_id


def _lease_is_free(task: Task, actor_id: str, now: datetime) -> bool:
    """Свободен для actor, если: ничей OR мой OR протух."""
    if task.lease_owner is None:
        return True
    if task.lease_owner == actor_id:
        return True
    if task.lease_expires_at is not None and task.lease_expires_at < now:
        return True
    return False


def _clear_lease(task: Task) -> None:
    task.lease_owner = None
    task.lease_session_id = None
    task.lease_origin = None
    task.claimed_at = None
    task.lease_expires_at = None


def _log_lease(
    session: Session,
    action: str,
    task: Task,
    actor: Optional[Participant],
    *,
    previous_holder: Optional[str] = None,
    session_id: Optional[str] = None,
    origin: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> None:
    details = {
        "previous_holder": _holder_slug(session, previous_holder),
        "session_id": session_id,
        "origin": origin,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }
    session.add(
        ActionLog(
            actor_id=actor.id if actor else None,
            entity_type="task",
            entity_id=task.id,
            action=action,
            details_json=json.dumps(details, ensure_ascii=False),
        )
    )


# --------------------------------------------------------------------------- #
# Операции                                                                    #
# --------------------------------------------------------------------------- #


def claim_task(
    session: Session,
    task: Task,
    actor: Participant,
    *,
    session_id: Optional[str] = None,
    origin: Optional[str] = None,
    ttl: timedelta = DEFAULT_TTL,
    now: Optional[datetime] = None,
) -> LeaseResult:
    """Атомарно взять задачу: lease + status=in_progress + assignee=actor.

    Свободна (ничей/мой/протух) → захват; занята другим → LeaseHeldError.
    Идемпотентно: повторный claim тем же actor (lease жив) → success.
    Гонка отсекается version_id_col: проигравший ловит StaleDataError, перечитывает
    и видит занятость → LeaseHeldError.
    """
    task_id = task.id
    for _ in range(MAX_CLAIM_RETRIES):
        cur_now = now or local_now()
        if not _lease_is_free(task, actor.id, cur_now):
            raise LeaseHeldError(
                _holder_slug(session, task.lease_owner), task.lease_expires_at
            )
        prev = task.lease_owner if task.lease_owner != actor.id else None
        same_live = (
            task.lease_owner == actor.id
            and task.lease_expires_at is not None
            and task.lease_expires_at >= cur_now
        )
        task.lease_owner = actor.id
        task.lease_session_id = session_id
        task.lease_origin = origin
        task.lease_expires_at = cur_now + ttl
        if not same_live:
            task.claimed_at = cur_now
        task.status = "in_progress"
        task.assignee_id = actor.id
        if task.started_at is None:
            task.started_at = cur_now
        try:
            session.flush()
        except StaleDataError:
            session.rollback()
            task = session.get(Task, task_id)
            continue
        _log_lease(
            session, "task_claimed", task, actor,
            previous_holder=prev, session_id=session_id, origin=origin,
            expires_at=task.lease_expires_at,
        )
        return LeaseResult(task=task, previous_holder=_holder_slug(session, prev))
    raise LeaseHeldError(
        _holder_slug(session, task.lease_owner), task.lease_expires_at
    )


def release_task(session: Session, task: Task, actor: Participant) -> None:
    """Отпустить lease (только держатель). Статус НЕ трогает. Идемпотентно."""
    if task.lease_owner is None:
        return
    if task.lease_owner != actor.id:
        raise LeaseNotOwnedError(_holder_slug(session, task.lease_owner))
    _clear_lease(task)
    session.flush()
    _log_lease(session, "task_released", task, actor)


def renew_lease(
    session: Session,
    task: Task,
    actor: Participant,
    *,
    ttl: timedelta = DEFAULT_TTL,
    now: Optional[datetime] = None,
) -> None:
    """Продлить lease (heartbeat); только держатель. Чужой/нет → LeaseNotOwnedError."""
    if task.lease_owner != actor.id:
        raise LeaseNotOwnedError(_holder_slug(session, task.lease_owner))
    cur_now = now or local_now()
    task.lease_expires_at = cur_now + ttl
    session.flush()
    _log_lease(session, "lease_renewed", task, actor, expires_at=task.lease_expires_at)


def take_task(
    session: Session,
    task: Task,
    actor: Participant,
    *,
    session_id: Optional[str] = None,
    origin: Optional[str] = None,
    ttl: timedelta = DEFAULT_TTL,
    now: Optional[datetime] = None,
) -> LeaseResult:
    """Принудительно отобрать задачу (даже занятую другим). Пишет task_taken."""
    task_id = task.id
    for _ in range(MAX_CLAIM_RETRIES):
        cur_now = now or local_now()
        prev = task.lease_owner if task.lease_owner != actor.id else None
        task.lease_owner = actor.id
        task.lease_session_id = session_id
        task.lease_origin = origin
        task.lease_expires_at = cur_now + ttl
        task.claimed_at = cur_now
        task.status = "in_progress"
        task.assignee_id = actor.id
        if task.started_at is None:
            task.started_at = cur_now
        try:
            session.flush()
        except StaleDataError:
            session.rollback()
            task = session.get(Task, task_id)
            continue
        _log_lease(
            session, "task_taken", task, actor,
            previous_holder=prev, session_id=session_id, origin=origin,
            expires_at=task.lease_expires_at,
        )
        return LeaseResult(task=task, previous_holder=_holder_slug(session, prev))
    raise OptimisticLockError("не удалось отобрать lease (постоянный version-конфликт)")


def expire_stale_leases(
    session: Session, now: Optional[datetime] = None
) -> list[Task]:
    """Освободить протухшие lease (lease_expires_at < now). Статус НЕ трогает.

    Детерминированно по lease_expires_at (а не эвристикой по updated_at, как
    `bd stale`). Возвращает освобождённые задачи; логирует lease_expired.
    """
    cur_now = now or local_now()
    stale = (
        session.execute(
            select(Task).where(
                Task.lease_owner.is_not(None),
                Task.lease_expires_at.is_not(None),
                Task.lease_expires_at < cur_now,
            )
        )
        .scalars()
        .all()
    )
    freed: list[Task] = []
    for task in stale:
        prev = task.lease_owner
        _clear_lease(task)
        _log_lease(session, "lease_expired", task, None, previous_holder=prev)
        freed.append(task)
    if freed:
        session.flush()
    return freed
