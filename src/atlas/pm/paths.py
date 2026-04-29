"""Утилиты физических путей для archive engine.

Компоненты:
- `get_projects_root()`: корневая директория PROJECT/ где лежат репозитории.
  Из env ATLAS_PROJECTS_ROOT, fallback — `~/Documents/PROJECT`.
- `type_slug_to_group()`: маппинг `project_type.slug` → группа
  (`clients` / `products` / `tests`).
- `archive_path()`: путь `root / _Archive / <group> / <slug>`.
- `group_path()`: путь `root / <Clients|Products|Tests> / <slug>` (активный).
- `expected_project_path()`: ожидаемое размещение проекта исходя из БД-полей
  (archived_at, archived_group, type_slug).

Design: см. NP-005 ARCHITECTURE.md §2.7, ADR-001.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

# --------------------------------------------------------------------------- #
# Types & Mappings                                                            #
# --------------------------------------------------------------------------- #

Group = Literal["clients", "products", "tests", "inbox"]

# Имя папки на диске для каждой группы.
# Нижний регистр в БД (archived_group), TitleCase на диске.
# inbox использует префикс "_" (как _Archive) — это специальная зона для
# материалов на переработку, а не обычная группа проектов.
GROUP_FOLDER_NAMES: dict[str, str] = {
    "clients": "Clients",
    "products": "Products",
    "tests": "Tests",
    "inbox": "_Inbox",
}

# Какой project_type.slug в какую группу попадает физически.
TYPE_TO_GROUP: dict[str, Group] = {
    "client-project": "clients",
    "business-product": "products",
    "personal-utility": "products",
    "personal-project": "products",
    "shared-infrastructure": "products",
    "test": "tests",
    "inbox": "inbox",
}


# --------------------------------------------------------------------------- #
# Root                                                                        #
# --------------------------------------------------------------------------- #


def get_projects_root() -> Path:
    """Корневая директория PROJECT/, где лежат все репозитории проектов.

    Порядок:
    1. Env ATLAS_PROJECTS_ROOT (с expanduser + resolve).
    2. Default: ~/Documents/PROJECT (под Windows — C:\\Users\\<USER>\\Documents\\PROJECT).
    """
    env = os.environ.get("ATLAS_PROJECTS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / "Documents" / "PROJECT").resolve()


# --------------------------------------------------------------------------- #
# Group mapping                                                               #
# --------------------------------------------------------------------------- #


def type_slug_to_group(type_slug: str) -> Group:
    """project_type.slug → группа для физического размещения.

    Raises:
        ValueError: если type_slug не в TYPE_TO_GROUP (пусть пользователь
                    добавит маппинг сам, чтобы не молча промахнуться).
    """
    try:
        return TYPE_TO_GROUP[type_slug]
    except KeyError:
        known = ", ".join(sorted(TYPE_TO_GROUP.keys()))
        raise ValueError(
            f"Неизвестный project_type.slug '{type_slug}'. "
            f"Известные: {known}. "
            f"Добавьте маппинг в atlas.pm.paths.TYPE_TO_GROUP."
        )


# --------------------------------------------------------------------------- #
# Path builders                                                               #
# --------------------------------------------------------------------------- #


def archive_path(root: Path, group: str, project_slug: str) -> Path:
    """Путь в архиве: `<root>/_Archive/<group>/<project_slug>`."""
    return root / "_Archive" / group / project_slug


def group_path(root: Path, type_slug: str, project_slug: str) -> Path:
    """Активный путь: `<root>/<Clients|Products|Tests>/<project_slug>`.

    Группа вычисляется из type_slug через TYPE_TO_GROUP.
    """
    group = type_slug_to_group(type_slug)
    folder = GROUP_FOLDER_NAMES[group]
    return root / folder / project_slug


def expected_project_path(
    root: Path,
    type_slug: str,
    project_slug: str,
    *,
    archived: bool,
    archived_group: Optional[str] = None,
) -> Path:
    """Ожидаемое физическое размещение проекта, исходя из БД-полей.

    Если archived=True:
      - если задан archived_group → `_Archive/<archived_group>/<slug>`.
      - иначе fallback на type_slug_to_group(type_slug).
    Иначе:
      - `<Clients|Products|Tests>/<slug>` через type_slug.
    """
    if archived:
        group = archived_group if archived_group else type_slug_to_group(type_slug)
        return archive_path(root, group, project_slug)
    return group_path(root, type_slug, project_slug)


# --------------------------------------------------------------------------- #
# Entity-kind routing (W45-39)                                                #
# --------------------------------------------------------------------------- #
# Логика физики записи зависит от entity_kind:
#   - 'project' — стандартный routing через TYPE_TO_GROUP.
#   - 'idea'    — единая папка `_Ideas/` (один MD-файл на идею).
#   - 'inbox'   — единая папка `_Inbox/` (свалка для AI-разбора).
IDEAS_FOLDER_NAME: str = "_Ideas"
INBOX_FOLDER_NAME: str = "_Inbox"
VALID_ENTITY_KINDS: tuple[str, ...] = ("project", "idea", "inbox")


def entity_kind_to_root(
    entity_kind: str,
    type_slug: Optional[str] = None,
    *,
    root: Optional[Path] = None,
) -> Path:
    """Корневая папка для записи по ``entity_kind``.

    - 'project' → ``<root>/<Clients|Products|Tests|_Inbox>`` (через TYPE_TO_GROUP).
        ``type_slug`` обязателен.
    - 'idea'    → ``<root>/_Ideas`` (type ignored — все идеи в одной папке).
    - 'inbox'   → ``<root>/_Inbox`` (type ignored).

    Raises:
        ValueError: если entity_kind не в VALID_ENTITY_KINDS, или для
                    entity_kind='project' если type_slug=None.
    """
    if entity_kind not in VALID_ENTITY_KINDS:
        raise ValueError(
            f"Неизвестный entity_kind '{entity_kind}'. "
            f"Допустимые: {', '.join(VALID_ENTITY_KINDS)}."
        )
    root = root or get_projects_root()

    if entity_kind == "idea":
        return root / IDEAS_FOLDER_NAME
    if entity_kind == "inbox":
        return root / INBOX_FOLDER_NAME

    if type_slug is None:
        raise ValueError(
            "entity_kind='project' требует type_slug для определения группы."
        )
    group = type_slug_to_group(type_slug)
    folder = GROUP_FOLDER_NAMES[group]
    return root / folder


def entity_logical_path(
    entity_kind: str,
    slug: str,
    type_slug: Optional[str] = None,
    *,
    root: Optional[Path] = None,
) -> Path:
    """Полный логический путь к записи.

    - kind=project → ``<root>/<Group>/<slug>``     (директория или junction).
    - kind=idea    → ``<root>/_Ideas/<slug>.md``    (один MD-файл!).
    - kind=inbox   → ``<root>/_Inbox/<slug>``       (директория/файл).
    """
    base = entity_kind_to_root(entity_kind, type_slug, root=root)
    if entity_kind == "idea":
        return base / f"{slug}.md"
    return base / slug
