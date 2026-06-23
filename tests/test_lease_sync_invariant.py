"""Инвариант синка для Волны 8: lease-поля НЕ уходят в ядро.

`_task_payload` (mapper исходящих событий) не должен сериализовать
lease_owner/session/origin/claimed_at/lease_expires_at/lock_version — это
ЛОКАЛЬНАЯ координация агентов; в ядро она не синкается (иначе протухание lease
на одной машине затрёт состояние на другой через LWW).
"""
from __future__ import annotations

from atlas.models import Epic, Task
from atlas.sync.mapper import _epic_payload, _task_payload

_LEASE_KEYS = {
    "lease_owner", "lease_session_id", "lease_origin",
    "claimed_at", "lease_expires_at", "lock_version",
}


def test_task_payload_excludes_lease_fields():
    t = Task(
        slug="s", title="T", status="todo", priority="P2",
        cpp_description="ц", due_date=None, backend_id="b",
        lease_owner="x", lease_session_id="sess", lock_version=5,
    )
    payload = _task_payload(t)
    assert _LEASE_KEYS.isdisjoint(payload.keys())


def test_epic_payload_excludes_lease_fields():
    """Эпик «Групповой lease»: epic lease-поля НЕ уходят в ядро (как у task)."""
    e = Epic(
        slug="e", title="E", status="active", backend_id="b",
        lease_owner="x", lease_session_id="sess", lease_origin="atlas",
        lock_version=3,
    )
    payload = _epic_payload(e)
    assert _LEASE_KEYS.isdisjoint(payload.keys())
