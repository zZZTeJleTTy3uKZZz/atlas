"""ORM-сущность Atlas → EventIn-payload для backend-хаба (F3c).

Контракт EventIn бэка: {entity_kind, op, entity_id, payload_json, source_portal_id}.
entity_id = backend_id если есть, иначе локальный id (бэк свяжет через
entity_link по source_portal_id). payload_json — ключевые поля сущности.
"""
from __future__ import annotations

from typing import Any


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def _project_payload(p: Any) -> dict:
    return {"slug": p.slug, "name": p.name, "backend_id": p.backend_id}


def _epic_payload(e: Any) -> dict:
    return {
        "slug": e.slug, "title": e.title, "status": e.status,
        "backend_id": e.backend_id,
    }


def _task_payload(t: Any) -> dict:
    return {
        "slug": t.slug, "title": t.title, "status": t.status,
        "priority": t.priority, "cpp": t.cpp_description,
        "due_date": _iso(t.due_date), "backend_id": t.backend_id,
    }


def _checklist_payload(c: Any) -> dict:
    return {
        "text": c.text, "is_done": c.is_done, "position": c.position,
        "backend_id": c.backend_id,
    }


_PAYLOAD = {
    "project": _project_payload,
    "epic": _epic_payload,
    "task": _task_payload,
    "checklist": _checklist_payload,
}


def to_event(
    op: str, entity_kind: str, obj: Any, *, portal_id: str, project: Any = None
) -> dict:
    """Построить EventIn-dict из ORM-сущности.

    ``project`` (если передан) добавляет в payload ``project_slug`` для task/epic —
    ядру нужен slug проекта-контейнера, чтобы привязать сущность (иначе
    ``_apply_to_core`` уходит в ``skipped_no_project``). enqueue знает проект.
    """
    build = _PAYLOAD[entity_kind]
    backend_id = getattr(obj, "backend_id", None)
    payload = build(obj)
    if project is not None and entity_kind in ("task", "epic"):
        payload["project_slug"] = project.slug
    return {
        "entity_kind": entity_kind,
        "op": op,
        "entity_id": backend_id or obj.id,
        "payload_json": payload,
        "source_portal_id": portal_id,
    }


__all__ = ["to_event"]
