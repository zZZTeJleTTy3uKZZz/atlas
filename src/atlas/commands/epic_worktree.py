"""CLI `atlas epic worktree …` — изолированные git worktree под эпик (#300).

Цикл работы над эпиком в отдельной ветке/дереве:
- ``create <epic> [--base --path]`` — завести worktree + ветку ``epic/<slug>``
  в репозитории проекта эпика;
- ``list [<epic>]``                — worktree'ы репо (или конкретного эпика);
- ``merge <epic> [--into --push --remove]`` — влить ветку эпика в base (после
  приёмки) — безопасно (основной репо чист + на base);
- ``remove <epic> [--force]``     — снять worktree.

Состояние worktree держит git (ветка детерминирована из slug), отдельной схемы
в БД нет. Регистрируется импортом модуля (см. ``atlas/cli.py``): добавляет
sub-typer ``worktree`` к ``epic_app``. autobackup веток эпика покрывает штатный
``atlas backup`` (это обычные ветки того же репо).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer
from clikit import CliError, command, emit_data, emit_table
from rich.console import Console

from atlas import epic_worktree as W
from atlas.commands.epic import epic_app
from atlas.commands.task import _resolve_epic_or_die
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import Epic, Project

console = Console()

worktree_app = typer.Typer(
    no_args_is_help=True,
    help="Git worktree-циклы под эпик: create / list / merge / remove.",
)
epic_app.add_typer(worktree_app, name="worktree")


def _epic_repo(session, ref: str) -> tuple[Epic, Project, Path, str]:
    """Резолв эпика → (epic, project, repo_path, base_branch). Чистые ошибки."""
    epic = _resolve_epic_or_die(session, ref)
    if not epic.slug:
        raise CliError(
            "no_slug",
            f"У эпика '{epic.title}' нет slug — worktree-ветка не детерминирована. "
            f"Задай slug: atlas epic update <ref> --slug <kebab>.",
        )
    project = session.get(Project, epic.project_id)
    if project is None:
        raise CliError("broken_data", "Проект эпика не найден.")
    if not project.local_path:
        raise CliError(
            "no_local_path",
            f"У проекта '{project.slug}' не задан local_path — негде делать worktree.",
        )
    repo = Path(project.local_path)
    if not (repo / ".git").exists():
        raise CliError(
            "no_git",
            f"В {repo} нет .git/ — инициализируй: atlas project git init {project.slug}.",
        )
    base = project.git_default_branch or "main"
    return epic, project, repo, base


@worktree_app.command("create")
@command
def create_cmd(
    ref: str = typer.Argument(..., help="slug | UUID эпика"),
    base: Optional[str] = typer.Option(
        None, "--base", help="Базовая ветка (default: git_default_branch проекта)."
    ),
    path: Optional[str] = typer.Option(
        None, "--path", help="Путь worktree (default: <repo>.worktrees/epic-<slug>)."
    ),
) -> None:
    """Создать worktree + ветку ``epic/<slug>`` для эпика."""
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        epic, project, repo, default_base = _epic_repo(session, ref)
        branch = W.epic_branch(epic.slug)
        base_branch = base or default_base
        wt_path = Path(path) if path else W.default_worktree_path(repo, epic.slug)

        existing = W.find_worktree(repo, branch)
        if existing is not None:
            emit_data(
                {"ok": True, "epic": epic.slug, "branch": branch,
                 "path": existing["path"], "created": False},
                text_renderer=lambda d: console.print(
                    f"[yellow]Worktree ветки '{d['branch']}' уже есть:[/yellow] {d['path']}"
                ),
            )
            return
        try:
            res = W.add_worktree(repo, wt_path, branch, base_branch)
        except W.WorktreeError as exc:
            raise CliError("worktree_add_failed", str(exc))

    emit_data(
        {"ok": True, "epic": epic.slug, "branch": res["branch"],
         "path": res["path"], "base": res["base"], "created": True},
        text_renderer=lambda d: (
            console.print(f"[green]✓ Worktree создан для эпика '{d['epic']}'[/green]"),
            console.print(f"  Ветка: {d['branch']} (от {d['base']})"),
            console.print(f"  Путь:  {d['path']}"),
            console.print(f"  [dim]cd {d['path']}  # работай тут; потом: "
                          f"atlas epic worktree merge {d['epic']}[/dim]"),
        ),
    )


@worktree_app.command("list")
@command
def list_cmd(
    ref: Optional[str] = typer.Argument(
        None, help="slug | UUID эпика (без аргумента — все worktree'ы его репо)."
    ),
) -> None:
    """Показать worktree'ы (репозитория эпика или все epic/* при наличии ref)."""
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        if ref is None:
            raise CliError(
                "ref_required",
                "Укажи эпик (slug|UUID) — worktree'ы привязаны к репо его проекта.",
            )
        epic, project, repo, _base = _epic_repo(session, ref)
        try:
            worktrees = W.list_worktrees(repo)
        except W.WorktreeError as exc:
            raise CliError("worktree_list_failed", str(exc))

    rows = [
        {"branch": wt.get("branch") or "(detached)",
         "epic": "★" if wt.get("is_epic") else "",
         "path": wt.get("path"),
         "head": (wt.get("head") or "")[:10]}
        for wt in worktrees
    ]
    emit_table(
        rows,
        title=f"Worktrees — {project.slug} ({len(rows)})",
        columns=[
            {"key": "branch", "header": "branch", "style": "magenta"},
            {"key": "epic", "header": "epic", "justify": "center"},
            {"key": "path", "header": "path", "style": "dim"},
            {"key": "head", "header": "head", "style": "grey62"},
        ],
        empty_message="[yellow]Worktree'ов нет.[/yellow]",
    )


@worktree_app.command("merge")
@command
def merge_cmd(
    ref: str = typer.Argument(..., help="slug | UUID эпика"),
    into: Optional[str] = typer.Option(
        None, "--into", help="Целевая ветка (default: git_default_branch проекта)."
    ),
    ff: bool = typer.Option(
        False, "--ff/--no-ff", help="Fast-forward merge (default: --no-ff, merge-commit)."
    ),
    push: bool = typer.Option(
        False, "--push", help="После merge — git push origin <into>."
    ),
    remove: bool = typer.Option(
        False, "--remove", help="После merge снять worktree эпика."
    ),
) -> None:
    """Влить ветку эпика в base (после приёмки). Безопасно: репо чист + на base."""
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        epic, project, repo, default_base = _epic_repo(session, ref)
        branch = W.epic_branch(epic.slug)
        target = into or default_base
        try:
            res = W.merge_into(repo, branch, target, no_ff=not ff, push=push)
            removed = False
            if remove:
                wt = W.find_worktree(repo, branch)
                if wt is not None:
                    W.remove_worktree(repo, wt["path"], force=True)
                    removed = True
            res["removed"] = removed
        except W.WorktreeError as exc:
            raise CliError("worktree_merge_failed", str(exc))

    emit_data(
        {"ok": True, "epic": epic.slug, **res},
        text_renderer=lambda d: (
            console.print(
                f"[green]✓ '{d['branch']}' влита в '{d['into']}'[/green]"
                + (" · pushed" if d.get("pushed") else "")
                + (" · worktree снят" if d.get("removed") else "")
            ),
        ),
    )


@worktree_app.command("remove")
@command
def remove_cmd(
    ref: str = typer.Argument(..., help="slug | UUID эпика"),
    force: bool = typer.Option(
        False, "--force", help="Снять даже с незакоммиченными изменениями."
    ),
) -> None:
    """Снять worktree эпика (ветка остаётся; merge не делает)."""
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        epic, project, repo, _base = _epic_repo(session, ref)
        branch = W.epic_branch(epic.slug)
        wt = W.find_worktree(repo, branch)
        if wt is None:
            raise CliError(
                "not_found", f"Worktree ветки '{branch}' не найден."
            )
        try:
            W.remove_worktree(repo, wt["path"], force=force)
        except W.WorktreeError as exc:
            raise CliError("worktree_remove_failed", str(exc))
        path = wt["path"]

    emit_data(
        {"ok": True, "epic": epic.slug, "branch": branch, "path": path,
         "removed": True},
        text_renderer=lambda d: console.print(
            f"[green]✓ Worktree '{d['branch']}' снят[/green] [dim]({d['path']})[/dim]"
        ),
    )
