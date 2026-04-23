"""CLI-команды `notionctl portfolio ...`.

MVP (Spike v0.4):
- `portfolio init` — создать БД, применить миграции, seed справочников.
- `portfolio list` — список проектов (фильтры).
- `portfolio show <slug>` — карточка проекта.
- `portfolio create <slug> ...` — добавить проект.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from notion_task_cli.pm.db import DEFAULT_DB_PATH, make_engine, make_session
from notion_task_cli.pm.models import Project, ProjectStatus, ProjectType
from notion_task_cli.pm.seeds import seed_all

portfolio_app = typer.Typer(
    no_args_is_help=True, help="Portfolio management: проекты, карта, CRUD."
)
console = Console()


def _db_url() -> str:
    """Получить URL БД: env var -> default."""
    return os.environ.get("NOTION_TASK_CLI_DB_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


def _find_project_root() -> Path:
    """Найти корень проекта notion-task-cli (где alembic.ini)."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "alembic.ini").exists():
            return parent
    raise RuntimeError("Не найден alembic.ini: не могу определить корень проекта")


@portfolio_app.command("init")
def init_cmd(
    db_url: Optional[str] = typer.Option(
        None, "--db-url", help="URL БД (override env NOTION_TASK_CLI_DB_URL и default)"
    ),
) -> None:
    """Инициализировать PM-БД: apply migrations + seed справочников."""
    url = db_url or _db_url()
    console.print(f"[bold]Database:[/bold] {url}")

    # Step 1: apply migrations через alembic CLI
    console.print("[cyan]1. Применяю миграции Alembic...[/cyan]")
    env = os.environ.copy()
    env["NOTION_TASK_CLI_DB_URL"] = url
    project_root = _find_project_root()
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print("[red]Ошибка миграций:[/red]")
        console.print(result.stderr)
        raise typer.Exit(code=1)
    console.print("[green]   ✓ миграции применены[/green]")

    # Step 2: seed справочников
    console.print("[cyan]2. Заселяю справочники (project_types, project_statuses, participants)...[/cyan]")
    engine = make_engine(url)
    with make_session(engine) as session:
        counts = seed_all(session)
    console.print(
        f"[green]   ✓ project_types={counts['project_types']}, "
        f"project_statuses={counts['project_statuses']}, "
        f"participants={counts['participants']}[/green]"
    )

    console.print("[bold green]Готово.[/bold green] БД `portfolio` инициализирована.")


@portfolio_app.command("list")
def list_cmd(
    type_slug: Optional[str] = typer.Option(None, "--type", help="Фильтр: slug типа"),
    status_slug: Optional[str] = typer.Option(None, "--status", help="Фильтр: slug статуса"),
) -> None:
    """Список проектов (табличный вывод)."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        stmt = select(
            Project.slug,
            Project.name,
            Project.priority,
            Project.one_line_summary,
            ProjectType.slug.label("type_slug"),
            ProjectStatus.slug.label("status_slug"),
        ).join(
            ProjectType, Project.type_id == ProjectType.id
        ).join(
            ProjectStatus, Project.status_id == ProjectStatus.id
        ).order_by(Project.priority, Project.name)

        if type_slug:
            stmt = stmt.where(ProjectType.slug == type_slug)
        if status_slug:
            stmt = stmt.where(ProjectStatus.slug == status_slug)

        rows = session.execute(stmt).all()

    if not rows:
        console.print("[yellow]Проектов не найдено. Подсказка: `portfolio init` + `portfolio create ...`[/yellow]")
        return

    table = Table(title=f"Projects ({len(rows)})")
    table.add_column("slug", style="cyan", no_wrap=True)
    table.add_column("name")
    table.add_column("P", justify="center", style="bold")
    table.add_column("type", style="magenta")
    table.add_column("status", style="green")
    table.add_column("summary", overflow="fold")

    for row in rows:
        table.add_row(
            row.slug, row.name, row.priority,
            row.type_slug, row.status_slug, row.one_line_summary,
        )
    console.print(table)


@portfolio_app.command("create")
def create_cmd(
    slug: str = typer.Argument(..., help="Уникальный slug проекта"),
    name: str = typer.Option(..., "--name", help="Человекочитаемое название"),
    type_slug: str = typer.Option(..., "--type", help="Тип: client-project / business-product / ..."),
    one_line: str = typer.Option(..., "--one-line", help="1-строчное описание"),
    priority: str = typer.Option("P1", "--priority", help="P0 / P1 / P2 / P3"),
    status_slug: str = typer.Option("active", "--status", help="Lifecycle-статус"),
    description: Optional[str] = typer.Option(None, "--description"),
    git_repo_url: Optional[str] = typer.Option(None, "--git"),
    local_path: Optional[str] = typer.Option(None, "--path"),
) -> None:
    """Создать новый проект в портфеле."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        pt = session.execute(
            select(ProjectType).where(ProjectType.slug == type_slug)
        ).scalar_one_or_none()
        if pt is None:
            console.print(f"[red]Тип '{type_slug}' не найден. Список: `portfolio types`[/red]")
            raise typer.Exit(code=1)

        ps = session.execute(
            select(ProjectStatus).where(ProjectStatus.slug == status_slug)
        ).scalar_one_or_none()
        if ps is None:
            console.print(f"[red]Статус '{status_slug}' не найден.[/red]")
            raise typer.Exit(code=1)

        existing = session.execute(
            select(Project).where(Project.slug == slug)
        ).scalar_one_or_none()
        if existing is not None:
            console.print(f"[red]Проект '{slug}' уже существует.[/red]")
            raise typer.Exit(code=1)

        project = Project(
            slug=slug,
            name=name,
            type_id=pt.id,
            status_id=ps.id,
            priority=priority,
            one_line_summary=one_line,
            description=description,
            git_repo_url=git_repo_url,
            local_path=local_path,
        )
        session.add(project)
        session.commit()

        console.print(f"[green]✓ Создан проект '{slug}' (id={project.id})[/green]")


@portfolio_app.command("show")
def show_cmd(slug: str = typer.Argument(..., help="Slug проекта")) -> None:
    """Показать карточку проекта."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        row = session.execute(
            select(Project, ProjectType.slug, ProjectStatus.slug)
            .join(ProjectType, Project.type_id == ProjectType.id)
            .join(ProjectStatus, Project.status_id == ProjectStatus.id)
            .where(Project.slug == slug)
        ).first()

        if row is None:
            console.print(f"[red]Проект '{slug}' не найден.[/red]")
            raise typer.Exit(code=1)

        project, type_slug, status_slug = row
        console.print(f"[bold cyan]{project.slug}[/bold cyan]  — {project.name}")
        console.print(f"  type:     {type_slug}")
        console.print(f"  status:   {status_slug}")
        console.print(f"  priority: {project.priority}")
        console.print(f"  summary:  {project.one_line_summary}")
        if project.description:
            console.print(f"  description: {project.description}")
        if project.git_repo_url:
            console.print(f"  git:      {project.git_repo_url}")
        if project.local_path:
            console.print(f"  path:     {project.local_path}")
        if project.notion_project_id:
            console.print(f"  notion:   {project.notion_project_id}")
        if project.b24_company_id:
            console.print(f"  b24:      {project.b24_company_id}")
        console.print(f"  created:  {project.created_at}")
        console.print(f"  updated:  {project.updated_at}")


@portfolio_app.command("types")
def types_cmd() -> None:
    """Список справочника project_types."""
    url = _db_url()
    engine = make_engine(url)
    with make_session(engine) as session:
        rows = session.execute(
            select(ProjectType).order_by(ProjectType.name)
        ).scalars().all()

    table = Table(title="Project Types")
    table.add_column("slug", style="cyan")
    table.add_column("name")
    table.add_column("description", overflow="fold")
    for t in rows:
        table.add_row(t.slug, t.name, t.description or "")
    console.print(table)


@portfolio_app.command("statuses")
def statuses_cmd() -> None:
    """Список справочника project_statuses."""
    url = _db_url()
    engine = make_engine(url)
    with make_session(engine) as session:
        rows = session.execute(
            select(ProjectStatus).order_by(ProjectStatus.order_idx)
        ).scalars().all()

    table = Table(title="Project Statuses")
    table.add_column("#", justify="right")
    table.add_column("slug", style="cyan")
    table.add_column("name")
    table.add_column("description", overflow="fold")
    for s in rows:
        table.add_row(str(s.order_idx), s.slug, s.name, s.description or "")
    console.print(table)
