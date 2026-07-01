"""Тесты для атласовских утилит slug/prefix/resolve.

Покрывают: slugify_text, generate_unique_slug, generate_prefix_from_slug,
build_task_slug, next_task_number, resolve_project_ref, resolve_task_ref.

TDD: эти тесты пишутся ДО реализации src/atlas/pm/slugs.py.
"""
from __future__ import annotations

import pytest


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def fresh_db():
    """Чистая in-memory SQLite БД с применённой схемой."""
    from atlas.db import make_engine, make_session
    from atlas.models import Base

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with make_session(engine) as session:
        yield session


@pytest.fixture()
def seed_refs(fresh_db):
    """Создаёт минимальные project_type + project_status для FK-проектов."""
    from atlas.models import ProjectStatus, ProjectType

    pt = ProjectType(slug="business-product", name="Business")
    ps = ProjectStatus(slug="active", name="Active", order_idx=1)
    fresh_db.add_all([pt, ps])
    fresh_db.commit()
    return {"type": pt, "status": ps}


# --------------------------------------------------------------------------- #
# slugify_text                                                                #
# --------------------------------------------------------------------------- #


class TestSlugifyText:
    def test_russian_with_dot(self):
        from atlas.slugs import slugify_text
        assert slugify_text("Example Org портал") == "example-org-portal"

    def test_english_simple(self):
        from atlas.slugs import slugify_text
        assert slugify_text("Fix Login Bug") == "fix-login-bug"

    def test_trim_whitespace(self):
        from atlas.slugs import slugify_text
        assert slugify_text("  Trim Whitespace  ") == "trim-whitespace"

    def test_only_special_chars_returns_empty(self):
        from atlas.slugs import slugify_text
        # фиксируем поведение: чисто-спецсимволы → пустая строка
        assert slugify_text("!@#$%^&*") == ""

    def test_max_length_truncates(self):
        from atlas.slugs import slugify_text
        out = slugify_text("a" * 100, max_length=20)
        assert len(out) <= 20
        assert out == "a" * 20

    def test_only_lowercase_ascii_and_hyphens(self):
        from atlas.slugs import slugify_text
        out = slugify_text("Привет МИР 2025")
        assert all(ch.isascii() for ch in out)
        assert out == out.lower()
        # допустимы только [a-z0-9-]
        assert all(ch.isalnum() or ch == "-" for ch in out)


# --------------------------------------------------------------------------- #
# generate_prefix_from_slug                                                   #
# --------------------------------------------------------------------------- #


class TestGeneratePrefixFromSlug:
    def test_single_word_takes_first_three(self):
        from atlas.slugs import generate_prefix_from_slug
        assert generate_prefix_from_slug("cifro") == "cif"

    def test_atlas(self):
        from atlas.slugs import generate_prefix_from_slug
        assert generate_prefix_from_slug("atlas") == "atl"

    def test_segmented_with_digits(self):
        # 'app-002' — слово + значимая цифра (ведущие нули отбрасываются)
        # фиксируем поведение: 'app-002' -> 'app2'
        from atlas.slugs import generate_prefix_from_slug
        assert generate_prefix_from_slug("app-002") == "app2"

    def test_two_word_segments(self):
        from atlas.slugs import generate_prefix_from_slug
        assert generate_prefix_from_slug("docs-parsing") == "dp"

    def test_three_segment_with_digits(self):
        # 'ml-model-v2' — m + m + v2 → но max_length=5 ограничивает
        # буквы первых символов сегментов: m, m, v + цифры из последнего сегмента: 2
        from atlas.slugs import generate_prefix_from_slug
        out = generate_prefix_from_slug("ml-model-v2")
        assert out == "mmv2"

    def test_short_slug_returns_as_is(self):
        from atlas.slugs import generate_prefix_from_slug
        assert generate_prefix_from_slug("a") == "a"

    def test_only_lowercase_ascii_alphanumeric(self):
        from atlas.slugs import generate_prefix_from_slug
        out = generate_prefix_from_slug("test-project-x9")
        assert all(ch.isalnum() and (ch.islower() or ch.isdigit()) for ch in out)

    def test_max_length_respected(self):
        from atlas.slugs import generate_prefix_from_slug
        out = generate_prefix_from_slug("very-long-multi-segment-slug-name", max_length=5)
        assert len(out) <= 5


# --------------------------------------------------------------------------- #
# generate_unique_slug                                                         #
# --------------------------------------------------------------------------- #


class TestGenerateUniqueSlug:
    def test_base_free_returns_base(self):
        from atlas.slugs import generate_unique_slug
        assert generate_unique_slug("foo", lambda _: False) == "foo"

    def test_base_taken_returns_base_2(self):
        from atlas.slugs import generate_unique_slug
        taken = {"foo"}
        assert generate_unique_slug("foo", lambda x: x in taken) == "foo-2"

    def test_base_and_2_taken_returns_3(self):
        from atlas.slugs import generate_unique_slug
        taken = {"foo", "foo-2", "foo-3"}
        assert generate_unique_slug("foo", lambda x: x in taken) == "foo-4"

    def test_max_attempts_raises(self):
        from atlas.slugs import generate_unique_slug
        with pytest.raises(Exception):
            generate_unique_slug("foo", lambda _: True, max_attempts=5)


# --------------------------------------------------------------------------- #
# build_task_slug                                                              #
# --------------------------------------------------------------------------- #


class TestBuildTaskSlug:
    def test_basic(self):
        from atlas.slugs import build_task_slug
        assert build_task_slug("cf", "fix-login") == "cf-fix-login"

    def test_with_digits_prefix(self):
        from atlas.slugs import build_task_slug
        assert build_task_slug("np5", "add-migration") == "np5-add-migration"


# --------------------------------------------------------------------------- #
# next_task_number                                                             #
# --------------------------------------------------------------------------- #


class TestNextTaskNumber:
    def test_empty_table_returns_1(self, fresh_db):
        from atlas.slugs import next_task_number
        assert next_task_number(fresh_db) == 1

    def test_three_consecutive_returns_4(self, fresh_db, seed_refs):
        from atlas.models import Project, Task
        from atlas.slugs import next_task_number

        proj = Project(
            slug="p1", name="P1", type_id=seed_refs["type"].id,
            status_id=seed_refs["status"].id, priority="P1",
            one_line_summary="...",
        )
        fresh_db.add(proj)
        fresh_db.commit()

        for n in (1, 2, 3):
            t = Task(
                project_id=proj.id, title=f"T{n}",
                cpp_description="cpp", priority="P2", number=n,
            )
            fresh_db.add(t)
        fresh_db.commit()

        assert next_task_number(fresh_db) == 4

    def test_gap_does_not_fill(self, fresh_db, seed_refs):
        from atlas.models import Project, Task
        from atlas.slugs import next_task_number

        proj = Project(
            slug="p2", name="P2", type_id=seed_refs["type"].id,
            status_id=seed_refs["status"].id, priority="P1",
            one_line_summary="...",
        )
        fresh_db.add(proj)
        fresh_db.commit()

        t = Task(
            project_id=proj.id, title="T42",
            cpp_description="cpp", priority="P2", number=42,
        )
        fresh_db.add(t)
        fresh_db.commit()

        assert next_task_number(fresh_db) == 43


# --------------------------------------------------------------------------- #
# resolve_project_ref                                                          #
# --------------------------------------------------------------------------- #


class TestResolveProjectRef:
    def _make_projects(self, session, seed_refs):
        from atlas.models import Project

        projects = []
        for slug in ("alpha", "beta"):
            p = Project(
                slug=slug, name=slug.upper(),
                type_id=seed_refs["type"].id,
                status_id=seed_refs["status"].id,
                priority="P1", one_line_summary="...",
            )
            session.add(p)
            projects.append(p)
        session.commit()
        return projects

    def test_resolve_by_slug(self, fresh_db, seed_refs):
        from atlas.slugs import resolve_project_ref

        projects = self._make_projects(fresh_db, seed_refs)
        result = resolve_project_ref(fresh_db, "alpha")
        assert result is not None
        assert result.id == projects[0].id

    def test_resolve_by_full_uuid(self, fresh_db, seed_refs):
        from atlas.slugs import resolve_project_ref

        projects = self._make_projects(fresh_db, seed_refs)
        result = resolve_project_ref(fresh_db, projects[0].id)
        assert result is not None
        assert result.id == projects[0].id

    def test_resolve_by_short_uuid(self, fresh_db, seed_refs):
        from atlas.slugs import resolve_project_ref

        projects = self._make_projects(fresh_db, seed_refs)
        short = projects[1].id[:8]
        result = resolve_project_ref(fresh_db, short)
        assert result is not None
        assert result.id == projects[1].id

    def test_resolve_unknown_returns_none(self, fresh_db, seed_refs):
        from atlas.slugs import resolve_project_ref

        self._make_projects(fresh_db, seed_refs)
        assert resolve_project_ref(fresh_db, "no-such-slug") is None


# --------------------------------------------------------------------------- #
# resolve_task_ref                                                             #
# --------------------------------------------------------------------------- #


class TestResolveTaskRef:
    def _make_task(self, session, seed_refs, *, number=1, slug="alpha-fix"):
        from atlas.models import Project, Task

        proj = Project(
            slug="alpha", name="A",
            type_id=seed_refs["type"].id,
            status_id=seed_refs["status"].id,
            priority="P1", one_line_summary="...",
        )
        session.add(proj)
        session.commit()

        t = Task(
            project_id=proj.id, title="Fix",
            cpp_description="cpp", priority="P2",
            number=number, slug=slug,
        )
        session.add(t)
        session.commit()
        return t

    def test_resolve_by_number(self, fresh_db, seed_refs):
        from atlas.slugs import resolve_task_ref

        t = self._make_task(fresh_db, seed_refs, number=1)
        result = resolve_task_ref(fresh_db, "1")
        assert result is not None
        assert result.id == t.id

    def test_resolve_by_slug(self, fresh_db, seed_refs):
        from atlas.slugs import resolve_task_ref

        t = self._make_task(fresh_db, seed_refs, slug="alpha-fix")
        result = resolve_task_ref(fresh_db, "alpha-fix")
        assert result is not None
        assert result.id == t.id

    def test_resolve_by_full_uuid(self, fresh_db, seed_refs):
        from atlas.slugs import resolve_task_ref

        t = self._make_task(fresh_db, seed_refs)
        result = resolve_task_ref(fresh_db, t.id)
        assert result is not None
        assert result.id == t.id

    def test_resolve_by_short_uuid(self, fresh_db, seed_refs):
        from atlas.slugs import resolve_task_ref

        t = self._make_task(fresh_db, seed_refs)
        result = resolve_task_ref(fresh_db, t.id[:8])
        assert result is not None
        assert result.id == t.id

    def test_resolve_by_short_uuid_when_prefix_is_all_digits(
        self, fresh_db, seed_refs
    ):
        """Регрессия: ref = '12345678' (всё цифры, ≥ 7) — должен резолвиться
        как UUID prefix, а не как Task.number."""
        from atlas.models import Project, Task
        from atlas.slugs import resolve_task_ref

        proj = Project(
            slug="alpha", name="A",
            type_id=seed_refs["type"].id,
            status_id=seed_refs["status"].id,
            priority="P1", one_line_summary="...",
        )
        fresh_db.add(proj)
        fresh_db.commit()

        # Конструируем UUID, начинающийся с цифровой последовательности — детерминированно.
        forced_uuid = "12345678-aaaa-bbbb-cccc-dddddddddddd"
        t = Task(
            id=forced_uuid,
            project_id=proj.id,
            title="Fix",
            cpp_description="cpp",
            priority="P2",
            number=1,
            slug="alpha-fix",
        )
        fresh_db.add(t)
        fresh_db.commit()

        # Резолв по 8-символьному цифровому префиксу UUID.
        result = resolve_task_ref(fresh_db, "12345678")
        assert result is not None
        assert result.id == forced_uuid

    def test_unknown_number_returns_none(self, fresh_db, seed_refs):
        from atlas.slugs import resolve_task_ref

        self._make_task(fresh_db, seed_refs, number=1)
        assert resolve_task_ref(fresh_db, "999") is None
