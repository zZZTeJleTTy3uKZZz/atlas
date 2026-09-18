"""Отправка pending-outbox на внешний backend-сервис (Atlas → /events)."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from atlas.sync import outbox


async def push_pending(
    session: Session, client, *, limit: int = 100,
    projects: set[str] | None = None,
) -> dict:
    """Выгрузить pending-события батчем; пометить sent. → {sent: N}.

    ``client`` — объект с async ``push_events(list[dict])`` (BackendClient).
    """
    items = outbox.pending(session, limit=limit, projects=projects)
    if not items:
        return {"sent": 0}
    events = [json.loads(o.payload_json) for o in items]
    try:
        await client.push_events(events)
    except Exception as exc:  # noqa: BLE001 — учитываем попытку и пробрасываем
        # [13] Раньше ошибка просто вылетала: mark_failed нигде не вызывался,
        # attempts/last_error оставались пустыми, а батч отправлялся заново целиком —
        # одно перманентно-отвергаемое событие держало очередь вечно (poison-pill).
        #
        # Волна 7 (Atlas #2718), дыра 2: наверх уходит САМО исключение, а не его
        # текст. Текст классифицировать не по чему, и до волны любой отказ вёл по
        # одной лестнице попыток — невалидное навсегда событие крутилось пять
        # кругов, а обрыв сети на пятом круге навсегда выбрасывал здоровое.
        for o in items:
            outbox.mark_failed(session, o.id, exc)
        session.commit()
        raise
    for o in items:
        outbox.mark_sent(session, o.id)
    session.commit()
    return {"sent": len(items)}


__all__ = ["push_pending"]
