"""Маппинг project → GitLab group path.

derive_group_path(project_type, status, archived_group) → строка вида
`cifropro1/clients` (top-level group + sub-group).

Семантика (приоритеты сверху вниз):
1. archived_group задан → `cifropro1/archive/{archived_group}`.
2. project_type='client-project' и status ∈ {archived, frozen, completed} →
   `cifropro1/archive/clients`.
3. project_type='client-project' → `cifropro1/clients`.
4. project_type ∈ {business-product, personal-utility, personal-project,
   shared-infrastructure} → `cifropro1/products`.
5. project_type='test' → `cifropro1/tests`.
6. project_type='inbox' → `cifropro1/inbox`.

Используется командой `atlas projects git init <ref>` когда `--group` не задан.

NB: Эта функция возвращает только path; маппинг между нашими project_type
и физическим layout каталогов (Clients/Products/Tests) живёт в
`atlas.pm.paths.TYPE_TO_GROUP` — там физика, тут — gitlab namespacing.
"""
from __future__ import annotations

from typing import Optional

# Top-level GitLab group, под которой живут все sub-группы Дмитрия.
TOP_LEVEL_GROUP = "cifropro1"

# Типы, которые попадают в "products" (а не в свою отдельную sub-группу).
_PRODUCT_LIKE_TYPES: frozenset[str] = frozenset(
    {
        "business-product",
        "personal-utility",
        "personal-project",
        "shared-infrastructure",
    }
)

# Статусы, при которых client-project считается архивным даже без archived_group.
_ARCHIVE_STATUSES: frozenset[str] = frozenset({"archived", "frozen", "completed"})

# Типы, известные функции. Если придёт что-то ещё — ValueError, чтобы
# не поместить проект в случайное место.
_KNOWN_TYPES: frozenset[str] = (
    frozenset({"client-project", "test", "inbox"}) | _PRODUCT_LIKE_TYPES
)


def derive_group_path(
    project_type: str,
    status: str,
    archived_group: Optional[str],
) -> str:
    """Сформировать gitlab group path для проекта.

    Параметры:
        project_type: project_type.slug (`client-project`, `business-product`, ...).
        status: project_status.slug (`active`, `paused`, `archived`, ...).
        archived_group: значение колонки projects.archived_group (если задано —
            проект архивный, и группа уже зафиксирована).

    Возвращает: строку вида `cifropro1/clients`, `cifropro1/archive/clients`.

    Raises:
        ValueError: если project_type не входит в известный список.
    """
    if archived_group is not None:
        return f"{TOP_LEVEL_GROUP}/archive/{archived_group}"

    if project_type not in _KNOWN_TYPES:
        known = ", ".join(sorted(_KNOWN_TYPES))
        raise ValueError(
            f"Неизвестный project_type '{project_type}'. Известные: {known}."
        )

    if project_type == "client-project":
        if status in _ARCHIVE_STATUSES:
            return f"{TOP_LEVEL_GROUP}/archive/clients"
        return f"{TOP_LEVEL_GROUP}/clients"

    if project_type in _PRODUCT_LIKE_TYPES:
        return f"{TOP_LEVEL_GROUP}/products"

    if project_type == "test":
        return f"{TOP_LEVEL_GROUP}/tests"

    if project_type == "inbox":
        return f"{TOP_LEVEL_GROUP}/inbox"

    # Не должно сюда попасть — _KNOWN_TYPES прикрывает.
    raise ValueError(f"Unhandled project_type '{project_type}'")
