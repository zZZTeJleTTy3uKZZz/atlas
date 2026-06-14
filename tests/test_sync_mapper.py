"""F3c: mapper.to_event — ORM-сущность → EventIn-dict."""
from types import SimpleNamespace

from atlas.pm.sync import mapper


def test_task_event_uses_local_id_when_no_backend():
    task = SimpleNamespace(
        id="loc-1", backend_id=None, slug="acme-t1", title="T", status="todo",
        priority="P2", cpp_description="ц", due_date=None,
    )
    ev = mapper.to_event("create", "task", task, portal_id="atlas-local")
    assert ev["entity_kind"] == "task"
    assert ev["op"] == "create"
    assert ev["entity_id"] == "loc-1"
    assert ev["source_portal_id"] == "atlas-local"
    assert ev["payload_json"]["title"] == "T"


def test_event_prefers_backend_id():
    epic = SimpleNamespace(id="loc", backend_id="be-9", slug="e", title="E", status="active")
    ev = mapper.to_event("update", "epic", epic, portal_id="atlas-local")
    assert ev["entity_id"] == "be-9"
