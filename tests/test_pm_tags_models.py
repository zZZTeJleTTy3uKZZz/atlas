"""Тесты для моделей Tag, ProjectTag + расширений Project (renewal_count, archived_group).

TDD: эти тесты пишутся ДО расширения src/atlas/pm/models.py и миграции 004.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def fresh_db():
    """Чистая in-memory SQLite БД с применённой схемой."""
    from atlas.pm.db import make_engine, make_session
    from atlas.pm.models import Base

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with make_session(engine) as session:
        yield session


@pytest.fixture()
def seed_refs(fresh_db):
    """Создаёт минимальные project_type + project_status для FK-проектов."""
    from atlas.pm.models import ProjectStatus, ProjectType

    pt = ProjectType(slug="business-product", name="Business")
    ps = ProjectStatus(slug="active", name="Active", order_idx=1)
    fresh_db.add_all([pt, ps])
    fresh_db.commit()
    return {"type": pt, "status": ps}


def _make_project(session, seed_refs, *, slug: str):
    from atlas.pm.models import Project

    proj = Project(
        slug=slug,
        name=slug.upper(),
        type_id=seed_refs["type"].id,
        status_id=seed_refs["status"].id,
        priority="P1",
        one_line_summary="...",
    )
    session.add(proj)
    session.commit()
    return proj


# --------------------------------------------------------------------------- #
# Tag создание                                                                #
# --------------------------------------------------------------------------- #


class TestTagCreation:
    def test_tag_create_minimal(self, fresh_db):
        """Создание тега без color/description — обязательные поля slug/name/category."""
        from atlas.pm.models import Tag

        tag = Tag(slug="dmitry", name="Дмитрий", category="owner")
        fresh_db.add(tag)
        fresh_db.commit()

        assert tag.id is not None
        assert tag.created_at is not None
        assert tag.color is None
        assert tag.description is None

    def test_tag_create_with_all_fields(self, fresh_db):
        from atlas.pm.models import Tag

        tag = Tag(
            slug="b24",
            name="Bitrix24",
            category="stack",
            color="#F97316",
            description="Bitrix24 integration",
        )
        fresh_db.add(tag)
        fresh_db.commit()

        assert tag.color == "#F97316"
        assert tag.description == "Bitrix24 integration"

    def test_tag_slug_unique(self, fresh_db):
        """Duplicate slug → IntegrityError."""
        from atlas.pm.models import Tag

        t1 = Tag(slug="dmitry", name="Dmitry One", category="owner")
        fresh_db.add(t1)
        fresh_db.commit()

        t2 = Tag(slug="dmitry", name="Dmitry Two", category="owner")
        fresh_db.add(t2)
        with pytest.raises(IntegrityError):
            fresh_db.commit()
        fresh_db.rollback()

    def test_tag_create_invalid_category_raises(self, fresh_db):
        """CHECK constraint на category: должен быть owner/stack/domain/other."""
        from atlas.pm.models import Tag

        tag = Tag(slug="bad", name="Bad", category="invalid_category")
        fresh_db.add(tag)
        with pytest.raises(IntegrityError):
            fresh_db.commit()
        fresh_db.rollback()

    def test_tag_valid_categories(self, fresh_db):
        """owner, stack, domain, other — все валидны."""
        from atlas.pm.models import Tag

        for cat in ("owner", "stack", "domain", "other"):
            tag = Tag(slug=f"t-{cat}", name=cat, category=cat)
            fresh_db.add(tag)
        fresh_db.commit()

        from atlas.pm.models import Tag as T
        count = fresh_db.execute(select(func.count()).select_from(T)).scalar()
        assert count == 4


# --------------------------------------------------------------------------- #
# ProjectTag M:N + cascade                                                    #
# --------------------------------------------------------------------------- #


class TestProjectTagCascade:
    def test_m2m_basic_attach(self, fresh_db, seed_refs):
        from atlas.pm.models import ProjectTag, Tag

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        tag = Tag(slug="dmitry", name="Dmitry", category="owner")
        fresh_db.add(tag)
        fresh_db.commit()

        link = ProjectTag(project_id=proj.id, tag_id=tag.id)
        fresh_db.add(link)
        fresh_db.commit()

        assert link.created_at is not None
        count = fresh_db.execute(
            select(func.count()).select_from(ProjectTag)
        ).scalar()
        assert count == 1

    def test_m2m_cascade_on_project_delete(self, fresh_db, seed_refs):
        """Удаление проекта удаляет project_tag-связи (ondelete=CASCADE)."""
        from atlas.pm.models import ProjectTag, Tag

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        tag = Tag(slug="dmitry", name="Dmitry", category="owner")
        fresh_db.add(tag)
        fresh_db.commit()

        fresh_db.add(ProjectTag(project_id=proj.id, tag_id=tag.id))
        fresh_db.commit()

        fresh_db.delete(proj)
        fresh_db.commit()

        count = fresh_db.execute(
            select(func.count()).select_from(ProjectTag)
        ).scalar()
        assert count == 0

        # Тег сам не удалён
        remaining_tags = fresh_db.execute(
            select(func.count()).select_from(Tag)
        ).scalar()
        assert remaining_tags == 1

    def test_m2m_cascade_on_tag_delete(self, fresh_db, seed_refs):
        """Удаление тега удаляет project_tag-связи (ondelete=CASCADE)."""
        from atlas.pm.models import Project, ProjectTag, Tag

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        tag = Tag(slug="dmitry", name="Dmitry", category="owner")
        fresh_db.add(tag)
        fresh_db.commit()

        fresh_db.add(ProjectTag(project_id=proj.id, tag_id=tag.id))
        fresh_db.commit()

        fresh_db.delete(tag)
        fresh_db.commit()

        count = fresh_db.execute(
            select(func.count()).select_from(ProjectTag)
        ).scalar()
        assert count == 0

        # Проект сам не удалён
        remaining = fresh_db.execute(
            select(func.count()).select_from(Project)
        ).scalar()
        assert remaining == 1

    def test_m2m_composite_pk_prevents_duplicates(self, fresh_db, seed_refs):
        """Composite PK (project_id, tag_id) — нельзя добавить дубликат."""
        from atlas.pm.models import ProjectTag, Tag

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        tag = Tag(slug="dmitry", name="Dmitry", category="owner")
        fresh_db.add(tag)
        fresh_db.commit()

        fresh_db.add(ProjectTag(project_id=proj.id, tag_id=tag.id))
        fresh_db.commit()

        fresh_db.add(ProjectTag(project_id=proj.id, tag_id=tag.id))
        with pytest.raises(IntegrityError):
            fresh_db.commit()
        fresh_db.rollback()


# --------------------------------------------------------------------------- #
# Project.renewal_count, Project.archived_group                               #
# --------------------------------------------------------------------------- #


class TestProjectRenewalAndGroup:
    def test_project_renewal_count_default_zero(self, fresh_db, seed_refs):
        """renewal_count по умолчанию = 0."""
        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        fresh_db.refresh(proj)
        assert proj.renewal_count == 0

    def test_project_renewal_count_increment(self, fresh_db, seed_refs):
        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        proj.renewal_count = 3
        fresh_db.commit()
        fresh_db.refresh(proj)
        assert proj.renewal_count == 3

    def test_project_archived_group_default_null(self, fresh_db, seed_refs):
        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        fresh_db.refresh(proj)
        assert proj.archived_group is None

    def test_project_archived_group_valid_values(self, fresh_db, seed_refs):
        """'clients' / 'products' / 'tests' — валидны."""
        from atlas.pm.models import Project

        for slug, group in (("a1", "clients"), ("a2", "products"), ("a3", "tests")):
            proj = _make_project(fresh_db, seed_refs, slug=slug)
            proj.archived_group = group
            fresh_db.commit()

        count = fresh_db.execute(
            select(func.count()).select_from(Project)
            .where(Project.archived_group.is_not(None))
        ).scalar()
        assert count == 3

    def test_project_archived_group_check_constraint(self, fresh_db, seed_refs):
        """Invalid group → IntegrityError (CHECK constraint)."""
        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        proj.archived_group = "invalid_group"
        with pytest.raises(IntegrityError):
            fresh_db.commit()
        fresh_db.rollback()
