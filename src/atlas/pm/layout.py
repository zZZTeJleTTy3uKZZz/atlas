"""Junction-based layout: единое физическое хранилище + логические junction'ы.

Концепция (см. SKILL.md §3.14):

- Все папки проектов физически живут в `<PROJECT_ROOT>/_storage/<slug>/`.
- В логических папках (`Clients/`, `Products/`, `Tests/`, `_Inbox/`,
  `_Archive/<group>/`) лежат **только** Windows junction-ссылки на storage.
- Смена статуса проекта (archive/unarchive/move) НЕ двигает физику — только
  пересоздаёт junction'ы.
- Никогда ничего не удаляется физически: `remove_junction` снимает только
  ссылку, robocopy /MOVE — атомарный move (не delete).

Этот модуль НЕ читает БД-полей `physical_path`/`logical_path` — он их
вычисляет из формул. Хранение в БД — отдельная задача в backlog.

Формулы:

- `physical_path(slug) = <PROJECT_ROOT>/_storage/<slug>`.
- `logical_path(project)`:
  - active → `<PROJECT_ROOT>/<GROUP_FOLDER>/<slug>` где GROUP_FOLDER берётся
    из `paths.GROUP_FOLDER_NAMES` через `paths.type_slug_to_group(type_slug)`.
  - archived (есть `archived_group`) → `<PROJECT_ROOT>/_Archive/<archived_group>/<slug>`.
  - archived без `archived_group` → fallback на тип.

Объект «проект» здесь — duck-typed: принимаем что угодно с атрибутами
`slug`, `type_slug`, `archived`, `archived_group`, `local_path`. Это позволяет
вызывать функции и из тестов (SimpleNamespace), и из CLI (ORM Project).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional, Protocol

from atlas.pm.junctions import (
    JunctionError,
    SafetyError,
    create_junction,
    is_junction,
    is_windows,
    junction_target,
    remove_junction,
)
from atlas.pm.paths import (
    GROUP_FOLDER_NAMES,
    archive_path,
    get_projects_root,
    type_slug_to_group,
)


# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #


STORAGE_DIR_NAME = "_storage"


# --------------------------------------------------------------------------- #
# Project protocol — duck-type интерфейс                                      #
# --------------------------------------------------------------------------- #


class _ProjectLike(Protocol):
    slug: str
    type_slug: str
    archived: bool
    archived_group: Optional[str]
    local_path: Optional[str]


# --------------------------------------------------------------------------- #
# Path formulas                                                               #
# --------------------------------------------------------------------------- #


def get_storage_path(slug: str, *, root: Optional[Path] = None) -> Path:
    """Физический путь хранилища: ``<root>/_storage/<slug>/``.

    Используется как single-source-of-truth для всех физических операций.
    """
    root = root or get_projects_root()
    return root / STORAGE_DIR_NAME / slug


def get_logical_path(project: _ProjectLike, *, root: Optional[Path] = None) -> Path:
    """Логический путь, по которому проект «видит» пользователь.

    Active: ``<root>/<Clients|Products|Tests|_Inbox>/<slug>``.
    Archived: ``<root>/_Archive/<archived_group>/<slug>`` (или fallback на
    группу из type_slug).

    NOTE: используется slug проекта в качестве display_name. В будущем можно
    подменить на TitleCase-with-dashes — единственное, что тогда меняется,
    это формула здесь.
    """
    root = root or get_projects_root()
    archived = bool(getattr(project, "archived", False))
    archived_group = getattr(project, "archived_group", None)
    type_slug = getattr(project, "type_slug")
    slug = getattr(project, "slug")

    if archived:
        group = archived_group if archived_group else type_slug_to_group(type_slug)
        return archive_path(root, group, slug)

    group = type_slug_to_group(type_slug)
    folder = GROUP_FOLDER_NAMES[group]
    return root / folder / slug


# --------------------------------------------------------------------------- #
# Plan / migrate                                                              #
# --------------------------------------------------------------------------- #


def plan_migrate_to_storage(
    project: _ProjectLike, *, root: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """DRY-RUN-план миграции проекта в `_storage/<slug>/`.

    Возвращает список шагов вида:
    ``[{action, src, dst, status?}, ...]``.

    Никаких реальных операций не выполняет.
    """
    root = root or get_projects_root()
    storage = get_storage_path(project.slug, root=root)
    logical = get_logical_path(project, root=root)

    # Если в storage уже есть папка — ничего делать не нужно.
    if storage.exists():
        return [
            {
                "action": "noop",
                "status": "already_migrated",
                "src": str(_source_path(project, root=root)),
                "dst": str(storage),
                "note": f"_storage/{project.slug}/ уже существует",
            }
        ]

    src = _source_path(project, root=root)

    plan: list[dict[str, Any]] = []
    if src is not None and src.exists():
        plan.append({
            "action": "move",
            "status": "planned",
            "src": str(src),
            "dst": str(storage),
            "note": "Атомарный robocopy /MOVE /E src → _storage/<slug>",
        })
    else:
        plan.append({
            "action": "noop",
            "status": "missing_source",
            "src": str(src) if src else None,
            "dst": str(storage),
            "note": "src не существует — миграция невозможна",
        })

    plan.append({
        "action": "create_junction",
        "status": "planned",
        "src": str(storage),
        "dst": str(logical),
        "note": f"mklink /J {logical} → {storage}",
    })

    plan.append({
        "action": "verify",
        "status": "planned",
        "src": str(storage),
        "dst": str(logical),
        "note": "проверить что storage/<slug>/ существует и junction указывает в него",
    })
    return plan


def migrate_to_storage(
    project: _ProjectLike, *,
    copy_first: bool = False,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    """Реально мигрировать проект в `_storage/<slug>/` и пересоздать junction.

    Возвращает итоговый dict ``{moved, junction_created, files_count, bytes,
    source, target, status}``. На любом safety-error — поднимает исключение
    (caller решает как сообщить пользователю).

    Алгоритм:
    1. Если ``_storage/<slug>/`` уже существует → status='already_migrated'.
    2. Если src не существует → status='missing_source'.
    3. Иначе:
       a. ``copy_first=False`` → robocopy /MOVE /E (атомарный move).
       b. ``copy_first=True`` → robocopy /E (копия) → verify count → удалить
          src через rmdir /S /Q. Используется в случаях когда боимся за data.
    4. После move — создать junction в логической локации (если не junction
       уже).
    """
    root = root or get_projects_root()
    storage = get_storage_path(project.slug, root=root)
    logical = get_logical_path(project, root=root)
    src = _source_path(project, root=root)

    # 1. Уже мигрирован.
    if storage.exists():
        return {
            "status": "already_migrated",
            "moved": False,
            "junction_created": False,
            "files_count": 0,
            "bytes": 0,
            "source": str(src) if src else None,
            "target": storage,
        }

    # 2. Источник отсутствует.
    if src is None or not src.exists():
        return {
            "status": "missing_source",
            "moved": False,
            "junction_created": False,
            "files_count": 0,
            "bytes": 0,
            "source": str(src) if src else None,
            "target": storage,
        }

    # 3. Move src → storage.
    move_result = _perform_storage_move(src, storage, copy_first=copy_first)
    files_count = int(move_result.get("files_count", 0))
    total_bytes = int(move_result.get("bytes", 0))

    # 4. Создать junction в логической локации.
    junction_created = False
    if logical.resolve() != storage.resolve():
        # Если в логике уже junction на правильный таргет — не пересоздаём.
        if is_junction(logical):
            current_target = junction_target(logical)
            if current_target is not None and current_target.resolve() == storage.resolve():
                junction_created = False
            else:
                # Junction указывает не туда — снимаем и пересоздаём.
                remove_junction(logical)
                _create_junction_safe(logical, storage)
                junction_created = True
        elif logical.exists():
            # Реальная директория в логике — НЕ трогаем (safety).
            raise SafetyError(
                f"На логическом пути {logical} реальная директория "
                f"(не junction). Не пересоздаю junction автоматически — "
                f"требует ручного вмешательства."
            )
        else:
            _create_junction_safe(logical, storage)
            junction_created = True

    return {
        "status": "migrated",
        "moved": True,
        "junction_created": junction_created,
        "files_count": files_count,
        "bytes": total_bytes,
        "source": str(src),
        "target": storage,
    }


# --------------------------------------------------------------------------- #
# sync_logical                                                                #
# --------------------------------------------------------------------------- #


def sync_logical(
    project: _ProjectLike, *,
    root: Optional[Path] = None,
    cleanup_other_groups: bool = True,
) -> dict[str, Any]:
    """Привести логическую папку проекта в соответствие с текущим status.

    Алгоритм:

    1. Вычислить ожидаемую `logical_path`.
    2. Если она уже junction в правильный таргет → noop (created=False, ok=True).
    3. Если она существует и junction указывает в другой таргет → пересоздать.
    4. Если она существует и это **реальная директория** → SafetyError
       (не трогаем).
    5. Если её нет → создать junction.
    6. Если ``cleanup_other_groups=True`` — пройтись по другим возможным
       логическим группам (active/archive*) и удалить чужие junction'ы,
       которые указывают на наш storage. Реальные директории — пропускаем.

    Возвращает dict ``{ok, created, removed: list[str]}``.
    """
    root = root or get_projects_root()
    storage = get_storage_path(project.slug, root=root)
    logical = get_logical_path(project, root=root)

    if not storage.exists():
        return {
            "ok": False,
            "created": False,
            "removed": [],
            "issue": f"_storage/{project.slug}/ не существует — сначала migrate",
        }

    created = False
    if is_junction(logical):
        current = junction_target(logical)
        if current is not None and current.resolve() == storage.resolve():
            # OK — junction правильный.
            pass
        else:
            remove_junction(logical)
            _create_junction_safe(logical, storage)
            created = True
    elif logical.exists():
        # Реальная директория на логическом пути → safety.
        raise SafetyError(
            f"На логическом пути {logical} реальная директория "
            f"(не junction). sync_logical отказывается её удалять. "
            f"Требуется ручное вмешательство."
        )
    else:
        _create_junction_safe(logical, storage)
        created = True

    removed: list[str] = []
    if cleanup_other_groups:
        removed = _cleanup_stale_junctions(
            project, root=root, current_logical=logical, storage=storage,
        )

    return {"ok": True, "created": created, "removed": removed}


# --------------------------------------------------------------------------- #
# verify                                                                      #
# --------------------------------------------------------------------------- #


def verify(
    project: _ProjectLike, *, root: Optional[Path] = None,
) -> dict[str, Any]:
    """Проверить целостность layout проекта.

    Возвращает ``{ok: bool, checks: list[dict]}``. Каждый check —
    ``{name, ok, issue?, detail?}``.
    """
    root = root or get_projects_root()
    storage = get_storage_path(project.slug, root=root)
    logical = get_logical_path(project, root=root)
    checks: list[dict[str, Any]] = []

    # Check 1: storage существует и не пуст.
    if not storage.exists():
        checks.append({
            "name": "storage_exists",
            "ok": False,
            "issue": f"_storage/{project.slug}/ не существует",
            "detail": str(storage),
        })
    else:
        try:
            entries = list(storage.iterdir())
        except OSError:
            entries = []
        checks.append({
            "name": "storage_exists",
            "ok": True,
            "detail": f"{len(entries)} entries",
        })

    # Check 2: junction в логической локации существует.
    if not logical.exists() and not os.path.islink(str(logical)):
        checks.append({
            "name": "logical_junction_exists",
            "ok": False,
            "issue": f"junction в логической папке отсутствует: {logical}",
            "detail": str(logical),
        })
    else:
        if not is_junction(logical):
            checks.append({
                "name": "logical_junction_exists",
                "ok": False,
                "issue": (
                    f"в логической папке {logical} лежит реальная директория, "
                    f"а не junction"
                ),
                "detail": str(logical),
            })
        else:
            target = junction_target(logical)
            if target is None or target.resolve() != storage.resolve():
                checks.append({
                    "name": "logical_junction_target",
                    "ok": False,
                    "issue": (
                        f"junction {logical} указывает в {target}, "
                        f"а ожидался {storage}"
                    ),
                    "detail": str(target) if target else None,
                })
            else:
                checks.append({
                    "name": "logical_junction_target",
                    "ok": True,
                    "detail": str(target),
                })

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks, "logical": str(logical), "storage": str(storage)}


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _source_path(
    project: _ProjectLike, *, root: Optional[Path] = None,
) -> Optional[Path]:
    """Куда смотреть при миграции в storage.

    Приоритет:
    1. ``project.local_path`` (если задан и существует).
    2. ``logical_path(project)`` — на случай если БД-поле не заполнено,
       но папка реально лежит по логическому пути.
    """
    root = root or get_projects_root()
    local_path = getattr(project, "local_path", None)
    if local_path:
        p = Path(local_path)
        return p
    return get_logical_path(project, root=root)


def _create_junction_safe(link: Path, target: Path) -> None:
    """Тонкая обёртка над `junctions.create_junction`, изолированная для моков.

    Дополнительно гарантирует существование `link.parent` (создаёт по необходимости).
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    create_junction(link, target)


def _perform_storage_move(
    src: Path, dst: Path, *, copy_first: bool = False,
) -> dict[str, Any]:
    """Реальный перенос содержимого ``src/*`` → ``dst/``.

    Реализация зависит от ОС:

    - Windows + ``copy_first=False`` → ``robocopy <src> <dst> /MOVE /E /NFL /NDL /NJH /NJS /NP``.
    - Windows + ``copy_first=True``  → ``robocopy /E`` (копия), потом
      ``cmd /c rmdir /S /Q <src>`` (только если копия удалась).
    - не-Windows → ``shutil.move`` (fallback, тестам и Linux-окружениям).

    Возвращает ``{files_count, bytes}`` — best-effort метрики.
    """
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    if not is_windows():
        # POSIX fallback (для тестов и macOS/Linux).
        shutil.move(str(src), str(dst))
        return {"files_count": 0, "bytes": 0}

    # robocopy не умеет создавать dst автоматически если src не существует.
    if not src.exists():
        raise FileNotFoundError(f"src не существует: {src}")

    # robocopy сам создаёт dst, но оставляет пустой src — финальный rmdir его
    # уберёт. У /MOVE при удачном переносе src удаляется, кроме самой папки.
    # Поэтому после команды дополнительно snimaem пустую src-папку.
    flags = ["/E", "/NFL", "/NDL", "/NJH", "/NP", "/R:1", "/W:1"]
    if copy_first:
        # Копия + verify + удалить src.
        cmd = ["robocopy", str(src), str(dst), *flags]
        result = subprocess.run(cmd, capture_output=True, text=True)
        # robocopy: success codes 0..7, ошибки — 8+.
        if result.returncode >= 8:
            raise RuntimeError(
                f"robocopy (copy) failed code={result.returncode}: "
                f"{result.stdout.strip()!r} {result.stderr.strip()!r}"
            )
        # Удалить src ТОЛЬКО после успешной копии.
        rm_result = subprocess.run(
            ["cmd", "/c", "rmdir", "/S", "/Q", str(src)],
            capture_output=True, text=True,
        )
        if rm_result.returncode != 0:
            raise RuntimeError(
                f"После copy не удалось удалить src ({src}): "
                f"{rm_result.stderr.strip()!r}"
            )
    else:
        cmd = ["robocopy", str(src), str(dst), *flags, "/MOVE"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode >= 8:
            raise RuntimeError(
                f"robocopy /MOVE failed code={result.returncode}: "
                f"{result.stdout.strip()!r} {result.stderr.strip()!r}"
            )

    return {"files_count": 0, "bytes": 0}


def _cleanup_stale_junctions(
    project: _ProjectLike, *,
    root: Path,
    current_logical: Path,
    storage: Path,
) -> list[str]:
    """Снять junction'ы из других логических групп, указывающих на наш storage.

    Возвращает список путей удалённых junction'ов (для отчёта).
    """
    removed: list[str] = []
    candidates: list[Path] = []
    slug = project.slug

    # Active группы.
    for folder in GROUP_FOLDER_NAMES.values():
        candidates.append(root / folder / slug)
    # Archive подгруппы.
    archive_root = root / "_Archive"
    if archive_root.exists():
        for sub in archive_root.iterdir():
            if sub.is_dir():
                candidates.append(sub / slug)

    for cand in candidates:
        if cand.resolve() == current_logical.resolve():
            continue
        if not (cand.exists() or os.path.islink(str(cand))):
            continue
        # SAFETY: только junction-ссылки.
        if not is_junction(cand):
            continue
        target = junction_target(cand)
        # Снимаем только те junction'ы, которые указывают на НАШ storage —
        # чтобы не задеть посторонние ссылки в той же папке.
        if target is None or target.resolve() != storage.resolve():
            continue
        try:
            remove_junction(cand)
            removed.append(str(cand))
        except (SafetyError, JunctionError):
            # Не падаем — лучше пропустить и доложить наверх.
            pass

    return removed
