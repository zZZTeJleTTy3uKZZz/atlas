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


# --------------------------------------------------------------------------- #
# PART A: ответственный/исполнитель доезжает в payload (assignee_slugs)        #
# --------------------------------------------------------------------------- #


def _make_task(**over):
    base = dict(
        id="loc-1", backend_id=None, slug="acme-t1", title="T", status="todo",
        priority="P2", cpp_description="ц", due_date=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_task_payload_includes_assignee_slugs_when_passed():
    """mapper кладёт participant-slug'и ответственных/исполнителей в payload под
    ключом assignee_slugs — отдельно от assignee_member_ids (тот несёт уже
    зарезолвленные core-member-id с Б24-пути; смешивать нельзя — сломает FK ядра)."""
    task = _make_task()
    ev = mapper.to_event(
        "create", "task", task, portal_id="atlas-local",
        assignee_slugs=["dmitry", "claude"],
    )
    assert ev["payload_json"]["assignee_slugs"] == ["dmitry", "claude"]


def test_task_payload_assignee_slugs_empty_by_default():
    """Без участников ключ присутствует как пустой список (стабильный контракт:
    ядро читает f.get(...) or [] — отсутствие и пустота эквивалентны, но явный
    [] не даёт мапперу терять ключ между версиями)."""
    task = _make_task()
    ev = mapper.to_event("create", "task", task, portal_id="atlas-local")
    assert ev["payload_json"]["assignee_slugs"] == []


def test_assignee_member_ids_not_polluted_with_slugs():
    """Ключ assignee_member_ids НЕ заполняется мапепром Atlas — Atlas не знает
    core-member-id, только slug'и; резолв slug→member_id — забота оркестратора (PART B)."""
    task = _make_task()
    ev = mapper.to_event(
        "create", "task", task, portal_id="atlas-local", assignee_slugs=["dmitry"],
    )
    assert "assignee_member_ids" not in ev["payload_json"]


def test_assignee_slugs_only_on_task_not_epic():
    """assignee_slugs — поле задачи; в epic-payload его быть не должно."""
    epic = SimpleNamespace(id="loc", backend_id=None, slug="e", title="E", status="active")
    ev = mapper.to_event(
        "create", "epic", epic, portal_id="atlas-local", assignee_slugs=["dmitry"],
    )
    assert "assignee_slugs" not in ev["payload_json"]
