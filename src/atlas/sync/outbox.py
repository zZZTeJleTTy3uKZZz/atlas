"""Локальная очередь исходящих операций (Atlas → хаб).

enqueue консультируется с policy.should_sync (потолок проекта) и кладёт
готовый EventIn-payload в Outbox. push (F3c push.py) читает pending и шлёт.
"""
from __future__ import annotations

import json
import os

from replicationkit import queue
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas._time import local_now
from atlas.models import ChecklistItem, Epic, Outbox, Project, Task
from atlas.sync import mapper, policy


def _backend_connected() -> bool:
    """Backend подключён? base_url (≠ localhost-плейсхолдер) И api_key. Без него
    `sync push` не пойдёт — держать outbox бессмысленно, он копится вхолостую (#879)."""
    try:
        from atlas.appconfig import load_config, resolve_api_key

        cfg = load_config()
        has_url = bool(cfg.base_url and cfg.base_url != "http://localhost:8000")
        return has_url and bool(resolve_api_key(cfg))
    except Exception:
        return False


def _enqueue_enabled() -> bool:
    """Ставить ли операции в outbox. Форс ``ATLAS_SYNC_ENQUEUE_FORCE=1`` (тесты —
    проверяют механику независимо от backend); иначе — только если backend подключён.

    Без гейта outbox рос на КАЖДЫЙ task add/update даже без backend (никто не читал
    очередь — `sync_cursors` пуст) — мёртвая нагрузка на горячем пути записи (#879)."""
    if os.environ.get("ATLAS_SYNC_ENQUEUE_FORCE") == "1":
        return True
    return _backend_connected()


def enqueue(
    session: Session, op: str, entity_kind: str, obj, *, project, portal_id: str
) -> Outbox | None:
    """Поставить операцию в outbox, ЕСЛИ backend подключён И политика проекта разрешает.

    Возвращает созданный Outbox или None (backend не подключён / уровень запрещён политикой).
    """
    if not _enqueue_enabled():
        return None  # backend не подключён — не копим мёртвую очередь (#879)
    if os.environ.get("ATLAS_SYNC_ENQUEUE_FORCE") != "1":
        from atlas.appconfig import load_config

        selected = load_config().sync_projects
        if selected and project.slug not in selected:
            return None
    if not policy.should_sync(session, entity_kind, project):
        return None
    members = mapper.assignees(session, obj) if entity_kind == "task" else None
    # checklist: родитель-Task несёт backend_id для parent_task_backend_id —
    # ядру он нужен, чтобы привязать пункт к задаче.
    parent_task = (
        session.get(Task, obj.task_id)
        if entity_kind == "checklist" and getattr(obj, "task_id", None)
        else None
    )
    event = mapper.to_event(
        op, entity_kind, obj, portal_id=portal_id, project=project,
        assignees=members, parent_task=parent_task,
    )
    ob = Outbox(
        op=op,
        entity_kind=entity_kind,
        entity_id=obj.id,
        payload_json=json.dumps(event, ensure_ascii=False, default=str),
    )
    session.add(ob)
    return ob


def _project_slug(session: Session, row: Outbox) -> str | None:
    """Проект события: сначала снимок на проводе, затем локальная сущность."""
    try:
        payload = json.loads(row.payload_json).get("payload_json") or {}
    except (TypeError, ValueError):
        payload = {}
    slug = payload.get("project_slug")
    if slug:
        return str(slug)
    if row.entity_kind == "project":
        return payload.get("slug")
    project_id = None
    if row.entity_kind == "task":
        task = session.get(Task, row.entity_id)
        project_id = task.project_id if task else None
    elif row.entity_kind == "epic":
        epic = session.get(Epic, row.entity_id)
        project_id = epic.project_id if epic else None
    elif row.entity_kind == "checklist":
        item = session.get(ChecklistItem, row.entity_id)
        task = session.get(Task, item.task_id) if item else None
        project_id = task.project_id if task else None
    project = session.get(Project, project_id) if project_id else None
    return project.slug if project else None


def pending(
    session: Session, *, limit: int = 100, projects: set[str] | None = None,
) -> list[Outbox]:
    """Невыгруженные записи старые первыми, опционально по разрешённым проектам."""
    stmt = (
        select(Outbox)
        .where(Outbox.status == "pending")
        .order_by(Outbox.created_at)
    )
    if projects is None:
        return list(session.execute(stmt.limit(limit)).scalars().all())
    selected = []
    for row in session.execute(stmt).scalars():
        if _project_slug(session, row) in projects:
            selected.append(row)
            if len(selected) >= limit:
                break
    return selected


def mark_sent(session: Session, outbox_id: str) -> None:
    ob = session.get(Outbox, outbox_id)
    if ob is not None:
        ob.status = "sent"
        ob.sent_at = local_now()


#: Порог неудачных попыток для ВРЕМЕННОГО отказа: после него запись уходит из
#: очереди в status='failed', чтобы один битый event не держал батч.
MAX_PUSH_ATTEMPTS = queue.MAX_ATTEMPTS


def mark_failed(
    session: Session,
    outbox_id: str,
    error: str | BaseException,
    *,
    max_attempts: int = MAX_PUSH_ATTEMPTS,
) -> None:
    """Учесть неудачную попытку отправки: attempts++, причина, судьба записи.

    Судьбу решает кит доставки (`replicationkit.queue.decide`), и решение
    зависит от ВИДА отказа — это и есть закрытие дыры 2 волны 7 (Atlas #2718):

    * постоянный отказ (сервер не примет это никогда: 400/403/404/409/422)
      уходит в ``failed`` СРАЗУ. Раньше он гонял пять кругов наравне с обрывом
      сети, а батч отправляется целиком — то есть одно такое событие роняло
      каждый push и задерживало всю очередь;
    * временный (сеть, таймаут, 5xx, 429) остаётся ``pending`` до порога.

    ``error`` принимает и исключение, и строку: строку классифицировать не по
    чему, поэтому она считается временной — прежнее поведение вызывающих,
    которые уже передавали текст.
    """
    ob = session.get(Outbox, outbox_id)
    if ob is None:
        return
    ob.attempts = (ob.attempts or 0) + 1
    exc = error if isinstance(error, BaseException) else Exception(str(error))
    verdict = queue.decide(exc, ob.attempts, max_attempts=max_attempts)
    ob.status = verdict.status
    ob.last_error = verdict.reason[:500]


__all__ = ["enqueue", "pending", "mark_sent", "mark_failed"]
