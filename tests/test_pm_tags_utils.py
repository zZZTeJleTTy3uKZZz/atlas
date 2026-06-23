"""Тесты для src/atlas/pm/tags.py — утилиты tags engine.

TDD: эти тесты пишутся ДО реализации модуля.
Покрытие: normalize_tag_ref, resolve_tag_ref, generate_tag_slug,
list_project_tags, filter_projects_by_tags, attach_tags, detach_tags.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from atlas.pm._time import local_now


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def fresh_db():
    from atlas.pm.db import make_engine, make_session
    from atlas.pm.models import Base

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with make_session(engine) as session:
        yield session


@pytest.fixture()
def seed_refs(fresh_db):
    from atlas.pm.models import ProjectStatus, ProjectType

    pt = ProjectType(slug="business-product", name="Business")
    ps = ProjectStatus(slug="active", name="Active", order_idx=1)
    fresh_db.add_all([pt, ps])
    fresh_db.commit()
    return {"type": pt, "status": ps}


def _make_project(session, seed_refs, *, slug: str, archived: bool = False):
    from atlas.pm.models import Project

    proj = Project(
        slug=slug,
        name=slug.upper(),
        type_id=seed_refs["type"].id,
        status_id=seed_refs["status"].id,
        priority="P1",
        one_line_summary="...",
        archived_at=local_now() if archived else None,
    )
    session.add(proj)
    session.commit()
    return proj


def _make_tag(session, *, slug: str, name: str, category: str = "owner"):
    from atlas.pm.models import Tag

    tag = Tag(slug=slug, name=name, category=category)
    session.add(tag)
    session.commit()
    return tag


def _attach(session, project_id: str, tag_id: str):
    from atlas.pm.models import ProjectTag

    session.add(ProjectTag(project_id=project_id, tag_id=tag_id))
    session.commit()


# --------------------------------------------------------------------------- #
# normalize_tag_ref                                                           #
# --------------------------------------------------------------------------- #


class TestNormalizeTagRef:
    def test_qualified_owner(self):
        from atlas.pm.tags import normalize_tag_ref
        assert normalize_tag_ref("owner:dmitry") == ("owner", "dmitry")

    def test_qualified_stack(self):
        from atlas.pm.tags import normalize_tag_ref
        assert normalize_tag_ref("stack:b24") == ("stack", "b24")

    def test_bare_slug(self):
        from atlas.pm.tags import normalize_tag_ref
        assert normalize_tag_ref("b24") == (None, "b24")

    def test_qualified_domain(self):
        from atlas.pm.tags import normalize_tag_ref
        assert normalize_tag_ref("domain:marketing") == ("domain", "marketing")

    def test_too_many_colons_raises(self):
        from atlas.pm.tags import normalize_tag_ref
        with pytest.raises(ValueError):
            normalize_tag_ref("cat:slug:extra")

    def test_invalid_category_raises(self):
        """category должен быть одним из owner/stack/domain/other."""
        from atlas.pm.tags import InvalidTagCategoryError, normalize_tag_ref
        with pytest.raises(InvalidTagCategoryError):
            normalize_tag_ref("unknown:dmitry")

    def test_empty_string_raises(self):
        from atlas.pm.tags import normalize_tag_ref
        with pytest.raises(ValueError):
            normalize_tag_ref("")


# --------------------------------------------------------------------------- #
# generate_tag_slug                                                           #
# --------------------------------------------------------------------------- #


class TestGenerateTagSlug:
    def test_auto_from_name(self):
        from atlas.pm.tags import generate_tag_slug
        assert generate_tag_slug("Дмитрий", "owner", lambda _: False) == "dmitrii"

    def test_english_name(self):
        from atlas.pm.tags import generate_tag_slug
        assert generate_tag_slug("Bitrix24", "stack", lambda _: False) == "bitrix24"

    def test_collision_gets_suffix(self):
        from atlas.pm.tags import generate_tag_slug
        taken = {"dmitrii"}
        out = generate_tag_slug("Дмитрий", "owner", lambda x: x in taken)
        assert out == "dmitrii-2"

    def test_multiple_collisions(self):
        from atlas.pm.tags import generate_tag_slug
        taken = {"b24", "b24-2"}
        out = generate_tag_slug("Bitrix24", "stack", lambda x: x in taken)
        assert out == "b24-3" or out.startswith("bitrix24")

    def test_category_not_prefixed_in_slug(self):
        """Category идёт в отдельное поле — не вплетается в slug."""
        from atlas.pm.tags import generate_tag_slug
        out = generate_tag_slug("Dmitry", "owner", lambda _: False)
        assert "owner" not in out
        assert ":" not in out


# --------------------------------------------------------------------------- #
# resolve_tag_ref                                                             #
# --------------------------------------------------------------------------- #


class TestResolveTagRef:
    def test_resolve_by_qualified(self, fresh_db):
        from atlas.pm.tags import resolve_tag_ref

        t = _make_tag(fresh_db, slug="dmitry", name="Dmitry", category="owner")
        found = resolve_tag_ref(fresh_db, "owner:dmitry")
        assert found is not None
        assert found.id == t.id

    def test_resolve_by_qualified_wrong_category_returns_none(self, fresh_db):
        """owner:dmitry когда существует только stack:dmitry → None."""
        from atlas.pm.tags import resolve_tag_ref

        _make_tag(fresh_db, slug="dmitry", name="Dmitry", category="stack")
        found = resolve_tag_ref(fresh_db, "owner:dmitry")
        assert found is None

    def test_resolve_bare_slug_when_unique(self, fresh_db):
        from atlas.pm.tags import resolve_tag_ref

        t = _make_tag(fresh_db, slug="dmitry", name="Dmitry", category="owner")
        found = resolve_tag_ref(fresh_db, "dmitry")
        assert found is not None
        assert found.id == t.id

    def test_resolve_bare_slug_not_found(self, fresh_db):
        from atlas.pm.tags import resolve_tag_ref
        assert resolve_tag_ref(fresh_db, "not-exists") is None

    def test_resolve_by_full_uuid(self, fresh_db):
        from atlas.pm.tags import resolve_tag_ref

        t = _make_tag(fresh_db, slug="dmitry", name="Dmitry", category="owner")
        found = resolve_tag_ref(fresh_db, t.id)
        assert found is not None
        assert found.id == t.id

    def test_resolve_by_short_uuid(self, fresh_db):
        from atlas.pm.tags import resolve_tag_ref

        t = _make_tag(fresh_db, slug="dmitry", name="Dmitry", category="owner")
        short = t.id[:8]
        found = resolve_tag_ref(fresh_db, short)
        assert found is not None
        assert found.id == t.id

    def test_empty_ref_returns_none(self, fresh_db):
        from atlas.pm.tags import resolve_tag_ref
        assert resolve_tag_ref(fresh_db, "") is None


# --------------------------------------------------------------------------- #
# list_project_tags                                                           #
# --------------------------------------------------------------------------- #


class TestListProjectTags:
    def test_empty_project_returns_empty(self, fresh_db, seed_refs):
        from atlas.pm.tags import list_project_tags

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        assert list_project_tags(fresh_db, proj.id) == []

    def test_returns_sorted_by_category_then_slug(self, fresh_db, seed_refs):
        from atlas.pm.tags import list_project_tags

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        t1 = _make_tag(fresh_db, slug="zulu", name="Z", category="stack")
        t2 = _make_tag(fresh_db, slug="alpha", name="A", category="owner")
        t3 = _make_tag(fresh_db, slug="bravo", name="B", category="owner")

        for tag in (t1, t2, t3):
            _attach(fresh_db, proj.id, tag.id)

        tags = list_project_tags(fresh_db, proj.id)
        assert [t.slug for t in tags] == ["alpha", "bravo", "zulu"]
        # Первые два — owner, третий — stack
        assert [t.category for t in tags] == ["owner", "owner", "stack"]


# --------------------------------------------------------------------------- #
# filter_projects_by_tags                                                     #
# --------------------------------------------------------------------------- #


class TestFilterProjectsByTags:
    def test_single_tag(self, fresh_db, seed_refs):
        from atlas.pm.tags import filter_projects_by_tags

        p1 = _make_project(fresh_db, seed_refs, slug="alpha")
        p2 = _make_project(fresh_db, seed_refs, slug="beta")
        _make_project(fresh_db, seed_refs, slug="gamma")
        tag = _make_tag(fresh_db, slug="b24", name="B24", category="stack")

        _attach(fresh_db, p1.id, tag.id)
        _attach(fresh_db, p2.id, tag.id)

        result = filter_projects_by_tags(fresh_db, ["b24"])
        ids = {p.id for p in result}
        assert ids == {p1.id, p2.id}

    def test_two_tags_and_semantics(self, fresh_db, seed_refs):
        """AND: только проекты со ВСЕМИ указанными тегами."""
        from atlas.pm.tags import filter_projects_by_tags

        p1 = _make_project(fresh_db, seed_refs, slug="alpha")
        p2 = _make_project(fresh_db, seed_refs, slug="beta")
        p3 = _make_project(fresh_db, seed_refs, slug="gamma")

        t_b24 = _make_tag(fresh_db, slug="b24", name="B24", category="stack")
        t_dmitry = _make_tag(fresh_db, slug="dmitry", name="D", category="owner")

        # p1: оба тега; p2: только b24; p3: только dmitry
        _attach(fresh_db, p1.id, t_b24.id)
        _attach(fresh_db, p1.id, t_dmitry.id)
        _attach(fresh_db, p2.id, t_b24.id)
        _attach(fresh_db, p3.id, t_dmitry.id)

        result = filter_projects_by_tags(fresh_db, ["b24", "dmitry"])
        ids = {p.id for p in result}
        assert ids == {p1.id}

    def test_archived_hidden_by_default(self, fresh_db, seed_refs):
        from atlas.pm.tags import filter_projects_by_tags

        p_active = _make_project(fresh_db, seed_refs, slug="active1")
        p_arch = _make_project(fresh_db, seed_refs, slug="arch1", archived=True)
        tag = _make_tag(fresh_db, slug="b24", name="B24", category="stack")

        _attach(fresh_db, p_active.id, tag.id)
        _attach(fresh_db, p_arch.id, tag.id)

        result = filter_projects_by_tags(fresh_db, ["b24"])
        ids = {p.id for p in result}
        assert ids == {p_active.id}

    def test_archived_included_when_flag_true(self, fresh_db, seed_refs):
        from atlas.pm.tags import filter_projects_by_tags

        p_active = _make_project(fresh_db, seed_refs, slug="active1")
        p_arch = _make_project(fresh_db, seed_refs, slug="arch1", archived=True)
        tag = _make_tag(fresh_db, slug="b24", name="B24", category="stack")

        _attach(fresh_db, p_active.id, tag.id)
        _attach(fresh_db, p_arch.id, tag.id)

        result = filter_projects_by_tags(fresh_db, ["b24"], archived=True)
        ids = {p.id for p in result}
        assert ids == {p_active.id, p_arch.id}

    def test_empty_tags_returns_all_active(self, fresh_db, seed_refs):
        """Пустой список тегов + archived=False → все active."""
        from atlas.pm.tags import filter_projects_by_tags

        p1 = _make_project(fresh_db, seed_refs, slug="a1")
        p2 = _make_project(fresh_db, seed_refs, slug="a2")
        _make_project(fresh_db, seed_refs, slug="arch1", archived=True)

        result = filter_projects_by_tags(fresh_db, [])
        ids = {p.id for p in result}
        assert ids == {p1.id, p2.id}

    def test_unknown_tag_slug_returns_empty(self, fresh_db, seed_refs):
        from atlas.pm.tags import filter_projects_by_tags

        _make_project(fresh_db, seed_refs, slug="alpha")
        # Никакого тега не создаём
        result = filter_projects_by_tags(fresh_db, ["nonexistent"])
        assert result == []


# --------------------------------------------------------------------------- #
# attach_tags / detach_tags                                                   #
# --------------------------------------------------------------------------- #


class TestAttachDetachTags:
    def test_attach_single_tag(self, fresh_db, seed_refs):
        from atlas.pm.models import ProjectTag
        from atlas.pm.tags import attach_tags

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        tag = _make_tag(fresh_db, slug="b24", name="B24", category="stack")

        n = attach_tags(fresh_db, proj.id, [tag.id])
        fresh_db.commit()
        assert n == 1

        count = fresh_db.execute(
            select(func.count()).select_from(ProjectTag)
        ).scalar()
        assert count == 1

    def test_attach_multiple_tags(self, fresh_db, seed_refs):
        from atlas.pm.tags import attach_tags

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        t1 = _make_tag(fresh_db, slug="b24", name="B24", category="stack")
        t2 = _make_tag(fresh_db, slug="dmitry", name="D", category="owner")

        n = attach_tags(fresh_db, proj.id, [t1.id, t2.id])
        fresh_db.commit()
        assert n == 2

    def test_attach_is_idempotent(self, fresh_db, seed_refs):
        """Повторный attach того же tag — не создаёт дубликат."""
        from atlas.pm.models import ProjectTag
        from atlas.pm.tags import attach_tags

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        tag = _make_tag(fresh_db, slug="b24", name="B24", category="stack")

        attach_tags(fresh_db, proj.id, [tag.id])
        fresh_db.commit()

        n = attach_tags(fresh_db, proj.id, [tag.id])
        fresh_db.commit()
        assert n == 0  # Ничего нового не добавлено

        count = fresh_db.execute(
            select(func.count()).select_from(ProjectTag)
        ).scalar()
        assert count == 1

    def test_attach_partial_existing(self, fresh_db, seed_refs):
        """Из [t1, t2] где t1 уже прикреплён — добавится только t2."""
        from atlas.pm.tags import attach_tags

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        t1 = _make_tag(fresh_db, slug="b24", name="B24", category="stack")
        t2 = _make_tag(fresh_db, slug="dmitry", name="D", category="owner")

        attach_tags(fresh_db, proj.id, [t1.id])
        fresh_db.commit()

        n = attach_tags(fresh_db, proj.id, [t1.id, t2.id])
        fresh_db.commit()
        assert n == 1

    def test_detach_single(self, fresh_db, seed_refs):
        from atlas.pm.models import ProjectTag
        from atlas.pm.tags import attach_tags, detach_tags

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        tag = _make_tag(fresh_db, slug="b24", name="B24", category="stack")

        attach_tags(fresh_db, proj.id, [tag.id])
        fresh_db.commit()

        n = detach_tags(fresh_db, proj.id, [tag.id])
        fresh_db.commit()
        assert n == 1

        count = fresh_db.execute(
            select(func.count()).select_from(ProjectTag)
        ).scalar()
        assert count == 0

    def test_detach_idempotent(self, fresh_db, seed_refs):
        """Detach несуществующей связи → 0, не падает."""
        from atlas.pm.tags import detach_tags

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        tag = _make_tag(fresh_db, slug="b24", name="B24", category="stack")

        n = detach_tags(fresh_db, proj.id, [tag.id])
        fresh_db.commit()
        assert n == 0

    def test_detach_multiple(self, fresh_db, seed_refs):
        from atlas.pm.tags import attach_tags, detach_tags

        proj = _make_project(fresh_db, seed_refs, slug="alpha")
        t1 = _make_tag(fresh_db, slug="b24", name="B24", category="stack")
        t2 = _make_tag(fresh_db, slug="dmitry", name="D", category="owner")

        attach_tags(fresh_db, proj.id, [t1.id, t2.id])
        fresh_db.commit()

        n = detach_tags(fresh_db, proj.id, [t1.id, t2.id])
        fresh_db.commit()
        assert n == 2
