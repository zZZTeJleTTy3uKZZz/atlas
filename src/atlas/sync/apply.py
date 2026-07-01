"""Применение входящего события (хаб → Atlas) к локальному стору (F3d).

Идемпотентно по backend_id: update существующих, create best-effort (с
резолвом родителя по backend_id/slug), delete = soft archived_at. Неизвестные
сущности/без родителя — skip (не плодим кривые записи).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas._time import local_now
from atlas.models import ChecklistItem, Epic, Project, Task


def _by_backend(session: Session, model, backend_id: str):
    return session.execute(
        select(model).where(model.backend_id == backend_id)
    ).scalar_one_or_none()


def _resolve_project(session: Session, payload: dict) -> Project | None:
    pbid = payload.get("project_backend_id")
    if pbid:
        p = _by_backend(session, Project, pbid)
        if p is not None:
            return p
    pslug = payload.get("project_slug")
    if pslug:
        return session.execute(
            select(Project).where(Project.slug == pslug)
        ).scalar_one_or_none()
    return None


def _norm_task_status(raw: Any) -> str:
    """Нормализовать входящий status задачи из внешнего payload (sync-ingress).

    Защита границы: ядро/хаб может прислать legacy-'backlog' (был валиден до
    редиза статусов) — приводим к 'todo' (симметрично миграции данных). Неизвестный
    статус → 'todo', чтобы не уронить sync нарушением CHECK на свежих БД."""
    from atlas.task_status import LIFECYCLE_STATUSES, PLANNING_STATUSES

    if not raw:
        return "todo"
    s = str(raw)
    if s == "backlog":  # уровень backlog переехал в пул idea; задача = todo
        return "todo"
    return s if s in (PLANNING_STATUSES | LIFECYCLE_STATUSES) else "todo"


_VALID_PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})


def _norm_priority(raw: Any) -> str:
    """Нормализовать входящий priority (CHECK ck_tasks_priority: P0..P3).

    Невалидное/пустое → 'P2'. Без этого payload с priority='URGENT' роняет commit
    (IntegrityError) и зацикливает poison-событие в pull_loop (курсор не двигается)."""
    s = str(raw) if raw else ""
    return s if s in _VALID_PRIORITIES else "P2"


def _upsert_task(session: Session, bid: str, payload: dict) -> dict:
    task = _by_backend(session, Task, bid)
    if task is None:
        proj = _resolve_project(session, payload)
        if proj is None:
            return {"skipped": "no_project"}
        task = Task(
            backend_id=bid, project_id=proj.id,
            title=payload.get("title") or "(no title)",
            cpp_description=payload.get("cpp") or "—",
            priority=_norm_priority(payload.get("priority")),
            status=_norm_task_status(payload.get("status")),
            slug=payload.get("slug"),
        )
        session.add(task)
        return {"created": "task"}
    for key in ("title", "status", "priority"):
        if payload.get(key) is not None:
            if key == "status":
                value = _norm_task_status(payload[key])
            elif key == "priority":
                value = _norm_priority(payload[key])
            else:
                value = payload[key]
            setattr(task, key, value)
    if payload.get("cpp"):
        task.cpp_description = payload["cpp"]
    return {"updated": "task"}


def _upsert_epic(session: Session, bid: str, payload: dict) -> dict:
    epic = _by_backend(session, Epic, bid)
    if epic is None:
        proj = _resolve_project(session, payload)
        if proj is None:
            return {"skipped": "no_project"}
        epic = Epic(
            backend_id=bid, project_id=proj.id,
            title=payload.get("title") or "(epic)",
            status=payload.get("status") or "active",
            slug=payload.get("slug"),
        )
        session.add(epic)
        return {"created": "epic"}
    if payload.get("title") is not None:
        epic.title = payload["title"]
    if payload.get("status") is not None:
        epic.status = payload["status"]
    return {"updated": "epic"}


def _parse_due(value: Any):
    """due (ISO "YYYY-MM-DD" или полный ISO) → datetime | None."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _upsert_checklist(session: Session, bid: str, payload: dict) -> dict:
    """Поля ЯДРА (контракт checklist_item): title→text, done→is_done(int 0/1),
    order_idx→position, due→due_date. Родитель резолвится по
    payload["parent_task_backend_id"] через Task.backend_id."""
    ci = _by_backend(session, ChecklistItem, bid)
    if ci is None:
        tbid = payload.get("parent_task_backend_id")
        task = _by_backend(session, Task, tbid) if tbid else None
        if task is None:
            return {"skipped": "no_task"}
        ci = ChecklistItem(
            backend_id=bid, task_id=task.id,
            text=payload.get("title") or "",
            is_done=int(bool(payload.get("done"))),
            position=int(payload.get("order_idx") or 0),
            due_date=_parse_due(payload.get("due")),
        )
        session.add(ci)
        return {"created": "checklist"}
    if payload.get("title") is not None:
        ci.text = payload["title"]
    if payload.get("done") is not None:
        ci.is_done = int(bool(payload["done"]))
    if payload.get("order_idx") is not None:
        ci.position = int(payload["order_idx"])
    if "due" in payload:
        ci.due_date = _parse_due(payload.get("due"))
    return {"updated": "checklist"}


def _delete(session: Session, kind: str, bid: str) -> dict:
    model = {"task": Task, "epic": Epic, "checklist_item": ChecklistItem}.get(kind)
    if model is None:
        return {"skipped": f"kind:{kind}"}
    obj = _by_backend(session, model, bid)
    if obj is None:
        return {"skipped": "not_found"}
    if hasattr(obj, "archived_at"):
        obj.archived_at = local_now()
    else:
        session.delete(obj)
    return {"deleted": kind}


# Ключ = entity_kind НА ПРОВОДЕ. Ядро шлёт пункты как "checklist_item" (канон),
# поэтому ключ именно такой (НЕ внутренний "checklist").
_UPSERT = {
    "task": _upsert_task,
    "epic": _upsert_epic,
    "checklist_item": _upsert_checklist,
}


def apply_event(session: Session, event: dict[str, Any]) -> dict:
    """Применить одно событие к локальному стору. Идемпотентно по backend_id."""
    kind = event.get("entity_kind", "")
    op = event.get("op", "")
    bid = event.get("entity_id")
    payload = event.get("payload_json") or {}
    if not bid:
        return {"skipped": "no_entity_id"}
    if op == "delete":
        return _delete(session, kind, bid)
    handler = _UPSERT.get(kind)
    if handler is None:
        return {"skipped": f"kind:{kind}"}
    return handler(session, bid, payload)


__all__ = ["apply_event"]
