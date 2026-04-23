"""Утилиты slug/prefix/resolve для PM-системы.

Содержит:
- slugify_text: текст (RU/EN) → kebab-case ASCII slug
- generate_unique_slug: добавляет суффикс -2, -3, ... при коллизии
- generate_prefix_from_slug: автогенерация короткого префикса проекта
- build_task_slug: склейка project.prefix + task-part
- next_task_number: следующий свободный глобальный номер задачи
- resolve_project_ref: найти Project по slug / UUID full / UUID short prefix
- resolve_task_ref: найти Task по number / slug / UUID

Дизайн см. NP-005 / Atlas CRUD spec.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atlas.pm.models import Project, Task

# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #


class AmbiguousRefError(ValueError):
    """Поднимается, когда UUID-prefix матчит >1 запись."""


class SlugGenerationError(RuntimeError):
    """Не удалось подобрать уникальный slug за max_attempts попыток."""


# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

UUID_FULL_LEN = 36
UUID_SHORT_MIN = 7
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_UUID_PREFIX_RE = re.compile(r"^[0-9a-f-]+$")


# --------------------------------------------------------------------------- #
# Slugify                                                                     #
# --------------------------------------------------------------------------- #


def slugify_text(text: str, max_length: int = 50) -> str:
    """Текст (RU/EN) → kebab-case ASCII slug.

    Использует python-slugify (translit RU → ASCII).
    Возвращает только [a-z0-9-], длина ≤ max_length.
    Пустая/мусорная строка → "".
    """
    if not text:
        return ""
    out = slugify(text, max_length=max_length, lowercase=True, separator="-")
    return out


# --------------------------------------------------------------------------- #
# Prefix generation                                                            #
# --------------------------------------------------------------------------- #


def generate_prefix_from_slug(slug: str, max_length: int = 5) -> str:
    """Авто-сгенерировать короткий префикс проекта из его slug.

    Логика:
    - Один сегмент длиной 1-2 → как есть.
    - Один сегмент длиной ≥3, без цифр → первые 3 буквы.
    - Несколько сегментов: первая буква каждого буквенного сегмента + цифры
      из числовых сегментов. Лидирующие нули в числовых сегментах съедаются.
      Пример: 'np-005' → 'np5', 'docs-parsing' → 'dp', 'ml-model-v2' → 'mmv2'.

    Возвращаемая строка: только [a-z0-9], длина ≤ max_length.
    """
    if not slug:
        return ""

    slug = slug.lower()
    segments = [s for s in slug.split("-") if s]

    if not segments:
        return ""

    # Один сегмент
    if len(segments) == 1:
        seg = segments[0]
        # Только буквы — берём первые 3 (или меньше)
        if seg.isalpha():
            return seg[:3][:max_length]
        # Смешанный (буквы+цифры) — сохраняем как есть, обрезая до max_length
        out = "".join(ch for ch in seg if ch.isalnum())
        return out[:max_length]

    # Многосегментный — три режима:
    # 1. Все сегменты alpha → первая буква каждого ('docs-parsing' → 'dp').
    # 2. Есть pure-numeric сегмент → alpha сегменты полностью + digits
    #    из numeric ('np-005' → 'np5').
    # 3. Есть mixed (буквы+цифры) сегмент, но нет pure-numeric → первая буква
    #    каждого alpha + первая буква и цифры из mixed ('ml-model-v2' → 'mmv2').
    has_pure_numeric = any(seg.isdigit() for seg in segments)
    all_alpha = all(seg.isalpha() for seg in segments)

    parts: list[str] = []
    if all_alpha:
        for seg in segments:
            parts.append(seg[0])
    elif has_pure_numeric:
        for seg in segments:
            if seg.isdigit():
                stripped = seg.lstrip("0") or "0"
                parts.append(stripped)
            elif seg.isalpha():
                parts.append(seg)
            else:
                first_alpha = next((ch for ch in seg if ch.isalpha()), "")
                digits = "".join(ch for ch in seg if ch.isdigit()).lstrip("0")
                parts.append(first_alpha + digits)
    else:
        # Mixed-режим (нет pure-numeric, есть mixed)
        for seg in segments:
            if seg.isalpha():
                parts.append(seg[0])
            else:
                # Mixed: первая буква + все цифры (без лидирующих нулей)
                first_alpha = next((ch for ch in seg if ch.isalpha()), "")
                digits = "".join(ch for ch in seg if ch.isdigit()).lstrip("0")
                parts.append(first_alpha + digits)

    out = "".join(parts)
    # Только [a-z0-9]
    out = "".join(ch for ch in out if ch.isalnum())
    return out[:max_length]


# --------------------------------------------------------------------------- #
# Unique slug                                                                 #
# --------------------------------------------------------------------------- #


def generate_unique_slug(
    base: str,
    exists_fn: Callable[[str], bool],
    max_attempts: int = 100,
) -> str:
    """Если base свободен — вернуть его. Иначе пробует base-2, base-3, ...

    exists_fn(candidate) -> True если занят.
    Raises SlugGenerationError если max_attempts исчерпан.
    """
    if not exists_fn(base):
        return base

    for n in range(2, max_attempts + 1):
        candidate = f"{base}-{n}"
        if not exists_fn(candidate):
            return candidate

    raise SlugGenerationError(
        f"Не удалось подобрать уникальный slug на основе '{base}' "
        f"за {max_attempts} попыток"
    )


# --------------------------------------------------------------------------- #
# Task slug builder                                                           #
# --------------------------------------------------------------------------- #


def build_task_slug(project_prefix: str, task_part: str) -> str:
    """Склеить префикс проекта и slug задачи через дефис.

    'cf' + 'fix-login' → 'cf-fix-login'.
    """
    return f"{project_prefix}-{task_part}"


# --------------------------------------------------------------------------- #
# Next task number                                                            #
# --------------------------------------------------------------------------- #


def next_task_number(session: Session) -> int:
    """Следующий свободный номер = MAX(Task.number) + 1.

    Пустая таблица → 1. Не закрывает gap'ы.
    """
    current_max = session.execute(
        select(func.max(Task.number))
    ).scalar()
    if current_max is None:
        return 1
    return int(current_max) + 1


# --------------------------------------------------------------------------- #
# Resolvers                                                                   #
# --------------------------------------------------------------------------- #


def _looks_like_uuid_prefix(ref: str) -> bool:
    """True если ref состоит из hex-символов и дефисов (потенциальный UUID prefix)."""
    return bool(_UUID_PREFIX_RE.match(ref))


def _is_full_uuid(ref: str) -> bool:
    return len(ref) == UUID_FULL_LEN and bool(_UUID_RE.match(ref))


def resolve_project_ref(session: Session, ref: str) -> Optional[Project]:
    """Найти Project по slug / UUID full / UUID short (≥ 7 chars).

    Порядок поиска:
    1. По slug (exact match).
    2. По UUID (full, 36 chars).
    3. По UUID short prefix (≥ 7 chars) через LIKE 'ref%'.

    Returns None если не найден.
    Raises AmbiguousRefError если UUID prefix матчит >1 запись.
    """
    if not ref:
        return None

    # 1. Slug
    project = session.execute(
        select(Project).where(Project.slug == ref)
    ).scalar_one_or_none()
    if project is not None:
        return project

    # 2. Full UUID
    if _is_full_uuid(ref):
        return session.execute(
            select(Project).where(Project.id == ref)
        ).scalar_one_or_none()

    # 3. UUID short prefix
    if len(ref) >= UUID_SHORT_MIN and _looks_like_uuid_prefix(ref):
        matches = session.execute(
            select(Project).where(Project.id.like(f"{ref}%"))
        ).scalars().all()
        if len(matches) == 0:
            return None
        if len(matches) > 1:
            raise AmbiguousRefError(
                f"UUID prefix '{ref}' матчит {len(matches)} проекта; "
                "уточни больше символов"
            )
        return matches[0]

    return None


def resolve_task_ref(session: Session, ref: str) -> Optional[Task]:
    """Найти Task по number (int) / slug / UUID full / UUID short.

    Порядок:
    1. Если ref — целое число → Task.number.
    2. По slug (exact match).
    3. По UUID full (36 chars).
    4. По UUID short prefix (≥ 7 chars) через LIKE.

    Returns None если не найден.
    Raises AmbiguousRefError если UUID prefix матчит >1 запись.
    """
    if not ref:
        return None

    # 1. Число → Task.number
    if ref.isdigit():
        n = int(ref)
        return session.execute(
            select(Task).where(Task.number == n)
        ).scalar_one_or_none()

    # 2. Slug
    task = session.execute(
        select(Task).where(Task.slug == ref)
    ).scalar_one_or_none()
    if task is not None:
        return task

    # 3. Full UUID
    if _is_full_uuid(ref):
        return session.execute(
            select(Task).where(Task.id == ref)
        ).scalar_one_or_none()

    # 4. UUID short prefix
    if len(ref) >= UUID_SHORT_MIN and _looks_like_uuid_prefix(ref):
        matches = session.execute(
            select(Task).where(Task.id.like(f"{ref}%"))
        ).scalars().all()
        if len(matches) == 0:
            return None
        if len(matches) > 1:
            raise AmbiguousRefError(
                f"UUID prefix '{ref}' матчит {len(matches)} задач; "
                "уточни больше символов"
            )
        return matches[0]

    return None
