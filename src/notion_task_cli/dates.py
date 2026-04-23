"""Парсинг «человеческих» дат: сегодня, завтра, +3д, 2026-04-25 15:00."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Literal

from dateutil import parser as date_parser
from dateutil import tz


_WEEKDAYS = {
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
    "пятница": 4, "суббота": 5, "воскресенье": 6,
}


def _today(tz_name: str) -> date:
    return datetime.now(tz.gettz(tz_name)).date()


def parse_human_date(
    s: str, *, tz_name: str = "Europe/Moscow"
) -> datetime | date:
    """Возвращает datetime (если указано время) либо date (если только день).
    Бросает ValueError, если не распарсили."""
    raw = s.strip().lower()
    today = _today(tz_name)

    if raw in {"today", "сегодня"}:
        return today
    if raw in {"tomorrow", "завтра"}:
        return today + timedelta(days=1)
    if raw in {"yesterday", "вчера"}:
        return today - timedelta(days=1)

    m = re.fullmatch(r"([+\-])(\d+)\s*([dдwнmм]?)", raw.replace(" ", ""))
    if m:
        sign = 1 if m.group(1) == "+" else -1
        n = int(m.group(2))
        unit = m.group(3) or "d"
        if unit in {"d", "д"}:
            return today + timedelta(days=sign * n)
        if unit in {"w", "н"}:
            return today + timedelta(weeks=sign * n)

    for name, wd in _WEEKDAYS.items():
        if raw == name:
            delta = (wd - today.weekday()) % 7 or 7
            return today + timedelta(days=delta)

    try:
        dt = date_parser.parse(
            s, dayfirst=False, yearfirst=True, fuzzy=False
        )
    except (ValueError, date_parser.ParserError) as exc:
        raise ValueError(f"не могу распарсить дату: {s!r}") from exc
    # если во входной строке не было цифр часов — возвращаем date
    if not re.search(r"\d[\s:T]\d", s):
        return dt.date()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz.gettz(tz_name))
    return dt


# ---- bucket helpers ----


Bucket = Literal["overdue", "today", "upcoming", "someday", "no_date"]


def bucketize(
    d: datetime | date | None, *, tz_name: str = "Europe/Moscow", horizon_days: int = 7
) -> Bucket:
    if d is None:
        return "no_date"
    today = _today(tz_name)
    the_date = d.date() if isinstance(d, datetime) else d
    if the_date < today:
        return "overdue"
    if the_date == today:
        return "today"
    if the_date <= today + timedelta(days=horizon_days):
        return "upcoming"
    return "someday"
