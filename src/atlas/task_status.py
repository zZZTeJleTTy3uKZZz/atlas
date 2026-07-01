"""Машина состояний задачи + lifecycle-переходы (глаголы done/review/block/...).

Статус задачи меняется ТОЛЬКО намеренными переходами, не «голым» update --status:

- ``start``   → claim (lease + in_progress + assignee)  — см. :func:`atlas.lease.claim_task`;
- ``review``  → review   (lease сохраняется);
- ``block``   → blocked  (lease сохраняется; опц. причина);
- ``unblock`` → blocked→in_progress (нужно держать lease);
- ``done``    → done      (release lease + completed_at);
- ``cancel``  → cancelled (release lease).

Гейтинг старого пути — в ``commands/task.py``: ``add``/``update --status`` разрешают лишь
planning-статус (todo); lifecycle-статусы идут через глаголы. Идеи-уровень — пул `atlas backlog`.

Lease-уважение: завершить/перевести задачу с ЖИВЫМ lease ДРУГОГО актора можно только
``--force`` (иначе :class:`atlas.lease.LeaseHeldError`) — чтобы не затирать чужую работу.

Pure-logic, без typer. Переиспользует lease-хелперы (``_lease_held_by_other`` /
``_clear_lease`` / ``_holder_slug``) и optimistic-lock семантику ``Task.version_id_col``.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from atlas._time import local_now
from atlas.lease import (
    LeaseHeldError,
    LeaseNotOwnedError,
    _clear_lease,
    _holder_slug,
    _lease_held_by_other,
)
from atlas.models import ActionLog, Participant, Task

#: Planning-статус — единственный, что разрешает «голый» add/update --status.
#: (бывший backlog-уровень переехал в отдельный пул `atlas backlog`).
PLANNING_STATUSES = frozenset({"todo"})
#: Lifecycle-статусы — меняются ТОЛЬКО глаголами (start/review/block/unblock/done/cancel).
LIFECYCLE_STATUSES = frozenset(
    {"in_progress", "review", "done", "blocked", "cancelled"}
)
#: Терминальные статусы (дальше переходов нет, кроме реоткрытия через planning).
TERMINAL_STATUSES = frozenset({"done", "cancelled"})

#: Допустимые исходные статусы для каждого целевого перехода (машина состояний).
_ALLOWED_FROM = {
    "review": {"in_progress", "blocked"},
    "blocked": {"todo", "in_progress", "review"},
    "done": {"todo", "in_progress", "review", "blocked"},
    "cancelled": {"todo", "in_progress", "review", "blocked"},
}

#: Подсказка-глагол для целевого статуса (для понятных сообщений гейтинга).
VERB_FOR_STATUS = {
    "in_progress": "task start",
    "review": "task review",
    "blocked": "task block",
    "done": "task done",
    "cancelled": "task cancel",
}


class TransitionError(Exception):
    """Недопустимый переход: из текущего статуса нельзя в целевой данным глаголом."""


class ReviewGateError(Exception):
    """Закрыть/одобрить задачу может только её reviewer (review-workflow)."""

    def __init__(self, reviewer_slug: str | None):
        self.reviewer_slug = reviewer_slug
        super().__init__(
            f"задачу закрывает только reviewer ({reviewer_slug or '?'}); "
            f"исполнителю — 'task submit' (на проверку)"
        )


def _require_reviewer(session: Session, task: Task, actor: Participant, force: bool) -> None:
    """Гейт review: если задан reviewer и actor ≠ reviewer → ReviewGateError (кроме --force).

    reviewer=None → гейта нет (задача без приёмки). reviewer==actor (соло/создатель)
    → проходит без трения."""
    if force or task.reviewer_id is None or task.reviewer_id == actor.id:
        return
    raise ReviewGateError(_holder_slug(session, task.reviewer_id))


# --------------------------------------------------------------------------- #
# Внутренние хелперы                                                          #
# --------------------------------------------------------------------------- #


def _log_transition(
    session: Session,
    action: str,
    task: Task,
    actor: Optional[Participant],
    **extra: object,
) -> None:
    """ActionLog перехода: action + to_status + произвольные детали (напр. reason)."""
    details = {"to_status": task.status, **extra}
    session.add(
        ActionLog(
            actor_id=actor.id if actor else None,
            entity_type="task",
            entity_id=task.id,
            action=action,
            details_json=json.dumps(details, ensure_ascii=False),
        )
    )


def _guard_other_lease(
    session: Session,
    task: Task,
    actor: Participant,
    now: datetime,
    force: bool,
) -> None:
    """Запретить переход, если задачу держит ЖИВОЙ lease ДРУГОГО актора (без --force)."""
    if not force and _lease_held_by_other(task, actor.id, now):
        raise LeaseHeldError(
            _holder_slug(session, task.lease_owner), task.lease_expires_at
        )


def _require_from(task: Task, target: str) -> None:
    """Проверить машину состояний; иначе TransitionError с подсказкой-глаголом."""
    if task.status not in _ALLOWED_FROM[target]:
        allowed = ", ".join(sorted(_ALLOWED_FROM[target]))
        raise TransitionError(
            f"нельзя перевести в '{target}' из '{task.status}' "
            f"(допустимо из: {allowed})"
        )


# --------------------------------------------------------------------------- #
# Переходы (глаголы)                                                          #
# --------------------------------------------------------------------------- #


def finish_task(
    session: Session,
    task: Task,
    actor: Participant,
    *,
    force: bool = False,
    now: Optional[datetime] = None,
) -> Task:
    """→ done: завершить задачу (release lease + completed_at). Идемпотентно.

    Чужой живой lease → LeaseHeldError (если не force). started_at проставляется,
    если задачу закрывают, минуя in_progress (быстрое закрытие).
    """
    cur = now or local_now()
    if task.status == "done":
        return task
    _require_from(task, "done")
    _require_reviewer(session, task, actor, force)  # закрывает только reviewer
    _guard_other_lease(session, task, actor, cur, force)
    task.status = "done"
    if task.started_at is None:
        task.started_at = cur
    task.completed_at = cur
    if task.lease_owner is not None:
        _clear_lease(task)
    session.flush()
    _log_transition(session, "task_done", task, actor)
    return task


def cancel_task(
    session: Session,
    task: Task,
    actor: Participant,
    *,
    force: bool = False,
    now: Optional[datetime] = None,
) -> Task:
    """→ cancelled: отменить задачу (release lease). Идемпотентно. completed_at не ставит."""
    cur = now or local_now()
    if task.status == "cancelled":
        return task
    _require_from(task, "cancelled")
    _guard_other_lease(session, task, actor, cur, force)
    task.status = "cancelled"
    if task.lease_owner is not None:
        _clear_lease(task)
    session.flush()
    _log_transition(session, "task_cancelled", task, actor)
    return task


def review_task(
    session: Session,
    task: Task,
    actor: Participant,
    *,
    force: bool = False,
    now: Optional[datetime] = None,
) -> Task:
    """→ review: отправить на ревью (lease СОХРАНЯЕТСЯ — задача всё ещё твоя)."""
    cur = now or local_now()
    if task.status == "review":
        return task
    _require_from(task, "review")
    _guard_other_lease(session, task, actor, cur, force)
    task.status = "review"
    session.flush()
    _log_transition(session, "task_review", task, actor)
    return task


def block_task(
    session: Session,
    task: Task,
    actor: Participant,
    *,
    reason: Optional[str] = None,
    force: bool = False,
    now: Optional[datetime] = None,
) -> Task:
    """→ blocked: пометить заблокированной (lease СОХРАНЯЕТСЯ). reason → в ActionLog."""
    cur = now or local_now()
    if task.status == "blocked":
        return task
    _require_from(task, "blocked")
    _guard_other_lease(session, task, actor, cur, force)
    task.status = "blocked"
    session.flush()
    _log_transition(session, "task_blocked", task, actor, reason=reason)
    return task


def unblock_task(
    session: Session,
    task: Task,
    actor: Participant,
    *,
    now: Optional[datetime] = None,
) -> Task:
    """blocked → in_progress: разблокировать. Нужно ДЕРЖАТЬ lease (иначе re-claim через start).

    Чужой/отсутствующий lease → LeaseNotOwnedError (подсказка: ``task start``).
    """
    if task.status != "blocked":
        raise TransitionError(
            f"unblock только из 'blocked', текущий статус '{task.status}'"
        )
    if task.lease_owner != actor.id:
        raise LeaseNotOwnedError(_holder_slug(session, task.lease_owner))
    task.status = "in_progress"
    session.flush()
    _log_transition(session, "task_unblocked", task, actor)
    return task
