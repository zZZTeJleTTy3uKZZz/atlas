"""Курсор pull-канала (SyncCursor): ISO occurred_at последнего применённого."""
from __future__ import annotations

from sqlalchemy.orm import Session

from atlas.models import SyncCursor


def get_cursor(session: Session, channel: str) -> str | None:
    sc = session.get(SyncCursor, channel)
    return sc.cursor if sc is not None else None


def set_cursor(session: Session, channel: str, value: str | None) -> None:
    sc = session.get(SyncCursor, channel)
    if sc is None:
        sc = SyncCursor(channel=channel, cursor=value)
        session.add(sc)
    else:
        sc.cursor = value


__all__ = ["get_cursor", "set_cursor"]
