"""CLI `atlas dashboard` — операционный board-обзор портфеля.

Человеку (по умолчанию) — красивый Rich-вывод: KPI-шапка, распределение задач по
статусам/приоритетам (горизонтальные бары), что в работе (in-flight + держатель),
что требует внимания (blocked / overdue / протухшие lease), разбивка по проектам,
недавняя активность. Агенту — ``--json`` (плоский dict, агент строит сам).

NB: дашборд — человеко-ориентированная команда, поэтому ПО УМОЛЧАНИЮ рендерит
text (а не общий clikit json-дефолт). Машинный вывод — явным ``--json``.
Аналитика (provenance/period/git) живёт в ``atlas stats``.
"""
from __future__ import annotations

import json as _json
import os
from typing import Any, Optional

import typer
from clikit import CliError, command
from rich.box import ROUNDED
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from atlas.dashboard import PRIORITY_ORDER, STATUS_ORDER, build_dashboard
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.slugs import AmbiguousRefError

console = Console()

#: Цвет статуса (синий=план, cyan=в работе, yellow=ревью, red=блок, green=done).
_STATUS_COLOR = {
    "todo": "blue",
    "in_progress": "cyan",
    "review": "yellow",
    "blocked": "red",
    "done": "green",
    "cancelled": "grey42",
}
_PRIORITY_COLOR = {"P0": "bold red", "P1": "orange1", "P2": "blue", "P3": "grey62"}


def _hbar(count: int, peak: int, width: int = 22, color: str = "cyan") -> Text:
    """Горизонтальный бар: заполнено ▇ цветом, остаток ░ тускло."""
    filled = 0 if peak <= 0 else round(count / peak * width)
    filled = min(width, max(0 if count == 0 else 1, filled))
    bar = Text()
    bar.append("▇" * filled, style=color)
    bar.append("░" * (width - filled), style="grey30")
    return bar


def _kpi_header(d: dict[str, Any]) -> Panel:
    t = d["tasks"]
    pr = d["projects"]
    le = d["leases"]
    line = Text()
    line.append("Проектов ", style="grey70")
    line.append(str(pr["total"]), style="bold white")
    line.append(f"  (активных {pr['active']})", style="grey50")
    line.append("   │   ", style="grey30")
    line.append("Открытых задач ", style="grey70")
    line.append(str(t["open"]), style="bold white")
    line.append("   │   ", style="grey30")
    line.append("В работе ", style="grey70")
    line.append(str(len(t["in_progress"])), style="bold cyan")
    line.append("   │   ", style="grey30")
    line.append("Заблок. ", style="grey70")
    line.append(str(len(t["blocked"])), style="bold red" if t["blocked"] else "grey50")
    line.append("   │   ", style="grey30")
    line.append("Просроч. ", style="grey70")
    line.append(str(len(t["overdue"])), style="bold red" if t["overdue"] else "grey50")
    line.append("   │   ", style="grey30")
    line.append("Активных эпиков ", style="grey70")
    line.append(str(d["epics"]["active"]), style="bold white")
    if le["stale"]:
        line.append("   │   ", style="grey30")
        line.append("⏰ протухших lease ", style="grey70")
        line.append(str(le["stale"]), style="bold yellow")
    scope = d["scope"]
    title = "Atlas — портфель" if scope == "portfolio" else f"Atlas — {scope}"
    return Panel(line, title=f"[bold magenta]{title}[/bold magenta]",
                 box=ROUNDED, border_style="magenta", padding=(0, 1))


def _distribution(d: dict[str, Any]) -> Panel:
    by_status = d["tasks"]["by_status"]
    by_prio = d["tasks"]["by_priority"]
    peak = max([1, *by_status.values()])
    grid = Table.grid(padding=(0, 1))
    grid.add_column(justify="right", style="grey70", min_width=12)
    grid.add_column()
    grid.add_column(justify="right", style="bold")
    grid.add_row("[bold]Задачи по статусам[/bold]", "", "")
    for st in STATUS_ORDER:
        cnt = by_status.get(st, 0)
        if st in ("done", "cancelled") and cnt == 0:
            continue
        grid.add_row(st, _hbar(cnt, peak, color=_STATUS_COLOR.get(st, "white")), str(cnt))
    # приоритеты открытых — компактной строкой чипов
    chips = Text("Приоритеты (откр.):  ", style="grey70")
    for p in PRIORITY_ORDER:
        chips.append(f" {p} ", style=f"reverse {_PRIORITY_COLOR.get(p, 'white')}")
        chips.append(f" {by_prio.get(p, 0)}   ", style="white")
    return Panel(Group(grid, Text(), chips), box=ROUNDED, border_style="grey37",
                 title="[grey70]Распределение[/grey70]", padding=(0, 1))


def _task_table(title: str, rows: list[dict], *, show_holder: bool = False,
                show_due: bool = False, empty: str = "—") -> Table:
    tbl = Table(title=f"[bold]{title}[/bold]", box=ROUNDED, border_style="grey37",
                title_justify="left", expand=True, padding=(0, 1))
    tbl.add_column("Задача", style="cyan", no_wrap=True, max_width=16)
    tbl.add_column("P", justify="center", max_width=3)
    tbl.add_column("Заголовок", style="white", ratio=1)
    tbl.add_column("Проект", style="grey62", no_wrap=True, max_width=18)
    if show_holder:
        tbl.add_column("Кто", style="green", no_wrap=True, max_width=14)
    if show_due:
        tbl.add_column("Дедлайн", style="red", no_wrap=True, max_width=12)
    if not rows:
        span = ["[dim]" + empty + "[/dim]"] + [""] * (
            3 + int(show_holder) + int(show_due)
        )
        tbl.add_row(*span)
        return tbl
    for r in rows[:12]:
        prio = r.get("priority", "")
        cells = [
            r.get("ref", "?"),
            Text(prio, style=_PRIORITY_COLOR.get(prio, "white")),
            (r.get("title") or "")[:60],
            r.get("project") or "—",
        ]
        if show_holder:
            cells.append(r.get("lease_owner") or r.get("assignee") or "[dim]никто[/dim]")
        if show_due:
            due = (r.get("due_date") or "")[:10]
            cells.append(("⚠ " + due) if r.get("overdue") else due)
        tbl.add_row(*cells)
    return tbl


def _by_project_table(rows: list[dict]) -> Table:
    tbl = Table(title="[bold]По проектам (открытые задачи)[/bold]", box=ROUNDED,
                border_style="grey37", title_justify="left", expand=True, padding=(0, 1))
    tbl.add_column("Проект", style="cyan", no_wrap=True, max_width=22)
    tbl.add_column("Откр.", justify="right", max_width=6)
    tbl.add_column("", ratio=1)
    tbl.add_column("▶", justify="right", style="cyan", max_width=4)
    tbl.add_column("⛔", justify="right", style="red", max_width=4)
    tbl.add_column("👀", justify="right", style="yellow", max_width=4)
    if not rows:
        tbl.add_row("[dim]нет открытых задач[/dim]", "", "", "", "", "")
        return tbl
    peak = max([1, *(r["open"] for r in rows)])
    for r in rows:
        tbl.add_row(
            r["project"], str(r["open"]), _hbar(r["open"], peak, width=18, color="blue"),
            str(r["in_progress"]) or "", str(r["blocked"]) or "", str(r["review"]) or "",
        )
    return tbl


def _render_dashboard(d: dict[str, Any]) -> None:
    console.print()
    console.print(_kpi_header(d))
    console.print(_distribution(d))
    t = d["tasks"]
    console.print(_task_table("▶ В работе", t["in_progress"], show_holder=True,
                              empty="ничего не в работе"))
    attention = t["blocked"] + [r for r in t["overdue"] if r["status"] != "blocked"]
    console.print(_task_table("⚠ Требует внимания (заблок. + просроч.)", attention,
                              show_holder=True, show_due=True, empty="всё спокойно"))
    if t["review"]:
        console.print(_task_table("👀 На ревью", t["review"], show_holder=True))
    console.print(_by_project_table(d["by_project"]))
    console.print("[dim]Журнал событий → [bold]atlas logs[/bold] (кто/что/проект/приоритет).[/dim]")


@command
def dashboard_cmd(
    project: Optional[str] = typer.Option(
        None, "--project", help="Только этот проект (slug | UUID); иначе весь портфель.",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Машинный JSON (для агентов); по умолчанию — Rich для человека.",
    ),
) -> None:
    """Операционный дашборд: статусы задач, что в работе, что требует внимания, по проектам."""
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        try:
            data = build_dashboard(session, project_ref=project)
        except AmbiguousRefError as exc:
            raise CliError("ambiguous_ref", str(exc))
        except ValueError as exc:
            raise CliError("not_found", str(exc))
    # json — по локальному флагу ИЛИ глобальному (--json в любой позиции → ATLAS_OUTPUT).
    if json_out or os.environ.get("ATLAS_OUTPUT") == "json":
        print(_json.dumps(data, ensure_ascii=False))
    else:
        _render_dashboard(data)
