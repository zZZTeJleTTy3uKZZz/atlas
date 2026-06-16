"""CLI-команды `atlas participants ...`.

CRUD по участникам PM-БД (NP-005).

Команды:
- ``add``     — создать участника (slug auto или явно).
- ``list``    — список участников (фильтры по kind / inactive).
- ``get``     — карточка участника (slug | UUID full | UUID short).
- ``update``  — изменить поля (любые, кроме slug).
- ``delete``  — hard delete / --force (cascade) / --soft (deactivate).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm.db import make_engine, make_session, resolve_db_url
from atlas.pm.models import (
    ActionLog,
    Participant,
    Project,
    ProjectParticipant,
    Task,
)
from atlas.pm.slugs import (
    AmbiguousRefError,
    SlugGenerationError,
    UUID_FULL_LEN,
    UUID_SHORT_MIN,
    _is_full_uuid,
    _looks_like_uuid_prefix,
    generate_unique_slug,
    slugify_text,
)

app = typer.Typer(
    no_args_is_help=True,
    help="Participants management: участники проектов (PM-БД), CRUD.",
)
console = Console()

VALID_KINDS = {"human", "ai_agent", "contractor"}
DEFAULT_ACTOR_SLUG = "dmitry"


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
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
        entity_type="participant",
        entity_id=entity_id,
        action=action,
        details_json=json.dumps(details, ensure_ascii=False, default=str),
    )
    session.add(entry)


def _slug_exists_fn(session: Session):
    def _check(candidate: str) -> bool:
        return session.execute(
            select(Participant.id).where(Participant.slug == candidate)
        ).scalar_one_or_none() is not None
    return _check


def _resolve_participant_ref(session: Session, ref: str) -> Optional[Participant]:
    """slug / UUID full / UUID short prefix."""
    if not ref:
        return None
    # slug
    p = session.execute(
        select(Participant).where(Participant.slug == ref)
    ).scalar_one_or_none()
    if p is not None:
        return p
    # full UUID
    if _is_full_uuid(ref):
        return session.execute(
            select(Participant).where(Participant.id == ref)
        ).scalar_one_or_none()
    # short UUID
    if len(ref) >= UUID_SHORT_MIN and _looks_like_uuid_prefix(ref):
        matches = session.execute(
            select(Participant).where(Participant.id.like(f"{ref}%"))
        ).scalars().all()
        if len(matches) == 0:
            return None
        if len(matches) > 1:
            raise AmbiguousRefError(
                f"UUID prefix '{ref}' матчит {len(matches)} участников; "
                "уточни больше символов"
            )
        return matches[0]
    return None


def _resolve_or_die(session: Session, ref: str) -> Participant:
    try:
        p = _resolve_participant_ref(session, ref)
    except AmbiguousRefError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if p is None:
        console.print(f"[red]Participant '{ref}' не найден.[/red]")
        raise typer.Exit(code=1)
    return p


def _validate_kind(kind: str) -> None:
    if kind not in VALID_KINDS:
        console.print(
            f"[red]Невалидный kind '{kind}': допустимы {sorted(VALID_KINDS)}.[/red]"
        )
        raise typer.Exit(code=1)


def _validate_metadata_json(value: str) -> str:
    try:
        json.loads(value)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Невалидный --metadata-json: {exc}[/red]")
        raise typer.Exit(code=1)
    return value


# --------------------------------------------------------------------------- #
# add                                                                         #
# --------------------------------------------------------------------------- #


@app.command("add")
def add_cmd(
    name: str = typer.Option(..., "--name", help="Человекочитаемое имя"),
    kind: str = typer.Option(..., "--kind", help="human | ai_agent | contractor"),
    slug: Optional[str] = typer.Option(
        None, "--slug",
        help="Уникальный slug. Если не задан — auto из --name.",
    ),
    role: Optional[str] = typer.Option(None, "--role", help="role_default (свободный текст)"),
    email: Optional[str] = typer.Option(None, "--email"),
    metadata_json: Optional[str] = typer.Option(
        None, "--metadata-json", help="Произвольный JSON-объект",
    ),
) -> None:
    """Создать нового участника."""
    _validate_kind(kind)

    if metadata_json is not None:
        _validate_metadata_json(metadata_json)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        # ----- slug -----
        slug_auto = False
        if slug:
            if _slug_exists_fn(session)(slug):
                console.print(
                    f"[red]Slug '{slug}' занят. Выберите другой.[/red]"
                )
                raise typer.Exit(code=1)
            final_slug = slug
        else:
            base = slugify_text(name)
            if not base:
                console.print(
                    f"[red]Не удалось сгенерировать slug из '{name}': "
                    f"передайте --slug явно.[/red]"
                )
                raise typer.Exit(code=1)
            try:
                final_slug = generate_unique_slug(base, _slug_exists_fn(session))
            except SlugGenerationError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
            slug_auto = True

        participant = Participant(
            kind=kind,
            slug=final_slug,
            name=name,
            role_default=role,
            email=email,
            metadata_json=metadata_json,
            is_active=True,
        )
        session.add(participant)
        session.flush()

        _log_action(
            session,
            action="participant_created",
            entity_id=participant.id,
            details={
                "slug": final_slug,
                "name": name,
                "kind": kind,
                "role": role,
                "email": email,
            },
        )
        session.commit()

        if slug_auto:
            console.print(f"[dim]slug auto-generated: {final_slug}[/dim]")
        console.print(
            f"[green]✓ Participant '{final_slug}' created[/green] · "
            f"{name} · {kind}"
        )


# --------------------------------------------------------------------------- #
# list                                                                        #
# --------------------------------------------------------------------------- #


@app.command("list")
def list_cmd(
    kind: Optional[str] = typer.Option(None, "--kind"),
    inactive: bool = typer.Option(
        False, "--inactive",
        help="Показывать неактивных (по умолчанию только активные)",
    ),
) -> None:
    """Список участников."""
    if kind is not None:
        _validate_kind(kind)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        stmt = select(Participant).order_by(Participant.kind, Participant.name)
        if kind is not None:
            stmt = stmt.where(Participant.kind == kind)
        if not inactive:
            stmt = stmt.where(Participant.is_active == True)  # noqa: E712
        rows = session.execute(stmt).scalars().all()

    if not rows:
        console.print("[yellow]Участников не найдено.[/yellow]")
        return

    table = Table(title=f"Participants ({len(rows)})")
    table.add_column("Slug", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Kind", style="magenta")
    table.add_column("Role", style="dim")
    table.add_column("Email", style="dim")
    table.add_column("Active", justify="center")

    for p in rows:
        active = "✓" if p.is_active else "—"
        table.add_row(
            p.slug, p.name, p.kind,
            p.role_default or "—",
            p.email or "—",
            active,
        )
    console.print(table)


# --------------------------------------------------------------------------- #
# get                                                                         #
# --------------------------------------------------------------------------- #


@app.command("get")
def get_cmd(
    ref: str = typer.Argument(..., help="slug | UUID full | UUID short prefix"),
) -> None:
    """Карточка участника."""
    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        p = _resolve_or_die(session, ref)

        # Проекты
        link_rows = session.execute(
            select(ProjectParticipant, Project)
            .join(Project, ProjectParticipant.project_id == Project.id)
            .where(ProjectParticipant.participant_id == p.id)
        ).all()

    console.print(f"[bold cyan]{p.slug}[/bold cyan] — {p.name}")
    console.print(f"  ID:        {p.id}")
    console.print(f"  Kind:      {p.kind}")
    if p.role_default:
        console.print(f"  Role:      {p.role_default}")
    if p.email:
        console.print(f"  Email:     {p.email}")
    console.print(f"  Active:    {'yes' if p.is_active else 'no'}")
    if p.metadata_json:
        try:
            meta = json.loads(p.metadata_json)
            console.print(f"  Metadata:  {json.dumps(meta, ensure_ascii=False)}")
        except json.JSONDecodeError:
            console.print(f"  Metadata:  {p.metadata_json}")
    console.print(f"  Created:   {p.created_at}")

    if link_rows:
        console.print("\n[bold]Projects:[/bold]")
        for link, proj in link_rows:
            hours = (
                f", {link.allocated_weekly_hours}h/нед"
                if link.allocated_weekly_hours else ""
            )
            console.print(
                f"  • {proj.slug} ({proj.name}) — "
                f"{link.role_in_project}{hours}"
            )
    else:
        console.print("\n[dim]Projects: —[/dim]")


# --------------------------------------------------------------------------- #
# update                                                                      #
# --------------------------------------------------------------------------- #


@app.command("update")
def update_cmd(
    ref: str = typer.Argument(..., help="slug | UUID"),
    name: Optional[str] = typer.Option(None, "--name"),
    role: Optional[str] = typer.Option(None, "--role"),
    email: Optional[str] = typer.Option(None, "--email"),
    metadata_json: Optional[str] = typer.Option(None, "--metadata-json"),
    kind: Optional[str] = typer.Option(None, "--kind"),
    active: Optional[bool] = typer.Option(
        None, "--active/--inactive",
        help="Активировать или деактивировать участника",
    ),
    slug: Optional[str] = typer.Option(
        None, "--slug",
        help="ЗАПРЕЩЕНО менять slug — immutable.",
    ),
) -> None:
    """Обновить поля участника (любые, кроме slug)."""
    if slug is not None:
        console.print(
            "[red]Изменение slug запрещено: slug — immutable.[/red]"
        )
        raise typer.Exit(code=1)

    if kind is not None:
        _validate_kind(kind)
    if metadata_json is not None:
        _validate_metadata_json(metadata_json)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        p = _resolve_or_die(session, ref)

        diffs: dict[str, dict[str, Any]] = {}

        def _maybe_update(field: str, new_value: Any) -> None:
            if new_value is None:
                return
            old_value = getattr(p, field)
            if old_value != new_value:
                diffs[field] = {"old": old_value, "new": new_value}
                setattr(p, field, new_value)

        _maybe_update("name", name)
        _maybe_update("role_default", role)
        _maybe_update("email", email)
        _maybe_update("metadata_json", metadata_json)
        _maybe_update("kind", kind)

        if active is not None:
            current = bool(p.is_active)
            if current != active:
                diffs["is_active"] = {"old": current, "new": active}
                p.is_active = active

        if not diffs:
            console.print("[yellow]Нечего обновлять.[/yellow]")
            return

        _log_action(
            session,
            action="participant_updated",
            entity_id=p.id,
            details=diffs,
        )
        session.commit()

        console.print(
            f"[green]✓ Participant '{p.slug}' updated[/green] "
            f"({len(diffs)} field(s))"
        )
        for field, diff in diffs.items():
            console.print(
                f"  {field}: [dim]{diff['old']}[/dim] → [bold]{diff['new']}[/bold]"
            )


# --------------------------------------------------------------------------- #
# delete                                                                      #
# --------------------------------------------------------------------------- #


@app.command("delete")
def delete_cmd(
    ref: str = typer.Argument(..., help="slug | UUID"),
    force: bool = typer.Option(
        False, "--force",
        help="Cascade: удалить связи в project_participants и обнулить assignee_id у tasks.",
    ),
    soft: bool = typer.Option(
        False, "--soft",
        help="Soft-delete: is_active=False (не удалять из БД).",
    ),
) -> None:
    """Удалить участника.

    Логика:
    - default: hard delete если нет привязок; иначе error (предложит --force).
    - --soft: is_active=False, в БД остаётся.
    - --force: cascade удаление (project_participants → DELETE, tasks.assignee_id → NULL).
    """
    if soft and force:
        console.print("[red]Нельзя одновременно --soft и --force.[/red]")
        raise typer.Exit(code=1)

    url = _db_url()
    engine = make_engine(url)

    with make_session(engine) as session:
        p = _resolve_or_die(session, ref)
        slug_for_msg = p.slug
        participant_id = p.id

        if soft:
            if not p.is_active:
                console.print(
                    f"[yellow]Participant '{slug_for_msg}' уже неактивен.[/yellow]"
                )
                return
            p.is_active = False
            _log_action(
                session,
                action="participant_deactivated",
                entity_id=participant_id,
                details={"slug": slug_for_msg},
            )
            session.commit()
            console.print(
                f"[green]✓ Participant '{slug_for_msg}' deactivated[/green]"
            )
            return

        # hard / cascade
        link_count = session.execute(
            select(ProjectParticipant).where(
                ProjectParticipant.participant_id == participant_id
            )
        ).scalars().all()
        task_rows = session.execute(
            select(Task).where(Task.assignee_id == participant_id)
        ).scalars().all()

        n_links = len(link_count)
        n_tasks = len(task_rows)

        if (n_links > 0 or n_tasks > 0) and not force:
            console.print(
                f"[red]Participant '{slug_for_msg}' used in {n_links} "
                f"project(s) / {n_tasks} task(s). Use --force to cascade.[/red]"
            )
            raise typer.Exit(code=1)

        cascade_details = {"links_removed": 0, "tasks_unassigned": 0}
        if force:
            for link in link_count:
                session.delete(link)
                cascade_details["links_removed"] += 1
            for task in task_rows:
                task.assignee_id = None
                cascade_details["tasks_unassigned"] += 1

        # Лог ДО удаления
        _log_action(
            session,
            action="participant_deleted",
            entity_id=participant_id,
            details={
                "slug": slug_for_msg,
                "name": p.name,
                "kind": p.kind,
                **({"cascade": cascade_details} if force else {}),
            },
        )
        # flush, чтобы log остался даже после удаления участника
        session.flush()
        session.delete(p)
        session.commit()

        cascade_msg = ""
        if force and (n_links > 0 or n_tasks > 0):
            cascade_msg = (
                f" (cascade: {n_links} link(s), {n_tasks} task(s) unassigned)"
            )
        console.print(
            f"[red]✗ Participant '{slug_for_msg}' deleted[/red]{cascade_msg}"
        )
