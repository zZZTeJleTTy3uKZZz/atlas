"""ORM-сущность Atlas → EventIn-payload для backend-хаба (F3c).

Контракт EventIn бэка: {entity_kind, op, entity_id, payload_json, source_portal_id}.
entity_id = backend_id если есть, иначе локальный id (бэк свяжет через
entity_link по source_portal_id). payload_json — ключевые поля сущности.
"""
from __future__ import annotations

from typing import Any

# Роли TaskMember, означающие «исполнение» задачи (а не наблюдение). watcher
# намеренно исключён: наблюдатель не отвечает за задачу и не маршрутизируется
# в личный портал как исполнитель.
_ASSIGNEE_ROLES = ("responsible", "executor")


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def assignee_slugs(session: Any, task: Any) -> list[str]:
    """Собрать participant-slug'и ответственных/исполнителей задачи.

    Источники (объединяются, порядок стабилен, дубли убираются):
      1. ``Task.assignee_id`` — denormalized «главный исполнитель» (его пишет
         ``task add --assignee``); он первым, чтобы responsible шёл в начале.
      2. ``TaskMember`` с ролью responsible|executor (его пишет ``member add``);
         watcher не входит.

    Ноль хардкода имён: всё резолвится через ``Participant.slug``. Возвращает
    [] если у задачи нет ни assignee_id, ни исполняющих TaskMember.
    """
    from sqlalchemy import select

    from atlas.pm.models import Participant, TaskMember

    ordered: list[str] = []
    seen: set[str] = set()

    def _add(slug: str | None) -> None:
        if slug and slug not in seen:
            seen.add(slug)
            ordered.append(slug)

    if getattr(task, "assignee_id", None):
        main = session.get(Participant, task.assignee_id)
        _add(main.slug if main else None)

    rows = session.execute(
        select(Participant.slug)
        .join(TaskMember, TaskMember.participant_id == Participant.id)
        .where(
            TaskMember.task_id == task.id,
            TaskMember.role.in_(_ASSIGNEE_ROLES),
        )
    ).scalars().all()
    for slug in rows:
        _add(slug)

    return ordered


def _project_payload(p: Any) -> dict:
    return {"slug": p.slug, "name": p.name, "backend_id": p.backend_id}


def _epic_payload(e: Any) -> dict:
    return {
        "slug": e.slug, "title": e.title, "status": e.status,
        "backend_id": e.backend_id,
    }


def _task_payload(t: Any) -> dict:
    return {
        "slug": t.slug, "title": t.title, "status": t.status,
        "priority": t.priority, "cpp": t.cpp_description,
        "due_date": _iso(t.due_date), "backend_id": t.backend_id,
    }


def _checklist_payload(c: Any) -> dict:
    return {
        "text": c.text, "is_done": c.is_done, "position": c.position,
        "backend_id": c.backend_id,
    }


_PAYLOAD = {
    "project": _project_payload,
    "epic": _epic_payload,
    "task": _task_payload,
    "checklist": _checklist_payload,
}


def to_event(
    op: str,
    entity_kind: str,
    obj: Any,
    *,
    portal_id: str,
    project: Any = None,
    assignee_slugs: list[str] | None = None,
) -> dict:
    """Построить EventIn-dict из ORM-сущности.

    ``project`` (если передан) добавляет в payload ``project_slug`` для task/epic —
    ядру нужен slug проекта-контейнера, чтобы привязать сущность (иначе
    ``_apply_to_core`` уходит в ``skipped_no_project``). enqueue знает проект.

    ``assignee_slugs`` (только для task) — participant-slug'и ответственных/
    исполнителей. Кладутся в payload под ключом ``assignee_slugs`` (НЕ
    ``assignee_member_ids``: тот несёт уже зарезолвленные core-member-id с
    Б24-пути — смешивать со slug'ами нельзя, сломает FK ядра). Резолв
    slug→core-member-id — на стороне оркестратора ядра (PART B). Ключ всегда
    присутствует (пустой список при отсутствии исполнителей) — стабильный контракт.
    """
    build = _PAYLOAD[entity_kind]
    backend_id = getattr(obj, "backend_id", None)
    payload = build(obj)
    if project is not None and entity_kind in ("task", "epic"):
        payload["project_slug"] = project.slug
    if entity_kind == "task":
        payload["assignee_slugs"] = list(assignee_slugs or [])
    return {
        "entity_kind": entity_kind,
        "op": op,
        "entity_id": backend_id or obj.id,
        "payload_json": payload,
        "source_portal_id": portal_id,
    }


__all__ = ["to_event"]
