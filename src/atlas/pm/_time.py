"""Time helpers for PM-system.

Naive Moscow time (UTC+3) — канонический timestamp всей PM-БД.

Почему Moscow time, а не UTC:
- Дмитрий работает в Москве (UTC+3). Timestamp'ы в `action_log`, `created_at`,
  `last_touched_at` читаются глазами и должны совпадать с часами на стене.
- Россия с 2014 не переходит на летнее время — offset константный, без DST-сюрпризов.

Почему naive (без tzinfo):
- SQLAlchemy columns в `atlas.pm.models` объявлены как `DateTime` без
  `timezone=True`. Запись tz-aware значений либо (a) молча теряет tz в SQLite,
  либо (b) бросает на PostgreSQL. Naive — совместимо и с SQLite, и с будущим
  PostgreSQL (с условием что все пишут в одной tz).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


MOSCOW_TZ = timezone(timedelta(hours=3), name="MSK")


def msk_now() -> datetime:
    """Текущее московское время как naive datetime (UTC+3, без tzinfo).

    Заменитель устаревшего `datetime.utcnow()` (который возвращал naive UTC —
    отличался от стенных часов на 3 часа). Именовано `msk_now` а не `now`,
    чтобы не конфликтовать с локальными переменными `now = ...` в кодовой базе.
    """
    return datetime.now(MOSCOW_TZ).replace(tzinfo=None)
