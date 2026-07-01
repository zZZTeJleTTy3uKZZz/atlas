"""CLI `atlas sprint ...` — Scrum-спринты (тайм-боксы) поверх доменной ``atlas.sprint``.

Команды: add / list / get / start / close / cancel / assign / board / velocity.
Спринт ≠ эпик (тема): тайм-бокс с датами, в него набирают задачи, считают velocity.
Церемонии: planning=add+assign, daily=board, review/retro=close --retro, velocity-тренд.
"""
from __future__ import annotations

import json as _json
import os
from datetime import datetime
from typing import Any, Optional

import typer
from clikit import CliError, command, emit_data
from rich.box import ROUNDED
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from atlas import sprint as S
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import Sprint, Task
from atlas.slugs import AmbiguousRefError, resolve_project_ref, slugify_text

sprint_app = typer.Typer(no_args_is_help=True, help="Спринты (Scrum-тайм-боксы).")
console = Console()


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


def _engine():
    return make_engine(resolve_db_url())


def _resolve_project_or_die(session, ref: str):
    try:
        proj = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        raise CliError("ambiguous_ref", str(exc))
    if proj is None:
        raise CliError("not_found", f"Проект '{ref}' не найден.")
    return proj


def _resolve_sprint_or_die(session, ref: str) -> Sprint:
    sp = S.resolve_sprint(session, ref)
    if sp is None:
        raise CliError("not_found", f"Спринт '{ref}' не найден.")
    return sp


def _resolve_task_or_die(session, ref: str) -> Task:
    from atlas.commands.task import _resolve_task_or_die as _rt

    return _rt(session, ref)


def _parse_date(value: Optional[str], label: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise CliError("invalid_date", f"Невалидный --{label} '{value}': YYYY-MM-DD.")


def _unique_slug(session, base: str) -> str:
    slug = slugify_text(base)[:90] or "sprint"
    cand, n = slug, 1
    while session.execute(
        select(Sprint).where(Sprint.slug == cand)
    ).scalar_one_or_none() is not None:
        n += 1
        cand = f"{slug}-{n}"
    return cand


def _sprint_dict(session, sp: Sprint, *, with_velocity: bool = False) -> dict[str, Any]:
    d = {
        "id": sp.id, "slug": sp.slug, "name": sp.name, "goal": sp.goal,
        "status": sp.status,
        "starts_at": sp.starts_at.isoformat() if sp.starts_at else None,
        "ends_at": sp.ends_at.isoformat() if sp.ends_at else None,
        "planned_velocity": sp.planned_velocity,
        "retro_notes": sp.retro_notes,
        "project_id": sp.project_id,
    }
    if with_velocity:
        d["velocity"] = S.sprint_velocity(session, sp)
    return d


def _render_sprint(d: dict[str, Any]) -> None:
    icons = {"planning": "📋", "active": "🏃", "closed": "✅", "cancelled": "🚫"}
    console.print(f"{icons.get(d['status'], '·')} [bold cyan]{d['slug'] or d['id']}[/bold cyan] — {d['name']}  [grey50]({d['status']})[/grey50]")
    if d.get("goal"):
        console.print(f"  Цель:   {d['goal']}")
    period = f"{(d['starts_at'] or '?')[:10]} → {(d['ends_at'] or '?')[:10]}"
    console.print(f"  Период: {period}")
    v = d.get("velocity")
    if v:
        console.print(f"  Velocity: план {v['planned_velocity'] or '—'} · факт {v['actual_velocity']} "
                      f"({v['done_tasks']}/{v['total_tasks']} задач, набрано {v['committed_points']} pts)")
    if d.get("retro_notes"):
        console.print(f"  Retro:  {d['retro_notes']}")


# --------------------------------------------------------------------------- #
# CRUD                                                                         #
# --------------------------------------------------------------------------- #


@sprint_app.command("add")
@command
def add_cmd(
    project: str = typer.Option(..., "--project", help="Проект (slug | UUID)."),
    name: str = typer.Option(..., "--name", help="Имя спринта, напр. «Sprint 26»."),
    goal: Optional[str] = typer.Option(None, "--goal", help="Цель спринта."),
    starts_at: Optional[str] = typer.Option(None, "--starts-at", help="YYYY-MM-DD."),
    ends_at: Optional[str] = typer.Option(None, "--ends-at", help="YYYY-MM-DD."),
    planned_velocity: Optional[int] = typer.Option(None, "--planned-velocity", help="План velocity (pts)."),
    slug: Optional[str] = typer.Option(None, "--slug", help="Явный slug (иначе из имени)."),
) -> None:
    """Создать спринт (status=planning)."""
    starts = _parse_date(starts_at, "starts-at")
    ends = _parse_date(ends_at, "ends-at")
    if starts and ends and ends < starts:
        raise CliError("invalid_range", "--ends-at раньше --starts-at.")
    with make_session(_engine()) as session:
        proj = _resolve_project_or_die(session, project)
        sp = Sprint(
            slug=slug or _unique_slug(session, name), project_id=proj.id, name=name,
            goal=goal, starts_at=starts, ends_at=ends, planned_velocity=planned_velocity,
            status="planning",
        )
        session.add(sp)
        session.commit()
        data = _sprint_dict(session, sp)
    emit_data(data, text_renderer=_render_sprint)


@sprint_app.command("list")
@command
def list_cmd(
    project: Optional[str] = typer.Option(None, "--project"),
    status: Optional[str] = typer.Option(None, "--status", help="planning|active|closed|cancelled"),
) -> None:
    """Список спринтов (фильтры project/status)."""
    with make_session(_engine()) as session:
        q = select(Sprint)
        if project:
            q = q.where(Sprint.project_id == _resolve_project_or_die(session, project).id)
        if status:
            q = q.where(Sprint.status == status)
        rows = [_sprint_dict(session, sp, with_velocity=True)
                for sp in session.execute(q.order_by(Sprint.created_at.desc())).scalars().all()]

    def _render(rows_: list[dict]) -> None:
        if not rows_:
            console.print("[dim]Спринтов нет.[/dim]")
            return
        tbl = Table(box=ROUNDED, border_style="grey37")
        tbl.add_column("Слаг", style="cyan")
        tbl.add_column("Имя")
        tbl.add_column("Статус")
        tbl.add_column("Период", style="grey62")
        tbl.add_column("Velocity (план/факт)", justify="right")
        for r in rows_:
            v = r["velocity"]
            tbl.add_row(r["slug"] or r["id"][:8], r["name"], r["status"],
                        f"{(r['starts_at'] or '?')[:10]}→{(r['ends_at'] or '?')[:10]}",
                        f"{v['planned_velocity'] or '—'} / {v['actual_velocity']}")
        console.print(tbl)

    emit_data(rows, text_renderer=_render)


@sprint_app.command("get")
@command
def get_cmd(ref: str = typer.Argument(..., help="slug | UUID")) -> None:
    """Карточка спринта + velocity."""
    with make_session(_engine()) as session:
        sp = _resolve_sprint_or_die(session, ref)
        data = _sprint_dict(session, sp, with_velocity=True)
    emit_data(data, text_renderer=_render_sprint)


# --------------------------------------------------------------------------- #
# Жизненный цикл                                                               #
# --------------------------------------------------------------------------- #


def _transition(ref: str, fn, *, action: str, **kw) -> None:
    with make_session(_engine()) as session:
        sp = _resolve_sprint_or_die(session, ref)
        try:
            fn(session, sp, **kw)
        except S.SprintTransitionError as exc:
            raise CliError("invalid_transition", str(exc))
        session.commit()
        data = _sprint_dict(session, sp, with_velocity=True)
    emit_data(data, text_renderer=_render_sprint)


@sprint_app.command("start")
@command
def start_cmd(ref: str = typer.Argument(...)) -> None:
    """planning → active (спринт начался)."""
    _transition(ref, S.start_sprint, action="started")


@sprint_app.command("close")
@command
def close_cmd(
    ref: str = typer.Argument(...),
    retro: Optional[str] = typer.Option(None, "--retro", help="Retro-заметки (что прошло хорошо/плохо)."),
) -> None:
    """active → closed (+ retro). Фиксирует velocity по факту."""
    _transition(ref, S.close_sprint, action="closed", retro=retro)


@sprint_app.command("cancel")
@command
def cancel_cmd(ref: str = typer.Argument(...)) -> None:
    """Отменить спринт → cancelled."""
    _transition(ref, S.cancel_sprint, action="cancelled")


# --------------------------------------------------------------------------- #
# Набор задач + доска + velocity                                              #
# --------------------------------------------------------------------------- #


@sprint_app.command("assign")
@command
def assign_cmd(
    sprint_ref: str = typer.Argument(..., help="Спринт (slug | UUID)."),
    task_refs: list[str] = typer.Argument(..., help="Задачи: number | slug | UUID."),
    clear: bool = typer.Option(False, "--clear", help="Отвязать задачи от спринта."),
) -> None:
    """Набрать задачи в спринт (или отвязать --clear)."""
    with make_session(_engine()) as session:
        sp = _resolve_sprint_or_die(session, sprint_ref)
        tasks = [_resolve_task_or_die(session, r) for r in task_refs]
        changed = S.assign_tasks(session, None if clear else sp, tasks)
        session.commit()
        data = {"sprint": sp.slug or sp.id, "cleared": clear,
                "changed": changed, "tasks": [t.slug or str(t.number) for t in tasks]}
    emit_data(
        data,
        text_renderer=lambda d: console.print(
            f"{'🧹 отвязано' if d['cleared'] else '➕ в спринт'} {d['sprint']}: "
            f"{d['changed']} задач(и) — {', '.join(d['tasks'])}"),
    )


@sprint_app.command("board")
@command
def board_cmd(
    ref: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Kanban-доска спринта (задачи по колонкам-статусам + points)."""
    with make_session(_engine()) as session:
        sp = _resolve_sprint_or_die(session, ref)
        board = S.sprint_board(session, sp)
        name, slug, status = sp.name, sp.slug or sp.id, sp.status
    if json_out or os.environ.get("ATLAS_OUTPUT") == "json":
        print(_json.dumps({"sprint": slug, "name": name, "status": status, **board}, ensure_ascii=False))
        return
    cols = ["todo", "in_progress", "review", "blocked", "done"]
    v = board["velocity"]
    console.print(f"\n[bold magenta]🏃 {slug} — {name}[/bold magenta] [grey50]({status})[/grey50]  "
                  f"velocity план {v['planned_velocity'] or '—'} / факт [bold green]{v['actual_velocity']}[/bold green]")
    tbl = Table(box=ROUNDED, border_style="grey37", expand=True)
    for c in cols:
        col = board["columns"].get(c, {"tasks": [], "points": 0})
        tbl.add_column(f"{c}\n[dim]{len(col['tasks'])} · {col['points']}pts[/dim]", ratio=1)
    maxn = max([1, *(len(board["columns"].get(c, {"tasks": []})["tasks"]) for c in cols)])
    for i in range(maxn):
        row = []
        for c in cols:
            ts = board["columns"].get(c, {"tasks": []})["tasks"]
            if i < len(ts):
                t = ts[i]
                row.append(f"[cyan]{t['ref']}[/cyan]\n{(t['title'] or '')[:24]} [grey50]{t['priority']}·{t['story_points'] or 0}p[/grey50]")
            else:
                row.append("")
        tbl.add_row(*row)
    console.print(tbl)


@sprint_app.command("velocity")
@command
def velocity_cmd(
    project: Optional[str] = typer.Option(None, "--project"),
    last_n: int = typer.Option(5, "--last-n", help="Сколько закрытых спринтов показать."),
) -> None:
    """Тренд velocity по закрытым спринтам (план vs факт)."""
    with make_session(_engine()) as session:
        q = select(Sprint).where(Sprint.status == "closed")
        if project:
            q = q.where(Sprint.project_id == _resolve_project_or_die(session, project).id)
        sprints = session.execute(q.order_by(Sprint.ends_at.desc())).scalars().all()[:last_n]
        rows = []
        for sp in reversed(list(sprints)):
            v = S.sprint_velocity(session, sp)
            rows.append({"sprint": sp.slug or sp.id, "name": sp.name,
                         "planned": v["planned_velocity"], "actual": v["actual_velocity"]})
        avg = round(sum(r["actual"] for r in rows) / len(rows), 1) if rows else 0
        data = {"sprints": rows, "avg_velocity": avg}

    def _render(d: dict) -> None:
        if not d["sprints"]:
            console.print("[dim]Закрытых спринтов нет.[/dim]")
            return
        tbl = Table(box=ROUNDED, border_style="grey37", title="Velocity-тренд")
        tbl.add_column("Спринт", style="cyan")
        tbl.add_column("План", justify="right")
        tbl.add_column("Факт", justify="right")
        tbl.add_column("", justify="right")
        for r in d["sprints"]:
            pl, ac = r["planned"], r["actual"]
            mark = "" if pl is None else ("↑" if ac > pl else "↓" if ac < pl else "=")
            tbl.add_row(r["sprint"], str(pl if pl is not None else "—"), str(ac), mark)
        console.print(tbl)
        console.print(f"[bold]Средняя velocity:[/bold] {d['avg_velocity']} pts")

    emit_data(data, text_renderer=_render)
