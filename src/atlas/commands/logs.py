"""CLI `atlas logs` — обогащённый журнал событий портфеля (поверх action_log).

В отличие от сырого ``atlas action-log list`` показывает КТО / ЧТО (заголовок) /
в каком ПРОЕКТЕ / приоритет — человеку Rich-таблицей, агенту ``--json``.
"""
from __future__ import annotations

import json as _json
import os
from datetime import datetime, timedelta
from typing import Any, Optional

import typer
from clikit import CliError, command
from rich.box import ROUNDED
from rich.console import Console
from rich.table import Table

from atlas._time import local_now
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.logs import build_logs

console = Console()

_PRIORITY_COLOR = {"P0": "bold red", "P1": "orange1", "P2": "blue", "P3": "grey62"}
#: Краткие иконки по типу действия (визуальная группировка ленты).
_ACTION_ICON = {
    "task_created": "✚", "task_claimed": "🔒", "task_started": "▶", "task_done": "✅",
    "task_cancelled": "🚫", "task_blocked": "⛔", "task_unblocked": "▶", "task_review": "👀",
    "task_updated": "✎", "task_released": "🔓", "task_taken": "⚡", "task_archived": "🗑",
}


def _parse_since(value: Optional[str]) -> Optional[datetime]:
    """'7d' | '24h' | 'YYYY-MM-DD' → datetime-порог; None → без фильтра."""
    if not value:
        return None
    v = value.strip().lower()
    if v.endswith("d") and v[:-1].isdigit():
        return local_now() - timedelta(days=int(v[:-1]))
    if v.endswith("h") and v[:-1].isdigit():
        return local_now() - timedelta(hours=int(v[:-1]))
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise CliError("invalid_since", f"Невалидный --since '{value}': 7d | 24h | YYYY-MM-DD.")


def _render(d: dict[str, Any]) -> None:
    rows = d["logs"]
    if not rows:
        console.print("[dim]Событий нет.[/dim]")
        return
    tbl = Table(box=ROUNDED, border_style="grey37", title="[bold]Журнал событий[/bold]",
                title_justify="left", expand=True)
    tbl.add_column("Когда", style="grey62", no_wrap=True)
    tbl.add_column("Кто", style="green", no_wrap=True, max_width=14)
    tbl.add_column("Действие", no_wrap=True)
    tbl.add_column("Что", style="white", ratio=1)
    tbl.add_column("Проект", style="cyan", no_wrap=True, max_width=18)
    tbl.add_column("P", justify="center", max_width=3)
    for r in rows:
        when = (r["at"] or "")[:16].replace("T", " ")
        icon = _ACTION_ICON.get(r["action"], "·")
        act = f"{icon} {r['action']}"
        what = r.get("title") or r.get("ref") or r.get("entity_id", "")[:8]
        prio = r.get("priority") or ""
        tbl.add_row(
            when, r.get("actor") or "[dim]—[/dim]", act, (what or "")[:60],
            r.get("project") or "[dim]—[/dim]",
            f"[{_PRIORITY_COLOR.get(prio, 'white')}]{prio}[/]" if prio else "",
        )
    console.print(tbl)


@command
def logs_cmd(
    limit: int = typer.Option(30, "--limit", "-n", help="Сколько последних событий."),
    project: Optional[str] = typer.Option(None, "--project", help="Фильтр по проекту (slug | UUID)."),
    entity_type: Optional[str] = typer.Option(None, "--entity-type", help="task | epic | project."),
    action: Optional[str] = typer.Option(None, "--action", help="Точное действие, напр. task_done."),
    actor: Optional[str] = typer.Option(None, "--actor", help="Фильтр по участнику (slug)."),
    since: Optional[str] = typer.Option(None, "--since", help="Порог: 7d | 24h | YYYY-MM-DD."),
    json_out: bool = typer.Option(False, "--json", help="Машинный JSON (для агентов)."),
) -> None:
    """Обогащённый журнал событий: кто / что / в каком проекте / приоритет."""
    since_dt = _parse_since(since)
    with make_session(make_engine(resolve_db_url())) as session:
        try:
            rows = build_logs(
                session, limit=limit, project_ref=project, entity_type=entity_type,
                action=action, actor=actor, since=since_dt,
            )
        except ValueError as exc:
            raise CliError("not_found", str(exc))
        data = {"count": len(rows), "logs": rows}
    if json_out or os.environ.get("ATLAS_OUTPUT") == "json":
        print(_json.dumps(data, ensure_ascii=False))
    else:
        _render(data)
