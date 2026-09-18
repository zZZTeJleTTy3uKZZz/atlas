"""Выборочная загрузка текущего состояния задач проекта в outbox."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.models import Project, Task
from atlas.sync import outbox, policy


def seed_tasks(
    session: Session, project_slug: str, *, portal_id: str, dry_run: bool = False,
    include_closed: bool = False,
) -> dict:
    """Поставить снимки открытых задач одного проекта в очередь отправки.

    Команда повторяема: хаб связывает события по исходному id портала и
    обновляет существующие задачи. Закрытая история — только по явному флагу.
    """
    project = session.execute(
        select(Project).where(Project.slug == project_slug)
    ).scalar_one_or_none()
    if project is None:
        raise ValueError(f"project not found: {project_slug}")
    if not policy.should_sync(session, "task", project):
        raise ValueError(f"task sync disabled for project: {project_slug}")
    query = select(Task).where(Task.project_id == project.id, Task.archived_at.is_(None))
    if not include_closed:
        query = query.where(Task.status.in_(("todo", "in_progress", "review", "blocked")))
    tasks = session.execute(query.order_by(Task.id)).scalars().all()
    queued = 0
    if not dry_run:
        for task in tasks:
            if outbox.enqueue(
                session, "update", "task", task, project=project,
                portal_id=portal_id,
            ) is not None:
                queued += 1
    return {"project": project_slug, "tasks": len(tasks), "queued": queued}


__all__ = ["seed_tasks"]
