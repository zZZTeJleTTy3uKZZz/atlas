"""Provenance: колонки происхождения на Task и Epic создаются и работают.

Стиль — как в test_pm_f3b_models.py: sqlite in-memory, Base.metadata.create_all,
Session. Покрываем: дефолт origin='native', round-trip provenance-полей,
CHECK ck_tasks_origin / ck_epics_origin отвергает неизвестный origin,
description у Epic.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atlas.models import (
    Base, Epic, Participant, Project, ProjectStatus, ProjectType, Task,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _mk_project(s, slug="acme"):
    t = ProjectType(slug=f"type-{slug}", name="Кл")
    st = ProjectStatus(slug=f"status-{slug}", name="A", order_idx=1)
    s.add_all([t, st])
    s.flush()
    proj = Project(
        slug=slug, name="Acme", type_id=t.id, status_id=st.id, priority="P2",
        one_line_summary="x",
    )
    s.add(proj)
    s.flush()
    return proj


# --------------------------------------------------------------------------- #
# Task                                                                        #
# --------------------------------------------------------------------------- #


def test_task_origin_defaults_to_native(session):
    proj = _mk_project(session)
    task = Task(project_id=proj.id, title="T", cpp_description="cpp", priority="P2")
    session.add(task)
    session.commit()
    got = session.get(Task, task.id)
    assert got.origin == "native"
    assert got.source_project_id is None
    assert got.rationale is None
    assert got.injected_by is None
    assert got.injected_at is None


def test_task_provenance_roundtrip(session):
    src = _mk_project(session, slug="source-proj")
    tgt = _mk_project(session, slug="target-proj")
    actor = Participant(kind="human", slug="dima", name="Дмитрий")
    session.add(actor)
    session.flush()
    from atlas._time import local_now
    now = local_now()
    task = Task(
        project_id=tgt.id, title="Injected", cpp_description="cpp", priority="P1",
        source_project_id=src.id, origin="injected",
        rationale="по принципу зеркалирования из источника",
        injected_by=actor.id, injected_at=now,
    )
    session.add(task)
    session.commit()
    got = session.get(Task, task.id)
    assert got.source_project_id == src.id
    assert got.origin == "injected"
    assert got.rationale == "по принципу зеркалирования из источника"
    assert got.injected_by == actor.id
    assert got.injected_at is not None


def test_task_origin_check_rejects_unknown(session):
    proj = _mk_project(session)
    session.add(Task(
        project_id=proj.id, title="T", cpp_description="cpp", priority="P2",
        origin="alien",
    ))
    with pytest.raises(IntegrityError):
        session.commit()


# --------------------------------------------------------------------------- #
# Epic                                                                        #
# --------------------------------------------------------------------------- #


def test_epic_origin_defaults_and_description(session):
    proj = _mk_project(session)
    epic = Epic(project_id=proj.id, title="E", description="описание эпика")
    session.add(epic)
    session.commit()
    got = session.get(Epic, epic.id)
    assert got.origin == "native"
    assert got.description == "описание эпика"
    assert got.source_project_id is None


def test_epic_provenance_roundtrip(session):
    src = _mk_project(session, slug="src-e")
    tgt = _mk_project(session, slug="tgt-e")
    actor = Participant(kind="ai_agent", slug="agent", name="Агент")
    session.add(actor)
    session.flush()
    epic = Epic(
        project_id=tgt.id, title="Injected epic",
        source_project_id=src.id, origin="imported",
        rationale="импорт из источника", injected_by=actor.id,
    )
    session.add(epic)
    session.commit()
    got = session.get(Epic, epic.id)
    assert got.source_project_id == src.id
    assert got.origin == "imported"
    assert got.rationale == "импорт из источника"
    assert got.injected_by == actor.id


def test_epic_origin_check_rejects_unknown(session):
    proj = _mk_project(session)
    session.add(Epic(project_id=proj.id, title="E", origin="alien"))
    with pytest.raises(IntegrityError):
        session.commit()
