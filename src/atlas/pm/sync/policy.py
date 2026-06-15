"""Политика-потолок синка: до какого уровня иерархии выгружать наружу.

DIP: движок outbox спрашивает should_sync(level, project), не зная типов
проектов. Резолв: Project.sync_policy → иначе ProjectType.default_sync_policy.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from atlas.pm.models import ProjectType, SyncPolicy

# project и epic — верхний уровень (поле sync_epic); task/checklist — свои.
_LEVEL_FIELD = {
    "project": "sync_epic",
    "epic": "sync_epic",
    "task": "sync_task",
    "checklist": "sync_checklist",
}


def _resolve_policy_slug(session: Session, project) -> str | None:
    if project.sync_policy:
        return project.sync_policy
    pt = session.get(ProjectType, project.type_id)
    return pt.default_sync_policy if pt is not None else None


def should_sync(session: Session, level: str, project) -> bool:
    """Синкать ли сущность уровня ``level`` проекта ``project`` наружу."""
    field = _LEVEL_FIELD.get(level)
    if field is None:
        return False
    slug = _resolve_policy_slug(session, project)
    if not slug:
        return False
    sp = session.get(SyncPolicy, slug)
    if sp is None:
        return False
    return getattr(sp, field) == 1


__all__ = ["should_sync"]
