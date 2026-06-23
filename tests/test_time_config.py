"""Часовой пояс PM-БД конфигурируется (env/конфиг), не захардкожен.

Каноническая функция — `local_now` (offset из конфига). Дефолт — MSK (UTC+3).
"""
from __future__ import annotations

from datetime import timedelta

import pytest


@pytest.fixture(autouse=True)
def _reset_tz():
    """Изоляция: кэш tz — модульный глобал; сбрасываем до и после теста."""
    from atlas.pm import _time

    _time.reset_timezone_cache()
    yield
    _time.reset_timezone_cache()


def test_default_offset_is_msk(monkeypatch):
    monkeypatch.delenv("ATLAS_TIMEZONE", raising=False)
    from atlas.pm import _time

    _time.reset_timezone_cache()
    assert _time.local_tz().utcoffset(None) == timedelta(hours=3)


def test_env_overrides_offset(monkeypatch):
    from atlas.pm import _time

    monkeypatch.setenv("ATLAS_TIMEZONE", "+00:00")
    _time.reset_timezone_cache()
    assert _time.local_tz().utcoffset(None) == timedelta(0)

    monkeypatch.setenv("ATLAS_TIMEZONE", "+05:30")
    _time.reset_timezone_cache()
    assert _time.local_tz().utcoffset(None) == timedelta(hours=5, minutes=30)

    monkeypatch.setenv("ATLAS_TIMEZONE", "-8")
    _time.reset_timezone_cache()
    assert _time.local_tz().utcoffset(None) == timedelta(hours=-8)


def test_canonical_function_is_local_now():
    """`local_now` — каноническое имя; прежнее `msk_now` переименовано (удалено)."""
    from atlas.pm import _time

    assert callable(_time.local_now)
    assert not hasattr(_time, "msk_now")


def test_invalid_offset_falls_back_to_default(monkeypatch):
    from atlas.pm import _time

    monkeypatch.setenv("ATLAS_TIMEZONE", "не-оффсет")
    _time.reset_timezone_cache()
    assert _time.local_tz().utcoffset(None) == timedelta(hours=3)
