"""CLI-команды `atlas projects layout ...` — junction-based layout.

Sub-typer-app, регистрируется в `projects_app` через
``projects_app.add_typer(layout_app, name="layout")``.

Концепция (см. SKILL.md §3.14):

- Все папки проектов физически живут в `<PROJECT_ROOT>/_storage/<slug>/`.
- В логических папках (`Clients/`, `Products/`, `Tests/`, `_Inbox/`,
  `_Archive/<group>/`) — junction-ссылки.
- Смена статуса/типа НЕ двигает физику, только пересоздаёт junction.

Команды:

- ``init <ref>``         — однократный перенос проекта в `_storage/<slug>/`
                            + создание junction в logical_path.
- ``sync <ref>``         — пересоздать junction в правильную логическую папку
                            согласно текущему type+status проекта.
- ``verify [<ref>]``     — диагностика layout: storage существует, junction
                            есть и указывает в правильный таргет.
- ``migrate-all``        — bulk init по всем проектам (с фильтрами).
- ``list-storage``       — overview всех `_storage/<slug>/` (диагностика).

Безопасность (см. ТЗ NP-005):
- destructive (`init` без `--confirm` / `migrate-all` без `--confirm`) —
  требует подтверждения через `typer.confirm()`.
- Все subprocess (robocopy/mklink/rmdir) — через layout-уровень, мокается в
  тестах (тесты НЕ дёргают реальный robocopy).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm._time import msk_now
from atlas.pm.db import make_engine, make_session, DEFAULT_DB_PATH
from atlas.pm.junctions import JunctionError, SafetyError
from atlas.pm import layout as layout_mod
from atlas.pm.layout import (
    get_logical_path,
    get_storage_path,
    plan_migrate_to_storage,
)
from atlas.pm.models import (
    ActionLog,
    Participant,
    Project,
    ProjectStatus,
    ProjectType,
)
from atlas.pm.paths import (
    GROUP_FOLDER_NAMES,
    get_projects_root,
    type_slug_to_group,
)
from atlas.pm.slugs import AmbiguousRefError, resolve_project_ref
from atlas.pm.tags import filter_projects_by_tags


def _is_junction(p: Path) -> bool:
    """Прокси к `layout_mod.is_junction` — чтобы тесты могли его патчить
    через `patch.object(layout, "is_junction", ...)` и наша CLI это
    подхватывала."""
    return layout_mod.is_junction(p)


layout_app = typer.Typer(
    no_args_is_help=True,
    help="Junction-based layout: единое физическое хранилище `_storage/`.",
)
console = Console()


DEFAULT_ACTOR_SLUG = "dmitry"


# --------------------------------------------------------------------------- #
# DB helpers                                                                  #
# --------------------------------------------------------------------------- #


def _db_url() -> str:
    return os.environ.get("ATLAS_DB_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


def _actor_id(session: Session) -> Optional[str]:
    actor = session.execute(
        select(Participant).where(Participant.slug == DEFAULT_ACTOR_SLUG)
    ).scalar_one_or_none()
    return actor.id if actor else None


def _log_action(
    session: Session,
    *,
    action: str,
    entity_id: str,
    details: dict[str, Any],
) -> None:
    entry = ActionLog(
        actor_id=_actor_id(session),
        entity_type="project",
        entity_id=entity_id,
        action=action,
        details_json=json.dumps(details, ensure_ascii=False, default=str),
    )
    session.add(entry)


# --------------------------------------------------------------------------- #
# Project adapter                                                             #
# --------------------------------------------------------------------------- #


def _project_view(session: Session, project: Project) -> SimpleNamespace:
    """Адаптер ORM `Project` → duck-typed view, который понимают функции
    из `atlas.pm.layout`.

    Layout-функции ожидают атрибуты ``slug, type_slug, archived,
    archived_group, local_path``. ORM Project их напрямую не отдаёт —
    нужен FK lookup в `project_types` + конвертация `archived_at` → bool.
    """
    pt = session.get(ProjectType, project.type_id)
    type_slug = pt.slug if pt else "client-project"
    return SimpleNamespace(
        id=project.id,
        slug=project.slug,
        name=project.name,
        type_slug=type_slug,
        archived=project.archived_at is not None,
        archived_group=project.archived_group,
        local_path=project.local_path,
    )


def _resolve_or_die(session: Session, ref: str) -> Project:
    try:
        project = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if project is None:
        console.print(f"[red]Project '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return project


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #


@layout_app.command("init")
def init_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    copy_first: bool = typer.Option(
        False, "--copy-first",
        help="Сначала robocopy /E (копия), потом удалить src — безопаснее /MOVE.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Печать плана без реальных операций.",
    ),
    confirm: bool = typer.Option(
        False, "--confirm",
        help="Пропустить интерактивное подтверждение destructive операций.",
    ),
    no_junction: bool = typer.Option(
        False, "--no-junction",
        help="Только move в `_storage/`, без создания junction.",
    ),
) -> None:
    """Однократный перенос проекта в `_storage/<slug>/` + junction.

    Алгоритм:
    1. `project.local_path` должен существовать.
    2. Если уже junction → error («использовать sync»).
    3. `_storage/<slug>` не должен существовать.
    4. `--dry-run` → печать плана и exit.
    5. Иначе destructive move (с/без `--copy-first`) → создать junction.
    """
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    with make_session(engine) as session:
        project = _resolve_or_die(session, ref)
        view = _project_view(session, project)

        if not view.local_path:
            console.print(
                f"[red]У проекта '{view.slug}' не задан local_path. "
                f"Установите его через `atlas projects update`.[/red]"
            )
            raise typer.Exit(code=1)

        local_path = Path(view.local_path)

        # Уже junction? Тогда sync, не init.
        if _is_junction(local_path):
            console.print(
                f"[red]Проект '{view.slug}' уже мигрирован "
                f"(local_path является junction). "
                f"Используйте `atlas projects layout sync {view.slug}`.[/red]"
            )
            raise typer.Exit(code=1)

        if not local_path.exists():
            console.print(
                f"[red]local_path не существует: {local_path}.[/red]"
            )
            raise typer.Exit(code=1)

        storage = get_storage_path(view.slug, root=root)
        if storage.exists():
            console.print(
                f"[red]_storage/{view.slug} уже существует: {storage}. "
                f"Сначала разберитесь с конфликтом руками.[/red]"
            )
            raise typer.Exit(code=1)

        logical = get_logical_path(view, root=root)

        # ---- DRY-RUN ----
        if dry_run:
            plan = plan_migrate_to_storage(view, root=root)
            console.print(f"[bold]Plan для '{view.slug}':[/bold]")
            for step in plan:
                action = step.get("action")
                src = step.get("src") or "—"
                dst = step.get("dst") or "—"
                note = step.get("note") or ""
                console.print(f"  • {action}: {src} → {dst}  [dim]{note}[/dim]")
            console.print(
                "\n[yellow]Dry-run. Реальные операции не выполнялись.[/yellow]"
            )
            return

        # ---- Confirmation для destructive ----
        if not confirm:
            confirmed = typer.confirm(
                f"Это переместит {local_path} → {storage}"
                f"{' и создаст junction ' + str(logical) if not no_junction else ''}. Продолжить?",
                default=False,
            )
            if not confirmed:
                console.print("[yellow]Отменено.[/yellow]")
                raise typer.Exit(code=1)

        # ---- Move ----
        try:
            move_result = layout_mod._perform_storage_move(
                local_path, storage, copy_first=copy_first,
            )
        except (RuntimeError, FileNotFoundError, OSError) as exc:
            console.print(f"[red]Ошибка переноса: {exc}[/red]")
            raise typer.Exit(code=1)

        files_count = int(move_result.get("files_count", 0))

        # ---- Junction ----
        junction_created = False
        if not no_junction:
            # Если logical совпадает со storage — пропускаем (странный случай).
            if logical.resolve() != storage.resolve():
                # Если что-то уже лежит на logical-пути:
                if _is_junction(logical):
                    console.print(
                        f"[yellow]⚠ junction уже есть в {logical} — пропускаю.[/yellow]"
                    )
                elif logical.exists():
                    console.print(
                        f"[red]На логическом пути {logical} реальная директория, "
                        f"не junction. Не пересоздаю автоматически.[/red]"
                    )
                    raise typer.Exit(code=1)
                else:
                    try:
                        layout_mod._create_junction_safe(logical, storage)
                        junction_created = True
                    except (JunctionError, SafetyError) as exc:
                        console.print(
                            f"[red]Не удалось создать junction: {exc}[/red]"
                        )
                        raise typer.Exit(code=1)

        # ---- Update local_path ----
        new_local = str(logical) if not no_junction else str(storage)
        old_local = project.local_path
        project.local_path = new_local
        project.last_touched_at = msk_now()

        details = {
            "kind": "init",
            "old_local_path": old_local,
            "new_local_path": new_local,
            "storage": str(storage),
            "logical": str(logical),
            "copy_first": copy_first,
            "no_junction": no_junction,
            "junction_created": junction_created,
            "files_count": files_count,
        }
        _log_action(
            session,
            action="project_layout_init",
            entity_id=project.id,
            details=details,
        )
        session.commit()

    console.print(
        f"[green]✓ Project '{ref}' migrated to _storage/[/green]"
    )
    console.print(f"  Storage:  [bold]{storage}[/bold]")
    if not no_junction:
        console.print(f"  Junction: [bold]{logical}[/bold] → {storage}")
    else:
        console.print("  [dim](--no-junction: junction не создан)[/dim]")


# --------------------------------------------------------------------------- #
# sync                                                                        #
# --------------------------------------------------------------------------- #


@layout_app.command("sync")
def sync_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Только показать что будет сделано.",
    ),
) -> None:
    """Пересоздать junction в правильной логической папке."""
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    with make_session(engine) as session:
        project = _resolve_or_die(session, ref)
        view = _project_view(session, project)

        storage = get_storage_path(view.slug, root=root)
        expected_logical = get_logical_path(view, root=root)
        current = Path(view.local_path) if view.local_path else None

        if not storage.exists():
            console.print(
                f"[red]_storage/{view.slug} не существует. "
                f"Сначала: `atlas projects layout init {view.slug}`.[/red]"
            )
            raise typer.Exit(code=1)

        # ---- Дeтерминируем что нужно сделать. ----
        plan_lines: list[str] = []
        same_path = (
            current is not None
            and str(current).rstrip("\\/") == str(expected_logical).rstrip("\\/")
        )
        action_kind = "noop"

        if same_path and _is_junction(expected_logical):
            action_kind = "noop"
            plan_lines.append(f"junction уже корректен: {expected_logical}")
        else:
            # 1. Текущий junction (если есть и не там где нужно) — снять.
            if current is not None and current != expected_logical:
                if _is_junction(current):
                    plan_lines.append(f"remove_junction: {current}")
                    action_kind = "recreate"
                elif current.exists():
                    console.print(
                        f"[red]current local_path '{current}' — реальная "
                        f"директория, не junction. Отказываюсь удалять.[/red]"
                    )
                    raise typer.Exit(code=1)
            # 2. На expected уже что-то есть?
            if _is_junction(expected_logical):
                plan_lines.append(f"remove_junction: {expected_logical}")
                action_kind = "recreate"
            elif expected_logical.exists():
                console.print(
                    f"[red]На expected_logical '{expected_logical}' лежит "
                    f"реальная директория. Не трогаю.[/red]"
                )
                raise typer.Exit(code=1)
            # 3. Создать новый junction.
            plan_lines.append(
                f"create_junction: {expected_logical} → {storage}"
            )
            if action_kind == "noop":
                action_kind = "create"

        if dry_run:
            console.print(f"[bold]Sync plan для '{view.slug}':[/bold]")
            for line in plan_lines:
                console.print(f"  • {line}")
            console.print(
                "\n[yellow]Dry-run. Реальные операции не выполнялись.[/yellow]"
            )
            return

        if action_kind == "noop":
            console.print(
                f"[green]✓ Project '{view.slug}' уже в синке: {expected_logical}[/green]"
            )
            return

        # ---- Apply ----
        # Snять старый junction.
        if current is not None and current != expected_logical and _is_junction(current):
            try:
                layout_mod.remove_junction(current)
            except (JunctionError, SafetyError) as exc:
                console.print(f"[red]Не удалось снять старый junction: {exc}[/red]")
                raise typer.Exit(code=1)
        # Snять junction на expected (если был «битый» — указывал не туда).
        if _is_junction(expected_logical):
            try:
                layout_mod.remove_junction(expected_logical)
            except (JunctionError, SafetyError) as exc:
                console.print(f"[red]Не удалось снять junction expected: {exc}[/red]")
                raise typer.Exit(code=1)
        # Создать новый.
        try:
            layout_mod._create_junction_safe(expected_logical, storage)
        except (JunctionError, SafetyError) as exc:
            console.print(f"[red]Не удалось создать junction: {exc}[/red]")
            raise typer.Exit(code=1)

        old_local = project.local_path
        new_local = str(expected_logical)
        project.local_path = new_local
        project.last_touched_at = msk_now()

        _log_action(
            session,
            action="project_layout_sync",
            entity_id=project.id,
            details={
                "kind": "sync",
                "old_local_path": old_local,
                "new_local_path": new_local,
                "storage": str(storage),
            },
        )
        session.commit()

    console.print(
        f"[green]✓ Project '{ref}' synced[/green]: junction в [bold]{expected_logical}[/bold] → {storage}"
    )


# --------------------------------------------------------------------------- #
# verify                                                                      #
# --------------------------------------------------------------------------- #


def _check_duplicate_junctions(view, *, root: Path, storage: Path) -> list[dict[str, Any]]:
    """Поискать «лишние» junction'ы в других группах, указывающие на наш storage.

    Возвращает список problem-dict'ов (если есть).
    """
    problems: list[dict[str, Any]] = []
    candidates: list[Path] = []
    for folder in GROUP_FOLDER_NAMES.values():
        candidates.append(root / folder / view.slug)
    archive_root = root / "_Archive"
    if archive_root.exists():
        try:
            for sub in archive_root.iterdir():
                if sub.is_dir():
                    candidates.append(sub / view.slug)
        except OSError:
            pass

    expected = get_logical_path(view, root=root)
    seen_paths: set[Path] = set()
    for cand in candidates:
        if cand in seen_paths:
            continue
        seen_paths.add(cand)
        if cand == expected:
            continue
        if not (cand.exists() or os.path.islink(str(cand))):
            continue
        if not _is_junction(cand):
            # реальная директория — отдельная история, репортим.
            problems.append({
                "name": "duplicate_real_dir",
                "ok": False,
                "issue": f"в {cand} лежит реальная директория, а не junction",
            })
            continue
        target = layout_mod.junction_target(cand)
        if target is not None and target.resolve() == storage.resolve():
            problems.append({
                "name": "duplicate_junction",
                "ok": False,
                "issue": (
                    f"лишний junction {cand} указывает в наш _storage. "
                    f"Снимите через sync или вручную."
                ),
            })
    return problems


def _verify_one(view, *, root: Path, quick: bool = False) -> dict[str, Any]:
    """Расширенная verify: layout.verify + duplicate-checks."""
    base = layout_mod.verify(view, root=root)
    checks = list(base.get("checks", []))
    if not quick:
        storage = get_storage_path(view.slug, root=root)
        if storage.exists():
            extra = _check_duplicate_junctions(view, root=root, storage=storage)
            checks.extend(extra)
    ok = all(c.get("ok", False) for c in checks)
    return {
        "ok": ok,
        "checks": checks,
        "logical": base.get("logical"),
        "storage": base.get("storage"),
    }


@layout_app.command("verify")
def verify_cmd(
    ref: Optional[str] = typer.Argument(
        None, help="slug | UUID; если не указан — проверить все проекты.",
    ),
    quick: bool = typer.Option(
        False, "--quick", help="Только основные проверки (быстрее).",
    ),
) -> None:
    """Проверить целостность layout.

    Без `<ref>` — все проекты в БД.
    Exit 0 если всё OK, exit 1 если есть issues.
    """
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    rows: list[dict[str, Any]] = []
    overall_ok = True

    with make_session(engine) as session:
        if ref is not None:
            project = _resolve_or_die(session, ref)
            views = [_project_view(session, project)]
        else:
            projects = session.execute(select(Project)).scalars().all()
            views = [_project_view(session, p) for p in projects]

        for view in views:
            try:
                result = _verify_one(view, root=root, quick=quick)
            except ValueError as exc:
                rows.append({
                    "slug": view.slug,
                    "ok": False,
                    "issues": [str(exc)],
                })
                overall_ok = False
                continue
            issues = [
                c.get("issue", c.get("name", "?"))
                for c in result.get("checks", [])
                if not c.get("ok", False)
            ]
            row_ok = result.get("ok", False)
            if not row_ok:
                overall_ok = False
            rows.append({
                "slug": view.slug,
                "ok": row_ok,
                "issues": issues,
                "logical": result.get("logical"),
                "storage": result.get("storage"),
            })

    if not rows:
        console.print("[yellow]В БД нет проектов для проверки.[/yellow]")
        return

    table = Table(title=f"Layout verify ({len(rows)})")
    table.add_column("slug", style="cyan", no_wrap=True)
    table.add_column("ok", justify="center")
    table.add_column("issues", style="dim")
    for row in rows:
        ok_mark = "[green]OK[/green]" if row["ok"] else "[red]FAIL[/red]"
        issues_text = "—"
        if row["issues"]:
            issues_text = "; ".join(row["issues"])
        table.add_row(row["slug"], ok_mark, issues_text)
    console.print(table)

    if not overall_ok:
        console.print(
            "\n[red]Найдены проблемы. Используйте `sync` или починку вручную.[/red]"
        )
        raise typer.Exit(code=1)
    console.print("\n[green]Всё в порядке.[/green]")


# --------------------------------------------------------------------------- #
# migrate-all                                                                 #
# --------------------------------------------------------------------------- #


@layout_app.command("migrate-all")
def migrate_all_cmd(
    type_filter: Optional[str] = typer.Option(
        None, "--type", help="Фильтр: project_type slug.",
    ),
    status_filter: Optional[str] = typer.Option(
        None, "--status", help="Фильтр: project_status slug.",
    ),
    tag_filters: Optional[list[str]] = typer.Option(
        None, "--tag",
        help="Фильтр по тегу (AND-семантика).",
    ),
    copy_first: bool = typer.Option(
        False, "--copy-first", help="Использовать copy+rmdir вместо robocopy /MOVE.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Только напечатать план.",
    ),
    confirm: bool = typer.Option(
        False, "--confirm",
        help="Подтвердить bulk-миграцию. Без флага → принудительный dry-run.",
    ),
) -> None:
    """Bulk-init: migrate всех подходящих проектов.

    Без `--confirm` всегда работает как dry-run (safety).
    """
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    effective_dry_run = dry_run or not confirm

    with make_session(engine) as session:
        stmt = select(Project)
        if type_filter:
            pt = session.execute(
                select(ProjectType).where(ProjectType.slug == type_filter)
            ).scalar_one_or_none()
            if pt is None:
                console.print(f"[red]Тип '{type_filter}' не найден.[/red]")
                raise typer.Exit(code=1)
            stmt = stmt.where(Project.type_id == pt.id)
        if status_filter:
            ps = session.execute(
                select(ProjectStatus).where(ProjectStatus.slug == status_filter)
            ).scalar_one_or_none()
            if ps is None:
                console.print(f"[red]Статус '{status_filter}' не найден.[/red]")
                raise typer.Exit(code=1)
            stmt = stmt.where(Project.status_id == ps.id)

        projects = list(session.execute(stmt).scalars().all())

        # Tag filter — отдельной функцией.
        if tag_filters:
            from atlas.pm.tags import resolve_tag_ref

            tag_slugs: list[str] = []
            for raw in tag_filters:
                try:
                    tag = resolve_tag_ref(session, raw)
                except (ValueError,):
                    console.print(f"[red]Tag '{raw}' invalid.[/red]")
                    raise typer.Exit(code=1)
                if tag is None:
                    console.print(f"[red]Tag '{raw}' не найден.[/red]")
                    raise typer.Exit(code=1)
                tag_slugs.append(tag.slug)
            matching = filter_projects_by_tags(session, tag_slugs, archived=True)
            allowed_ids = {p.id for p in matching}
            projects = [p for p in projects if p.id in allowed_ids]

        if not projects:
            console.print("[yellow]Подходящих проектов нет.[/yellow]")
            return

        summary = {"migrated": 0, "skipped": 0, "failed": 0, "planned": 0}
        rows: list[dict[str, Any]] = []

        # progressbar в typer = typer.progressbar (typer пробрасывает click).
        with typer.progressbar(projects, label="migrate-all") as bar:
            for project in bar:
                view = _project_view(session, project)
                row: dict[str, Any] = {
                    "slug": view.slug,
                    "type": view.type_slug,
                    "status": "?",
                    "note": "",
                }
                # Skip-conditions:
                if not view.local_path:
                    row["status"] = "skipped"
                    row["note"] = "no local_path"
                    summary["skipped"] += 1
                    rows.append(row)
                    continue

                local_path = Path(view.local_path)
                if _is_junction(local_path):
                    row["status"] = "skipped"
                    row["note"] = "уже junction (используй sync)"
                    summary["skipped"] += 1
                    rows.append(row)
                    continue

                storage = get_storage_path(view.slug, root=root)
                if storage.exists():
                    row["status"] = "skipped"
                    row["note"] = "_storage уже существует"
                    summary["skipped"] += 1
                    rows.append(row)
                    continue

                if not local_path.exists():
                    row["status"] = "failed"
                    row["note"] = f"local_path не существует: {local_path}"
                    summary["failed"] += 1
                    rows.append(row)
                    continue

                if effective_dry_run:
                    row["status"] = "planned"
                    row["note"] = f"будет mv → {storage}"
                    summary["planned"] += 1
                    rows.append(row)
                    continue

                # ---- Apply ----
                logical = get_logical_path(view, root=root)
                try:
                    layout_mod._perform_storage_move(
                        local_path, storage, copy_first=copy_first,
                    )
                except (RuntimeError, FileNotFoundError, OSError) as exc:
                    row["status"] = "failed"
                    row["note"] = f"move failed: {exc}"
                    summary["failed"] += 1
                    rows.append(row)
                    continue

                if logical.resolve() != storage.resolve():
                    if _is_junction(logical):
                        pass
                    elif logical.exists():
                        row["status"] = "failed"
                        row["note"] = (
                            f"в logical {logical} реальная директория"
                        )
                        summary["failed"] += 1
                        rows.append(row)
                        continue
                    else:
                        try:
                            layout_mod._create_junction_safe(logical, storage)
                        except (JunctionError, SafetyError) as exc:
                            row["status"] = "failed"
                            row["note"] = f"junction failed: {exc}"
                            summary["failed"] += 1
                            rows.append(row)
                            continue

                project.local_path = str(logical)
                project.last_touched_at = msk_now()
                _log_action(
                    session,
                    action="project_layout_init",
                    entity_id=project.id,
                    details={
                        "kind": "migrate-all",
                        "new_local_path": str(logical),
                        "storage": str(storage),
                        "copy_first": copy_first,
                    },
                )
                row["status"] = "migrated"
                row["note"] = f"→ {storage}"
                summary["migrated"] += 1
                rows.append(row)

        if not effective_dry_run:
            session.commit()

    table = Table(title=f"migrate-all ({len(rows)})")
    table.add_column("slug", style="cyan", no_wrap=True)
    table.add_column("type", style="magenta")
    table.add_column("result", style="bold")
    table.add_column("note", style="dim")
    for row in rows:
        result_color = {
            "migrated": "[green]migrated[/green]",
            "planned": "[yellow]planned[/yellow]",
            "skipped": "[dim]skipped[/dim]",
            "failed": "[red]failed[/red]",
        }.get(row["status"], row["status"])
        table.add_row(row["slug"], row["type"], result_color, row["note"])
    console.print(table)

    console.print(
        f"\nSummary:\n"
        f"  migrated: {summary['migrated']}\n"
        f"  planned:  {summary['planned']}\n"
        f"  skipped:  {summary['skipped']}\n"
        f"  failed:   {summary['failed']}"
    )

    if effective_dry_run:
        console.print(
            "\n[yellow]Dry-run (передайте `--confirm` для применения).[/yellow]"
        )


# --------------------------------------------------------------------------- #
# list-storage                                                                #
# --------------------------------------------------------------------------- #


def _dir_size_mb(path: Path) -> Optional[float]:
    """Best-effort: суммарный размер файлов в path в MB. None при ошибке."""
    if not path.exists():
        return None
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    continue
    except OSError:
        return None
    return total / (1024 * 1024)


@layout_app.command("list-storage")
def list_storage_cmd() -> None:
    """Overview всех `_storage/<slug>/` (с физикой и логикой)."""
    url = _db_url()
    engine = make_engine(url)
    root = get_projects_root()

    with make_session(engine) as session:
        projects = list(session.execute(select(Project)).scalars().all())
        rows: list[dict[str, Any]] = []
        for project in projects:
            pt = session.get(ProjectType, project.type_id)
            ps = session.get(ProjectStatus, project.status_id)
            view = _project_view(session, project)
            storage = get_storage_path(view.slug, root=root)
            try:
                logical = get_logical_path(view, root=root)
            except ValueError:
                logical = None
            size_mb = _dir_size_mb(storage)
            rows.append({
                "slug": view.slug,
                "physical": str(storage) if storage.exists() else "—",
                "size_mb": size_mb,
                "logical": str(logical) if logical else "—",
                "status": ps.slug if ps else "—",
                "type": pt.slug if pt else "—",
            })

    if not rows:
        console.print("[yellow]В БД нет проектов.[/yellow]")
        return

    table = Table(title=f"list-storage ({len(rows)})")
    table.add_column("slug", style="cyan", no_wrap=True)
    table.add_column("physical", style="dim")
    table.add_column("size MB", justify="right")
    table.add_column("logical", style="dim")
    table.add_column("status", style="green")
    table.add_column("type", style="magenta")
    for row in rows:
        size_str = f"{row['size_mb']:.1f}" if row["size_mb"] is not None else "—"
        table.add_row(
            row["slug"],
            row["physical"],
            size_str,
            row["logical"],
            row["status"],
            row["type"],
        )
    console.print(table)
