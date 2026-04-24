"""Typer-CLI: `atlas today | overdue | tasks list | ...`."""
from __future__ import annotations

import json as _json
from datetime import date, datetime
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from . import files as files_mod
from . import projects as proj_mod
from . import tasks as tasks_mod
from .client import NotionClient
from .config import load_settings
from .dates import bucketize, parse_human_date
from .index import Index, employees_index, projects_index
from .props import DateValue
from .resolve import (
    AmbiguousMatchError,
    NamedPage,
    NotFoundError,
    normalize_page_id,
    resolve_employee,
    resolve_project,
)


from .pm.commands.projects import projects_app as pm_projects_app
from .pm.commands.pm_tasks import pm_tasks_app

app = typer.Typer(no_args_is_help=True, help="Notion: задачи, проекты, файлы. + PM-слой projects/pm-tasks.")
tasks_app = typer.Typer(no_args_is_help=True, help="Задачи (БД _Задачи Notion).")
notion_projects_app = typer.Typer(no_args_is_help=True, help="Проекты/клиенты (Notion).")
files_app = typer.Typer(no_args_is_help=True, help="Файлы клиентов (Notion).")
app.add_typer(tasks_app, name="tasks")
app.add_typer(notion_projects_app, name="notion-projects")  # бывшая команда `projects`
app.add_typer(files_app, name="files")
app.add_typer(pm_projects_app, name="projects")  # локальная PM-БД (NP-005)
app.add_typer(pm_tasks_app, name="pm-tasks")  # PM-tasks (NP-005)

console = Console()


def _client() -> tuple[NotionClient, Any]:
    s = load_settings()
    return NotionClient(s.token, version=s.notion_version), s


def _me(client: NotionClient, settings: Any) -> str | None:
    if settings.self_employee_id:
        return settings.self_employee_id
    if settings.self_employee_name:
        try:
            emp = resolve_employee(client, settings.self_employee_name)
            return emp.id
        except (NotFoundError, AmbiguousMatchError):
            return None
    return None


def _project_id(client: NotionClient, query: str) -> str:
    try:
        return resolve_project(client, query).id
    except AmbiguousMatchError as exc:
        console.print(f"[yellow]Неоднозначно «{exc.query}»:[/yellow]")
        for m in exc.matches:
            console.print(f"  • {m.title} ({m.id})")
        raise typer.Exit(2)
    except NotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(3)


# ---------- rendering ----------


def _fmt_date(d: DateValue | None, tz_name: str) -> str:
    if not d or d.start is None:
        return "—"
    if isinstance(d.start, datetime):
        return d.start.strftime("%Y-%m-%d %H:%M")
    return d.start.strftime("%Y-%m-%d")


def _render_tasks(
    title: str,
    tasks: list[tasks_mod.Task],
    *,
    tz_name: str,
    as_json: bool = False,
    client: NotionClient | None = None,
) -> None:
    if as_json:
        proj_idx = projects_index(client) if client else Index({})
        emp_idx = employees_index(client) if client else Index({})
        console.print_json(data=[_task_as_json(t, proj_idx, emp_idx) for t in tasks])
        return
    table = Table(title=f"{title} — {len(tasks)}", show_lines=False)
    table.add_column("Дата", style="cyan", no_wrap=True)
    table.add_column("Статус", style="magenta")
    table.add_column("Тип", style="yellow")
    table.add_column("Задача")
    table.add_column("b24", style="dim")
    for t in tasks:
        types = ", ".join(t.types) or "—"
        b24 = f"#{t.b24_item_id}" if t.b24_item_id else (
            f"t{t.b24_task_id}" if t.b24_task_id else "—"
        )
        table.add_row(_fmt_date(t.date_value, tz_name), t.status or "—", types, t.title, b24)
    console.print(table)


def _task_as_json(
    t: tasks_mod.Task,
    proj_idx: Index | None = None,
    emp_idx: Index | None = None,
) -> dict[str, Any]:
    d = t.date_value
    date_obj = None
    if d and d.start is not None:
        date_obj = d.start.isoformat()

    def _pairs(ids: list[str], idx: Index | None) -> list[dict[str, str]]:
        if idx is None:
            return [{"id": i, "title": ""} for i in ids]
        return idx.pairs(ids)

    return {
        "id": t.id,
        "title": t.title,
        "status": t.status,
        "date": date_obj,
        "types": t.types,
        "projects": _pairs(t.projects, proj_idx),
        "subprojects": _pairs(t.subprojects, proj_idx),
        "responsible": _pairs(t.responsible, emp_idx),
        "executors": _pairs(t.executors, emp_idx),
        "b24_task_id": t.b24_task_id,
        "b24_item_id": t.b24_item_id,
        "url": t.url,
    }


# ---------- top-level shortcuts ----------


@app.command("today")
def cmd_today(
    mine: bool = typer.Option(True, "--mine/--all"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Активные задачи на сегодня."""
    with _client()[0] as c:
        s = load_settings()
        me = _me(c, s) if mine else None
        lst = tasks_mod.list_today(c, responsible_id=me, tz_name=s.portal_tz)
        _render_tasks("Сегодня", lst, tz_name=s.portal_tz, as_json=as_json, client=c)


@app.command("overdue")
def cmd_overdue(
    mine: bool = typer.Option(True, "--mine/--all"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Просроченные активные задачи (дата раньше сегодня)."""
    with _client()[0] as c:
        s = load_settings()
        me = _me(c, s) if mine else None
        lst = tasks_mod.list_overdue(c, responsible_id=me, tz_name=s.portal_tz)
        _render_tasks("Просроченные", lst, tz_name=s.portal_tz, as_json=as_json, client=c)


@app.command("no-date")
def cmd_no_date(
    mine: bool = typer.Option(True, "--mine/--all"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Активные задачи без дедлайна."""
    with _client()[0] as c:
        s = load_settings()
        me = _me(c, s) if mine else None
        lst = tasks_mod.list_no_date(c, responsible_id=me)
        _render_tasks("Без дедлайна", lst, tz_name=s.portal_tz, as_json=as_json, client=c)


@app.command("agenda")
def cmd_agenda(
    mine: bool = typer.Option(True, "--mine/--all"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Сводка: overdue + today + no-date."""
    with _client()[0] as c:
        s = load_settings()
        me = _me(c, s) if mine else None
        od = tasks_mod.list_overdue(c, responsible_id=me, tz_name=s.portal_tz)
        td = tasks_mod.list_today(c, responsible_id=me, tz_name=s.portal_tz)
        nd = tasks_mod.list_no_date(c, responsible_id=me)
        if as_json:
            pi = projects_index(c)
            ei = employees_index(c)
            console.print_json(data={
                "overdue": [_task_as_json(t, pi, ei) for t in od],
                "today":   [_task_as_json(t, pi, ei) for t in td],
                "no_date": [_task_as_json(t, pi, ei) for t in nd],
            })
            return
        _render_tasks("Просроченные", od, tz_name=s.portal_tz)
        _render_tasks("Сегодня", td, tz_name=s.portal_tz)
        _render_tasks("Без дедлайна", nd, tz_name=s.portal_tz)


# ---------- tasks ----------


@tasks_app.command("list")
def cmd_tasks_list(
    project: str | None = typer.Option(None, "--project"),
    mine: bool = typer.Option(False, "--mine/--all"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Активные задачи (не Выполнена/Отмена). Без --project — все, куда есть доступ."""
    with _client()[0] as c:
        s = load_settings()
        me = _me(c, s) if mine else None
        pid = _project_id(c, project) if project else None
        lst = tasks_mod.list_active(c, responsible_id=me, project_id=pid)
        title = f"Проект «{project}»" if project else "Активные задачи"
        _render_tasks(title, lst, tz_name=s.portal_tz, as_json=as_json, client=c)


@tasks_app.command("show")
def cmd_tasks_show(id_or_url: str) -> None:
    """Показать одну задачу в JSON."""
    with _client()[0] as c:
        pid = normalize_page_id(id_or_url)
        page = c.retrieve_page(pid)
        task = tasks_mod.task_from_page(page)
        console.print_json(data=_task_as_json(task, projects_index(c), employees_index(c)))


@tasks_app.command("create")
def cmd_tasks_create(
    title: str = typer.Argument(...),
    project: str = typer.Option(..., "--project", "-p"),
    when: str | None = typer.Option(None, "--date", "-d", help="сегодня|завтра|+3д|YYYY-MM-DD [HH:MM]"),
    responsible: str | None = typer.Option(None, "--responsible", "-r"),
    task_type: list[str] = typer.Option(None, "--type", "-t"),
    status: str = typer.Option("В планах", "--status"),
) -> None:
    """Создать задачу."""
    with _client()[0] as c:
        s = load_settings()
        proj = resolve_project(c, project)
        resp_id = resolve_employee(c, responsible).id if responsible else _me(c, s)
        d: date | datetime | None = None
        if when:
            d = parse_human_date(when, tz_name=s.portal_tz)
        new = tasks_mod.create_task(
            c,
            title=title,
            subproject_id=proj.id,
            date_value=d,
            types=list(task_type) if task_type else None,
            responsible_id=resp_id,
            status=status,
            tz_name=s.portal_tz,
        )
        console.print(f"[green]Создано:[/green] {new.get('url')}")


@tasks_app.command("status")
def cmd_tasks_status(id_or_url: str, status: str) -> None:
    """Изменить статус (planned|working|paused|done|cancelled)."""
    with _client()[0] as c:
        pid = normalize_page_id(id_or_url)
        tasks_mod.set_status(c, pid, status)
        console.print(f"[green]Статус изменён[/green] → {status}")


@tasks_app.command("date")
def cmd_tasks_date(id_or_url: str, when: str) -> None:
    """Назначить/очистить дату. `clear` — очистить."""
    with _client()[0] as c:
        s = load_settings()
        pid = normalize_page_id(id_or_url)
        if when.lower() == "clear":
            tasks_mod.set_date(c, pid, None)
            console.print("[green]Дата очищена[/green]")
            return
        d = parse_human_date(when, tz_name=s.portal_tz)
        tasks_mod.set_date(c, pid, d, tz_name=s.portal_tz)
        console.print(f"[green]Дата:[/green] {d}")


@tasks_app.command("archive")
def cmd_tasks_archive(id_or_url: str, unarchive: bool = typer.Option(False, "--unarchive")) -> None:
    with _client()[0] as c:
        pid = normalize_page_id(id_or_url)
        c.archive_page(pid, archived=not unarchive)
        console.print("[green]ok[/green]")


# ---------- projects ----------


@notion_projects_app.command("list")
def cmd_projects_list(
    status: str | None = typer.Option(None, "--status"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    with _client()[0] as c:
        projects = proj_mod.list_projects(c, status=status)
        if as_json:
            console.print_json(data=[p.__dict__ for p in projects])
            return
        table = Table(title=f"Проекты — {len(projects)}")
        table.add_column("Название")
        table.add_column("Статус")
        table.add_column("b24_company", style="dim")
        table.add_column("b24_contact", style="dim")
        for p in projects:
            table.add_row(p.title, p.status or "—",
                          str(p.b24_company_id or "—"),
                          str(p.b24_contact_id or "—"))
        console.print(table)


@notion_projects_app.command("show")
def cmd_projects_show(name_or_id: str) -> None:
    with _client()[0] as c:
        s = load_settings()
        try:
            proj = resolve_project(c, name_or_id)
        except (NotFoundError, AmbiguousMatchError):
            pid = normalize_page_id(name_or_id)
            page = c.retrieve_page(pid)
            proj_obj = proj_mod.project_from_page(page)
            proj = NamedPage(id=proj_obj.id, title=proj_obj.title)
        tasks_lst = tasks_mod.list_by_project(c, proj.id)
        files_lst = files_mod.list_for_client(c, proj.id)
        console.print(f"[bold]Проект[/bold]: {proj.title} ({proj.id})")
        _render_tasks("Активные задачи", tasks_lst, tz_name=s.portal_tz)
        console.print(f"[bold]Файлы[/bold]: {len(files_lst)}")
        for f in files_lst:
            mark = "✓" if f.done else "□"
            console.print(f"  {mark} {f.title}")


# ---------- files ----------


@files_app.command("list")
def cmd_files_list(
    project: str = typer.Option(..., "--project", "-p"),
    open_only: bool = typer.Option(False, "--open-only"),
) -> None:
    with _client()[0] as c:
        pid = _project_id(c, project)
        lst = files_mod.list_for_client(c, pid, only_open=open_only)
        for f in lst:
            mark = "✓" if f.done else "□"
            console.print(f"{mark} {f.title}")


@files_app.command("done")
def cmd_files_done(id_or_url: str, reopen: bool = typer.Option(False, "--reopen")) -> None:
    with _client()[0] as c:
        pid = normalize_page_id(id_or_url)
        files_mod.mark_done(c, pid, done=not reopen)
        console.print("[green]ok[/green]")


@files_app.command("create")
def cmd_files_create(
    title: str = typer.Argument(...),
    project: str = typer.Option(..., "--project", "-p"),
    when: str | None = typer.Option(None, "--date"),
) -> None:
    with _client()[0] as c:
        s = load_settings()
        pid = _project_id(c, project)
        d = parse_human_date(when, tz_name=s.portal_tz) if when else None
        new = files_mod.create_file(
            c, title=title, client_page_id=pid, date_value=d, tz_name=s.portal_tz
        )
        console.print(f"[green]Создано:[/green] {new.get('url')}")


@app.command("whoami")
def cmd_whoami() -> None:
    with _client()[0] as c:
        s = load_settings()
        me = _me(c, s)
        console.print(f"token: ...{s.token[-6:]}")
        console.print(f"tz: {s.portal_tz}")
        console.print(f"self: {s.self_employee_name} → {me}")


if __name__ == "__main__":
    app()
