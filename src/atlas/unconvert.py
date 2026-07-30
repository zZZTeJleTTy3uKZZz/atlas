"""Обратный путь: задача/эпик → пул `backlog` и возврат назад (#1301).

Дорога была односторонней: `backlog convert` превращал идею в задачу, а
обратно — только `task cancel` + ручной `backlog add` с копипастом. Это врало
дважды: «отменено» — не та причина (задача не провалилась, а вернулась в
раздумья), и содержимое переносилось руками, теряя ЦКП и связи.

Ключевые решения:

- **Задача не удаляется**, а помечается `status="converted"` + архивируется:
  история, комментарии и action_log остаются на месте, а из активных списков
  запись уходит.
- **Прежний slug сохраняется** в `origin_ref` и восстанавливается при возврате —
  ссылки на задачу в описаниях и коммитах не протухают.
- **Поля, которых нет в BacklogItem** (ЦКП, описание, assignee, эпик), едут в
  `origin_payload` как JSON: без этого возврат — тот же копипаст, только через
  БД.
- **Эпик уводится каскадом**: его задачи становятся записями пула с
  `parent_id` на запись эпика. Возврат поднимает всю структуру разом.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas._time import local_now
from atlas.models import BacklogItem, Epic, Task
from atlas.slugs import generate_unique_slug, slugify_text


class UnconvertError(Exception):
    """Обратная конвертация невозможна (с человекочитаемой причиной)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _backlog_slug(session: Session, base: str) -> str:
    """Свободный slug для записи пула."""
    def taken(candidate: str) -> bool:
        return session.execute(
            select(BacklogItem.id).where(BacklogItem.slug == candidate)
        ).scalar_one_or_none() is not None

    return generate_unique_slug(slugify_text(base) or "item", taken)


def _task_payload(task: Task) -> dict[str, Any]:
    """Поля задачи, которым нет места в BacklogItem."""
    return {
        "cpp": task.cpp_description,
        "description": task.description,
        "assignee_id": task.assignee_id,
        "reviewer_id": task.reviewer_id,
        "quality_tier": task.quality_tier,
        "origin": task.origin,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "number": task.number,
    }


def _epic_payload(epic: Epic) -> dict[str, Any]:
    return {
        "goal": epic.goal,
        "description": epic.description,
        "origin": epic.origin,
        "rationale": epic.rationale,
    }


def _already_in_pool(session: Session, kind: str, ref: str) -> Optional[BacklogItem]:
    """Открытая запись пула, уже созданная из этой сущности."""
    return session.execute(
        select(BacklogItem).where(
            BacklogItem.origin_kind == kind,
            BacklogItem.origin_ref == ref,
            BacklogItem.status == "open",
        )
    ).scalar_one_or_none()


def task_to_backlog(
    session: Session,
    task: Task,
    *,
    parent: Optional[BacklogItem] = None,
) -> BacklogItem:
    """Увести задачу в пул. Возвращает созданную запись."""
    if task.slug and _already_in_pool(session, "task", task.slug):
        raise UnconvertError(
            "already_in_pool",
            f"Задача '{task.slug}' уже уведена в пул — повторный увод создал бы "
            f"дубль. Верните её (`atlas backlog convert …`) или уводите другую.",
        )

    item = BacklogItem(
        title=task.title,
        note=task.description,
        project_id=task.project_id,
        priority=task.priority,
        slug=_backlog_slug(session, task.title),
        status="open",
        source="native",
        origin_kind="task",
        origin_ref=task.slug,
        origin_payload=json.dumps(_task_payload(task), ensure_ascii=False, default=str),
        parent_id=parent.id if parent else None,
    )
    session.add(item)
    session.flush()

    # Задача не удаляется: история и комментарии остаются, но из активных
    # списков она уходит через archived_at.
    #
    # Статус НЕ меняем. Отдельный "converted" потребовал бы правки CHECK
    # ck_tasks_status, а `cancelled` соврал бы о причине: задача не провалилась.
    # Чем она стала — видно по записи пула с origin_ref == slug задачи; это и
    # есть источник правды, дублировать его статусом незачем.
    task.archived_at = local_now()
    task.lease_owner = None
    task.lease_expires_at = None
    # Освобождаем slug: он глобально уникален, и архивная запись держала бы имя,
    # не давая возврату восстановить прежнюю ссылку. Суффикс — id записи пула,
    # так что по архивной задаче видно, куда она ушла.
    if task.slug:
        task.slug = f"{task.slug}~{item.id[:8]}"
    return item


def epic_to_backlog(session: Session, epic: Epic) -> tuple[BacklogItem, list[BacklogItem]]:
    """Увести эпик В ПУЛ вместе с его задачами (каскад)."""
    if epic.slug and _already_in_pool(session, "epic", epic.slug):
        raise UnconvertError(
            "already_in_pool",
            f"Эпик '{epic.slug}' уже уведён в пул — повторный увод создал бы дубль.",
        )

    parent = BacklogItem(
        title=epic.title,
        note=epic.goal or epic.description,
        project_id=epic.project_id,
        slug=_backlog_slug(session, epic.title),
        status="open",
        source="native",
        origin_kind="epic",
        origin_ref=epic.slug,
        origin_payload=json.dumps(_epic_payload(epic), ensure_ascii=False, default=str),
    )
    session.add(parent)
    session.flush()

    children: list[BacklogItem] = []
    tasks = session.execute(
        select(Task).where(Task.epic_id == epic.id, Task.archived_at.is_(None))
    ).scalars().all()
    for task in tasks:
        children.append(task_to_backlog(session, task, parent=parent))

    # У Epic нет archived_at (в отличие от Task) — состояние живёт в status.
    epic.status = "archived"
    if epic.slug:
        epic.slug = f"{epic.slug}~{parent.id[:8]}"   # см. комментарий выше
    return parent, children


def backlog_to_task(
    session: Session,
    item: BacklogItem,
    *,
    epic_id: Optional[str] = None,
) -> Task:
    """Вернуть запись пула обратно в задачу, восстановив прежний slug и ЦКП."""
    payload = json.loads(item.origin_payload or "{}")
    cpp = payload.get("cpp")
    if not cpp:
        raise UnconvertError(
            "no_cpp",
            f"У записи '{item.slug}' нет сохранённого ЦКП — задайте его явно: "
            f"`atlas backlog convert {item.slug} --as task --cpp \"…\"`.",
        )

    slug = item.origin_ref
    if slug and session.execute(
        select(Task.id).where(Task.slug == slug, Task.archived_at.is_(None))
    ).scalar_one_or_none():
        raise UnconvertError(
            "slug_taken",
            f"Slug '{slug}' занят активной задачей — верните её вручную.",
        )

    task = Task(
        slug=slug,
        # Номер НЕ восстанавливаем: он глобально уникален и остаётся за
        # архивной записью. Прежний лежит в payload как исторический след;
        # для ссылок хватает slug, который возвращается прежним.
        number=_next_number(session),
        project_id=item.project_id,
        epic_id=epic_id,
        title=item.title,
        description=item.note or payload.get("description"),
        cpp_description=cpp,
        status="todo",
        priority=item.priority or "P2",
        assignee_id=payload.get("assignee_id"),
        reviewer_id=payload.get("reviewer_id"),
        quality_tier=payload.get("quality_tier"),
        origin=payload.get("origin") or "native",
    )
    session.add(task)
    session.flush()

    item.status = "converted"
    item.converted_kind = "task"
    item.converted_ref = task.slug
    return task


def backlog_to_epic(session: Session, item: BacklogItem) -> tuple[Epic, list[Task]]:
    """Вернуть эпик из пула ВМЕСТЕ с его задачами и прежними связями."""
    if item.origin_kind != "epic":
        raise UnconvertError(
            "not_an_epic",
            f"Запись '{item.slug}' — не уведённый эпик (origin_kind="
            f"{item.origin_kind!r}). Для задачи используйте `--as task`.",
        )

    payload = json.loads(item.origin_payload or "{}")
    epic = Epic(
        slug=item.origin_ref,
        project_id=item.project_id,
        title=item.title,
        goal=payload.get("goal"),
        description=payload.get("description"),
        origin=payload.get("origin") or "native",
        rationale=payload.get("rationale"),
    )
    session.add(epic)
    session.flush()

    restored: list[Task] = []
    children = session.execute(
        select(BacklogItem).where(
            BacklogItem.parent_id == item.id, BacklogItem.status == "open"
        )
    ).scalars().all()
    for child in children:
        restored.append(backlog_to_task(session, child, epic_id=epic.id))

    item.status = "converted"
    item.converted_kind = "epic"
    item.converted_ref = epic.slug
    return epic, restored


def _next_number(session: Session) -> int:
    from atlas.slugs import next_task_number

    return next_task_number(session)


# --------------------------------------------------------------------------- #
# Перенос между проектами (#1126)                                              #
# --------------------------------------------------------------------------- #


def _reslug_for_project(session: Session, task: Task, project) -> Optional[str]:
    """Пересобрать slug задачи под prefix нового проекта.

    Slug несёт prefix проекта (``ALP-…``), поэтому после переноса он врал бы о
    принадлежности. Хвост (осмысленную часть) сохраняем — по нему задачу и
    узнают."""
    from atlas.slugs import build_task_slug

    if not task.slug or not project.prefix:
        return task.slug
    tail = task.slug.split("-", 1)[1] if "-" in task.slug else task.slug

    def taken(candidate: str) -> bool:
        return session.execute(
            select(Task.id).where(Task.slug == candidate, Task.id != task.id)
        ).scalar_one_or_none() is not None

    return generate_unique_slug(build_task_slug(project.prefix, tail), taken)


def move_task_to_project(session: Session, task: Task, project) -> dict[str, Any]:
    """Перенести задачу в другой проект, сохранив запись (номер и история)."""
    if task.project_id == project.id:
        raise UnconvertError(
            "same_project",
            f"Задача уже в проекте '{project.slug}' — переносить некуда.",
        )

    old_slug = task.slug
    dropped_epic = None
    if task.epic_id is not None:
        epic = session.get(Epic, task.epic_id)
        # Эпик остаётся в прежнем проекте: увезти связь значило бы создать ту
        # самую межпроектную привязку, которую запрещает гейт #1125.
        if epic is not None and epic.project_id != project.id:
            dropped_epic = epic.slug or epic.id[:8]
            task.epic_id = None

    task.project_id = project.id
    task.slug = _reslug_for_project(session, task, project)
    task.last_touched_at = local_now()
    return {
        "task": task.slug,
        "old_slug": old_slug,
        "number": task.number,
        "to_project": project.slug,
        "epic_unlinked": dropped_epic,
    }


def move_epic_to_project(session: Session, epic: Epic, project) -> dict[str, Any]:
    """Перенести эпик вместе с его задачами: связь эпик↔задача внутрипроектная."""
    if epic.project_id == project.id:
        raise UnconvertError(
            "same_project",
            f"Эпик уже в проекте '{project.slug}' — переносить некуда.",
        )

    tasks = session.execute(
        select(Task).where(Task.epic_id == epic.id, Task.archived_at.is_(None))
    ).scalars().all()

    epic.project_id = project.id
    moved: list[str] = []
    for task in tasks:
        task.project_id = project.id
        task.slug = _reslug_for_project(session, task, project)
        task.last_touched_at = local_now()
        moved.append(task.slug)
    return {"epic": epic.slug, "to_project": project.slug, "tasks_moved": moved}
