"""Review-workflow + комментарии задачи (мультиагентная приёмка). Pure-logic.

Сценарий: исполнитель берёт задачу (`start`), делает, отправляет на проверку
(`submit` → review) с комментарием-передачей; reviewer (по умолч. создатель)
либо одобряет (`approve` → done), либо возвращает (`reject` → in_progress) с
причиной; закрытую можно `reopen`. Гейт «закрывает только reviewer» — в
``task_status.finish_task`` (см. :func:`atlas.task_status._require_reviewer`).

Комментарии (`Comment`) — намеренные заметки (что сделано/зарелизено/что дальше,
причины reject/approve), в отличие от авто-аудита ``action_log``. ``task get``
отдаёт их вместе с карточкой — следующий агент получает весь контекст.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas import task_status as TS
from atlas.models import Comment, Participant, Task

#: Допустимые kind комментария (связь с переходом).
COMMENT_KINDS = ("comment", "submit", "approve", "reject", "reopen")


def add_comment(
    session: Session,
    task: Task,
    author: Optional[Participant],
    body: str,
    *,
    kind: str = "comment",
) -> Comment:
    """Добавить комментарий к задаче."""
    c = Comment(
        task_id=task.id,
        author_id=author.id if author else None,
        body=body,
        kind=kind if kind in COMMENT_KINDS else "comment",
    )
    session.add(c)
    session.flush()
    return c


def list_comments(session: Session, task: Task) -> list[Comment]:
    """Комментарии задачи по времени."""
    return list(
        session.execute(
            select(Comment).where(Comment.task_id == task.id).order_by(Comment.created_at)
        ).scalars().all()
    )


def submit_task(
    session: Session,
    task: Task,
    actor: Participant,
    *,
    comment: Optional[str] = None,
    force: bool = False,
    now: Optional[datetime] = None,
) -> Task:
    """Исполнитель → review (отправить на проверку). Опц. комментарий-передача.

    Handoff: lease исполнителя снимается — чтобы reviewer мог approve/reject без
    конфликта аренды (после reject исполнитель берёт задачу заново через `start`)."""
    TS.review_task(session, task, actor, force=force, now=now)
    if task.lease_owner == actor.id:
        from atlas.lease import _clear_lease

        _clear_lease(task)
        session.flush()
    if comment:
        add_comment(session, task, actor, comment, kind="submit")
    return task


def approve_task(
    session: Session,
    task: Task,
    actor: Participant,
    *,
    comment: Optional[str] = None,
    force: bool = False,
    now: Optional[datetime] = None,
) -> Task:
    """Reviewer → done (одобрить). Гейт reviewer — в finish_task. Опц. комментарий."""
    TS.finish_task(session, task, actor, force=force, now=now)
    if comment:
        add_comment(session, task, actor, comment, kind="approve")
    return task


def reject_task(
    session: Session,
    task: Task,
    actor: Participant,
    *,
    comment: str,
    force: bool = False,
    now: Optional[datetime] = None,
) -> Task:
    """Reviewer возвращает в работу: review → in_progress. Причина (comment) обязательна."""
    if task.status not in ("review", "blocked"):
        raise TS.TransitionError(
            f"reject только из 'review', текущий статус '{task.status}'"
        )
    TS._require_reviewer(session, task, actor, force)
    task.status = "in_progress"
    session.flush()
    TS._log_transition(session, "task_rejected", task, actor)
    add_comment(session, task, actor, comment, kind="reject")
    return task


def reopen_task(
    session: Session,
    task: Task,
    actor: Participant,
    *,
    comment: Optional[str] = None,
    force: bool = False,
    now: Optional[datetime] = None,
) -> Task:
    """Переоткрыть закрытую: done/cancelled → todo (reviewer-gated). Опц. комментарий."""
    if task.status not in ("done", "cancelled"):
        raise TS.TransitionError(
            f"reopen только из done/cancelled, текущий статус '{task.status}'"
        )
    TS._require_reviewer(session, task, actor, force)
    old = task.status
    task.status = "todo"
    if task.completed_at is not None:
        task.completed_at = None
    session.flush()
    TS._log_transition(session, "task_reopened", task, actor, from_status=old)
    if comment:
        add_comment(session, task, actor, comment, kind="reopen")
    return task
