"""F3c: mapper.to_event — ORM-сущность → EventIn-dict."""
from types import SimpleNamespace

from atlas.sync import mapper


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


def test_task_event_includes_project_backend_id():
    """payload задачи несёт project_backend_id (= core-id проекта-контейнера).
    Ядро резолвит контейнер по нему — надёжно при разнобое имён Atlas↔ядро
    (Atlas-slug 'mediyka' ≠ ядро-slug 'b24-group-44'); project_slug — fallback."""
    task = SimpleNamespace(
        id="loc-1", backend_id=None, slug="acme-t1", title="T", status="todo",
        priority="P2", cpp_description="ц", due_date=None,
    )
    project = SimpleNamespace(slug="mediyka", backend_id="core-44")
    ev = mapper.to_event("create", "task", task, portal_id="atlas-personal", project=project)
    assert ev["payload_json"]["project_backend_id"] == "core-44"
    assert ev["payload_json"]["project_slug"] == "mediyka"


def test_task_event_project_backend_id_none_when_unlinked():
    """Проект ещё не связан с ядром (backend_id=None) → ключ присутствует как None
    (стабильный контракт); ядро тогда падает в fallback по project_slug."""
    task = SimpleNamespace(
        id="loc-1", backend_id=None, slug="acme-t1", title="T", status="todo",
        priority="P2", cpp_description="ц", due_date=None,
    )
    project = SimpleNamespace(slug="mediyka", backend_id=None)
    ev = mapper.to_event("create", "task", task, portal_id="atlas-personal", project=project)
    assert ev["payload_json"]["project_backend_id"] is None


# --------------------------------------------------------------------------- #
# PART A: причастные доезжают в payload (assignees = [{slug, role}])           #
# --------------------------------------------------------------------------- #


def _make_task(**over):
    base = dict(
        id="loc-1", backend_id=None, slug="acme-t1", title="T", status="todo",
        priority="P2", cpp_description="ц", due_date=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_task_payload_includes_assignees_when_passed():
    """mapper кладёт причастных как [{slug, role}] в payload под ключом
    assignees — отдельно от assignee_member_ids (тот несёт уже зарезолвленные
    core-member-id с Б24-пути; смешивать нельзя — сломает FK ядра). Роль
    (responsible/executor) сохраняется."""
    task = _make_task()
    ev = mapper.to_event(
        "create", "task", task, portal_id="atlas-local",
        assignees=[
            {"slug": "owner", "role": "responsible"},
            {"slug": "claude", "role": "executor"},
        ],
    )
    assert ev["payload_json"]["assignees"] == [
        {"slug": "owner", "role": "responsible"},
        {"slug": "claude", "role": "executor"},
    ]


def test_task_payload_assignees_empty_by_default():
    """Без причастных ключ присутствует как пустой список (стабильный контракт:
    присутствие ключа = «полный список причастных» — сигнал reconcile для ядра)."""
    task = _make_task()
    ev = mapper.to_event("create", "task", task, portal_id="atlas-local")
    assert ev["payload_json"]["assignees"] == []


def test_assignee_member_ids_not_polluted_with_slugs():
    """Ключ assignee_member_ids НЕ заполняется мапепром Atlas — Atlas не знает
    core-member-id, только slug'и; резолв slug→member_id — забота оркестратора (PART B)."""
    task = _make_task()
    ev = mapper.to_event(
        "create", "task", task, portal_id="atlas-local",
        assignees=[{"slug": "owner", "role": "responsible"}],
    )
    assert "assignee_member_ids" not in ev["payload_json"]


def test_assignees_only_on_task_not_epic():
    """assignees — поле задачи; в epic-payload его быть не должно."""
    epic = SimpleNamespace(id="loc", backend_id=None, slug="e", title="E", status="active")
    ev = mapper.to_event(
        "create", "epic", epic, portal_id="atlas-local",
        assignees=[{"slug": "owner", "role": "responsible"}],
    )
    assert "assignees" not in ev["payload_json"]
