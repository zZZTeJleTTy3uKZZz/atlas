"""Time helpers for PM-system.

Naive local time как канонический timestamp всей PM-БД. Часовой пояс — это
**фиксированный offset из конфига** (`AtlasConfig.timezone`, дефолт MSK=UTC+3),
а НЕ хардкод: меняется в TOML или через env ``ATLAS_TIMEZONE`` без правки кода.

Почему фиксированный offset, а не IANA-зона с DST:
- Канон PM-БД — naive datetime (см. ниже). Фиксированный offset исключает
  DST-сюрпризы (Россия с 2014 без перехода; для других зон выбирается явный
  offset, под который пишут все участники одной БД).

Почему naive (без tzinfo):
- SQLAlchemy columns в `atlas.models` объявлены как `DateTime` без
  `timezone=True`. Запись tz-aware значений либо (a) молча теряет tz в SQLite,
  либо (b) бросает на PostgreSQL. Naive — совместимо и с SQLite, и с будущим
  PostgreSQL (с условием что все пишут в одной tz).

Каноническая функция — ``local_now`` (локальное время PM-БД, offset из
конфига). Прежнее имя ``msk_now`` переименовано в ``local_now`` во всех
вызовах: фиксированное "MSK" в имени вводило в заблуждение после того, как
часовой пояс стал конфигурируемым.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

# Дефолтный offset, если конфиг недоступен/не задан — MSK (UTC+3).
_DEFAULT_TZ = timezone(timedelta(hours=3), name="MSK")
# Сохранено для обратной совместимости (исторически экспортировалось отсюда).
MOSCOW_TZ = _DEFAULT_TZ

_cached_tz: timezone | None = None


def _parse_offset(raw: str) -> timedelta:
    """'+03:00' | '-05:30' | '+5' | '3' | 'UTC' → timedelta. Иначе ValueError."""
    s = (raw or "").strip().upper()
    if s in ("UTC", "Z", "GMT"):
        return timedelta(0)
    sign = 1
    if s and s[0] in "+-":
        sign = -1 if s[0] == "-" else 1
        s = s[1:]
    if ":" in s:
        h, m = s.split(":", 1)
        return sign * timedelta(hours=int(h), minutes=int(m))
    return sign * timedelta(hours=float(s))


def _resolve_offset_raw() -> str | None:
    """Сырое значение offset: env ATLAS_TIMEZONE → конфиг → None.

    Конфиг импортируется лениво (избегаем цикла `_time`←models, `appconfig`→clikit)
    и best-effort (на раннем импорте/без конфига → откат на дефолт).
    """
    env = os.environ.get("ATLAS_TIMEZONE")
    if env:
        return env
    try:
        from atlas.appconfig import load_config

        return getattr(load_config(), "timezone", None)
    except Exception:
        return None


def _build_tz() -> timezone:
    raw = _resolve_offset_raw()
    if not raw:
        return _DEFAULT_TZ
    try:
        return timezone(_parse_offset(raw))
    except (ValueError, TypeError):
        return _DEFAULT_TZ


def local_tz() -> timezone:
    """Сконфигурированный часовой пояс PM-БД (кэшируется на процесс)."""
    global _cached_tz
    if _cached_tz is None:
        _cached_tz = _build_tz()
    return _cached_tz


def reset_timezone_cache() -> None:
    """Сбросить кэш tz (для тестов / после смены конфига в рантайме)."""
    global _cached_tz
    _cached_tz = None


def local_now() -> datetime:
    """Текущее локальное время PM-БД как naive datetime (offset из конфига).

    Фактический offset берётся из ``AtlasConfig.timezone`` / env
    ``ATLAS_TIMEZONE`` (дефолт MSK=UTC+3). Заменитель устаревшего
    ``datetime.utcnow()``. Прежнее имя ``msk_now`` переименовано в ``local_now``.
    """
    return datetime.now(local_tz()).replace(tzinfo=None)
