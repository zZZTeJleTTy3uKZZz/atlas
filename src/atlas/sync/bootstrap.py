"""Явный переход к концу старой ленты перед выборочной загрузкой проектов."""
from __future__ import annotations

from sqlalchemy.orm import Session

from atlas.sync import cursor


async def bootstrap_cursor(
    session: Session, client, *, channel: str = "atlas", dry_run: bool = False,
    max_pages: int = 10000,
) -> dict:
    """Прочитать адресную ленту до конца и сохранить её курсор без применения.

    Используется перед `sync seed`: историю чужих проектов сознательно
    пропускаем, затем отправляем актуальные снимки выбранных задач.
    """
    since = None
    count = 0
    for _ in range(max_pages):
        response = await client.poll_events(since, timeout=0, scope="all")
        events = response.get("events") or []
        if not events:
            break
        candidate = str(response.get("cursor") or "")
        if not candidate.isdecimal() or candidate == since:
            raise ValueError("Хаб не продвинул числовой курсор")
        count += len(events)
        since = candidate
    else:
        raise ValueError("Слишком длинная лента для bootstrap")
    if since is not None and not dry_run:
        cursor.set_cursor(session, f"{channel}:hub", since)
        session.commit()
    return {"skipped_events": count, "cursor": since, "dry_run": dry_run}


__all__ = ["bootstrap_cursor"]
