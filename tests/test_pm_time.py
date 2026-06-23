"""Unit tests for ``atlas.pm._time.now``."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from atlas.pm._time import MOSCOW_TZ, local_now


def _baseline_naive_moscow() -> datetime:
    """Naive Moscow time baseline без использования самого `now()`."""
    return datetime.now(MOSCOW_TZ).replace(tzinfo=None)


class TestNow:
    def test_returns_datetime_instance(self) -> None:
        assert isinstance(local_now(), datetime)

    def test_is_naive_no_tzinfo(self) -> None:
        """Result must be naive so it stores cleanly into DateTime (no tz) columns."""
        observed = local_now()
        assert observed.tzinfo is None

    def test_is_recent_moscow_time(self) -> None:
        """Returned value must be close to current Moscow wall-clock."""
        before = _baseline_naive_moscow()
        observed = local_now()
        after = _baseline_naive_moscow()

        assert before - timedelta(seconds=1) <= observed <= after + timedelta(seconds=1)

    def test_offset_is_plus_three_hours(self) -> None:
        """Moscow time должно опережать UTC ровно на 3 часа."""
        observed = local_now()
        utc_now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        delta = observed - utc_now_naive
        # допускаем ± 2 секунды на выполнение двух вызовов
        assert timedelta(hours=3) - timedelta(seconds=2) <= delta <= timedelta(hours=3) + timedelta(seconds=2)

    def test_triggers_no_deprecation_warning(self, recwarn) -> None:
        """Calling local_now() must not raise the datetime.utcnow() deprecation."""
        local_now()
        for w in recwarn.list:
            assert "utcnow" not in str(w.message).lower()
