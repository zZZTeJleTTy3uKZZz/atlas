"""Unit tests for ``atlas.pm._time.utcnow``."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from atlas.pm._time import utcnow


def _baseline_naive_utc() -> datetime:
    """Return naive-UTC `datetime` without using the deprecated `utcnow()`."""
    return datetime.now(UTC).replace(tzinfo=None)


class TestUtcNow:
    def test_returns_datetime_instance(self) -> None:
        assert isinstance(utcnow(), datetime)

    def test_is_naive_no_tzinfo(self) -> None:
        """Result must be naive so it stores cleanly into DateTime (no tz) columns."""
        now = utcnow()
        assert now.tzinfo is None

    def test_is_recent(self) -> None:
        """Returned value must be close to current wall-clock UTC."""
        before = _baseline_naive_utc()
        observed = utcnow()
        after = _baseline_naive_utc()

        # observed must fall within [before - 1s, after + 1s] to tolerate any
        # microsecond-level skew on slow CI.
        assert before - timedelta(seconds=1) <= observed <= after + timedelta(seconds=1)

    def test_triggers_no_deprecation_warning(self, recwarn) -> None:
        """Calling utcnow() must not raise the datetime.utcnow() deprecation."""
        utcnow()
        for w in recwarn.list:
            assert "utcnow" not in str(w.message).lower()
