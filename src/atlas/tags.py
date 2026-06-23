"""Утилиты tags engine для PM-системы.

Содержит:
- normalize_tag_ref: парс 'category:slug' | 'slug' → tuple.
- generate_tag_slug: slug по name + uniqueness check с суффиксами.
- resolve_tag_ref: найти Tag по slug / category:slug / UUID full / UUID short.
- list_project_tags: все теги проекта, sorted by (category, slug).
- filter_projects_by_tags: AND-фильтр по списку tag-slug'ов, опция archived.
- attach_tags / detach_tags: массовое добавление/удаление связей, идемпотентно.

Дизайн — см. NP-005 MODEL.md.
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from atlas.models import Project, ProjectTag, Tag
from atlas.slugs import (
    UUID_SHORT_MIN,
    _is_full_uuid,
    _looks_like_uuid_prefix,
    generate_unique_slug,
    slugify_text,
)

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

VALID_CATEGORIES: frozenset[str] = frozenset({"owner", "stack", "domain", "other"})


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #


class InvalidTagCategoryError(ValueError):
    """Поднимается когда category не из VALID_CATEGORIES."""


class AmbiguousTagRefError(ValueError):
    """Поднимается когда bare-slug tag ref матчит >1 тег в разных категориях,
    либо UUID prefix матчит >1 запись.
    """


# --------------------------------------------------------------------------- #
# normalize_tag_ref                                                           #
# --------------------------------------------------------------------------- #


def normalize_tag_ref(ref: str) -> tuple[Optional[str], str]:
    """Парсит tag-ref в формате 'category:slug' или 'slug'.

    Возвращает (category, slug). category = None, если не указан.
    Примеры:
        'owner:dmitry' → ('owner', 'dmitry')
        'b24' → (None, 'b24')

    Raises:
        ValueError: если ref пустой, или > 1 двоеточия.
        InvalidTagCategoryError: если category не из VALID_CATEGORIES.
    """
    if not ref:
        raise ValueError("Пустой tag ref")

    parts = ref.split(":")
    if len(parts) == 1:
        return (None, parts[0])
    if len(parts) == 2:
        category, slug = parts[0], parts[1]
        if category not in VALID_CATEGORIES:
            raise InvalidTagCategoryError(
                f"Неизвестная категория '{category}'. "
                f"Допустимо: {sorted(VALID_CATEGORIES)}"
            )
        if not slug:
            raise ValueError("Пустой slug в tag ref")
        return (category, slug)

    raise ValueError(
        f"Неверный формат tag ref '{ref}'. "
        "Ожидается 'category:slug' или 'slug'."
    )


# --------------------------------------------------------------------------- #
# generate_tag_slug                                                           #
# --------------------------------------------------------------------------- #


def generate_tag_slug(
    name: str,
    category: str,
    exists_fn: Callable[[str], bool],
) -> str:
    """Сгенерировать уникальный slug для тега из имени.

    category в slug НЕ зашивается — это отдельное поле в БД.
    Логика: slugify_text(name) → generate_unique_slug(-2, -3, …).

    Args:
        name: исходное имя (RU/EN).
        category: для передачи в проверку уникальности, но не участвует в slug.
        exists_fn: callable(slug) → bool; True если slug уже занят.

    Returns:
        Уникальный slug (≤ 50 chars).
    """
    base = slugify_text(name, max_length=50)
    if not base:
        # Fallback если slugify вернул пустую строку
        base = "tag"
    return generate_unique_slug(base, exists_fn)


# --------------------------------------------------------------------------- #
# resolve_tag_ref                                                             #
# --------------------------------------------------------------------------- #


def resolve_tag_ref(session: Session, ref: str) -> Optional[Tag]:
    """Найти Tag по:
    - 'category:slug' (exact match в рамках категории);
    - 'slug' (глобально; если тегов с этим slug > 1 — AmbiguousTagRefError);
    - UUID full (36 chars);
    - UUID short prefix (≥ 7 hex chars).

    Returns None если не найден.

    Raises:
        AmbiguousTagRefError: если bare slug или UUID prefix матчит >1 запись.
        ValueError: если ref невалидного формата (например, 'a:b:c').
    """
    if not ref:
        return None

    # 1. Сначала пробуем UUID (full)
    if _is_full_uuid(ref):
        return session.execute(
            select(Tag).where(Tag.id == ref)
        ).scalar_one_or_none()

    # 2. UUID short prefix (только если это похоже на hex-ref
    # и не содержит ':' — у tag slug'а может быть цифра/буква, но
    # category:slug не может совпадать с UUID prefix).
    if (
        ":" not in ref
        and len(ref) >= UUID_SHORT_MIN
        and _looks_like_uuid_prefix(ref)
    ):
        matches = session.execute(
            select(Tag).where(Tag.id.like(f"{ref}%"))
        ).scalars().all()
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousTagRefError(
                f"UUID prefix '{ref}' матчит {len(matches)} тегов; "
                "уточни больше символов"
            )
        # 0 матчей — fallthrough в slug-lookup (маловероятно для hex,
        # но возможно если slug ровно hex).

    # 3. category:slug или bare slug
    category, slug = normalize_tag_ref(ref)

    stmt = select(Tag).where(Tag.slug == slug)
    if category is not None:
        stmt = stmt.where(Tag.category == category)

    matches = session.execute(stmt).scalars().all()
    if len(matches) == 0:
        return None
    if len(matches) > 1:
        # У нас slug unique глобально, так что len > 1 быть не должно,
        # но защищаемся на случай если схема поменяется в будущем.
        raise AmbiguousTagRefError(
            f"Tag ref '{ref}' матчит {len(matches)} тегов; "
            "уточни 'category:slug'"
        )
    return matches[0]


# --------------------------------------------------------------------------- #
# list_project_tags                                                           #
# --------------------------------------------------------------------------- #


def list_project_tags(session: Session, project_id: str) -> list[Tag]:
    """Все теги проекта, sorted by (category ASC, slug ASC)."""
    stmt = (
        select(Tag)
        .join(ProjectTag, ProjectTag.tag_id == Tag.id)
        .where(ProjectTag.project_id == project_id)
        .order_by(Tag.category, Tag.slug)
    )
    return list(session.execute(stmt).scalars().all())


# --------------------------------------------------------------------------- #
# filter_projects_by_tags                                                     #
# --------------------------------------------------------------------------- #


def filter_projects_by_tags(
    session: Session,
    tag_slugs: list[str],
    archived: bool = False,
) -> list[Project]:
    """AND-фильтр: проекты у которых есть ВСЕ указанные теги.

    Args:
        tag_slugs: список tag.slug'ов. Пустой список → все проекты
            (с учётом archived).
        archived: если False (default) — скрываем archived_at IS NOT NULL.

    Returns:
        Список Project.
    """
    # Пустой фильтр тегов → просто применяем archived.
    if not tag_slugs:
        stmt = select(Project)
        if not archived:
            stmt = stmt.where(Project.archived_at.is_(None))
        stmt = stmt.order_by(Project.slug)
        return list(session.execute(stmt).scalars().all())

    # GROUP BY HAVING COUNT(DISTINCT tag.id) = len(tag_slugs)
    required = len(set(tag_slugs))
    stmt = (
        select(Project)
        .join(ProjectTag, ProjectTag.project_id == Project.id)
        .join(Tag, Tag.id == ProjectTag.tag_id)
        .where(Tag.slug.in_(tag_slugs))
        .group_by(Project.id)
        .having(func.count(func.distinct(Tag.id)) == required)
    )
    if not archived:
        stmt = stmt.where(Project.archived_at.is_(None))
    stmt = stmt.order_by(Project.slug)

    return list(session.execute(stmt).scalars().all())


# --------------------------------------------------------------------------- #
# attach_tags / detach_tags                                                   #
# --------------------------------------------------------------------------- #


def attach_tags(session: Session, project_id: str, tag_ids: list[str]) -> int:
    """Массово прикрепить tag_ids к project_id. Идемпотентно.

    Returns:
        Количество фактически добавленных (новых) связей.
    """
    if not tag_ids:
        return 0

    # Узнаём, какие уже прикреплены
    existing = set(
        session.execute(
            select(ProjectTag.tag_id)
            .where(ProjectTag.project_id == project_id)
            .where(ProjectTag.tag_id.in_(tag_ids))
        ).scalars().all()
    )

    added = 0
    for tag_id in tag_ids:
        if tag_id in existing:
            continue
        session.add(ProjectTag(project_id=project_id, tag_id=tag_id))
        existing.add(tag_id)  # защита от дубликатов в самом tag_ids
        added += 1
    return added


def detach_tags(session: Session, project_id: str, tag_ids: list[str]) -> int:
    """Массово открепить связи project_id <-> tag_ids.

    Returns:
        Количество удалённых связей.
    """
    if not tag_ids:
        return 0

    result = session.execute(
        delete(ProjectTag)
        .where(ProjectTag.project_id == project_id)
        .where(ProjectTag.tag_id.in_(tag_ids))
    )
    return result.rowcount or 0
