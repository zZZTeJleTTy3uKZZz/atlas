"""Time helpers for PM-system.

Provides a drop-in replacement for the deprecated ``datetime.utcnow()``.
See Python 3.12+ deprecation notice; ``datetime.utcnow()`` is removed in 3.14.

Rationale for returning a **naive** UTC datetime (not tz-aware):
- SQLAlchemy columns in ``atlas.pm.models`` are declared as ``DateTime`` without
  ``timezone=True``. Persisting tz-aware values would either (a) silently drop
  tz info on SQLite or (b) raise on PostgreSQL. Keeping semantics identical to
  the former ``datetime.utcnow()`` avoids any schema migration.
- Callers that compare timestamps coming from the DB expect naive datetimes.
"""
from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return current UTC time as a **naive** ``datetime`` (no tzinfo).

    Equivalent to the deprecated ``datetime.utcnow()`` but uses the modern
    ``datetime.now(UTC)`` under the hood, then strips the tzinfo so the result
    is compatible with SQLAlchemy ``DateTime`` columns that lack
    ``timezone=True``.
    """
    return datetime.now(UTC).replace(tzinfo=None)
