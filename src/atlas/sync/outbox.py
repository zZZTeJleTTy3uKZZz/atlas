"""Локальная очередь исходящих операций (Atlas → хаб).

enqueue консультируется с policy.should_sync (потолок проекта) и кладёт
готовый EventIn-payload в Outbox. push (F3c push.py) читает pending и шлёт.
"""
from __future__ import annotations

import json
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas._time import local_now
from atlas.models import Outbox, Task
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


def pending(session: Session, *, limit: int = 100) -> list[Outbox]:
    """Невыгруженные записи (status=pending), старые первыми."""
    stmt = (
        select(Outbox)
        .where(Outbox.status == "pending")
        .order_by(Outbox.created_at)
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def mark_sent(session: Session, outbox_id: str) -> None:
    ob = session.get(Outbox, outbox_id)
    if ob is not None:
        ob.status = "sent"
        ob.sent_at = local_now()


#: Порог неудачных попыток: после него запись считается «отравленной» (poison-pill)
#: и уходит из очереди в status='failed', чтобы один битый event не держал батч.
MAX_PUSH_ATTEMPTS = 5


def mark_failed(
    session: Session, outbox_id: str, error: str, *, max_attempts: int = MAX_PUSH_ATTEMPTS
) -> None:
    """Учесть неудачную попытку отправки (attempts++, last_error).

    В ``failed`` переводим ТОЛЬКО по достижении порога: одиночная сетевая ошибка
    не должна навсегда выбрасывать событие из очереди — до порога запись остаётся
    ``pending`` и уйдёт следующим push (#894 [13])."""
    ob = session.get(Outbox, outbox_id)
    if ob is None:
        return
    ob.attempts = (ob.attempts or 0) + 1
    ob.last_error = str(error)[:500]
    if ob.attempts >= max_attempts:
        ob.status = "failed"


__all__ = ["enqueue", "pending", "mark_sent", "mark_failed"]
