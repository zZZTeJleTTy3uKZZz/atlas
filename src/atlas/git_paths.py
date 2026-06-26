"""Маппинг project → GitLab group path.

derive_group_path(project_type, status, archived_group, *, owner_tags=None)
→ строка вида `<org-namespace>/clients` (top-level group + sub-group).

Top-level group выбирается по owner-тегу:
- если у проекта есть owner-тег `<personal-owner>` — namespace = личный (config.personal_namespace)
  (личный namespace пользователя);
- иначе — `<org-namespace>` (организационный namespace).

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
`atlas.paths.TYPE_TO_GROUP` — там физика, тут — gitlab namespacing.
"""
from __future__ import annotations

from typing import Iterable, Optional

from atlas.appconfig import load_config


def _namespaces() -> tuple[str, str, str]:
    """(org_namespace, personal_namespace, personal_owner) из конфига.

    Раньше — хардкод org/personal namespace + owner. Теперь config-driven
    (``AtlasConfig.org_namespace`` / ``personal_namespace`` / ``personal_owner``);
    generic-дефолты пусты — пользователь задаёт свои в config.toml/env.
    """
    try:
        cfg = load_config()
        return cfg.org_namespace, cfg.personal_namespace, cfg.personal_owner
    except Exception:  # pragma: no cover — конфиг недоступен
        return "", "", ""

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
    """Выбрать top-level GitLab group по owner-тегам (config-driven).

    Если в `owner_tags` есть ``personal_owner`` (из конфига) — возвращаем личный
    namespace, иначе организационный namespace. Namespaces берутся из конфига.
    """
    org, personal, personal_owner = _namespaces()
    if owner_tags is not None and personal_owner and personal_owner in owner_tags:
        return personal
    return org


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
            Если содержит `<personal-owner>` — переключаем top-level на личный
            namespace пользователя. Если None или пусто — fallback на <org-namespace>.

    Возвращает: строку вида `<org-namespace>/clients`, `<personal-namespace>/archive/clients`.

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
