"""CLI-команды `atlas projects git ...` — Git/GitLab integration.

Sub-typer `git_app`, регистрируется в `projects.py`:
    projects_app.add_typer(git_app, name="git")

Команды:
- ``init <ref>``      — инициализировать локальный repo, создать remote в GitLab,
                        запушить, обновить БД (URL, provider, branch, timestamps).
- ``status <ref>``    — таблица remote/local состояния (URL, branch, dirty, ahead/behind).
- ``push <ref>``      — `git push -u origin <branch>` + update last_pushed_at.
- ``link <ref>``      — привязать существующий GitLab repo (без create_remote/push).
- ``move <ref>``      — `glab repo transfer` + локальный `git remote set-url`.
- ``status-all``      — массовый обзор (с фильтрами по type/status/tag).
- ``sync-from-remote``— проверить, не сменился ли URL у каждого проекта в GitLab,
                        и обновить БД (с `--dry-run` — только показать diff).

Принципы:
- Все subprocess (`git`/`glab`) идут через ``atlas.git_backend.run`` —
  тестируются мокингом одной функции, не реальные процессы.
- `resolve_project_ref` — единый способ найти проект по slug/UUID/short.
- ошибки → ``typer.Exit(code=1)`` + понятное сообщение в console.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import typer
from clikit import command, emit_data, emit_table, is_json
from rich.console import Console
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas._time import local_now
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.git_backend import GitLabBackend, LocalGitOps
from atlas.git_paths import derive_group_path
from atlas.models import (
    ActionLog,
    Participant,
    Project,
    ProjectStatus,
    ProjectType,
    Tag,
)
from atlas.slugs import AmbiguousRefError, resolve_project_ref
from atlas.tags import (
    AmbiguousTagRefError,
    InvalidTagCategoryError,
    filter_projects_by_tags,
    list_project_tags,
    resolve_tag_ref,
)

git_app = typer.Typer(
    no_args_is_help=True,
    help="Git/GitLab integration: init / status / push / link / move / status-all / sync-from-remote.",
)
console = Console()

DEFAULT_ACTOR_SLUG = "dmitry"
DEFAULT_COMMIT_MESSAGE = "feat: initial baseline (atlas-managed bootstrap)"
URL_RE = re.compile(r"^(https?|git|ssh)://|^git@", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# DB helpers (повторяют projects.py — намеренно, чтобы не плодить циркуляры) #
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


def _resolve_project_or_die(session: Session, ref: str) -> Project:
    try:
        project = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if project is None:
        console.print(f"[red]Project '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return project


def _project_owner_tags(session: Session, project_id: str) -> list[str]:
    """Список slug owner-тегов проекта (для derive_group_path)."""
    tags = list_project_tags(session, project_id)
    return [t.slug for t in tags if t.category == "owner"]


def _resolve_tags_or_die(session: Session, tag_refs: list[str]) -> list[Tag]:
    resolved: list[Tag] = []
    for ref in tag_refs:
        try:
            tag = resolve_tag_ref(session, ref)
        except (AmbiguousTagRefError, InvalidTagCategoryError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        if tag is None:
            console.print(f"[red]Tag '{ref}' не найден.[/red]")
            raise typer.Exit(code=1)
        resolved.append(tag)
    return resolved


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #


def perform_git_init(
    session: Session,
    project: Project,
    *,
    group: Optional[str] = None,
    private: bool = True,
    commit_message: str = DEFAULT_COMMIT_MESSAGE,
    log_action_fn=None,
) -> dict[str, Any]:
    """Полная git-init для проекта: local init + GitLab create + push + БД.

    Извлечено из ``init_cmd`` для повторного использования из
    ``atlas projects add --init-git``. Caller'у возвращается dict с
    ``{url, group_path, branch}``. Вся error-handling — через
    ``RuntimeError`` (caller сам конвертит в typer.Exit или своё).

    Все W45-32a/b/j/k фиксы тут унифицированы:
      - ``GitLabBackend.create_remote`` — через ``glab api POST projects``.
      - ``LocalGitOps.add_remote`` — idempotent (set-url если remote есть).
      - ``project.git_remote_url`` И legacy ``git_repo_url`` обновляются
        синхронно (W45-32k).

    NOTE: caller должен вызвать ``session.commit()`` после возврата.
    """
    if not project.local_path:
        raise RuntimeError(
            f"Project '{project.slug}': local_path не задан"
        )
    local = Path(project.local_path)
    if not local.exists():
        raise RuntimeError(
            f"Project local_path не существует: {local}"
        )
    if project.git_remote_url:
        raise RuntimeError(
            f"У '{project.slug}' уже есть git_remote_url: "
            f"{project.git_remote_url}. Используй `link` или `push`."
        )

    if group is None:
        pt = session.get(ProjectType, project.type_id)
        ps = session.get(ProjectStatus, project.status_id)
        if pt is None or ps is None:
            raise RuntimeError("Broken data: type_id или status_id не найден")
        owner_slugs = _project_owner_tags(session, project.id)
        group_path = derive_group_path(
            pt.slug, ps.slug, project.archived_group,
            owner_tags=owner_slugs,
        )
    else:
        group_path = group

    local_ops = LocalGitOps()
    if not (local / ".git").exists():
        local_ops.init(local)
    # ВАЖНО: set_default_branch ДО первого commit, иначе HEAD будет на
    # `master` и ни одна refspec на `main` не пройдёт push (W45-32n).
    local_ops.set_default_branch(local, "main")
    # commit может упасть если staged пуст — это OK для каллера, который
    # уже создал хотя бы один файл (canonical README).
    local_ops.add_all_commit(local, commit_message)

    backend = GitLabBackend()
    url = backend.create_remote(group_path, project.slug, private=private)

    # add_remote теперь idempotent (W45-32b).
    local_ops.add_remote(local, "origin", url)
    local_ops.push(local, branch="main")

    now = local_now()
    project.git_remote_url = url
    project.git_repo_url = url  # W45-32k: sync legacy field too
    project.git_default_branch = "main"
    project.git_provider = "gitlab"
    project.git_initialized_at = now
    project.git_last_pushed_at = now
    project.last_touched_at = now

    if log_action_fn is not None:
        log_action_fn(
            session,
            action="project_git_initialized",
            entity_id=project.id,
            details={
                "url": url,
                "group": group_path,
                "private": private,
            },
        )

    return {"url": url, "group_path": group_path, "branch": "main"}


@git_app.command("init")
@command
def init_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix проекта"),
    provider: str = typer.Option("gitlab", "--provider", help="git host provider (gitlab|github)"),
    private: bool = typer.Option(True, "--private/--public", help="видимость репо"),
    group: Optional[str] = typer.Option(
        None, "--group",
        help="GitLab group path (если опущен — derive по type/status/tags).",
    ),
    commit_message: str = typer.Option(
        DEFAULT_COMMIT_MESSAGE, "--commit-message",
        help="Сообщение initial коммита.",
    ),
) -> None:
    """Инициализировать git-репо проекта: local init + GitLab create + push."""
    if provider != "gitlab":
        console.print(f"[red]Provider '{provider}' пока не поддерживается (только gitlab).[/red]")
        raise typer.Exit(code=1)

    engine = make_engine(_db_url())
    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)
        try:
            result = perform_git_init(
                session, project,
                group=group,
                private=private,
                commit_message=commit_message,
                log_action_fn=_log_action,
            )
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        session.commit()

    emit_data(
        {
            "ok": True,
            "slug": project.slug,
            "url": result["url"],
            "branch": result["branch"],
            "group": result["group_path"],
        },
        text_renderer=lambda d: (
            console.print(f"[green]✓ Git initialized for '{d['slug']}'[/green]"),
            console.print(f"  URL:     {d['url']}"),
            console.print(f"  Branch:  {d['branch']}"),
            console.print(f"  Group:   {d['group']}"),
        ),
    )


# --------------------------------------------------------------------------- #
# status                                                                      #
# --------------------------------------------------------------------------- #


@git_app.command("status")
@command
def status_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
) -> None:
    """Показать состояние remote и local репозитория проекта."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)

        url = project.git_remote_url
        branch = project.git_default_branch
        last_pushed = project.git_last_pushed_at
        initialized = project.git_initialized_at
        local_status: Optional[dict[str, Any]] = None
        warning: Optional[str] = None

        if project.local_path:
            local = Path(project.local_path)
            if (local / ".git").exists():
                try:
                    local_status = LocalGitOps().status(local)
                except RuntimeError as exc:
                    warning = f"git status failed: {exc}"
            else:
                warning = (
                    f"Локальный репо не инициализирован (нет .git/) "
                    f"в {local}."
                )
        else:
            warning = "local_path не задан."

    data: dict[str, Any] = {
        "slug": project.slug,
        "url": url,
        "default_branch": branch,
        "initialized_at": initialized.isoformat() if initialized else None,
        "last_pushed_at": last_pushed.isoformat() if last_pushed else None,
        "local": None,
        "warning": warning,
    }
    if local_status is not None:
        data["local"] = {
            "branch": local_status.get("branch"),
            "dirty": bool(local_status.get("dirty")),
            "last_sha": local_status.get("last_sha"),
            "ahead": int(local_status.get("ahead", 0)),
            "behind": int(local_status.get("behind", 0)),
        }

    def _render(d: dict[str, Any]) -> None:
        from rich.table import Table

        table = Table(title=f"Git status — {d['slug']}")
        table.add_column("Field", style="cyan", no_wrap=True)
        table.add_column("Value", style="bold")
        table.add_row("URL", d["url"] or "—")
        table.add_row("Default branch", d["default_branch"] or "—")
        table.add_row(
            "Initialized at",
            initialized.strftime("%Y-%m-%d %H:%M") if initialized else "—",
        )
        table.add_row(
            "Last pushed at",
            last_pushed.strftime("%Y-%m-%d %H:%M") if last_pushed else "—",
        )
        local = d["local"]
        if local is not None:
            table.add_row("Local branch", str(local.get("branch") or "—"))
            table.add_row("Dirty", "yes" if local.get("dirty") else "no")
            table.add_row("Last SHA", str(local.get("last_sha") or "—"))
            table.add_row(
                "Ahead/behind",
                f"+{local.get('ahead', 0)} / -{local.get('behind', 0)}",
            )
        console.print(table)
        if d["warning"]:
            console.print(f"[yellow]⚠ {d['warning']}[/yellow]")

    emit_data(data, text_renderer=_render)


# --------------------------------------------------------------------------- #
# push                                                                        #
# --------------------------------------------------------------------------- #


@git_app.command("push")
@command
def push_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
) -> None:
    """`git push -u origin <branch>` + обновить git_last_pushed_at."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)

        if not project.local_path:
            console.print(
                f"[red]Project '{project.slug}': local_path не задан.[/red]"
            )
            raise typer.Exit(code=1)
        local = Path(project.local_path)
        if not local.exists():
            console.print(
                f"[red]local_path не существует: {local}.[/red]"
            )
            raise typer.Exit(code=1)

        branch = project.git_default_branch or "main"

        try:
            LocalGitOps().push(local, branch=branch)
        except RuntimeError as exc:
            console.print(f"[red]Push failed: {exc}[/red]")
            raise typer.Exit(code=1)

        now = local_now()
        project.git_last_pushed_at = now
        project.last_touched_at = now

        _log_action(
            session,
            action="project_git_pushed",
            entity_id=project.id,
            details={"branch": branch},
        )
        session.commit()

    emit_data(
        {"ok": True, "slug": project.slug, "branch": branch},
        text_renderer=lambda d: console.print(
            f"[green]✓ Pushed '{d['slug']}' to origin/{d['branch']}[/green]"
        ),
    )


# --------------------------------------------------------------------------- #
# link                                                                        #
# --------------------------------------------------------------------------- #


@git_app.command("link")
@command
def link_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
    url: str = typer.Option(..., "--url", help="URL уже существующего репо"),
    branch: str = typer.Option("main", "--branch", help="Default branch"),
    provider: str = typer.Option("gitlab", "--provider"),
) -> None:
    """Привязать существующий remote к проекту (без create / push)."""
    if not URL_RE.search(url):
        console.print(
            f"[red]Невалидный URL '{url}': ожидается http(s)://… или git@…[/red]"
        )
        raise typer.Exit(code=1)
    if provider not in ("gitlab", "github"):
        console.print(f"[red]Provider '{provider}' не поддерживается.[/red]")
        raise typer.Exit(code=1)

    engine = make_engine(_db_url())
    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)

        if not project.local_path:
            console.print(
                f"[red]Project '{project.slug}': local_path не задан.[/red]"
            )
            raise typer.Exit(code=1)
        local = Path(project.local_path)
        if not (local / ".git").exists():
            console.print(
                f"[red]В {local} нет .git/ — сначала `git init` или используй "
                f"`atlas projects git init` (новое создание).[/red]"
            )
            raise typer.Exit(code=1)

        try:
            LocalGitOps().add_remote(local, "origin", url)
        except RuntimeError as exc:
            console.print(f"[red]git remote add failed: {exc}[/red]")
            raise typer.Exit(code=1)

        now = local_now()
        project.git_remote_url = url
        project.git_default_branch = branch
        project.git_provider = provider
        project.git_initialized_at = now
        project.git_last_pushed_at = None
        project.last_touched_at = now

        _log_action(
            session,
            action="project_git_linked",
            entity_id=project.id,
            details={
                "url": url,
                "branch": branch,
                "provider": provider,
            },
        )
        session.commit()

    emit_data(
        {
            "ok": True,
            "slug": project.slug,
            "url": url,
            "branch": branch,
            "provider": provider,
        },
        text_renderer=lambda d: (
            console.print(
                f"[green]✓ Linked '{d['slug']}' to existing remote[/green]"
            ),
            console.print(f"  URL:    {d['url']}"),
            console.print(f"  Branch: {d['branch']}"),
        ),
    )


# --------------------------------------------------------------------------- #
# move                                                                        #
# --------------------------------------------------------------------------- #


def _repo_full_path_from_url(url: str) -> str:
    """Извлечь group/.../repo путь из URL.

    Поддерживает:
        https://gitlab.com/cifropro1/clients/cifro      -> cifropro1/clients/cifro
        https://gitlab.com/cifropro1/clients/cifro.git  -> cifropro1/clients/cifro
        git@gitlab.com:cifropro1/clients/cifro.git      -> cifropro1/clients/cifro
    """
    s = url.strip()
    # SSH-style git@host:group/repo
    if s.startswith("git@") and ":" in s:
        s = s.split(":", 1)[1]
    elif "://" in s:
        # http(s)://host/group/repo[.git]
        _, rest = s.split("://", 1)
        if "/" in rest:
            s = rest.split("/", 1)[1]
    if s.endswith(".git"):
        s = s[: -len(".git")]
    return s.strip("/")


@git_app.command("move")
@command
def move_cmd(
    ref: str = typer.Argument(..., help="slug | UUID проекта"),
    to_group: str = typer.Option(..., "--to-group", help="Новая GitLab group path"),
) -> None:
    """Перенести GitLab репо в другую группу + обновить локальный remote и БД."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        project = _resolve_project_or_die(session, ref)

        if not project.git_remote_url:
            console.print(
                f"[red]У '{project.slug}' нет git_remote_url — нечего двигать.[/red]"
            )
            raise typer.Exit(code=1)

        old_url = project.git_remote_url
        repo_full_path = _repo_full_path_from_url(old_url)

        backend = GitLabBackend()
        try:
            new_url = backend.transfer_to_group(repo_full_path, to_group)
        except RuntimeError as exc:
            console.print(f"[red]glab transfer failed: {exc}[/red]")
            raise typer.Exit(code=1)

        # обновить локальный origin (если репо есть)
        if project.local_path:
            local = Path(project.local_path)
            if (local / ".git").exists():
                # `git remote set-url origin <url>` выполняем через ту же run().
                from atlas.git_backend import run as run_cmd
                rc, _, err = run_cmd(
                    ["git", "remote", "set-url", "origin", new_url], cwd=local,
                )
                if rc != 0:
                    console.print(
                        f"[yellow]⚠ Локальный remote set-url failed: {err}. "
                        f"Обнови вручную: git remote set-url origin {new_url}[/yellow]"
                    )

        project.git_remote_url = new_url
        project.last_touched_at = local_now()

        _log_action(
            session,
            action="project_git_moved",
            entity_id=project.id,
            details={
                "old_url": old_url,
                "new_url": new_url,
                "to_group": to_group,
            },
        )
        session.commit()

    emit_data(
        {
            "ok": True,
            "slug": project.slug,
            "old_url": old_url,
            "new_url": new_url,
            "to_group": to_group,
        },
        text_renderer=lambda d: console.print(
            f"[green]✓ Project '{d['slug']}' moved → {d['new_url']}[/green]"
        ),
    )


# --------------------------------------------------------------------------- #
# status-all                                                                  #
# --------------------------------------------------------------------------- #


@git_app.command("status-all")
@command
def status_all_cmd(
    type_slug: Optional[str] = typer.Option(None, "--type"),
    status_slug: Optional[str] = typer.Option(None, "--status"),
    tags: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="AND-фильтр по тегам.",
    ),
) -> None:
    """Сводная таблица: slug | URL | branch | dirty | last_pushed."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        # Опционально применяем тег-фильтр.
        candidate_ids: Optional[set[str]] = None
        if tags:
            resolved = _resolve_tags_or_die(session, tags)
            slugs = [t.slug for t in resolved]
            matching = filter_projects_by_tags(session, slugs, archived=True)
            candidate_ids = {p.id for p in matching}
            if not candidate_ids:
                emit_table([], empty_message="[yellow]Проектов не найдено.[/yellow]")
                return

        stmt = (
            select(Project, ProjectType.slug, ProjectStatus.slug)
            .join(ProjectType, Project.type_id == ProjectType.id)
            .join(ProjectStatus, Project.status_id == ProjectStatus.id)
            .where(Project.archived_at.is_(None))
            .order_by(Project.slug)
        )
        if type_slug:
            stmt = stmt.where(ProjectType.slug == type_slug)
        if status_slug:
            stmt = stmt.where(ProjectStatus.slug == status_slug)
        if candidate_ids is not None:
            stmt = stmt.where(Project.id.in_(candidate_ids))

        rows = session.execute(stmt).all()

        # Local-status: для каждого проекта пробуем `git status` если .git есть.
        table_rows: list[dict[str, Any]] = []
        for proj, _t_slug, _s_slug in rows:
            entry: dict[str, Any] = {
                "slug": proj.slug,
                "url": proj.git_remote_url,
                "branch": proj.git_default_branch,
                "dirty": None,
                "last_pushed": (
                    proj.git_last_pushed_at.strftime("%Y-%m-%d %H:%M")
                    if proj.git_last_pushed_at else None
                ),
            }
            if proj.local_path:
                local = Path(proj.local_path)
                if (local / ".git").exists():
                    try:
                        st = LocalGitOps().status(local)
                        entry["branch"] = st.get("branch") or entry["branch"]
                        entry["dirty"] = "yes" if st.get("dirty") else "no"
                    except RuntimeError:
                        entry["dirty"] = "?"
            table_rows.append(entry)

    emit_table(
        table_rows,
        title=f"Git status-all ({len(table_rows)})",
        columns=[
            {"key": "slug", "header": "slug", "style": "cyan", "no_wrap": True},
            {"key": "url", "header": "URL", "style": "dim"},
            {"key": "branch", "header": "branch", "style": "magenta"},
            {"key": "dirty", "header": "dirty", "justify": "center"},
            {"key": "last_pushed", "header": "last_pushed", "style": "dim"},
        ],
        empty_message="[yellow]Проектов не найдено.[/yellow]",
    )


# --------------------------------------------------------------------------- #
# sync-from-remote                                                            #
# --------------------------------------------------------------------------- #


@git_app.command("sync-from-remote")
@command
def sync_from_remote_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run/--apply",
        help="--dry-run — только показать diff, не применять.",
    ),
) -> None:
    """Сравнить URL'ы в БД с реальными в GitLab; обновить отличающиеся."""
    engine = make_engine(_db_url())
    backend = GitLabBackend()

    actions: list[dict[str, Any]] = []
    with make_session(engine) as session:
        projects = (
            session.execute(
                select(Project).where(Project.git_remote_url.is_not(None))
            )
            .scalars()
            .all()
        )

        for proj in projects:
            stored = proj.git_remote_url or ""
            repo_full_path = _repo_full_path_from_url(stored)
            try:
                info = backend.get_remote_status(repo_full_path)
            except RuntimeError as exc:
                actions.append({
                    "slug": proj.slug,
                    "stored": stored,
                    "remote": None,
                    "action": "error",
                    "reason": str(exc),
                })
                continue

            actual = info.get("web_url") or stored
            if actual != stored:
                actions.append({
                    "slug": proj.slug,
                    "stored": stored,
                    "remote": actual,
                    "action": "update",
                })
            else:
                actions.append({
                    "slug": proj.slug,
                    "stored": stored,
                    "remote": actual,
                    "action": "ok",
                })

        # Render — результат команды (per-project diff) JSON-консистентно.
        emit_table(
            actions,
            title=f"sync-from-remote ({len(actions)} projects)",
            columns=[
                {"key": "slug", "header": "slug", "style": "cyan"},
                {"key": "stored", "header": "stored", "style": "dim"},
                {"key": "remote", "header": "remote", "style": "bold"},
                {"key": "action", "header": "action"},
            ],
            empty_message="sync-from-remote (0 projects)",
        )
        # action+reason — человеку, в text-режиме отдельной строкой к таблице
        # выше не вписать (raw action идёт в json), поэтому в text печатаем
        # причины ошибок дополнительным блоком.
        if not is_json():
            for a in actions:
                if a.get("reason"):
                    console.print(
                        f"  [dim]{a['slug']}: {a['action']} ({a['reason']})[/dim]"
                    )

        if dry_run:
            if not is_json():
                console.print("[yellow]Dry run. Use --apply to write DB.[/yellow]")
            return

        # apply updates
        updated = 0
        for a in actions:
            if a["action"] != "update":
                continue
            proj = session.execute(
                select(Project).where(Project.slug == a["slug"])
            ).scalar_one_or_none()
            if proj is None:
                continue
            old = proj.git_remote_url
            proj.git_remote_url = a["remote"]
            proj.last_touched_at = local_now()
            _log_action(
                session,
                action="project_git_url_synced",
                entity_id=proj.id,
                details={"old_url": old, "new_url": a["remote"]},
            )
            updated += 1

        if updated:
            session.commit()
        if not is_json():
            if updated:
                console.print(f"[green]✓ Updated {updated} project(s).[/green]")
            else:
                console.print("[dim]Нечего обновлять.[/dim]")
