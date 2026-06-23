"""Канон типов: storage_group на ProjectType + parent_id на Project.

Стиль — как в test_pm_provenance_models.py: sqlite in-memory,
Base.metadata.create_all, Session. Покрываем: storage_group читается/пишется
и дефолтит в None; parent_id round-trip (self-FK на projects).
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.models import (
    Base, Project, ProjectStatus, ProjectType,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _mk_project(s, slug="acme", parent_id=None):
    t = ProjectType(slug=f"type-{slug}", name="Кл")
    st = ProjectStatus(slug=f"status-{slug}", name="A", order_idx=1)
    s.add_all([t, st])
    s.flush()
    proj = Project(
        slug=slug, name="Acme", type_id=t.id, status_id=st.id, priority="P2",
        one_line_summary="x", parent_id=parent_id,
    )
    s.add(proj)
    s.flush()
    return proj


# --------------------------------------------------------------------------- #
# ProjectType.storage_group                                                   #
# --------------------------------------------------------------------------- #


def test_project_type_storage_group_defaults_none(session):
    pt = ProjectType(slug="t1", name="T1")
    session.add(pt)
    session.commit()
    got = session.get(ProjectType, pt.id)
    assert got.storage_group is None


@pytest.mark.parametrize("group", ["clients", "products", "tests", "inbox"])
def test_project_type_storage_group_roundtrip(session, group):
    pt = ProjectType(slug=f"t-{group}", name="T", storage_group=group)
    session.add(pt)
    session.commit()
    got = session.get(ProjectType, pt.id)
    assert got.storage_group == group


# --------------------------------------------------------------------------- #
# Project.parent_id                                                           #
# --------------------------------------------------------------------------- #


def test_project_parent_id_defaults_none(session):
    proj = _mk_project(session)
    got = session.get(Project, proj.id)
    assert got.parent_id is None


def test_project_parent_id_roundtrip(session):
    parent = _mk_project(session, slug="parent")
    t = ProjectType(slug="type-child", name="Кл")
    st = ProjectStatus(slug="status-child", name="A", order_idx=1)
    session.add_all([t, st])
    session.flush()
    child = Project(
        slug="child", name="Child", type_id=t.id, status_id=st.id,
        priority="P2", one_line_summary="x", parent_id=parent.id,
    )
    session.add(child)
    session.commit()
    got = session.get(Project, child.id)
    assert got.parent_id == parent.id
