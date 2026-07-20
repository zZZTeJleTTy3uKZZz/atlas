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
    try:
        await client.push_events(events)
    except Exception as exc:  # noqa: BLE001 — учитываем попытку и пробрасываем
        # [13] Раньше ошибка просто вылетала: mark_failed нигде не вызывался,
        # attempts/last_error оставались пустыми, а батч отправлялся заново целиком —
        # одно перманентно-отвергаемое событие держало очередь вечно (poison-pill).
        for o in items:
            outbox.mark_failed(session, o.id, str(exc))
        session.commit()
        raise
    for o in items:
        outbox.mark_sent(session, o.id)
    session.commit()
    return {"sent": len(items)}


__all__ = ["push_pending"]
