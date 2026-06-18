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


def test_task_event_includes_project_slug():
    """payload задачи несёт project_slug проекта-контейнера — без него ядро
    _apply_to_core не резолвит проект и уходит в skipped_no_project."""
    task = SimpleNamespace(
        id="loc-1", backend_id=None, slug="acme-t1", title="T", status="todo",
        priority="P2", cpp_description="ц", due_date=None,
    )
    project = SimpleNamespace(slug="acme")
    ev = mapper.to_event("create", "task", task, portal_id="atlas-local", project=project)
    assert ev["payload_json"]["project_slug"] == "acme"


def test_epic_event_includes_project_slug():
    epic = SimpleNamespace(id="loc", backend_id=None, slug="e", title="E", status="active")
    project = SimpleNamespace(slug="acme")
    ev = mapper.to_event("create", "epic", epic, portal_id="atlas-local", project=project)
    assert ev["payload_json"]["project_slug"] == "acme"
