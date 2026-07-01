"""F3f: двунаправленный синк checklist_item Atlas ↔ ядро.

WIRE-контракт (на проводе):
  entity_kind = "checklist_item" (канон ядра, НЕ "checklist")
  payload = {title, done(bool), due(ISO|null), order_idx(int), parent_task_backend_id}

Исходящее (mapper): ChecklistItem(text, is_done, position, due_date) +
родитель-Task → провод. Входящее (apply): провод → ChecklistItem с резолвом
родителя по Task.backend_id == parent_task_backend_id.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from atlas.models import (
    Base, ChecklistItem, Project, ProjectStatus, ProjectType, Task,
)
from atlas.sync import apply, mapper


# --------------------------------------------------------------------------- #
# mapper (ИСХОДЯЩЕЕ: Atlas → провод)                                          #
# --------------------------------------------------------------------------- #


def _ci(**over):
    base = dict(
        id="ci-loc", backend_id=None, text="Шаг", is_done=0, position=3,
        due_date=None, task_id="t-loc",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_checklist_event_wire_kind_is_checklist_item():
    """На проводе entity_kind = checklist_item (канон ядра), НЕ checklist."""
    ci = _ci()
    parent = SimpleNamespace(backend_id="task-be-1")
    ev = mapper.to_event(
        "create", "checklist", ci, portal_id="atlas-personal", parent_task=parent,
    )
    assert ev["entity_kind"] == "checklist_item"


def test_checklist_payload_core_fields():
    """payload содержит поля ЯДРА: title, done(bool), order_idx, due, parent."""
    ci = _ci(text="Сделать ревью", is_done=1, position=5, backend_id="ci-be")
    parent = SimpleNamespace(backend_id="task-be-1")
    ev = mapper.to_event(
        "update", "checklist", ci, portal_id="atlas-personal", parent_task=parent,
    )
    pl = ev["payload_json"]
    assert pl["title"] == "Сделать ревью"
    assert pl["done"] is True
    assert pl["order_idx"] == 5
    assert pl["parent_task_backend_id"] == "task-be-1"
    # старых internal-ключей быть не должно
    assert "text" not in pl
    assert "is_done" not in pl
    assert "position" not in pl


def test_checklist_payload_done_false_from_zero():
    ci = _ci(is_done=0)
    ev = mapper.to_event(
        "create", "checklist", ci, portal_id="atlas-personal",
        parent_task=SimpleNamespace(backend_id="task-be-1"),
    )
    assert ev["payload_json"]["done"] is False


def test_checklist_payload_due_iso_string():
    ci = _ci(due_date=datetime(2026, 6, 20, 14, 0, 0))
    ev = mapper.to_event(
        "create", "checklist", ci, portal_id="atlas-personal",
        parent_task=SimpleNamespace(backend_id="task-be-1"),
    )
    assert ev["payload_json"]["due"] == "2026-06-20"


def test_checklist_payload_due_none_when_absent():
    ci = _ci(due_date=None)
    ev = mapper.to_event(
        "create", "checklist", ci, portal_id="atlas-personal",
        parent_task=SimpleNamespace(backend_id="task-be-1"),
    )
    assert ev["payload_json"]["due"] is None


def test_checklist_entity_id_prefers_backend_id():
    ci = _ci(id="ci-loc", backend_id="ci-be")
    ev = mapper.to_event(
        "create", "checklist", ci, portal_id="atlas-personal",
        parent_task=SimpleNamespace(backend_id="task-be-1"),
    )
    assert ev["entity_id"] == "ci-be"


def test_checklist_parent_backend_id_none_when_no_parent():
    """parent_task не передан → ключ присутствует как None (стабильный контракт)."""
    ci = _ci()
    ev = mapper.to_event("create", "checklist", ci, portal_id="atlas-personal")
    assert ev["payload_json"]["parent_task_backend_id"] is None


# --------------------------------------------------------------------------- #
# apply (ВХОДЯЩЕЕ: провод → Atlas)                                           #
# --------------------------------------------------------------------------- #


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _project(s):
    t = ProjectType(slug="t", name="t")
    st = ProjectStatus(slug="a", name="a", order_idx=1)
    s.add_all([t, st]); s.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2",
                one_line_summary="x", backend_id="proj-be")
    s.add(p); s.flush()
    return p


def _task(s, p, backend_id="task-be-1"):
    t = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2",
             backend_id=backend_id, slug="p-t1")
    s.add(t); s.flush()
    return t


def test_apply_create_checklist_item_resolves_parent(session):
    p = _project(session)
    _task(session, p, backend_id="task-be-1")
    ev = {
        "entity_kind": "checklist_item", "op": "create", "entity_id": "ci-be",
        "payload_json": {
            "title": "Шаг А", "done": True, "order_idx": 2, "due": "2026-06-21",
            "parent_task_backend_id": "task-be-1",
        },
    }
    res = apply.apply_event(session, ev)
    session.commit()
    assert res["created"] == "checklist"
    ci = session.execute(
        select(ChecklistItem).where(ChecklistItem.backend_id == "ci-be")
    ).scalar_one()
    assert ci.text == "Шаг А"
    assert ci.is_done == 1
    assert ci.position == 2
    assert ci.due_date == datetime(2026, 6, 21)


def test_apply_create_skipped_when_parent_unknown(session):
    p = _project(session)
    _task(session, p, backend_id="task-be-1")
    ev = {
        "entity_kind": "checklist_item", "op": "create", "entity_id": "ci-be",
        "payload_json": {"title": "x", "parent_task_backend_id": "nope"},
    }
    res = apply.apply_event(session, ev)
    assert "skipped" in res


def test_apply_update_checklist_item_all_fields(session):
    p = _project(session)
    t = _task(session, p)
    ci = ChecklistItem(task_id=t.id, text="old", is_done=0, position=0,
                       backend_id="ci-be")
    session.add(ci); session.commit()
    ev = {
        "entity_kind": "checklist_item", "op": "update", "entity_id": "ci-be",
        "payload_json": {
            "title": "new", "done": True, "order_idx": 7, "due": "2026-06-25",
            "parent_task_backend_id": "task-be-1",
        },
    }
    res = apply.apply_event(session, ev)
    session.commit()
    assert res["updated"] == "checklist"
    got = session.execute(
        select(ChecklistItem).where(ChecklistItem.backend_id == "ci-be")
    ).scalar_one()
    assert got.text == "new"
    assert got.is_done == 1
    assert got.position == 7
    assert got.due_date == datetime(2026, 6, 25)


def test_apply_delete_checklist_item(session):
    p = _project(session)
    t = _task(session, p)
    ci = ChecklistItem(task_id=t.id, text="x", is_done=0, position=0,
                       backend_id="ci-be")
    session.add(ci); session.commit()
    ev = {"entity_kind": "checklist_item", "op": "delete", "entity_id": "ci-be",
          "payload_json": {}}
    res = apply.apply_event(session, ev)
    session.commit()
    assert res["deleted"] == "checklist_item"
    # checklist_items не имеет archived_at → физическое удаление
    assert session.execute(
        select(ChecklistItem).where(ChecklistItem.backend_id == "ci-be")
    ).scalar_one_or_none() is None


def test_apply_does_not_enqueue_outbox(session):
    """Анти-петля: apply (PULL из ядра) НЕ кладёт ничего в Outbox."""
    from atlas.models import Outbox
    p = _project(session)
    _task(session, p, backend_id="task-be-1")
    ev = {
        "entity_kind": "checklist_item", "op": "create", "entity_id": "ci-be",
        "payload_json": {"title": "x", "parent_task_backend_id": "task-be-1"},
    }
    apply.apply_event(session, ev)
    session.commit()
    assert session.execute(select(Outbox)).scalars().all() == []
