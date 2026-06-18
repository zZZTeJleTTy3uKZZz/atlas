"""Локальная очередь исходящих операций (Atlas → хаб).

enqueue консультируется с policy.should_sync (потолок проекта) и кладёт
готовый EventIn-payload в Outbox. push (F3c push.py) читает pending и шлёт.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm._time import msk_now
from atlas.pm.models import Outbox
from atlas.pm.sync import mapper, policy


def enqueue(
    session: Session, op: str, entity_kind: str, obj, *, project, portal_id: str
) -> Outbox | None:
    """Поставить операцию в outbox, ЕСЛИ политика проекта разрешает уровень.

    Возвращает созданный Outbox или None (если синк уровня запрещён политикой).
    """
    if not policy.should_sync(session, entity_kind, project):
        return None
    members = mapper.assignees(session, obj) if entity_kind == "task" else None
    event = mapper.to_event(
        op, entity_kind, obj, portal_id=portal_id, project=project,
        assignees=members,
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
        ob.sent_at = msk_now()


def mark_failed(session: Session, outbox_id: str, error: str) -> None:
    ob = session.get(Outbox, outbox_id)
    if ob is not None:
        ob.status = "failed"
        ob.attempts = (ob.attempts or 0) + 1
        ob.last_error = str(error)[:500]


__all__ = ["enqueue", "pending", "mark_sent", "mark_failed"]
