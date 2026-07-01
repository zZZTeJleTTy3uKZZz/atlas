"""CLI `atlas epic ...` — эпики (вехи/спринты). На clikit (--json по умолчанию)."""
from __future__ import annotations

from atlas.appconfig import default_actor

import json

import typer
from clikit import CliError, command, emit_data
from sqlalchemy import select

from atlas._time import local_now
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import ActionLog, Epic, Participant, Project
from atlas.slugs import AmbiguousRefError, resolve_project_ref, slugify_text
from atlas.sync import outbox as _outbox

epic_app = typer.Typer(no_args_is_help=True, help="Эпики (тематическая группировка задач).")

VALID_ORIGINS = {"native", "injected", "imported", "split"}
_DEFAULT_ACTOR_SLUG = default_actor()


def _sync_portal_id() -> str:
    """Slug портала-стора этого Atlas для source_portal_id событий синка — из
    активного конфига (cfg.portal_id), а НЕ хардкод (как в task/checklist).
    Неправильный slug → событие зависает pending на ядре."""
    from atlas.appconfig import load_config
    return load_config().portal_id


def _db_url() -> str:
    return resolve_db_url()


def _enqueue(session, op, obj, project):
    try:
        _outbox.enqueue(session, op, "epic", obj, project=project,
                        portal_id=_sync_portal_id())
    except Exception:
        pass


def _resolve_project_or_die(session, ref: str) -> Project:
    """Резолв проекта с чистой ошибкой (ambiguous/not-found → CliError, не traceback)."""
    try:
        proj = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        raise CliError("ambiguous_ref", str(exc))
    if proj is None:
        raise CliError("not_found", f"Проект '{ref}' не найден.")
    return proj


def _resolve_participant(session, slug: str | None) -> Participant | None:
    if slug is None:
        return None
    return session.execute(
        select(Participant).where(Participant.slug == slug)
    ).scalar_one_or_none()


@epic_app.command("add")
@command
def add_cmd(
    project: str = typer.Option(..., "--project", help="Project ref (slug | UUID)"),
    title: str = typer.Option(..., "--title"),
    slug: str | None = typer.Option(None, "--slug"),
    goal: str | None = typer.Option(None, "--goal"),
    description: str | None = typer.Option(None, "--description"),
    source_project: str | None = typer.Option(
        None, "--source-project",
        help="Проект-источник (slug | UUID). Задаёт provenance + origin='injected'.",
    ),
    rationale: str | None = typer.Option(
        None, "--rationale", help="Почему/по какому принципу заведён эпик.",
    ),
    origin: str | None = typer.Option(
        None, "--origin", help="native | injected | imported | split.",
    ),
    injected_by: str | None = typer.Option(
        None, "--injected-by", help="Participant slug, кто инжектировал.",
    ),
) -> None:
    """Создать эпик."""
    if origin is not None and origin not in VALID_ORIGINS:
        raise CliError(
            "invalid_origin",
            f"Невалидный origin '{origin}': допустимы {sorted(VALID_ORIGINS)}.",
        )

    engine = make_engine(_db_url())
    with make_session(engine) as session:
        proj = _resolve_project_or_die(session, project)

        # ----- provenance / инвариант origin↔source -----
        source_id: str | None = None
        final_origin = origin or "native"
        injected_at = None
        injector = None

        if source_project is not None:
            src = _resolve_project_or_die(session, source_project)
            if src.id == proj.id:
                # self-inject — это не инжект: warning (в stderr), остаёмся native.
                from clikit import emit_message

                emit_message(
                    "source-project совпадает с целевым — не инжект, origin='native'.",
                    level="warning",
                )
            else:
                source_id = src.id
                if origin is None:
                    final_origin = "injected"
                injected_at = local_now()
                injector = _resolve_participant(session, injected_by)
                if injected_by is not None and injector is None:
                    raise CliError("not_found", f"Участник '{injected_by}' не найден.")

        epic = Epic(
            project_id=proj.id, title=title,
            slug=slug or slugify_text(title) or None, goal=goal,
            description=description,
            source_project_id=source_id,
            origin=final_origin,
            rationale=rationale,
            injected_by=injector.id if injector else None,
            injected_at=injected_at,
        )
        session.add(epic)
        session.flush()

        actor = _resolve_participant(session, _DEFAULT_ACTOR_SLUG)
        session.add(ActionLog(
            actor_id=actor.id if actor else None,
            entity_type="epic",
            entity_id=epic.id,
            action="epic_created",
            details_json=json.dumps(
                {
                    "slug": epic.slug,
                    "title": title,
                    "project": proj.slug,
                    "origin": final_origin,
                    "source_project": (
                        resolve_project_ref(session, source_project).slug
                        if source_id else None
                    ),
                    "rationale": rationale,
                },
                ensure_ascii=False,
                default=str,
            ),
        ))

        _enqueue(session, "create", epic, proj)
        session.commit()
        emit_data(
            {"id": epic.id, "slug": epic.slug, "title": epic.title,
             "status": epic.status, "origin": epic.origin},
            text_renderer=lambda d: print(f"✓ epic {d['slug'] or d['id']} — {d['title']}"),
        )


@epic_app.command("list")
@command
def list_cmd(
    project: str | None = typer.Option(
        None, "--project", help="Project ref. Без него — портфель (все эпики).",
    ),
    source_project: str | None = typer.Option(
        None, "--source-project", help="Фильтр по проекту-источнику.",
    ),
) -> None:
    """Список эпиков: портфель целиком или по проекту."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        stmt = (
            select(Epic, Project.slug.label("project_slug"))
            .join(Project, Epic.project_id == Project.id)
            .order_by(Epic.created_at)
        )
        if project is not None:
            proj = _resolve_project_or_die(session, project)
            stmt = stmt.where(Epic.project_id == proj.id)
        if source_project is not None:
            src = _resolve_project_or_die(session, source_project)
            stmt = stmt.where(Epic.source_project_id == src.id)

        rows = session.execute(stmt).all()
        data = [
            {
                "id": e.id, "slug": e.slug, "title": e.title,
                "status": e.status, "origin": e.origin, "project": project_slug,
                "lease_owner": (
                    session.get(Participant, e.lease_owner).slug
                    if e.lease_owner and session.get(Participant, e.lease_owner)
                    else e.lease_owner
                ),
                "lease_expires_at": (
                    e.lease_expires_at.isoformat() if e.lease_expires_at else None
                ),
            }
            for e, project_slug in rows
        ]
        emit_data(
            data,
            text_renderer=lambda items: [
                print(
                    f"{i['slug'] or i['id']}: {i['title']} "
                    f"({i['status']}) [{i['project']}]"
                )
                for i in items
            ],
        )


@epic_app.command("get")
@command
def get_cmd(ref: str = typer.Argument(..., help="slug | UUID эпика")) -> None:
    """Карточка эпика по slug или id."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        epic = session.execute(
            select(Epic).where((Epic.slug == ref) | (Epic.id == ref))
        ).scalar_one_or_none()
        if epic is None:
            raise typer.Exit(1)

        source_slug = None
        if epic.source_project_id:
            src = session.get(Project, epic.source_project_id)
            source_slug = src.slug if src else epic.source_project_id
        injector_slug = None
        if epic.injected_by:
            inj = session.get(Participant, epic.injected_by)
            injector_slug = inj.slug if inj else epic.injected_by
        lease_holder = None
        if epic.lease_owner:
            lp = session.get(Participant, epic.lease_owner)
            lease_holder = lp.slug if lp else epic.lease_owner

        data = {
            "id": epic.id, "slug": epic.slug, "title": epic.title,
            "status": epic.status, "goal": epic.goal,
            "description": epic.description,
            "project_id": epic.project_id, "backend_id": epic.backend_id,
            "origin": epic.origin,
            "source_project": source_slug,
            "rationale": epic.rationale,
            "injected_by": injector_slug,
            "injected_at": epic.injected_at.isoformat() if epic.injected_at else None,
            # lease/claim (эпик «Групповой lease») — кто/откуда/когда держит эпик
            "lease_owner": lease_holder,
            "lease_session_id": epic.lease_session_id,
            "lease_origin": epic.lease_origin,
            "claimed_at": epic.claimed_at.isoformat() if epic.claimed_at else None,
            "lease_expires_at": (
                epic.lease_expires_at.isoformat() if epic.lease_expires_at else None
            ),
        }

        def _render(d):
            print(f"epic {d['slug'] or d['id']} — {d['title']}")
            print(f"  Status:    {d['status']}")
            if d["goal"]:
                print(f"  Goal:      {d['goal']}")
            if d["description"]:
                print(f"  Description: {d['description']}")
            print(f"  Project:   {d['project_id']}")
            if d["backend_id"]:
                print(f"  Backend:   {d['backend_id']}")
            print("\n  Provenance:")
            print(f"    origin:         {d['origin']}")
            print(f"    source-project: {d['source_project'] or '—'}")
            print(f"    rationale:      {d['rationale'] or '—'}")
            print(f"    injected-by:    {d['injected_by'] or '—'}")
            print(f"    injected-at:    {d['injected_at'] or '—'}")

        emit_data(data, text_renderer=_render)
