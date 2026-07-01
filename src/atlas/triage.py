"""Триаж задач: что в работе, что застряло, что ЗАБЫТО (pure-logic, без typer).

``build_triage`` отвечает на «что происходит и за что взяться»: активная работа
(in_progress/review/blocked), и главное — STALE: активные задачи, которых давно
не касались (``updated_at`` старше N дней). Лечит боль «задачи копятся, непонятно
какие отработаны, а какие забили». Агент в начале сессии смотрит триаж.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas._time import local_now
from atlas.models import Participant, Project, Task

#: Статусы «открытой» задачи (не терминальные).
OPEN_STATUSES = ("todo", "in_progress", "review", "blocked")
#: Активные статусы — где идёт/ожидается работа (для stale = «забытая работа»).
ACTIVE_STATUSES = ("in_progress", "review", "blocked")
_PRIO_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _brief(task: Task, pmap: dict, parts: dict, now: datetime) -> dict[str, Any]:
    proj = pmap.get(task.project_id)
    updated = task.updated_at
    age_days = (now - updated).days if updated else None
    return {
        "ref": task.slug or (str(task.number) if task.number else task.id),
        "number": task.number,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "project": proj.slug if proj else None,
        "assignee": parts.get(task.assignee_id) if task.assignee_id else None,
        "reviewer": parts.get(task.reviewer_id) if task.reviewer_id else None,
        "updated_at": updated.isoformat() if updated else None,
        "age_days": age_days,
    }


def build_triage(
    session: Session,
    *,
    project_ref: Optional[str] = None,
    assignee: Optional[str] = None,
    stale_days: int = 7,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Срез триажа открытых задач + stale (активные, не тронутые > stale_days)."""
    cur = now or local_now()
    threshold = cur - timedelta(days=stale_days)

    proj_filter: Optional[Project] = None
    if project_ref:
        from atlas.slugs import resolve_project_ref

        proj_filter = resolve_project_ref(session, project_ref)
        if proj_filter is None:
            raise ValueError(f"Проект '{project_ref}' не найден.")

    assignee_id: Optional[str] = None
    if assignee:
        ap = session.execute(
            select(Participant).where(Participant.slug == assignee)
        ).scalar_one_or_none()
        if ap is None:
            raise ValueError(f"Участник '{assignee}' не найден.")
        assignee_id = ap.id

    pmap = {p.id: p for p in session.execute(select(Project)).scalars().all()}
    parts = {
        p.id: p.slug for p in session.execute(select(Participant)).scalars().all()
    }

    q = select(Task).where(
        Task.archived_at.is_(None), Task.status.in_(OPEN_STATUSES)
    )
    if proj_filter:
        q = q.where(Task.project_id == proj_filter.id)
    if assignee_id:
        q = q.where(Task.assignee_id == assignee_id)
    tasks = session.execute(q).scalars().all()

    counts = {s: 0 for s in OPEN_STATUSES}
    in_progress: list[dict] = []
    review: list[dict] = []
    blocked: list[dict] = []
    stale: list[dict] = []

    for t in tasks:
        counts[t.status] = counts.get(t.status, 0) + 1
        b = _brief(t, pmap, parts, cur)
        if t.status == "in_progress":
            in_progress.append(b)
        elif t.status == "review":
            review.append(b)
        elif t.status == "blocked":
            blocked.append(b)
        # STALE: активная задача, не тронутая дольше порога (забытая работа)
        if t.status in ACTIVE_STATUSES and t.updated_at is not None and t.updated_at < threshold:
            stale.append(b)

    for lst in (in_progress, review, blocked):
        lst.sort(key=lambda x: _PRIO_RANK.get(x["priority"], 9))
    stale.sort(key=lambda x: -(x["age_days"] or 0))  # самые забытые сверху

    return {
        "generated_at": cur.isoformat(),
        "scope": proj_filter.slug if proj_filter else "portfolio",
        "assignee": assignee,
        "stale_days": stale_days,
        "counts": counts,
        "total_open": sum(counts.values()),
        "in_progress": in_progress,
        "review": review,
        "blocked": blocked,
        "stale": stale,
    }
