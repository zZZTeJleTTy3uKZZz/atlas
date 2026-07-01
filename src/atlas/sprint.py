"""Доменная логика спринтов (Scrum-тайм-бокс). Pure-logic, без typer.

Спринт ≠ эпик: эпик — тематическая группировка, спринт — временной бокс с датами,
в который набирают задачи и по итогу считают velocity (сумма story_points
завершённых задач). Жизненный цикл: ``planning → active → closed`` (+ ``cancelled``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.models import Sprint, Task

#: Статусы спринта (тайм-бокс).
SPRINT_STATUSES = ("planning", "active", "closed", "cancelled")
#: Терминальные статусы задачи (для velocity = «завершённые»).
_DONE = ("done",)
_TASK_STATUS_ORDER = ("todo", "in_progress", "review", "blocked", "done", "cancelled")


class SprintError(Exception):
    """База ошибок спринта."""


class SprintTransitionError(SprintError):
    """Недопустимый переход статуса спринта."""


def resolve_sprint(session: Session, ref: str) -> Optional[Sprint]:
    """Спринт по slug | UUID. Не найден → None."""
    return session.execute(
        select(Sprint).where((Sprint.slug == ref) | (Sprint.id == ref))
    ).scalar_one_or_none()


def _sprint_tasks(session: Session, sprint: Sprint) -> list[Task]:
    return list(
        session.execute(
            select(Task).where(
                Task.sprint_id == sprint.id, Task.archived_at.is_(None)
            )
        ).scalars().all()
    )


def sprint_velocity(session: Session, sprint: Sprint) -> dict[str, Any]:
    """Velocity спринта: план vs факт (сумма story_points завершённых задач)."""
    tasks = _sprint_tasks(session, sprint)
    done = [t for t in tasks if t.status in _DONE]
    actual = sum(t.story_points or 0 for t in done)
    committed = sum(t.story_points or 0 for t in tasks)
    return {
        "planned_velocity": sprint.planned_velocity,
        "actual_velocity": actual,
        "committed_points": committed,
        "done_tasks": len(done),
        "total_tasks": len(tasks),
    }


def sprint_board(session: Session, sprint: Sprint) -> dict[str, Any]:
    """Доска спринта: задачи по статусам + сумма points в колонке."""
    tasks = _sprint_tasks(session, sprint)
    columns: dict[str, dict[str, Any]] = {
        st: {"tasks": [], "points": 0} for st in _TASK_STATUS_ORDER
    }
    for t in tasks:
        col = columns.setdefault(t.status, {"tasks": [], "points": 0})
        col["tasks"].append(
            {
                "ref": t.slug or (str(t.number) if t.number else t.id),
                "title": t.title,
                "priority": t.priority,
                "story_points": t.story_points,
            }
        )
        col["points"] += t.story_points or 0
    return {"columns": columns, "velocity": sprint_velocity(session, sprint)}


def start_sprint(session: Session, sprint: Sprint) -> Sprint:
    """planning → active. Из active → idempotent. Иначе TransitionError."""
    if sprint.status == "active":
        return sprint
    if sprint.status != "planning":
        raise SprintTransitionError(
            f"start только из 'planning', текущий '{sprint.status}'"
        )
    sprint.status = "active"
    session.flush()
    return sprint


def close_sprint(
    session: Session,
    sprint: Sprint,
    *,
    retro: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Sprint:
    """active → closed (+ retro-заметки). Из closed → idempotent."""
    if sprint.status == "closed":
        if retro is not None:
            sprint.retro_notes = retro
            session.flush()
        return sprint
    if sprint.status not in ("active", "planning"):
        raise SprintTransitionError(
            f"close только из 'active'/'planning', текущий '{sprint.status}'"
        )
    sprint.status = "closed"
    if retro is not None:
        sprint.retro_notes = retro
    session.flush()
    return sprint


def cancel_sprint(session: Session, sprint: Sprint) -> Sprint:
    """→ cancelled (из любого нетерминального). Идемпотентно."""
    if sprint.status == "cancelled":
        return sprint
    sprint.status = "cancelled"
    session.flush()
    return sprint


def assign_tasks(session: Session, sprint: Optional[Sprint], tasks: list[Task]) -> int:
    """Привязать задачи к спринту (sprint=None → отвязать). Возвращает число изменённых."""
    changed = 0
    target = sprint.id if sprint else None
    for t in tasks:
        if t.sprint_id != target:
            t.sprint_id = target
            changed += 1
    session.flush()
    return changed
