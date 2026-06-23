"""Инвариант синка для Волны 8: lease-поля НЕ уходят в ядро.

`_task_payload` (mapper исходящих событий) не должен сериализовать
lease_owner/session/origin/claimed_at/lease_expires_at/lock_version — это
ЛОКАЛЬНАЯ координация агентов; в ядро она не синкается (иначе протухание lease
на одной машине затрёт состояние на другой через LWW).
"""
from __future__ import annotations

from atlas.pm.models import Task
from atlas.pm.sync.mapper import _task_payload

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
