"""CLI-команды `atlas ideas ...` (W45-38).

Idea = `Project` с `entity_kind='idea'`. Живёт как один MD-файл в
`_Ideas/<slug>.md`, без `_storage/<slug>/` и без junction'ов.

Команды:
- ``add``     — создать идею: запись в БД + `_Ideas/<slug>.md` из template.
- ``list``    — список идей (фильтры по type/tag/status).
- ``show``    — карточка БД + содержимое .md.
- ``promote`` — перевести в полноценный проект:
                  entity_kind='project',
                  + setup_layout (`_storage/<slug>/` + junction),
                  + mv MD → `_storage/<slug>/IDEA.md`,
                  + extract_idea_backlog (секцию `### #<slug>` из
                    `_Ideas/BACKLOG.md` → `_storage/<slug>/BACKLOG.md`),
                  + опц. canonical files / git init.
- ``demote``  — обратное (если решили что не время).

Все команды используют существующие atlas helpers из projects.py
(_resolve_tags_or_die, _setup_storage_and_junction и т.п.).
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import typer
from clikit import command, emit_data, emit_table
from rich.console import Console
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm._time import local_now
from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.ideas import (
    ensure_ideas_root,
    extract_idea_backlog,
    render_idea_md,
    render_promoted_backlog,
    write_idea_md,
)
from atlas.pm.models import (
    ActionLog,
    Participant,
    Project,
    ProjectStatus,
    ProjectTag,
    ProjectType,
    Tag,
)
from atlas.pm.paths import IDEAS_FOLDER_NAME, get_projects_root
from atlas.pm.slugs import (
    AmbiguousRefError,
    SlugGenerationError,
    generate_prefix_from_slug,
    generate_unique_slug,
    resolve_project_ref,
    slugify_text,
)
from atlas.pm.tags import (
    AmbiguousTagRefError,
    InvalidTagCategoryError,
    attach_tags,
    list_project_tags,
    resolve_tag_ref,
)


ideas_app = typer.Typer(
    no_args_is_help=True,
    help="Idea management: incubator для idea-stage записей (entity_kind=idea).",
)
console = Console()

DEFAULT_ACTOR_SLUG = "dmitry"


# --------------------------------------------------------------------------- #
# Helpers (упрощённые версии из projects.py — namespace ideas)                #
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


def _resolve_tags_or_die(session: Session, tag_refs: list[str]) -> list[Tag]:
    resolved: list[Tag] = []
    for ref in tag_refs:
        try:
            tag = resolve_tag_ref(session, ref)
        except (AmbiguousTagRefError, InvalidTagCategoryError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        if tag is None:
            console.print(
                f"[red]Tag '{ref}' не найден. "
                f"Создайте: `atlas tags add --slug ... --category ...`.[/red]"
            )
            raise typer.Exit(code=1)
        resolved.append(tag)
    return resolved


def _slug_exists(session: Session, candidate: str) -> bool:
    return session.execute(
        select(Project.id).where(Project.slug == candidate)
    ).scalar_one_or_none() is not None


def _resolve_idea_or_die(session: Session, ref: str) -> Project:
    """Найти Project с entity_kind='idea'."""
    try:
        proj = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if proj is None:
        console.print(f"[red]Idea '{ref}' не найдена.[/red]")
        raise typer.Exit(code=1)
    if proj.entity_kind != "idea":
        console.print(
            f"[red]Project '{proj.slug}' не idea (entity_kind={proj.entity_kind}). "
            f"Используйте `atlas projects` или `atlas inbox` соответственно.[/red]"
        )
        raise typer.Exit(code=1)
    return proj


def _project_owner_tags(session: Session, project_id: str) -> list[str]:
    rows = session.execute(
        select(Tag.slug)
        .join(ProjectTag, ProjectTag.tag_id == Tag.id)
        .where(ProjectTag.project_id == project_id, Tag.category == "owner")
    ).all()
    return [r[0] for r in rows]


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


@ideas_app.command("add")
@command
def add_cmd(
    name: str = typer.Option(..., "--name", help="Название идеи"),
    type_slug: str = typer.Option(
        ..., "--type",
        help="Type-hint: каким типом проекта станет при promote "
             "(business-product / personal-utility / personal-project / "
             "shared-infrastructure / client-project / test).",
    ),
    slug: Optional[str] = typer.Option(None, "--slug"),
    priority: str = typer.Option("P2", "--priority", help="P0|P1|P2|P3"),
    one_line: Optional[str] = typer.Option(None, "--one-line"),
    tags: Optional[list[str]] = typer.Option(
        None, "--tag", "-t", help="Тег: 'slug', 'category:slug' или UUID."
    ),
    status_slug: str = typer.Option(
        "active", "--status",
        help="Статус идеи (default: active). Для отказа — `update --status cancelled`.",
    ),
) -> None:
    """Создать новую идею (entity_kind=idea) + `_Ideas/<slug>.md` из template."""
    if priority not in {"P0", "P1", "P2", "P3"}:
        console.print(f"[red]Невалидный priority '{priority}'.[/red]")
        raise typer.Exit(code=1)

    engine = make_engine(_db_url())
    with make_session(engine) as session:
        pt = session.execute(
            select(ProjectType).where(ProjectType.slug == type_slug)
        ).scalar_one_or_none()
        if pt is None:
            console.print(
                f"[red]Тип '{type_slug}' не найден.[/red]"
            )
            raise typer.Exit(code=1)

        ps = session.execute(
            select(ProjectStatus).where(ProjectStatus.slug == status_slug)
        ).scalar_one_or_none()
        if ps is None:
            console.print(f"[red]Статус '{status_slug}' не найден.[/red]")
            raise typer.Exit(code=1)

        # ----- slug -----
        if slug:
            if _slug_exists(session, slug):
                console.print(f"[red]Slug '{slug}' занят.[/red]")
                raise typer.Exit(code=1)
            final_slug = slug
        else:
            base = slugify_text(name)
            try:
                final_slug = generate_unique_slug(
                    base, lambda s: _slug_exists(session, s)
                )
            except SlugGenerationError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)

        # ----- prefix (auto) -----
        from atlas.pm.commands.projects import _generate_unique_prefix

        base_prefix = generate_prefix_from_slug(final_slug)
        try:
            final_prefix = _generate_unique_prefix(session, base_prefix)
        except SlugGenerationError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        # ----- create -----
        root = get_projects_root()
        ideas_dir = ensure_ideas_root(root)
        local_path = str(ideas_dir / f"{final_slug}.md")

        project = Project(
            slug=final_slug,
            prefix=final_prefix,
            name=name,
            type_id=pt.id,
            status_id=ps.id,
            priority=priority,
            one_line_summary=one_line or "",
            entity_kind="idea",
            local_path=local_path,
        )
        session.add(project)
        session.flush()

        # ----- tags -----
        tag_slugs_for_log: list[str] = []
        if tags:
            resolved_tags = _resolve_tags_or_die(session, tags)
            tag_slugs_for_log = [t.slug for t in resolved_tags]
            attach_tags(session, project.id, [t.id for t in resolved_tags])

        # ----- write _Ideas/<slug>.md -----
        owner_tags = [t for t in tag_slugs_for_log if t in {"dmitry", "cifro-pro"}]
        content = render_idea_md(
            name=name,
            slug=final_slug,
            type_slug=type_slug,
            priority=priority,
            status_slug=status_slug,
            one_line=one_line or "",
            owner_tags=owner_tags,
            all_tags=tag_slugs_for_log,
        )
        try:
            md_path = write_idea_md(ideas_dir, final_slug, content)
        except FileExistsError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)

        _log_action(
            session,
            action="idea_created",
            entity_id=project.id,
            details={
                "slug": final_slug,
                "type": type_slug,
                "priority": priority,
                "status": status_slug,
                "tags": tag_slugs_for_log,
            },
        )
        session.commit()

    def _render(d: dict[str, Any]) -> None:
        console.print(f"[green]✓ Idea '{d['slug']}' created[/green]")
        console.print(f"  Name:     {d['name']}")
        console.print(f"  Type:     {d['type']}")
        console.print(f"  Priority: {d['priority']}")
        console.print(f"  Status:   {d['status']}")
        console.print(f"  Path:     {d['path']}")

    emit_data(
        {
            "slug": final_slug,
            "name": name,
            "type": type_slug,
            "priority": priority,
            "status": status_slug,
            "path": str(md_path),
        },
        text_renderer=_render,
    )


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


@ideas_app.command("list")
@command
def list_cmd(
    type_slug: Optional[str] = typer.Option(None, "--type"),
    status_slug: Optional[str] = typer.Option(None, "--status"),
    tags: Optional[list[str]] = typer.Option(None, "--tag", "-t"),
) -> None:
    """Список идей (entity_kind='idea')."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        stmt = select(Project).where(Project.entity_kind == "idea")
        if type_slug:
            pt = session.execute(
                select(ProjectType).where(ProjectType.slug == type_slug)
            ).scalar_one_or_none()
            if pt:
                stmt = stmt.where(Project.type_id == pt.id)
        if status_slug:
            ps = session.execute(
                select(ProjectStatus).where(ProjectStatus.slug == status_slug)
            ).scalar_one_or_none()
            if ps:
                stmt = stmt.where(Project.status_id == ps.id)

        ideas = list(session.execute(stmt).scalars().all())

        # AND-фильтр по тегам.
        if tags:
            from atlas.pm.tags import filter_projects_by_tags

            resolved = _resolve_tags_or_die(session, tags)
            tag_slugs = [t.slug for t in resolved]
            allowed_set = {p.id for p in filter_projects_by_tags(
                session, tag_slugs, archived=False
            )}
            ideas = [p for p in ideas if p.id in allowed_set]

        data: list[dict[str, Any]] = []
        for p in ideas:
            pt = session.get(ProjectType, p.type_id)
            ps = session.get(ProjectStatus, p.status_id)
            tag_slugs = [t.slug for t in list_project_tags(session, p.id)]
            data.append({
                "slug": p.slug,
                "name": p.name,
                "type": pt.slug if pt else "?",
                "status": ps.slug if ps else "?",
                "priority": p.priority,
                "tags": tag_slugs,
            })

        emit_table(
            data,
            columns=[
                ("slug", "Slug"),
                ("name", "Name"),
                ("type", "Type"),
                ("status", "Status"),
                ("priority", "P"),
                {"key": "tags", "header": "Tags",
                 "format": lambda v: ", ".join(v) if v else "—"},
            ],
            title=f"Ideas ({len(data)})",
            empty_message="[dim]Идей не найдено.[/dim]",
        )


# --------------------------------------------------------------------------- #
# show                                                                        #
# --------------------------------------------------------------------------- #


@ideas_app.command("show")
@command
def show_cmd(
    ref: str = typer.Argument(..., help="slug | UUID идеи"),
    no_md: bool = typer.Option(False, "--no-md", help="Не печатать содержимое .md"),
) -> None:
    """Карточка идеи: метаданные БД + содержимое `_Ideas/<slug>.md`."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        proj = _resolve_idea_or_die(session, ref)
        pt = session.get(ProjectType, proj.type_id)
        ps = session.get(ProjectStatus, proj.status_id)
        tags = list_project_tags(session, proj.id)

        md_content: Optional[str] = None
        md_missing: Optional[str] = None
        if not no_md and proj.local_path:
            path = Path(proj.local_path)
            if path.exists():
                md_content = path.read_text(encoding="utf-8")
            else:
                md_missing = str(path)

        data = {
            "slug": proj.slug,
            "name": proj.name,
            "kind": "idea",
            "type": pt.slug if pt else None,
            "status": ps.slug if ps else None,
            "priority": proj.priority,
            "created": f"{proj.created_at:%Y-%m-%d}",
            "path": proj.local_path,
            "tags": [t.slug for t in tags],
            "md_content": md_content,
            "md_missing": md_missing,
        }

    def _render(d: dict[str, Any]) -> None:
        console.print(f"[bold]{d['slug']}[/bold]  — {d['name']}")
        console.print(f"  Kind:     idea")
        console.print(f"  Type:     {d['type'] or '—'}")
        console.print(f"  Status:   {d['status'] or '—'}")
        console.print(f"  Priority: {d['priority']}")
        console.print(f"  Created:  {d['created']}")
        console.print(f"  Path:     {d['path']}")
        if d["tags"]:
            console.print(f"  Tags:     {', '.join(d['tags'])}")
        if d["md_content"] is not None:
            console.print("\n[bold]--- _Ideas/<slug>.md ---[/bold]\n")
            console.print(d["md_content"])
        elif d["md_missing"] is not None:
            console.print(f"\n[yellow]⚠ MD не найден: {d['md_missing']}[/yellow]")

    emit_data(data, text_renderer=_render)


# --------------------------------------------------------------------------- #
# promote                                                                     #
# --------------------------------------------------------------------------- #


@ideas_app.command("promote")
@command
def promote_cmd(
    ref: str = typer.Argument(..., help="slug идеи"),
    status_slug: str = typer.Option(
        "active", "--status",
        help="Целевой status проекта (default: active).",
    ),
    priority: Optional[str] = typer.Option(
        None, "--priority", help="Поменять priority при promote (опционально)."
    ),
    canonical: bool = typer.Option(
        True, "--canonical/--no-canonical",
        help="Дописать README/AGENTS/.gitignore в _storage/<slug>/.",
    ),
    init_git: bool = typer.Option(
        False, "--init-git/--no-init-git",
        help="После promote — git init + GitLab create + push.",
    ),
    private: bool = typer.Option(True, "--private/--public"),
    group: Optional[str] = typer.Option(None, "--group"),
) -> None:
    """Идея → проект: entity_kind, layout, MD→IDEA.md, extract backlog, опц. git."""
    from atlas.pm.commands.projects import (
        _create_canonical_files,
        _setup_storage_and_junction,
    )

    if priority is not None and priority not in {"P0", "P1", "P2", "P3"}:
        console.print(f"[red]Невалидный priority '{priority}'.[/red]")
        raise typer.Exit(code=1)

    engine = make_engine(_db_url())
    with make_session(engine) as session:
        proj = _resolve_idea_or_die(session, ref)
        pt = session.get(ProjectType, proj.type_id)
        if pt is None:
            console.print("[red]Broken data: type не найден.[/red]")
            raise typer.Exit(code=1)
        type_slug = pt.slug

        ps_target = session.execute(
            select(ProjectStatus).where(ProjectStatus.slug == status_slug)
        ).scalar_one_or_none()
        if ps_target is None:
            console.print(f"[red]Статус '{status_slug}' не найден.[/red]")
            raise typer.Exit(code=1)

        # 1. Setup storage + junction.
        try:
            logical, storage, junction_created = _setup_storage_and_junction(
                proj.slug, type_slug,
            )
        except Exception as exc:
            console.print(f"[red]setup_layout failed: {exc}[/red]")
            raise typer.Exit(code=1)

        # 2. Move _Ideas/<slug>.md → _storage/<slug>/IDEA.md.
        idea_md_path: Optional[Path] = None
        if proj.local_path and Path(proj.local_path).exists():
            idea_md_path = Path(proj.local_path)
            target_md = storage / "IDEA.md"
            target_md.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(idea_md_path), str(target_md))

        # 3. Extract idea backlog section → _storage/<slug>/BACKLOG.md.
        root = get_projects_root()
        ideas_backlog = root / IDEAS_FOLDER_NAME / "BACKLOG.md"
        backlog_extracted = False
        if ideas_backlog.exists():
            text = ideas_backlog.read_text(encoding="utf-8")
            extracted, remaining = extract_idea_backlog(text, proj.slug)
            if extracted:
                date_str = datetime.now().strftime("%Y-%m-%d")
                target_backlog = storage / "BACKLOG.md"
                if not target_backlog.exists():
                    target_backlog.write_text(
                        render_promoted_backlog(extracted, date_str),
                        encoding="utf-8",
                    )
                    ideas_backlog.write_text(remaining, encoding="utf-8")
                    backlog_extracted = True

        # 4. Update DB: entity_kind=project, status, priority, local_path.
        proj.entity_kind = "project"
        proj.status_id = ps_target.id
        if priority is not None:
            proj.priority = priority
        proj.local_path = str(logical)
        proj.last_touched_at = local_now()

        _log_action(
            session,
            action="idea_promoted_to_project",
            entity_id=proj.id,
            details={
                "slug": proj.slug,
                "to_status": status_slug,
                "priority": proj.priority,
                "backlog_extracted": backlog_extracted,
            },
        )
        session.commit()

        result: dict[str, Any] = {
            "slug": proj.slug,
            "promoted": True,
            "storage": str(storage),
            "junction": f"{logical} → {storage}",
            "md_moved": idea_md_path.name if idea_md_path is not None else None,
            "backlog_extracted": backlog_extracted,
            "canonical_files": None,
            "canonical_error": None,
            "git_url": None,
            "git_error": None,
        }

        # 5. Canonical files (optional).
        if canonical:
            try:
                created = _create_canonical_files(
                    storage,
                    project=proj,
                    type_slug=type_slug,
                    status_slug=status_slug,
                    tag_slugs=[t.slug for t in list_project_tags(session, proj.id)],
                    logical_rel=str(logical.relative_to(root))
                    if str(logical).startswith(str(root)) else str(logical),
                )
                if created:
                    result["canonical_files"] = list(created)
            except Exception as exc:
                result["canonical_error"] = str(exc)

        # 6. Git init (optional).
        if init_git:
            from atlas.pm.commands.projects_git import (
                DEFAULT_COMMIT_MESSAGE,
                perform_git_init,
            )
            try:
                proj_for_git = session.execute(
                    select(Project).where(Project.id == proj.id)
                ).scalar_one()
                git_result = perform_git_init(
                    session, proj_for_git,
                    group=group,
                    private=private,
                    commit_message=DEFAULT_COMMIT_MESSAGE,
                    log_action_fn=_log_action,
                )
                session.commit()
                result["git_url"] = git_result["url"]
            except RuntimeError as exc:
                result["git_error"] = str(exc)

    def _render(d: dict[str, Any]) -> None:
        console.print(f"[green]✓ Idea '{d['slug']}' promoted to project[/green]")
        console.print(f"  Storage:  {d['storage']}")
        console.print(f"  Junction: {d['junction']}")
        if d["md_moved"] is not None:
            console.print(f"  MD moved: {d['md_moved']} → IDEA.md")
        if d["backlog_extracted"]:
            console.print(f"  Backlog:  extracted from _Ideas/BACKLOG.md")
        if d["canonical_files"]:
            console.print(f"  Files:    {', '.join(d['canonical_files'])}")
        if d["canonical_error"]:
            console.print(f"  [yellow]⚠ canonical files: {d['canonical_error']}[/yellow]")
        if d["git_url"]:
            console.print(f"  [green]✓ Git initialized[/green]")
            console.print(f"    URL:    {d['git_url']}")
        if d["git_error"]:
            console.print(f"  [red]✗ Git init failed: {d['git_error']}[/red]")

    emit_data(result, text_renderer=_render)


# --------------------------------------------------------------------------- #
# demote                                                                      #
# --------------------------------------------------------------------------- #


@ideas_app.command("demote")
@command
def demote_cmd(
    ref: str = typer.Argument(..., help="slug проекта (бывшая идея)"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """Project → Idea: вернуть запись обратно в idea-stage.

    Деструктивно: переносит `_storage/<slug>/` → `_old_git_backups/`,
    снимает junction, восстанавливает `_Ideas/<slug>.md` (из IDEA.md если
    есть, иначе из template).

    Не трогает GitLab repo (если был создан).
    """
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        try:
            proj = resolve_project_ref(session, ref)
        except AmbiguousRefError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        if proj is None or proj.entity_kind != "project":
            console.print(
                f"[red]Project '{ref}' не найден или уже не entity_kind='project'.[/red]"
            )
            raise typer.Exit(code=1)

        if not confirm and not typer.confirm(
            f"Demote '{proj.slug}' обратно в idea? "
            f"_storage/<slug>/ переедет в _old_git_backups/."
        ):
            console.print("[yellow]Отменено.[/yellow]")
            raise typer.Exit(code=1)

        from atlas.pm.commands.projects import _hard_delete_physical
        from atlas.pm.layout import get_logical_path, get_storage_path

        root = get_projects_root()
        storage = get_storage_path(proj.slug, root=root)
        # logical через model — ищем как обычный project.
        try:
            logical = get_logical_path(
                type("P", (), {
                    "slug": proj.slug,
                    "type_slug": session.get(ProjectType, proj.type_id).slug,
                    "archived": False,
                    "archived_group": None,
                })(),
                root=root,
            )
        except Exception:
            logical = None

        # 1. Восстановить _Ideas/<slug>.md из storage/IDEA.md если есть.
        ideas_dir = ensure_ideas_root(root)
        new_md_path = ideas_dir / f"{proj.slug}.md"
        idea_md_in_storage = storage / "IDEA.md"
        if idea_md_in_storage.exists() and not new_md_path.exists():
            shutil.copy2(str(idea_md_in_storage), str(new_md_path))

        # 2. Удалить storage + junction (через _hard_delete_physical).
        if logical is not None:
            _hard_delete_physical(
                slug=proj.slug, logical=logical, storage=storage, root=root,
            )

        # 3. Update DB.
        proj.entity_kind = "idea"
        proj.local_path = str(new_md_path)
        proj.last_touched_at = local_now()

        _log_action(
            session,
            action="project_demoted_to_idea",
            entity_id=proj.id,
            details={"slug": proj.slug},
        )
        session.commit()
        demoted_slug = proj.slug

    emit_data(
        {"slug": demoted_slug, "demoted": True},
        text_renderer=lambda d: console.print(
            f"[green]✓ Project '{d['slug']}' demoted to idea[/green]"
        ),
    )


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #


@ideas_app.command("update")
@command
def update_cmd(
    ref: str = typer.Argument(...),
    name: Optional[str] = typer.Option(None, "--name"),
    priority: Optional[str] = typer.Option(None, "--priority"),
    status_slug: Optional[str] = typer.Option(None, "--status"),
    one_line: Optional[str] = typer.Option(None, "--one-line"),
) -> None:
    """Обновить поля идеи (name/priority/status/one-line)."""
    if priority is not None and priority not in {"P0", "P1", "P2", "P3"}:
        console.print(f"[red]Невалидный priority '{priority}'.[/red]")
        raise typer.Exit(code=1)

    engine = make_engine(_db_url())
    with make_session(engine) as session:
        proj = _resolve_idea_or_die(session, ref)

        diffs: dict[str, dict[str, Any]] = {}
        if name is not None and name != proj.name:
            diffs["name"] = {"old": proj.name, "new": name}
            proj.name = name
        if priority is not None and priority != proj.priority:
            diffs["priority"] = {"old": proj.priority, "new": priority}
            proj.priority = priority
        if one_line is not None and one_line != proj.one_line_summary:
            diffs["one_line"] = {"old": proj.one_line_summary, "new": one_line}
            proj.one_line_summary = one_line
        if status_slug is not None:
            ps = session.execute(
                select(ProjectStatus).where(ProjectStatus.slug == status_slug)
            ).scalar_one_or_none()
            if ps is None:
                console.print(f"[red]Статус '{status_slug}' не найден.[/red]")
                raise typer.Exit(code=1)
            if ps.id != proj.status_id:
                old = session.get(ProjectStatus, proj.status_id)
                diffs["status"] = {
                    "old": old.slug if old else "?",
                    "new": status_slug,
                }
                proj.status_id = ps.id

        if not diffs:
            emit_data(
                {"slug": proj.slug, "updated": False, "changes": {}},
                text_renderer=lambda d: console.print("[dim]Нечего обновлять.[/dim]"),
            )
            return

        proj.last_touched_at = local_now()
        _log_action(
            session,
            action="idea_updated",
            entity_id=proj.id,
            details=diffs,
        )
        session.commit()
        updated_slug = proj.slug

    def _render(d: dict[str, Any]) -> None:
        console.print(f"[green]✓ Idea '{d['slug']}' updated[/green]")
        for field, change in d["changes"].items():
            console.print(
                f"  {field}: [dim]{change['old']}[/dim] → [bold]{change['new']}[/bold]"
            )

    emit_data(
        {"slug": updated_slug, "updated": True, "changes": diffs},
        text_renderer=_render,
    )
