"""Отправка pending-outbox на внешний backend-сервис (Atlas → /events)."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from atlas.sync import outbox


async def push_pending(session: Session, client, *, limit: int = 100) -> dict:
    """Выгрузить pending-события батчем; пометить sent. → {sent: N}.

    ``client`` — объект с async ``push_events(list[dict])`` (BackendClient).
    """
    items = outbox.pending(session, limit=limit)
    if not items:
        return {"sent": 0}
    events = [json.loads(o.payload_json) for o in items]
    await client.push_events(events)
    for o in items:
        outbox.mark_sent(session, o.id)
    session.commit()
    return {"sent": len(items)}


__all__ = ["push_pending"]
