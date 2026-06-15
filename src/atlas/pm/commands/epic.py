"""CLI `atlas epic ...` — эпики (вехи/спринты). На clikit (--json по умолчанию)."""
from __future__ import annotations

import os

import typer
from clikit import command, emit_data
from sqlalchemy import select

from atlas.pm.db import DEFAULT_DB_PATH, make_engine, make_session
from atlas.pm.models import Epic, Project
from atlas.pm.slugs import resolve_project_ref, slugify_text
from atlas.pm.sync import outbox as _outbox

epic_app = typer.Typer(no_args_is_help=True, help="Эпики (вехи/спринты).")
_PORTAL = "atlas-local"


def _db_url() -> str:
    return os.environ.get("ATLAS_DB_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


def _enqueue(session, op, obj, project):
    try:
        _outbox.enqueue(session, op, "epic", obj, project=project, portal_id=_PORTAL)
    except Exception:
        pass


@epic_app.command("add")
@command
def add_cmd(
    project: str = typer.Option(..., "--project", help="Project ref (slug | UUID)"),
    title: str = typer.Option(..., "--title"),
    slug: str | None = typer.Option(None, "--slug"),
    goal: str | None = typer.Option(None, "--goal"),
) -> None:
    """Создать эпик."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        proj = resolve_project_ref(session, project)
        if proj is None:
            raise typer.Exit(1)
        epic = Epic(
            project_id=proj.id, title=title,
            slug=slug or slugify_text(title) or None, goal=goal,
        )
        session.add(epic)
        session.flush()
        _enqueue(session, "create", epic, proj)
        session.commit()
        emit_data(
            {"id": epic.id, "slug": epic.slug, "title": epic.title, "status": epic.status},
            text_renderer=lambda d: print(f"✓ epic {d['slug'] or d['id']} — {d['title']}"),
        )


@epic_app.command("list")
@command
def list_cmd(
    project: str = typer.Option(..., "--project"),
) -> None:
    """Список эпиков проекта."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        proj = resolve_project_ref(session, project)
        if proj is None:
            raise typer.Exit(1)
        rows = session.execute(
            select(Epic).where(Epic.project_id == proj.id).order_by(Epic.created_at)
        ).scalars().all()
        data = [{"id": e.id, "slug": e.slug, "title": e.title, "status": e.status} for e in rows]
        emit_data(
            data,
            text_renderer=lambda items: [print(f"{i['slug'] or i['id']}: {i['title']} ({i['status']})") for i in items],
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
        emit_data({
            "id": epic.id, "slug": epic.slug, "title": epic.title,
            "status": epic.status, "goal": epic.goal, "project_id": epic.project_id,
            "backend_id": epic.backend_id,
        })
