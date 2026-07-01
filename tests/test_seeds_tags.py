"""Тесты для `seed_base_tags` — базовый набор owner/stack/domain тегов.

TDD: эти тесты пишутся ДО реализации функции `seed_base_tags` в seeds.py.
"""
from __future__ import annotations

from sqlalchemy import func, select


# --------------------------------------------------------------------------- #
# Ожидаемый состав тегов (синхронизировано с seeds.BASE_TAGS)                #
# --------------------------------------------------------------------------- #

EXPECTED_OWNER_SLUGS = {"example-org", "owner"}
EXPECTED_STACK_SLUGS = {
    "b24",
    "notion",
    "telegram",
    "anthropic-api",
    "openai",
    "python",
    "typescript",
    "notebooklm",
    "playwright",
    "sqlalchemy",
    "fastapi",
    "sqlite",
    "alembic",
    "typer",
}
EXPECTED_DOMAIN_SLUGS = {
    "marketing",
    "sales",
    "ai-agents",
    "knowledge-management",
    "dev-tools",
    "analytics",
    "pm-tools",
    "crm",
    "content",
    "finance",
    "research",
    "integrations",
}

EXPECTED_TOTAL = (
    len(EXPECTED_OWNER_SLUGS)
    + len(EXPECTED_STACK_SLUGS)
    + len(EXPECTED_DOMAIN_SLUGS)
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_session():
    from atlas.db import make_engine, make_session
    from atlas.models import Base

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return make_session(engine)


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


def test_seed_base_tags_creates_all():
    """Empty DB → все 28 тегов создаются с правильными slug'ами по категориям."""
    from atlas.models import Tag
    from atlas.seeds import seed_base_tags

    with _make_session() as session:
        seed_base_tags(session)
        session.commit()

        total = session.execute(select(func.count()).select_from(Tag)).scalar()
        assert total == EXPECTED_TOTAL, f"Ожидал {EXPECTED_TOTAL} тегов, got {total}"

        owner_slugs = set(
            session.execute(
                select(Tag.slug).where(Tag.category == "owner")
            ).scalars().all()
        )
        stack_slugs = set(
            session.execute(
                select(Tag.slug).where(Tag.category == "stack")
            ).scalars().all()
        )
        domain_slugs = set(
            session.execute(
                select(Tag.slug).where(Tag.category == "domain")
            ).scalars().all()
        )

        assert owner_slugs == EXPECTED_OWNER_SLUGS
        assert stack_slugs == EXPECTED_STACK_SLUGS
        assert domain_slugs == EXPECTED_DOMAIN_SLUGS


def test_seed_base_tags_idempotent():
    """2-й вызов → все skipped, количество не растёт."""
    from atlas.models import Tag
    from atlas.seeds import seed_base_tags

    with _make_session() as session:
        first = seed_base_tags(session)
        session.commit()
        assert first["created"] == EXPECTED_TOTAL
        assert first["skipped"] == 0

        second = seed_base_tags(session)
        session.commit()
        assert second["created"] == 0
        assert second["skipped"] == EXPECTED_TOTAL

        total = session.execute(select(func.count()).select_from(Tag)).scalar()
        assert total == EXPECTED_TOTAL


def test_seed_base_tags_categories_correct():
    """Категория каждого тега правильная: owner=2, stack=14, domain=12."""
    from atlas.models import Tag
    from atlas.seeds import seed_base_tags

    with _make_session() as session:
        seed_base_tags(session)
        session.commit()

        owner_count = session.execute(
            select(func.count()).select_from(Tag).where(Tag.category == "owner")
        ).scalar()
        stack_count = session.execute(
            select(func.count()).select_from(Tag).where(Tag.category == "stack")
        ).scalar()
        domain_count = session.execute(
            select(func.count()).select_from(Tag).where(Tag.category == "domain")
        ).scalar()

        assert owner_count == 2
        assert stack_count == 14
        assert domain_count == 12


def test_seed_base_tags_names_russian_ok():
    """Русские names сохранились (например 'Маркетинг' для 'marketing')."""
    from atlas.models import Tag
    from atlas.seeds import seed_base_tags

    with _make_session() as session:
        seed_base_tags(session)
        session.commit()

        marketing = session.execute(
            select(Tag).where(Tag.slug == "marketing")
        ).scalar_one()
        assert marketing.name == "Маркетинг"

        owner = session.execute(
            select(Tag).where(Tag.slug == "owner")
        ).scalar_one()
        # owner-тег config-driven: name = slug (generic), не русское имя.
        assert owner.name == "owner"

        research = session.execute(
            select(Tag).where(Tag.slug == "research")
        ).scalar_one()
        assert research.name == "Ресёрч"


def test_seed_base_tags_returns_dict():
    """Возвращает {'created': N, 'skipped': M}."""
    from atlas.seeds import seed_base_tags

    with _make_session() as session:
        result = seed_base_tags(session)
        session.commit()

        assert isinstance(result, dict)
        assert set(result.keys()) == {"created", "skipped"}
        assert isinstance(result["created"], int)
        assert isinstance(result["skipped"], int)
        assert result["created"] + result["skipped"] == EXPECTED_TOTAL


def test_seed_base_tags_partial_existing():
    """Если некоторые теги уже есть — скипает их, создаёт остальные."""
    from atlas.models import Tag
    from atlas.seeds import seed_base_tags

    with _make_session() as session:
        # Предзаполним 3 тега вручную (из разных категорий).
        preexisting = [
            Tag(slug="owner", name="Pre-existing Dmitry", category="owner"),
            Tag(slug="python", name="Pre-existing Python", category="stack"),
            Tag(slug="marketing", name="Pre-existing Marketing", category="domain"),
        ]
        for t in preexisting:
            session.add(t)
        session.commit()

        result = seed_base_tags(session)
        session.commit()

        assert result["skipped"] == 3
        assert result["created"] == EXPECTED_TOTAL - 3

        total = session.execute(select(func.count()).select_from(Tag)).scalar()
        assert total == EXPECTED_TOTAL

        # Предзаданные names не перезатёрты.
        owner = session.execute(
            select(Tag).where(Tag.slug == "owner")
        ).scalar_one()
        assert owner.name == "Pre-existing Dmitry"
