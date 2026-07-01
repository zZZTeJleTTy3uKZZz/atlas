"""Обогащённый журнал событий (action_log + резолв сущностей). Pure-logic.

`atlas action-log list` отдаёт сырые строки (action + entity_id + время). Здесь —
ОБОГАЩЁННАЯ лента для человека/агента: кто (actor-slug), что (заголовок задачи/имя
проекта), в каком проекте, приоритет/статус задачи. Резолвит entity_id → объект.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.models import ActionLog, Epic, Participant, Project, Task


def build_logs(
    session: Session,
    *,
    limit: int = 30,
    project_ref: Optional[str] = None,
    entity_type: Optional[str] = None,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    since: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Обогащённые записи журнала (новые сверху). Фильтры опциональны.

    project_ref сужает до событий по сущностям этого проекта (задачи/эпики/проект).
    """
    proj_id: Optional[str] = None
    if project_ref:
        from atlas.slugs import resolve_project_ref

        proj = resolve_project_ref(session, project_ref)
        if proj is None:
            raise ValueError(f"Проект '{project_ref}' не найден.")
        proj_id = proj.id

    actor_id: Optional[str] = None
    if actor:
        p = session.execute(
            select(Participant).where(Participant.slug == actor)
        ).scalar_one_or_none()
        actor_id = p.id if p else "__none__"

    # Кэши резолва (избегаем N+1 на повторных сущностях).
    parts = {p.id: p.slug for p in session.execute(select(Participant)).scalars().all()}
    pmap = {p.id: p for p in session.execute(select(Project)).scalars().all()}

    stmt = select(ActionLog).order_by(ActionLog.timestamp.desc())
    if entity_type:
        stmt = stmt.where(ActionLog.entity_type == entity_type)
    if action:
        stmt = stmt.where(ActionLog.action == action)
    if actor_id:
        stmt = stmt.where(ActionLog.actor_id == actor_id)
    if since:
        stmt = stmt.where(ActionLog.timestamp >= since)

    rows: list[dict[str, Any]] = []
    # Берём с запасом, потом фильтруем по проекту (требует резолва сущности) и режем до limit.
    for log in session.execute(stmt.limit(limit * 4 if proj_id else limit)).scalars().all():
        enriched = _enrich(session, log, parts, pmap)
        if proj_id and enriched.get("project_id") != proj_id:
            continue
        rows.append(enriched)
        if len(rows) >= limit:
            break
    return rows


def _enrich(
    session: Session,
    log: ActionLog,
    parts: dict[str, str],
    pmap: dict[str, Project],
) -> dict[str, Any]:
    """Резолв одной записи: actor-slug + сущность (задача/эпик/проект) → заголовок/проект/приоритет."""
    base: dict[str, Any] = {
        "at": log.timestamp.isoformat() if log.timestamp else None,
        "actor": parts.get(log.actor_id) if log.actor_id else None,
        "action": log.action,
        "entity_type": log.entity_type,
        "entity_id": log.entity_id,
        "title": None,
        "project": None,
        "project_id": None,
        "priority": None,
        "status": None,
        "ref": None,
    }
    et = log.entity_type
    if et == "task":
        t = session.get(Task, log.entity_id)
        if t is not None:
            proj = pmap.get(t.project_id)
            base.update(
                ref=t.slug or (str(t.number) if t.number else t.id),
                title=t.title, priority=t.priority, status=t.status,
                project=proj.slug if proj else None, project_id=t.project_id,
            )
    elif et == "epic":
        e = session.get(Epic, log.entity_id)
        if e is not None:
            proj = pmap.get(e.project_id)
            base.update(
                ref=e.slug or e.id, title=e.title, status=e.status,
                project=proj.slug if proj else None, project_id=e.project_id,
            )
    elif et == "project":
        p = pmap.get(log.entity_id) or session.get(Project, log.entity_id)
        if p is not None:
            base.update(ref=p.slug, title=p.name, project=p.slug, project_id=p.id)
    return base
