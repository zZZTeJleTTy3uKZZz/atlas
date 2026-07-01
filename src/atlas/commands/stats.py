"""CLI `atlas stats ...` + `atlas dashboard` — аналитика портфеля (read-only).

На clikit (--json по умолчанию). Считает всё по существующим моделям и git
через pure-logic ``atlas.stats`` (без нового стора).

`atlas stats` — мультирежимная команда (callback, invoke_without_command):
  - без флагов            → overview: counts всего + по типу/контрагенту/статусу;
  - ``--period <spec>``   → активность в окне (+ фильтры --type/--counterparty/--tag);
  - ``--provenance``      → топ источников/приёмников + доля реализованных;
  - ``--project <ref>``   → git-статистика проекта (commits/last commit/последний
                            пуш/каденс).

`atlas dashboard` — объединённый обзор: counts + activity + provenance (+ git,
если задан --project). В json — единый объект; в text — читаемый блок.
"""
from __future__ import annotations

from typing import Any, Optional

import typer
from clikit import CliError, command, emit_data
from rich.console import Console

from atlas import stats as _stats
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.slugs import AmbiguousRefError, resolve_project_ref

stats_app = typer.Typer(
    no_args_is_help=False,
    invoke_without_command=True,
    help="Аналитика портфеля: overview / --period / --provenance / --project (git).",
)
console = Console()


def _db_url() -> str:
    return resolve_db_url()


# --------------------------------------------------------------------------- #
# Render helpers (text-режим)                                                  #
# --------------------------------------------------------------------------- #


def _render_breakdown(title: str, rows: list[dict[str, Any]]) -> None:
    """Печать одной разбивки (key/count) как мини-таблицы в text-режиме."""
    console.print(f"[bold]{title}[/bold]")
    if not rows:
        console.print("  [dim]—[/dim]")
        return
    for r in rows:
        console.print(f"  {r['key']:<24} {r['count']}")


def _render_counts(d: dict[str, Any]) -> None:
    console.print(
        f"[bold cyan]Проектов всего:[/bold cyan] {d['total']} "
        f"[dim](архивных: {d['archived']})[/dim]"
    )
    _render_breakdown("По типу:", d["by_type"])
    _render_breakdown("По статусу:", d["by_status"])
    _render_breakdown("По владельцу:", d["by_owner"])
    _render_breakdown("По заказчику:", d["by_customer"])


def _render_activity(d: dict[str, Any]) -> None:
    console.print(
        f"[bold]Окно:[/bold] {d['start']} → {d['end']}"
    )
    console.print(f"  Активных проектов: {d['projects_active']}")
    console.print(f"  Задач создано:     {d['tasks_created']}")
    console.print(f"  Задач завершено:   {d['tasks_completed']}")
    console.print(f"  Эпиков создано:    {d['epics_created']}")
    if d["projects"]:
        console.print("  [dim]Проекты:[/dim]")
        for p in d["projects"]:
            console.print(f"    {p['slug']:<24} {p['last_touched_at'] or '—'}")


def _render_provenance(d: dict[str, Any]) -> None:
    share_pct = round(d["realized_share"] * 100, 1)
    console.print(
        f"[bold]Инжектировано задач:[/bold] {d['total_injected']} · "
        f"реализовано: {d['realized']} ({share_pct}%)"
    )
    console.print("[bold]Топ источников:[/bold]")
    for r in d["top_sources"] or []:
        console.print(f"  {r['slug']:<24} {r['count']}")
    if not d["top_sources"]:
        console.print("  [dim]—[/dim]")
    console.print("[bold]Топ приёмников:[/bold]")
    for r in d["top_sinks"] or []:
        console.print(f"  {r['slug']:<24} {r['count']}")
    if not d["top_sinks"]:
        console.print("  [dim]—[/dim]")


def _render_git(d: dict[str, Any]) -> None:
    if not d.get("is_git"):
        console.print(
            f"[yellow]'{d.get('path')}' — не git-репозиторий "
            "(или нет local_path).[/yellow]"
        )
        return
    console.print(f"[bold]Git:[/bold] {d['path']}")
    console.print(f"  Коммитов:        {d['commits']}")
    console.print(f"  Первый коммит:   {d['first_commit_at'] or '—'}")
    console.print(f"  Последний комм.: {d['last_commit_at'] or '—'}")
    console.print(f"  Последний пуш:   {d.get('last_pushed_at') or '—'}")
    console.print(f"  Период (дней):   {d['span_days'] if d['span_days'] is not None else '—'}")
    cadence = d["cadence_days"]
    console.print(
        f"  Каденс:          {f'раз в {cadence} дн.' if cadence is not None else '—'}"
    )


# --------------------------------------------------------------------------- #
# Resolve helpers                                                              #
# --------------------------------------------------------------------------- #


def _resolve_project_or_die(session, ref: str):
    try:
        project = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        raise CliError("ambiguous_ref", str(exc))
    if project is None:
        raise CliError("not_found", f"Project '{ref}' не найден.")
    return project


# --------------------------------------------------------------------------- #
# stats (callback — мультирежим)                                              #
# --------------------------------------------------------------------------- #


@stats_app.callback(invoke_without_command=True)
@command
def stats_cmd(
    ctx: typer.Context,
    period: Optional[str] = typer.Option(
        None, "--period",
        help="Окно активности: 7d | 30d | month | year | YYYY-MM-DD..YYYY-MM-DD.",
    ),
    provenance: bool = typer.Option(
        False, "--provenance",
        help="Provenance-аналитика: источники/приёмники инжектированных задач.",
    ),
    project: Optional[str] = typer.Option(
        None, "--project",
        help="Git-статистика проекта по local_path (slug | UUID).",
    ),
    type_slug: Optional[str] = typer.Option(
        None, "--type", help="Фильтр по типу проекта (для --period).",
    ),
    counterparty: Optional[str] = typer.Option(
        None, "--counterparty",
        help="Фильтр по контрагенту-владельцу (slug, для --period).",
    ),
    tag: Optional[str] = typer.Option(
        None, "--tag", help="Фильтр по тегу проекта (slug, для --period).",
    ),
) -> None:
    """Аналитика портфеля. Без флагов — overview-счётчики."""
    # Если вызвана подкоманда — не выполняем overview-логику.
    if ctx.invoked_subcommand is not None:
        return

    engine = make_engine(_db_url())

    # --- режим git per-project (#131) -------------------------------------
    if project is not None:
        with make_session(engine) as session:
            proj = _resolve_project_or_die(session, project)
            local_path = proj.local_path
            slug = proj.slug
            pushed = proj.git_last_pushed_at.isoformat() if proj.git_last_pushed_at else None
        data = _stats.git_stats(local_path, last_pushed_at=pushed) or {
            "is_git": False, "path": None, "commits": 0, "last_pushed_at": pushed,
        }
        data = {"project": slug, **data}
        emit_data(data, text_renderer=_render_git)
        return

    # --- режим provenance (#130) ------------------------------------------
    if provenance:
        with make_session(engine) as session:
            data = _stats.provenance_stats(session)
        emit_data(data, text_renderer=_render_provenance)
        return

    # --- режим period / активность (#129) ---------------------------------
    if period is not None:
        try:
            start, end = _stats.parse_period(period)
        except ValueError as exc:
            raise CliError("invalid_period", str(exc))
        with make_session(engine) as session:
            data = _stats.activity_window(
                session, start=start, end=end,
                type_slug=type_slug, owner_slug=counterparty,
                tag_slug=tag,
            )
        emit_data(data, text_renderer=_render_activity)
        return

    # --- режим overview по умолчанию (#128) -------------------------------
    with make_session(engine) as session:
        data = _stats.project_counts(session)
    emit_data(data, text_renderer=_render_counts)


# `atlas dashboard` (операционный board-обзор) переехал в commands/dashboard.py.
# Аналитические срезы (counts/period/provenance/git) — здесь, в `atlas stats`.
