"""Входящий синк через long-poll (хаб → Atlas): poll → apply → курсор."""
from __future__ import annotations

from sqlalchemy.orm import Session

from atlas.sync import apply, cursor


async def pull_once(
    session: Session, client, *, channel: str = "atlas", timeout: float = 25.0,
    scope: str = "all",
) -> dict:
    """Один цикл: long-poll событий позже курсора → применить → продвинуть курсор.

    ``client`` — объект с async ``poll_events(since, *, timeout, scope)``
    (BackendClient), возвращающим ``{events: [...], cursor: str|None}``.
    ``scope`` — профиль видимости: ``all`` (все) | ``personal`` (мои задачи).
    → {applied, cursor}.
    """
    since = cursor.get_cursor(session, channel)
    resp = await client.poll_events(since, timeout=timeout, scope=scope)
    events = resp.get("events") or []
    applied = 0
    skipped: list[dict] = []
    for ev in events:
        # [12] apply_event штатно возвращает {'skipped': причина} — напр. task/epic без
        # резолвленного проекта, checklist без родительской задачи (гонка порядка
        # доставки). Раньше applied++ считался безусловно, и skip выглядел успехом:
        # потеря событий была НЕВИДИМА. Теперь считаем раздельно и отдаём наружу
        # (watch_loop логирует результат) — так пропуск виден в логе.
        res = apply.apply_event(session, ev) or {}
        reason = res.get("skipped") if isinstance(res, dict) else None
        if reason:
            skipped.append({"kind": ev.get("entity_kind"), "reason": str(reason)})
        else:
            applied += 1
    new_cursor = resp.get("cursor")
    if new_cursor:
        cursor.set_cursor(session, channel, new_cursor)
    session.commit()
    out = {"applied": applied, "cursor": cursor.get_cursor(session, channel)}
    if skipped:  # видимость потери; полноценный retry-буфер — отдельная задача
        out["skipped"] = len(skipped)
        out["skipped_details"] = skipped[:10]
    return out


async def watch_loop(
    engine, client, *, channel: str = "atlas", timeout: float = 25.0,
    scope: str = "all", on_result=None, max_backoff: float = 60.0, _sleep=None,
) -> None:
    """Бесконечный устойчивый цикл pull: сетевые/HTTP-ошибки НЕ валят цикл —
    логируются через on_result и ретраятся с экспоненциальным backoff
    (сброс при успехе). KeyboardInterrupt/CancelledError пробрасываются (стоп).
    """
    import asyncio

    from atlas.db import make_session

    sleep = _sleep or asyncio.sleep
    backoff = 1.0
    while True:
        try:
            with make_session(engine) as session:
                result = await pull_once(session, client, channel=channel,
                                         timeout=timeout, scope=scope)
            backoff = 1.0
            if on_result is not None:
                on_result(result)
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001 — устойчивость важнее точечной обработки
            if on_result is not None:
                on_result({"error": str(exc), "retry_in": backoff})
            await sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


__all__ = ["pull_once", "watch_loop"]
