"""Маппинг project → GitLab group path.

derive_group_path(project_type, status, archived_group, *, owner_tags=None)
→ строка вида `cifropro1/clients` (top-level group + sub-group).

Top-level group выбирается по owner-тегу:
- если у проекта есть owner-тег `dmitry` — namespace = `zzztejletty3ukzzz`
  (личный namespace Дмитрия в gitlab.com);
- иначе — `cifropro1` (бизнес-namespace Cifro.pro).

Sub-group определяется по `project_type` + `status` + `archived_group`
(приоритеты сверху вниз):
1. archived_group задан → `<top>/archive/{archived_group}`.
2. project_type='client-project' и status ∈ {archived, frozen, completed} →
   `<top>/archive/clients`.
3. project_type='client-project' → `<top>/clients`.
4. project_type ∈ {business-product, personal-utility, personal-project,
   shared-infrastructure} → `<top>/products`.
5. project_type='test' → `<top>/tests`.
6. project_type='inbox' → `<top>/inbox`.

Используется командой `atlas projects git init <ref>` когда `--group` не задан.

NB: Эта функция возвращает только path; маппинг между нашими project_type
и физическим layout каталогов (Clients/Products/Tests) живёт в
`atlas.pm.paths.TYPE_TO_GROUP` — там физика, тут — gitlab namespacing.
"""
from __future__ import annotations

from typing import Iterable, Optional

# Бизнес-namespace Cifro.pro (по умолчанию).
TOP_LEVEL_GROUP = "cifropro1"

# Личный namespace Дмитрия в gitlab.com (для проектов с owner:dmitry).
PERSONAL_TOP_LEVEL_GROUP = "zzztejletty3ukzzz"

# Slug owner-тега, который переключает namespace на личный.
PERSONAL_OWNER_SLUG = "dmitry"

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


def _select_top_level(owner_tags: Optional[Iterable[str]]) -> str:
    """Выбрать top-level GitLab group по owner-тегам.

    Если в `owner_tags` есть `dmitry` — возвращаем личный namespace, иначе
    дефолтный бизнес-namespace.
    """
    if owner_tags is None:
        return TOP_LEVEL_GROUP
    if PERSONAL_OWNER_SLUG in owner_tags:
        return PERSONAL_TOP_LEVEL_GROUP
    return TOP_LEVEL_GROUP


def derive_group_path(
    project_type: str,
    status: str,
    archived_group: Optional[str],
    *,
    owner_tags: Optional[Iterable[str]] = None,
) -> str:
    """Сформировать gitlab group path для проекта.

    Параметры:
        project_type: project_type.slug (`client-project`, `business-product`, ...).
        status: project_status.slug (`active`, `paused`, `archived`, ...).
        archived_group: значение колонки projects.archived_group (если задано —
            проект архивный, и группа уже зафиксирована).
        owner_tags: список owner-tag slug'ов проекта (категория `owner`).
            Если содержит `dmitry` — переключаем top-level на личный
            namespace Дмитрия. Если None или пусто — fallback на cifropro1.

    Возвращает: строку вида `cifropro1/clients`, `zzztejletty3ukzzz/archive/clients`.

    Raises:
        ValueError: если project_type не входит в известный список.
    """
    top = _select_top_level(owner_tags)

    if archived_group is not None:
        return f"{top}/archive/{archived_group}"

    if project_type not in _KNOWN_TYPES:
        known = ", ".join(sorted(_KNOWN_TYPES))
        raise ValueError(
            f"Неизвестный project_type '{project_type}'. Известные: {known}."
        )

    if project_type == "client-project":
        if status in _ARCHIVE_STATUSES:
            return f"{top}/archive/clients"
        return f"{top}/clients"

    if project_type in _PRODUCT_LIKE_TYPES:
        return f"{top}/products"

    if project_type == "test":
        return f"{top}/tests"

    if project_type == "inbox":
        return f"{top}/inbox"

    # Не должно сюда попасть — _KNOWN_TYPES прикрывает.
    raise ValueError(f"Unhandled project_type '{project_type}'")
