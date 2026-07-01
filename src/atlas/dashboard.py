"""Агрегация данных дашборда портфеля (pure-logic, без typer/Rich).

``build_dashboard(session, project_ref=None)`` собирает срез состояния портфеля
(или одного проекта): распределение задач по статусам/приоритетам, что в работе
(in-flight + держатель lease), что требует внимания (blocked/overdue/протухшие
lease), разбивку по проектам, активные эпики и недавнюю активность.

Возврат — обычный dict (JSON-сериализуемый): агент потребляет как есть, человек
получает Rich-рендер (см. ``commands/dashboard.py``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas._time import local_now
from atlas.models import ActionLog, Epic, Participant, Project, ProjectStatus, Task

#: Терминальные статусы задачи (не «открытые»).
TERMINAL_STATUSES = ("done", "cancelled")
#: Порядок статусов для распределения (слева-направо по жизненному циклу).
STATUS_ORDER = (
    "todo", "in_progress", "review", "blocked", "done", "cancelled",
)
#: Порядок приоритетов.
PRIORITY_ORDER = ("P0", "P1", "P2", "P3")


def _task_brief(
    task: Task,
    pmap: dict[str, Project],
    parts: dict[str, str],
    now: datetime,
) -> dict[str, Any]:
    """Компактная карточка задачи для списков дашборда."""
    proj = pmap.get(task.project_id)
    overdue = bool(
        task.due_date is not None
        and task.due_date < now
        and task.status not in TERMINAL_STATUSES
    )
    return {
        "ref": task.slug or (str(task.number) if task.number else task.id),
        "number": task.number,
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "project": proj.slug if proj else None,
        "assignee": parts.get(task.assignee_id) if task.assignee_id else None,
        "lease_owner": parts.get(task.lease_owner) if task.lease_owner else None,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "overdue": overdue,
        "story_points": task.story_points,
    }


def build_dashboard(
    session: Session,
    *,
    project_ref: Optional[str] = None,
    now: Optional[datetime] = None,
    top_projects: int = 10,
    recent: int = 8,
) -> dict[str, Any]:
    """Собрать срез дашборда. project_ref → только этот проект, иначе весь портфель."""
    cur_now = now or local_now()

    proj_filter: Optional[Project] = None
    if project_ref:
        from atlas.slugs import resolve_project_ref

        proj_filter = resolve_project_ref(session, project_ref)
        if proj_filter is None:
            raise ValueError(f"Проект '{project_ref}' не найден.")
    proj_id = proj_filter.id if proj_filter else None

    # pmap — по ВСЕМ проектам (для резолва имён задач под архивными проектами);
    # projects (счётчик/by_status) — только активные.
    all_projects = session.execute(select(Project)).scalars().all()
    pmap = {p.id: p for p in all_projects}
    projects = [p for p in all_projects if p.archived_at is None]
    pstatus = {
        s.id: s for s in session.execute(select(ProjectStatus)).scalars().all()
    }
    parts = {
        p.id: p.slug for p in session.execute(select(Participant)).scalars().all()
    }

    tq = select(Task).where(Task.archived_at.is_(None))
    if proj_id:
        tq = tq.where(Task.project_id == proj_id)
    tasks = session.execute(tq).scalars().all()

    status_counts = {s: 0 for s in STATUS_ORDER}
    prio_counts = {p: 0 for p in PRIORITY_ORDER}
    in_progress: list[dict] = []
    blocked: list[dict] = []
    review: list[dict] = []
    overdue: list[dict] = []
    stale_leases = 0
    active_leases = 0
    per_project: dict[str, dict[str, int]] = {}

    for t in tasks:
        status_counts[t.status] = status_counts.get(t.status, 0) + 1
        is_open = t.status not in TERMINAL_STATUSES

        if t.lease_owner is not None:
            if t.lease_expires_at is not None and t.lease_expires_at < cur_now:
                stale_leases += 1
            else:
                active_leases += 1

        if is_open:
            prio_counts[t.priority] = prio_counts.get(t.priority, 0) + 1
            pp = per_project.setdefault(
                t.project_id,
                {"open": 0, "in_progress": 0, "blocked": 0, "review": 0},
            )
            pp["open"] += 1
            if t.status in pp:
                pp[t.status] += 1
            if t.due_date is not None and t.due_date < cur_now:
                overdue.append(_task_brief(t, pmap, parts, cur_now))

        if t.status == "in_progress":
            in_progress.append(_task_brief(t, pmap, parts, cur_now))
        elif t.status == "blocked":
            blocked.append(_task_brief(t, pmap, parts, cur_now))
        elif t.status == "review":
            review.append(_task_brief(t, pmap, parts, cur_now))

    by_project: list[dict] = []
    for pid, counts in per_project.items():
        proj = pmap.get(pid)
        by_project.append(
            {
                "project": proj.slug if proj else pid,
                "name": proj.name if proj else "",
                **counts,
            }
        )
    by_project.sort(key=lambda x: x["open"], reverse=True)
    by_project = by_project[:top_projects]

    proj_by_status: dict[str, int] = {}
    for p in projects:
        st = pstatus.get(p.status_id)
        key = st.slug if st else "?"
        proj_by_status[key] = proj_by_status.get(key, 0) + 1

    epics_active = (
        session.execute(select(Epic).where(Epic.status == "active"))
        .scalars()
        .all()
    )

    acts = (
        session.execute(
            select(ActionLog).order_by(ActionLog.timestamp.desc()).limit(recent)
        )
        .scalars()
        .all()
    )
    recent_activity = [
        {
            "action": a.action,
            "entity": a.entity_type,
            "at": a.timestamp.isoformat() if a.timestamp else None,
        }
        for a in acts
    ]

    # Сортировки списков: приоритет (P0 первым), затем overdue.
    _prio_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    overdue.sort(key=lambda x: _prio_rank.get(x["priority"], 9))
    in_progress.sort(key=lambda x: _prio_rank.get(x["priority"], 9))
    blocked.sort(key=lambda x: _prio_rank.get(x["priority"], 9))

    return {
        "scope": proj_filter.slug if proj_filter else "portfolio",
        "generated_at": cur_now.isoformat(),
        "projects": {
            "total": len(projects),
            "active": proj_by_status.get("active", 0),
            "by_status": proj_by_status,
        },
        "tasks": {
            "open": sum(prio_counts.values()),
            "by_status": status_counts,
            "by_priority": prio_counts,
            "in_progress": in_progress,
            "blocked": blocked,
            "review": review,
            "overdue": overdue,
        },
        "epics": {"active": len(epics_active)},
        "leases": {"active": active_leases, "stale": stale_leases},
        "by_project": by_project,
        "recent_activity": recent_activity,
    }
