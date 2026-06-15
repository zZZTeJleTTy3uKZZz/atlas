"""Входящий синк через long-poll (хаб → Atlas): poll → apply → курсор."""
from __future__ import annotations

from sqlalchemy.orm import Session

from atlas.pm.sync import apply, cursor


async def pull_once(
    session: Session, client, *, channel: str = "atlas", timeout: float = 25.0
) -> dict:
    """Один цикл: long-poll событий позже курсора → применить → продвинуть курсор.

    ``client`` — объект с async ``poll_events(since, *, timeout)`` (BackendClient),
    возвращающим ``{events: [...], cursor: str|None}``. → {applied, cursor}.
    """
    since = cursor.get_cursor(session, channel)
    resp = await client.poll_events(since, timeout=timeout)
    events = resp.get("events") or []
    applied = 0
    for ev in events:
        apply.apply_event(session, ev)
        applied += 1
    new_cursor = resp.get("cursor")
    if new_cursor:
        cursor.set_cursor(session, channel, new_cursor)
    session.commit()
    return {"applied": applied, "cursor": cursor.get_cursor(session, channel)}


__all__ = ["pull_once"]
