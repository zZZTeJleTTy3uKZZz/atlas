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
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import typer
from clikit import command, emit_data, emit_table, is_json
from rich.console import Console
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm._time import local_now
from atlas.pm.db import make_engine, make_session, resolve_db_url
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
    get_projects_root,
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
    return resolve_db_url()


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
        parent_id=project.parent_id,
    )


def _container_logical(session: Session, view, *, root: Path) -> Optional[Path]:
    """Логический путь контейнера для модуля (#126). None для standalone.

    resolver рекурсивно строит логический путь родителя (поддержка вложенных
    контейнеров: контейнер сам может быть модулем).
    """
    def _resolver(parent_id: str):
        return _view_by_id(session, parent_id)

    return layout_mod.resolve_container_logical(view, _resolver, root=root)


def _view_by_id(session: Session, project_id: str):
    """duck-typed view контейнера по id (для resolve_container_logical)."""
    proj = session.get(Project, project_id)
    if proj is None:
        return None
    return _project_view(session, proj)


def _logical_for(session: Session, view, *, root: Path) -> Path:
    """Module-aware логический путь проекта (#126).

    Для модуля (parent_id задан) — `<container_logical>/modules/<slug>`;
    для standalone/контейнера — прежний type-группа путь.
    """
    container_logical = _container_logical(session, view, root=root)
    return get_logical_path(view, root=root, container_logical=container_logical)


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
@command
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

        logical = _logical_for(session, view, root=root)

        # ---- DRY-RUN ----
        if dry_run:
            plan = plan_migrate_to_storage(view, root=root)

            def _render_plan(d: dict[str, Any]) -> None:
                console.print(f"[bold]Plan для '{d['slug']}':[/bold]")
                for step in d["plan"]:
                    action = step.get("action")
                    src = step.get("src") or "—"
                    dst = step.get("dst") or "—"
                    note = step.get("note") or ""
                    console.print(f"  • {action}: {src} → {dst}  [dim]{note}[/dim]")
                console.print(
                    "\n[yellow]Dry-run. Реальные операции не выполнялись.[/yellow]"
                )

            emit_data(
                {"slug": view.slug, "dry_run": True, "plan": plan},
                text_renderer=_render_plan,
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
        project.last_touched_at = local_now()

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

    def _render_init(d: dict[str, Any]) -> None:
        console.print(
            f"[green]✓ Project '{ref}' migrated to _storage/[/green]"
        )
        console.print(f"  Storage:  [bold]{d['storage']}[/bold]")
        if not d["no_junction"]:
            console.print(f"  Junction: [bold]{d['logical']}[/bold] → {d['storage']}")
        else:
            console.print("  [dim](--no-junction: junction не создан)[/dim]")

    emit_data(
        {
            "ok": True,
            "slug": view.slug,
            "storage": str(storage),
            "logical": str(logical),
            "no_junction": no_junction,
            "junction_created": junction_created,
            "files_count": files_count,
        },
        text_renderer=_render_init,
    )


# --------------------------------------------------------------------------- #
# sync                                                                        #
# --------------------------------------------------------------------------- #


@layout_app.command("sync")
@command
def sync_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Только показать что будет сделано.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help=(
            "(W45-32d) Если current local_path или expected_logical — реальная "
            "директория (не junction), переместить её в `_old_git_backups/` и "
            "создать junction поверх. БЕЗ --force такие случаи отвергаются."
        ),
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
        expected_logical = _logical_for(session, view, root=root)
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
            real_dirs_to_backup: list[Path] = []
            if current is not None and current != expected_logical:
                if _is_junction(current):
                    plan_lines.append(f"remove_junction: {current}")
                    action_kind = "recreate"
                elif current.exists():
                    if not force:
                        console.print(
                            f"[red]current local_path '{current}' — реальная "
                            f"директория, не junction. Отказываюсь удалять. "
                            f"Используй --force для переноса в _old_git_backups/.[/red]"
                        )
                        raise typer.Exit(code=1)
                    plan_lines.append(
                        f"backup real-dir: {current} → _old_git_backups/"
                    )
                    real_dirs_to_backup.append(current)
                    action_kind = "recreate"
            # 2. На expected уже что-то есть?
            if _is_junction(expected_logical):
                plan_lines.append(f"remove_junction: {expected_logical}")
                action_kind = "recreate"
            elif expected_logical.exists():
                if not force:
                    console.print(
                        f"[red]На expected_logical '{expected_logical}' лежит "
                        f"реальная директория. Не трогаю. "
                        f"Используй --force для переноса в _old_git_backups/.[/red]"
                    )
                    raise typer.Exit(code=1)
                plan_lines.append(
                    f"backup real-dir: {expected_logical} → _old_git_backups/"
                )
                real_dirs_to_backup.append(expected_logical)
                action_kind = "recreate"
            # 3. Создать новый junction.
            plan_lines.append(
                f"create_junction: {expected_logical} → {storage}"
            )
            if action_kind == "noop":
                action_kind = "create"

        if dry_run:
            def _render_plan(d: dict[str, Any]) -> None:
                console.print(f"[bold]Sync plan для '{d['slug']}':[/bold]")
                for line in d["plan"]:
                    console.print(f"  • {line}")
                console.print(
                    "\n[yellow]Dry-run. Реальные операции не выполнялись.[/yellow]"
                )

            emit_data(
                {
                    "slug": view.slug,
                    "dry_run": True,
                    "action": action_kind,
                    "plan": plan_lines,
                    "expected_logical": str(expected_logical),
                    "storage": str(storage),
                },
                text_renderer=_render_plan,
            )
            return

        if action_kind == "noop":
            emit_data(
                {
                    "ok": True,
                    "slug": view.slug,
                    "action": "noop",
                    "logical": str(expected_logical),
                    "storage": str(storage),
                },
                text_renderer=lambda d: console.print(
                    f"[green]✓ Project '{d['slug']}' уже в синке: "
                    f"{d['logical']}[/green]"
                ),
            )
            return

        # ---- Apply ----
        # W45-32d: backup реальных директорий в _old_git_backups/.
        if real_dirs_to_backup:
            from datetime import datetime as _dt

            backups_root = root / "_old_git_backups"
            backups_root.mkdir(parents=True, exist_ok=True)
            date_tag = _dt.now().strftime("%Y-%m-%d")
            for real_dir in real_dirs_to_backup:
                bk = backups_root / f"{real_dir.name}-real-{date_tag}"
                suffix = 1
                while bk.exists():
                    suffix += 1
                    bk = backups_root / f"{real_dir.name}-real-{date_tag}-{suffix}"
                try:
                    layout_mod._perform_storage_move(
                        real_dir, bk, copy_first=False
                    )
                except Exception as exc:
                    console.print(
                        f"[red]Не удалось перенести {real_dir} → {bk}: {exc}[/red]"
                    )
                    raise typer.Exit(code=1)
                if not is_json():
                    console.print(
                        f"  [yellow]backup → {bk}[/yellow]"
                    )
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
        project.last_touched_at = local_now()

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

    emit_data(
        {
            "ok": True,
            "slug": view.slug,
            "action": action_kind,
            "logical": str(expected_logical),
            "storage": str(storage),
        },
        text_renderer=lambda d: console.print(
            f"[green]✓ Project '{ref}' synced[/green]: junction в "
            f"[bold]{d['logical']}[/bold] → {d['storage']}"
        ),
    )


# --------------------------------------------------------------------------- #
# verify                                                                      #
# --------------------------------------------------------------------------- #


def _check_duplicate_junctions(
    view, *, root: Path, storage: Path, expected: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Поискать «лишние» junction'ы в других группах, указывающие на наш storage.

    ``expected`` — module-aware ожидаемый логический путь (если не передан,
    считаем по type-группе как fallback). Возвращает список problem-dict'ов.
    """
    problems: list[dict[str, Any]] = []
    # #15: кандидаты включают и <container>/modules/<slug> — орфанные
    # module-junction'ы тоже репортятся как дубли.
    candidates = layout_mod.stale_junction_candidates(view.slug, root=root)

    if expected is None:
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


def _verify_one(
    view, *, root: Path, quick: bool = False,
    container_logical: Optional[Path] = None,
) -> dict[str, Any]:
    """Расширенная verify: layout.verify + duplicate-checks (module-aware #126)."""
    base = layout_mod.verify(view, root=root, container_logical=container_logical)
    checks = list(base.get("checks", []))
    if not quick:
        storage = get_storage_path(view.slug, root=root)
        if storage.exists():
            expected = get_logical_path(
                view, root=root, container_logical=container_logical
            )
            extra = _check_duplicate_junctions(
                view, root=root, storage=storage, expected=expected,
            )
            checks.extend(extra)
    ok = all(c.get("ok", False) for c in checks)
    return {
        "ok": ok,
        "checks": checks,
        "logical": base.get("logical"),
        "storage": base.get("storage"),
    }


@layout_app.command("verify")
@command
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
                container_logical = _container_logical(session, view, root=root)
                result = _verify_one(
                    view, root=root, quick=quick,
                    container_logical=container_logical,
                )
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

    emit_table(
        rows,
        title=f"Layout verify ({len(rows)})",
        columns=[
            {"key": "slug", "header": "slug", "style": "cyan", "no_wrap": True},
            {
                "key": "ok",
                "header": "ok",
                "justify": "center",
                "format": lambda v: "[green]OK[/green]" if v else "[red]FAIL[/red]",
            },
            {
                "key": "issues",
                "header": "issues",
                "style": "dim",
                "format": lambda v: "; ".join(v) if v else "—",
            },
        ],
        empty_message="[yellow]В БД нет проектов для проверки.[/yellow]",
    )
    if not rows:
        return

    if not overall_ok:
        if not is_json():
            console.print(
                "\n[red]Найдены проблемы. Используйте `sync` или починку вручную.[/red]"
            )
        raise typer.Exit(code=1)
    if not is_json():
        console.print("\n[green]Всё в порядке.[/green]")


# --------------------------------------------------------------------------- #
# migrate-all                                                                 #
# --------------------------------------------------------------------------- #


@layout_app.command("migrate-all")
@command
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
    exclude: Optional[list[str]] = typer.Option(
        None, "--exclude",
        help=(
            "Slug'и проектов, которые НЕ мигрировать (W45-32f). Можно несколько. "
            "По умолчанию `atlas` всегда исключён (W45-32h: self-migration safeguard)."
        ),
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
    allow_self: bool = typer.Option(
        False, "--allow-self",
        help=(
            "(W45-32h) Разрешить миграцию самого atlas — по умолчанию запрещена, "
            "т.к. .venv/Scripts/atlas.exe залочен и migration ломает state."
        ),
    ),
) -> None:
    """Bulk-init: migrate всех подходящих проектов.

    Без `--confirm` всегда работает как dry-run (safety).
    Atlas-проект сам себя по умолчанию НЕ мигрирует (W45-32h).
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

        # W45-32f: --exclude фильтр + W45-32h: self-migration safeguard.
        excluded_set: set[str] = set(exclude or [])
        if not allow_self:
            excluded_set.add("atlas")
        if excluded_set:
            projects = [p for p in projects if p.slug not in excluded_set]

        if not projects:
            note = ""
            if excluded_set:
                note = f" (исключены: {', '.join(sorted(excluded_set))})"
            emit_table(
                [],
                empty_message=f"[yellow]Подходящих проектов нет.{note}[/yellow]",
            )
            return

        summary = {"migrated": 0, "skipped": 0, "failed": 0, "planned": 0}
        rows: list[dict[str, Any]] = []
        if excluded_set and not is_json():
            console.print(
                f"[dim]Excluded {len(excluded_set)}: "
                f"{', '.join(sorted(excluded_set))}[/dim]"
            )

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
                logical = _logical_for(session, view, root=root)
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
                project.last_touched_at = local_now()
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

    def _result_color(status: str) -> str:
        return {
            "migrated": "[green]migrated[/green]",
            "planned": "[yellow]planned[/yellow]",
            "skipped": "[dim]skipped[/dim]",
            "failed": "[red]failed[/red]",
        }.get(status, status)

    emit_table(
        rows,
        title=f"migrate-all ({len(rows)})",
        columns=[
            {"key": "slug", "header": "slug", "style": "cyan", "no_wrap": True},
            {"key": "type", "header": "type", "style": "magenta"},
            {
                "key": "status",
                "header": "result",
                "style": "bold",
                "format": _result_color,
            },
            {"key": "note", "header": "note", "style": "dim"},
        ],
    )

    def _render_summary(d: dict[str, Any]) -> None:
        console.print(
            f"\nSummary:\n"
            f"  migrated: {d['migrated']}\n"
            f"  planned:  {d['planned']}\n"
            f"  skipped:  {d['skipped']}\n"
            f"  failed:   {d['failed']}"
        )
        if d["dry_run"]:
            console.print(
                "\n[yellow]Dry-run (передайте `--confirm` для применения).[/yellow]"
            )

    emit_data(
        {
            "migrated": summary["migrated"],
            "planned": summary["planned"],
            "skipped": summary["skipped"],
            "failed": summary["failed"],
            "dry_run": effective_dry_run,
        },
        text_renderer=_render_summary,
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
@command
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
                logical = _logical_for(session, view, root=root)
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

    emit_table(
        rows,
        title=f"list-storage ({len(rows)})",
        columns=[
            {"key": "slug", "header": "slug", "style": "cyan", "no_wrap": True},
            {"key": "physical", "header": "physical", "style": "dim"},
            {
                "key": "size_mb",
                "header": "size MB",
                "justify": "right",
                "format": lambda v: f"{v:.1f}" if v is not None else "—",
            },
            {"key": "logical", "header": "logical", "style": "dim"},
            {"key": "status", "header": "status", "style": "green"},
            {"key": "type", "header": "type", "style": "magenta"},
        ],
        empty_message="[yellow]В БД нет проектов.[/yellow]",
    )
