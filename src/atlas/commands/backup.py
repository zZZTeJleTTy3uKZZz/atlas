"""CLI-команды ``atlas backup ...`` — атомарный бэкап git-репозиториев портфеля.

Команды:
- ``run``                — запустить backup для проектов из БД (с фильтрами).
- ``status``             — показать историю backup'ов из action_log.
- ``schedule install``   — зарегистрировать Windows Scheduled Task (register_task.ps1).
- ``schedule uninstall`` — снять Scheduled Task atlas-daily-backup.
- ``schedule list``      — показать состояние зарегистрированной задачи.

Логика самого backup'а вынесена в ``atlas.backup`` (функция
``backup_repo``) — отдельный модуль без typer-зависимостей, удобно
unit-тестировать.

Ограничения:
- ``install`` / ``uninstall`` / ``list-tasks`` используют PowerShell —
  рассчитаны на Windows. На других OS они отдадут exit_code != 0.
- Все subprocess вызовы мокаются в тестах. Реальных push'ов / Scheduled
  Task операций не происходит.
"""
from __future__ import annotations

from atlas.appconfig import default_actor

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import typer
from clikit import command, emit_data, emit_table, is_json
from rich.console import Console
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.backup import backup_repo
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import (
    ActionLog,
    Participant,
    Project,
    ProjectStatus,
    ProjectTag,
    ProjectType,
    Tag,
)
from atlas.slugs import AmbiguousRefError, resolve_project_ref

backup_app = typer.Typer(
    no_args_is_help=True,
    help="Backup management: snapshot всех git-репо портфеля → branch 'backup'.",
)
console = Console()

DEFAULT_ACTOR_SLUG = default_actor()
TASK_NAME = "atlas-daily-backup"
DEFAULT_TIME = "03:00"

# Планировщик авто-бэкапа (Windows Scheduled Task) — суб-группа `atlas backup schedule`
# (симметрия с `atlas sync daemon`). run/status остаются прямыми глаголами backup.
schedule_app = typer.Typer(
    no_args_is_help=True,
    help="Планировщик авто-бэкапа: install | uninstall | list (Windows Scheduled Task).",
)
backup_app.add_typer(schedule_app, name="schedule")


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


def _log_backup(
    session: Session,
    *,
    project_id: str,
    project_slug: str,
    payload: dict[str, Any],
) -> None:
    """Append-only запись в action_log (action='backup')."""
    details = {"project_slug": project_slug, **payload}
    entry = ActionLog(
        actor_id=_actor_id(session),
        entity_type="project",
        entity_id=project_id,
        action="backup",
        details_json=json.dumps(details, ensure_ascii=False, default=str),
    )
    session.add(entry)


def _find_register_task_script() -> Path:
    """Найти scripts/backup/register_task.ps1 относительно проекта."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "scripts" / "backup" / "register_task.ps1"
        if candidate.exists():
            return candidate
    raise RuntimeError(
        "Не найден scripts/backup/register_task.ps1 — проверьте установку atlas."
    )


# --------------------------------------------------------------------------- #
# Project filter helper                                                       #
# --------------------------------------------------------------------------- #


def _select_projects(
    session: Session,
    *,
    type_slug: Optional[str],
    status_slug: Optional[str],
    tag_slugs: Optional[list[str]],
    ref: Optional[str],
    include_archived: bool = False,
) -> list[Project]:
    """Получить выборку проектов с применёнными фильтрами."""
    if ref is not None:
        try:
            project = resolve_project_ref(session, ref)
        except AmbiguousRefError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        if project is None:
            console.print(f"[red]Project '{ref}' не найден.[/red]")
            raise typer.Exit(code=1)
        if not include_archived and project.archived_at is not None:
            return []
        return [project]

    stmt = select(Project)
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))
    if type_slug is not None:
        pt = session.execute(
            select(ProjectType).where(ProjectType.slug == type_slug)
        ).scalar_one_or_none()
        if pt is None:
            console.print(f"[red]Тип '{type_slug}' не найден.[/red]")
            raise typer.Exit(code=1)
        stmt = stmt.where(Project.type_id == pt.id)
    if status_slug is not None:
        ps = session.execute(
            select(ProjectStatus).where(ProjectStatus.slug == status_slug)
        ).scalar_one_or_none()
        if ps is None:
            console.print(f"[red]Статус '{status_slug}' не найден.[/red]")
            raise typer.Exit(code=1)
        stmt = stmt.where(Project.status_id == ps.id)
    if tag_slugs:
        # AND-семантика: проект должен иметь все указанные теги.
        tag_objs = session.execute(
            select(Tag).where(Tag.slug.in_(tag_slugs))
        ).scalars().all()
        if len(tag_objs) != len(tag_slugs):
            found = {t.slug for t in tag_objs}
            missing = [s for s in tag_slugs if s not in found]
            console.print(f"[red]Теги не найдены: {missing}[/red]")
            raise typer.Exit(code=1)
        for t in tag_objs:
            stmt = stmt.where(
                Project.id.in_(
                    select(ProjectTag.project_id).where(ProjectTag.tag_id == t.id)
                )
            )

    return list(session.execute(stmt).scalars().all())


# --------------------------------------------------------------------------- #
# atlas backup run                                                            #
# --------------------------------------------------------------------------- #


@backup_app.command("run")
@command
def run_cmd(
    type_slug: Optional[str] = typer.Option(None, "--type", help="Фильтр: тип проекта"),
    status_slug: Optional[str] = typer.Option(None, "--status", help="Фильтр: статус"),
    tags: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Фильтр: тег (slug). Можно несколько (AND)."
    ),
    ref: Optional[str] = typer.Option(None, "--ref", help="Один проект (slug или UUID)."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Только показать выборку, не запускать backup."
    ),
) -> None:
    """Запустить backup для проектов из БД atlas.

    По умолчанию идёт по всем активным (non-archived) проектам с непустым
    ``local_path``. Проекты без ``git_repo_url`` пропускаются с warning.
    Все действия логируются в ``action_log`` (entity_type='project',
    action='backup').
    """
    url = _db_url()
    engine = make_engine(url)

    summary_rows: list[dict[str, Any]] = []

    with make_session(engine) as session:
        projects = _select_projects(
            session,
            type_slug=type_slug,
            status_slug=status_slug,
            tag_slugs=tags,
            ref=ref,
            include_archived=False,
        )

        if not projects:
            emit_table(
                [],
                columns=[("slug", "slug"), ("status", "status"), ("message", "message")],
                title="Backup summary (0)",
                empty_message="[yellow]Подходящих проектов не найдено.[/yellow]",
            )
            return

        for project in projects:
            if not project.local_path:
                summary_rows.append({
                    "slug": project.slug,
                    "status": "skipped",
                    "message": "no local_path",
                })
                continue
            # [5] Источник правды — git_remote_url (его пишут git init/link/move/
            # sync-from-remote); legacy git_repo_url оставлен как fallback. Раньше
            # гейт смотрел ТОЛЬКО на legacy-поле, которое link/move/sync не заполняли,
            # и привязанные через `project git link` проекты молча НЕ бэкапились.
            if not (project.git_remote_url or project.git_repo_url):
                if not is_json():
                    console.print(
                        f"[yellow]⚠ {project.slug}: нет git remote url — пропуск.[/yellow]"
                    )
                summary_rows.append({
                    "slug": project.slug,
                    "status": "skipped",
                    "message": "no git remote url",
                })
                continue

            if dry_run:
                summary_rows.append({
                    "slug": project.slug,
                    "status": "dry-run",
                    "message": str(project.local_path),
                })
                continue

            if not is_json():
                console.print(f"[cyan]→ {project.slug}[/cyan]  ({project.local_path})")
            try:
                result = backup_repo(Path(project.local_path))
            except Exception as exc:  # pragma: no cover — defensive
                result = {"status": "failed", "error": str(exc)}

            row = {"slug": project.slug, "status": result.get("status", "unknown")}
            if result.get("status") == "pushed":
                sha = str(result.get("commit_sha", ""))
                row["message"] = sha[:12]
            elif result.get("status") == "skipped":
                row["message"] = str(result.get("reason", ""))
            else:
                row["message"] = str(result.get("error", ""))[:120]

            summary_rows.append(row)
            _log_backup(
                session,
                project_id=project.id,
                project_slug=project.slug,
                payload=result,
            )

        if not dry_run:
            session.commit()

    # Render summary (json: сырые dict'ы; text: rich-таблица с раскраской).
    _status_styles = {
        "pushed": "[green]", "skipped": "[yellow]",
        "failed": "[red]", "dry-run": "[magenta]",
    }

    def _fmt_status(status: str) -> str:
        style = _status_styles.get(status, "")
        return f"{style}{status}[/]" if style else status

    emit_table(
        summary_rows,
        columns=[
            {"key": "slug", "header": "slug", "style": "cyan", "no_wrap": True},
            {"key": "status", "header": "status", "format": _fmt_status},
            {"key": "message", "header": "message"},
        ],
        title=f"Backup summary ({len(summary_rows)})",
    )


# --------------------------------------------------------------------------- #
# atlas backup status                                                         #
# --------------------------------------------------------------------------- #


@backup_app.command("status")
@command
def status_cmd(
    days: int = typer.Option(7, "--days", help="За сколько дней показывать (default 7)."),
    ref: Optional[str] = typer.Option(None, "--ref", help="Один проект."),
) -> None:
    """Показать историю backup'ов из action_log."""
    url = _db_url()
    engine = make_engine(url)

    threshold = datetime.now() - timedelta(days=days)

    with make_session(engine) as session:
        stmt = (
            select(ActionLog)
            .where(ActionLog.action == "backup")
            .where(ActionLog.timestamp >= threshold)
            .order_by(ActionLog.timestamp.desc())
        )

        if ref is not None:
            try:
                project = resolve_project_ref(session, ref)
            except AmbiguousRefError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            if project is None:
                console.print(f"[red]Project '{ref}' не найден.[/red]")
                raise typer.Exit(code=1)
            stmt = stmt.where(ActionLog.entity_id == project.id)

        rows = session.execute(stmt).scalars().all()

        data: list[dict[str, Any]] = []
        for r in rows:
            ts = r.timestamp.strftime("%Y-%m-%d %H:%M") if r.timestamp else "—"
            try:
                details = json.loads(r.details_json) if r.details_json else {}
            except json.JSONDecodeError:
                details = {}
            status = str(details.get("status", "—"))
            slug = str(details.get("project_slug") or (r.entity_id or "")[:8])

            commit = details.get("commit_sha") or details.get("reason") or ""
            if (
                isinstance(commit, str)
                and len(commit) > 12
                and "no_changes" not in commit
            ):
                commit = commit[:12]
            error = str(details.get("error", ""))[:80]

            data.append({
                "timestamp": ts,
                "project": slug,
                "status": status,
                "commit_or_reason": str(commit),
                "error": error,
            })

    _status_styles = {
        "pushed": "[green]", "skipped": "[yellow]", "failed": "[red]",
    }

    def _fmt_status(status: str) -> str:
        style = _status_styles.get(status, "")
        return f"{style}{status}[/]" if style else status

    emit_table(
        data,
        columns=[
            {"key": "timestamp", "header": "Timestamp", "style": "cyan", "no_wrap": True},
            {"key": "project", "header": "Project", "style": "magenta"},
            {"key": "status", "header": "Status", "format": _fmt_status},
            {"key": "commit_or_reason", "header": "Commit / Reason"},
            {"key": "error", "header": "Error"},
        ],
        title=f"Backup history (last {days}d, {len(data)} entries)",
        empty_message="[yellow]Записей backup нет.[/yellow]",
    )


# --------------------------------------------------------------------------- #
# atlas backup schedule install                                               #
# --------------------------------------------------------------------------- #


@schedule_app.command("install")
@command
def install_cmd(
    time: str = typer.Option(
        DEFAULT_TIME, "--time", help="HH:MM — время ежедневного запуска."
    ),
) -> None:
    """Зарегистрировать Windows Scheduled Task ``atlas-daily-backup``."""
    try:
        script = _find_register_task_script()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(script),
        "-Time", time,
    ]
    if not is_json():
        console.print(
            f"[cyan]Регистрирую Scheduled Task '{TASK_NAME}' на {time}...[/cyan]"
        )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        if proc.stdout and not is_json():
            console.print(proc.stdout)
        if proc.stderr:
            console.print(f"[red]{proc.stderr}[/red]")
        console.print("[red]Установка не удалась.[/red]")
        raise typer.Exit(code=proc.returncode or 1)

    def _render(d: dict[str, Any]) -> None:
        if d["stdout"]:
            console.print(d["stdout"])
        console.print(f"[green]✓ Task '{d['task']}' установлен на {d['time']}.[/green]")
        console.print("Команды управления:")
        console.print(
            f"  Запустить сейчас:  Start-ScheduledTask -TaskName '{d['task']}'"
        )
        console.print("  Состояние:         atlas backup schedule list")
        console.print("  Удалить:           atlas backup schedule uninstall")

    emit_data(
        {
            "task": TASK_NAME,
            "time": time,
            "installed": True,
            "stdout": proc.stdout or "",
        },
        text_renderer=_render,
    )


# --------------------------------------------------------------------------- #
# atlas backup schedule uninstall                                             #
# --------------------------------------------------------------------------- #


@schedule_app.command("uninstall")
@command
def uninstall_cmd() -> None:
    """Снять Scheduled Task ``atlas-daily-backup``."""
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-Command",
        f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        if proc.stdout and not is_json():
            console.print(proc.stdout)
        if proc.stderr:
            console.print(f"[red]{proc.stderr}[/red]")
        console.print("[red]Не удалось удалить Scheduled Task.[/red]")
        raise typer.Exit(code=proc.returncode or 1)

    def _render(d: dict[str, Any]) -> None:
        if d["stdout"]:
            console.print(d["stdout"])
        console.print(f"[green]✓ Task '{d['task']}' удалён.[/green]")

    emit_data(
        {"task": TASK_NAME, "uninstalled": True, "stdout": proc.stdout or ""},
        text_renderer=_render,
    )


# --------------------------------------------------------------------------- #
# atlas backup schedule list                                                  #
# --------------------------------------------------------------------------- #


@schedule_app.command("list")
@command
def list_tasks_cmd() -> None:
    """Показать состояние зарегистрированной Scheduled Task."""
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-Command",
        f"Get-ScheduledTaskInfo -TaskName '{TASK_NAME}' | "
        f"Select-Object TaskName,LastRunTime,LastTaskResult,NextRunTime | Format-List",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        if proc.stderr:
            console.print(f"[yellow]{proc.stderr.strip()}[/yellow]")
        console.print(
            f"[yellow]Task '{TASK_NAME}' не найден или не доступен. "
            f"Установите через `atlas backup schedule install`.[/yellow]"
        )
        raise typer.Exit(code=proc.returncode or 1)

    emit_data(
        {"task": TASK_NAME, "info": proc.stdout or ""},
        text_renderer=lambda d: console.print(d["info"]) if d["info"] else None,
    )
