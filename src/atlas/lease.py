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

from atlas.appconfig import default_actor

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from atlas._time import local_now
from atlas.models import ActionLog, Epic, Participant, Task

DEFAULT_TTL = timedelta(hours=2)
DEFAULT_ACTOR_SLUG = default_actor()
MAX_CLAIM_RETRIES = 3

# Статусы, при которых задача считается завершённой — каскад epic-lease их НЕ
# трогает (нечего блокировать; единый источник с авто-снятием lease в task update).
DONE_STATUSES = ("done", "cancelled")

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


@dataclass
class MultiClaimResult:
    """Итог multi-claim: взятые задачи + пропущенные (занятые другим)."""

    claimed: list[Task]
    skipped: list[Task]


@dataclass
class EpicLeaseResult:
    epic: Epic
    previous_holder: Optional[str]
    claimed_tasks: list[Task]


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


def _epic_unfinished_tasks(session: Session, epic: Epic) -> list[Task]:
    """Незавершённые задачи эпика (status not in done/cancelled) — цели каскада."""
    return (
        session.execute(
            select(Task).where(
                Task.epic_id == epic.id,
                Task.status.not_in(DONE_STATUSES),
            )
        )
        .scalars()
        .all()
    )


def _lease_held_by_other(obj, actor_id: str, now: datetime) -> bool:
    """Lease ЖИВ и принадлежит ДРУГОМУ (не actor, не протух). obj = Task|Epic."""
    if obj.lease_owner is None or obj.lease_owner == actor_id:
        return False
    if obj.lease_expires_at is not None and obj.lease_expires_at < now:
        return False
    return True


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


def _log_epic(
    session: Session,
    action: str,
    epic: Epic,
    actor: Optional[Participant],
    *,
    previous_holder: Optional[str] = None,
    session_id: Optional[str] = None,
    origin: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    cascaded: Optional[list[str]] = None,
) -> None:
    details = {
        "previous_holder": _holder_slug(session, previous_holder),
        "session_id": session_id,
        "origin": origin,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "cascaded_tasks": cascaded or [],
    }
    session.add(
        ActionLog(
            actor_id=actor.id if actor else None,
            entity_type="epic",
            entity_id=epic.id,
            action=action,
            details_json=json.dumps(details, ensure_ascii=False),
        )
    )


@contextmanager
def _cas_savepoint(session: Session):
    """SAVEPOINT для одного optimistic-lock CAS. yield → мутируй+flush внутри.

    Открывается ДО мутаций объекта: ``begin_nested`` снимает снапшот, флашит
    лишь уже-чистое состояние; последующие мутации и наш ``flush`` живут ВНУТРИ
    savepoint. При version-конфликте (``StaleDataError``) откатывается ТОЛЬКО
    savepoint, а окружающая транзакция (epic-lease, сиблинги в каскаде/батче)
    остаётся целой — в отличие от полного ``session.rollback()``, который снёс
    бы всю уже проделанную работу. Возвращает list[bool] из одного элемента:
    [True] — CAS прошёл, [False] — конфликт (вызывающий перечитывает и ретраит).
    """
    sp = session.begin_nested()
    ok = [True]
    try:
        yield ok
        session.flush()
        sp.commit()
    except StaleDataError:
        sp.rollback()
        ok[0] = False


def _guard_epic_mutex(
    session: Session, task: Task, actor_id: str, now: datetime
) -> None:
    """Мьютекс #195: задачу нельзя взять, если её эпик держит другой живой lease."""
    if not task.epic_id:
        return
    epic = session.get(Epic, task.epic_id)
    if epic is not None and _lease_held_by_other(epic, actor_id, now):
        raise LeaseHeldError(
            _holder_slug(session, epic.lease_owner), epic.lease_expires_at
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
        # Guard мьютекса «эпик ⊻ задачи» (#195): если эпик задачи держит ЖИВОЙ
        # lease ДРУГОГО актора — отдельную задачу взять нельзя (она под epic-claim).
        _guard_epic_mutex(session, task, actor.id, cur_now)
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
        # SAVEPOINT (открыт ДО мутаций), а не полный rollback: при каскаде/батче
        # version-конфликт одной задачи НЕ должен сносить уже зафлашенные
        # epic-lease и leases сиблингов.
        with _cas_savepoint(session) as ok:
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
        if not ok[0]:
            session.expire(task)
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
        # SAVEPOINT (см. claim_task): не сносить окружающую транзакцию при
        # version-конфликте отдельной задачи.
        with _cas_savepoint(session) as ok:
            task.lease_owner = actor.id
            task.lease_session_id = session_id
            task.lease_origin = origin
            task.lease_expires_at = cur_now + ttl
            task.claimed_at = cur_now
            task.status = "in_progress"
            task.assignee_id = actor.id
            if task.started_at is None:
                task.started_at = cur_now
        if not ok[0]:
            session.expire(task)
            task = session.get(Task, task_id)
            continue
        _log_lease(
            session, "task_taken", task, actor,
            previous_holder=prev, session_id=session_id, origin=origin,
            expires_at=task.lease_expires_at,
        )
        return LeaseResult(task=task, previous_holder=_holder_slug(session, prev))
    raise OptimisticLockError("не удалось отобрать lease (постоянный version-конфликт)")


# --------------------------------------------------------------------------- #
# Групповой lease: multi-claim задач + epic-claim с каскадом                  #
# --------------------------------------------------------------------------- #


def claim_tasks(
    session: Session,
    tasks: list[Task],
    actor: Participant,
    *,
    all_or_nothing: bool = True,
    session_id: Optional[str] = None,
    origin: Optional[str] = None,
    ttl: timedelta = DEFAULT_TTL,
    now: Optional[datetime] = None,
) -> MultiClaimResult:
    """Multi-claim: взять список задач за один логический вызов (#193).

    all_or_nothing (по умолчанию): предпроверить — все ли свободны для actor
    (учитывая мьютекс эпика). Если хоть одна занята другим живым lease →
    ``LeaseHeldError`` (НИ ОДНА не берётся; вызывающий откатывает транзакцию).

    best-effort (all_or_nothing=False): берёт свободные, занятые собирает в
    ``skipped``. Возвращает :class:`MultiClaimResult` (claimed/skipped).
    """
    cur_now = now or local_now()
    if all_or_nothing:
        for t in tasks:
            held = not _lease_is_free(t, actor.id, cur_now)
            epic = session.get(Epic, t.epic_id) if t.epic_id else None
            epic_blocks = epic is not None and _lease_held_by_other(
                epic, actor.id, cur_now
            )
            if held or epic_blocks:
                holder = t.lease_owner if held else (epic.lease_owner if epic else None)
                expires = (
                    t.lease_expires_at
                    if held
                    else (epic.lease_expires_at if epic else None)
                )
                raise LeaseHeldError(_holder_slug(session, holder), expires)
        all_claimed = [
            claim_task(
                session, t, actor, session_id=session_id, origin=origin,
                ttl=ttl, now=cur_now,
            ).task
            for t in tasks
        ]
        return MultiClaimResult(claimed=all_claimed, skipped=[])

    claimed: list[Task] = []
    skipped: list[Task] = []
    for t in tasks:
        try:
            claim_task(
                session, t, actor, session_id=session_id, origin=origin,
                ttl=ttl, now=cur_now,
            )
            claimed.append(t)
        except LeaseHeldError:
            skipped.append(t)
    return MultiClaimResult(claimed=claimed, skipped=skipped)


def claim_epic(
    session: Session,
    epic: Epic,
    actor: Participant,
    *,
    session_id: Optional[str] = None,
    origin: Optional[str] = None,
    ttl: timedelta = DEFAULT_TTL,
    now: Optional[datetime] = None,
) -> EpicLeaseResult:
    """Поставить lease на эпик + каскадно залочить незавершённые задачи (#194).

    Guard (#195): если ЛЮБАЯ незавершённая задача эпика занята ДРУГИМ живым
    lease → ``LeaseHeldError`` (эпик НЕ берётся). Иначе lease на epic +
    каскад ``claim_task`` под actor. Идемпотентно для того же actor.
    Optimistic-lock через version_id_col с retry на ``StaleDataError``.
    """
    epic_id = epic.id
    for _ in range(MAX_CLAIM_RETRIES):
        cur_now = now or local_now()
        if not _lease_is_free(epic, actor.id, cur_now):
            raise LeaseHeldError(
                _holder_slug(session, epic.lease_owner), epic.lease_expires_at
            )
        tasks = _epic_unfinished_tasks(session, epic)
        # Guard #195: ни одна незавершённая задача не должна быть занята другим.
        for t in tasks:
            if _lease_held_by_other(t, actor.id, cur_now):
                raise LeaseHeldError(
                    _holder_slug(session, t.lease_owner), t.lease_expires_at
                )
        prev = epic.lease_owner if epic.lease_owner != actor.id else None
        same_live = (
            epic.lease_owner == actor.id
            and epic.lease_expires_at is not None
            and epic.lease_expires_at >= cur_now
        )
        with _cas_savepoint(session) as ok:
            epic.lease_owner = actor.id
            epic.lease_session_id = session_id
            epic.lease_origin = origin
            epic.lease_expires_at = cur_now + ttl
            if not same_live:
                epic.claimed_at = cur_now
        if not ok[0]:
            session.expire(epic)
            epic = session.get(Epic, epic_id)
            continue
        # Каскад: эпик уже держит actor → guard в claim_task пройдёт (self).
        claimed: list[Task] = []
        for t in tasks:
            claim_task(
                session, t, actor, session_id=session_id, origin=origin,
                ttl=ttl, now=cur_now,
            )
            claimed.append(t)
        _log_epic(
            session, "epic_claimed", epic, actor,
            previous_holder=prev, session_id=session_id, origin=origin,
            expires_at=epic.lease_expires_at,
            cascaded=[t.slug or t.id for t in claimed],
        )
        return EpicLeaseResult(
            epic=epic,
            previous_holder=_holder_slug(session, prev),
            claimed_tasks=claimed,
        )
    raise OptimisticLockError("не удалось взять epic-lease (постоянный version-конфликт)")


def release_epic(session: Session, epic: Epic, actor: Participant) -> EpicLeaseResult:
    """Снять lease эпика + каскадно release незавершённые задачи держателя.

    Только держатель (иначе ``LeaseNotOwnedError``). Каскад снимает lease лишь
    с задач, которые держит actor (чужие/протухшие не трогает). Идемпотентно.
    """
    if epic.lease_owner is None:
        return EpicLeaseResult(epic=epic, previous_holder=None, claimed_tasks=[])
    if epic.lease_owner != actor.id:
        raise LeaseNotOwnedError(_holder_slug(session, epic.lease_owner))
    released: list[Task] = []
    for t in _epic_unfinished_tasks(session, epic):
        if t.lease_owner == actor.id:
            _clear_lease(t)
            _log_lease(session, "task_released", t, actor)
            released.append(t)
    _clear_lease(epic)
    session.flush()
    _log_epic(
        session, "epic_released", epic, actor,
        cascaded=[t.slug or t.id for t in released],
    )
    return EpicLeaseResult(epic=epic, previous_holder=None, claimed_tasks=released)


def expire_stale_leases(
    session: Session, now: Optional[datetime] = None
) -> list:
    """Освободить протухшие lease (lease_expires_at < now). Статус НЕ трогает.

    Реапит И задачи, И эпики симметрично. Детерминированно по lease_expires_at
    (а не эвристикой по updated_at, как `bd stale`). Возвращает освобождённые
    сущности (Task|Epic); логирует lease_expired / epic_lease_expired.
    """
    cur_now = now or local_now()
    freed: list = []
    stale_tasks = (
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
    for task in stale_tasks:
        prev = task.lease_owner
        _clear_lease(task)
        _log_lease(session, "lease_expired", task, None, previous_holder=prev)
        freed.append(task)
    stale_epics = (
        session.execute(
            select(Epic).where(
                Epic.lease_owner.is_not(None),
                Epic.lease_expires_at.is_not(None),
                Epic.lease_expires_at < cur_now,
            )
        )
        .scalars()
        .all()
    )
    for epic in stale_epics:
        prev = epic.lease_owner
        _clear_lease(epic)
        _log_epic(session, "epic_lease_expired", epic, None, previous_holder=prev)
        freed.append(epic)
    if freed:
        session.flush()
    return freed
