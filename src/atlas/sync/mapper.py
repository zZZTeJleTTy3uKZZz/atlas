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


def assignees(session: Any, task: Any) -> list[dict]:
    """Собрать причастных задачи как ``[{"slug": ..., "role": ...}]``.

    Источники (объединяются, порядок стабилен, дубли по slug убираются —
    первое вхождение побеждает):
      1. ``Task.assignee_id`` — denormalized «главный исполнитель» (его пишет
         ``task add --assignee``); роль всегда ``responsible``, идёт первым,
         поэтому при коллизии slug responsible приоритетнее executor.
      2. ``TaskMember`` с ролью responsible|executor (его пишет ``member add``);
         роль берётся из строки. watcher НЕ входит — наблюдатель не исполнитель.

    Ноль хардкода имён: всё резолвится через ``Participant.slug``. role ∈
    {"responsible","executor"}. Возвращает [] если причастных нет — стабильный
    контракт (ключ ``assignees`` всегда присутствует на task-событиях).
    """
    from sqlalchemy import select

    from atlas.models import Participant, TaskMember

    ordered: list[dict] = []
    seen: set[str] = set()

    def _add(slug: str | None, role: str) -> None:
        if slug and slug not in seen:
            seen.add(slug)
            ordered.append({"slug": slug, "role": role})

    if getattr(task, "assignee_id", None):
        main = session.get(Participant, task.assignee_id)
        _add(main.slug if main else None, "responsible")

    rows = session.execute(
        select(Participant.slug, TaskMember.role)
        .join(TaskMember, TaskMember.participant_id == Participant.id)
        .where(
            TaskMember.task_id == task.id,
            TaskMember.role.in_(_ASSIGNEE_ROLES),
        )
    ).all()
    for slug, role in rows:
        _add(slug, role)

    return ordered


def assignee_slugs(session: Any, task: Any) -> list[str]:
    """LEGACY: плоский список slug'ов причастных (без ролей).

    Сохранён для обратной совместимости. Внутри — те же источники, что и
    :func:`assignees`; роль отбрасывается. Новый код должен использовать
    :func:`assignees`, чтобы не терять responsible/executor.
    """
    return [a["slug"] for a in assignees(session, task)]


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


def _checklist_due(c: Any) -> Any:
    """due_date → ISO-строка "YYYY-MM-DD" (если datetime) | None."""
    due = getattr(c, "due_date", None)
    if due is None:
        return None
    return due.strftime("%Y-%m-%d") if hasattr(due, "strftime") else due


def _checklist_payload(c: Any) -> dict:
    """Словарь ЯДРА (контракт checklist_item). parent_task_backend_id
    докладывается в ``to_event`` (он знает родителя). text→title,
    is_done(0/1)→done(bool), position→order_idx, due_date→due(ISO|null)."""
    return {
        "title": c.text,
        "done": bool(c.is_done),
        "due": _checklist_due(c),
        "order_idx": c.position,
        "parent_task_backend_id": None,
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
    assignees: list[dict] | None = None,
    parent_task: Any = None,
) -> dict:
    """Построить EventIn-dict из ORM-сущности.

    ``project`` (если передан) добавляет в payload ``project_slug`` для task/epic —
    ядру нужен slug проекта-контейнера, чтобы привязать сущность (иначе
    ``_apply_to_core`` уходит в ``skipped_no_project``). enqueue знает проект.

    ``assignees`` (только для task) — список причастных как
    ``[{"slug": str, "role": str}]``, role ∈ {"responsible","executor"}
    (watcher НЕ включается). Кладётся в payload под ключом ``assignees`` (НЕ
    ``assignee_member_ids``: тот несёт уже зарезолвленные core-member-id с
    Б24-пути — смешивать со slug'ами нельзя, сломает FK ядра). Резолв
    slug→core-member-id и роли — на стороне оркестратора ядра. Ключ всегда
    присутствует (пустой список при отсутствии причастных) — стабильный
    контракт; присутствие ключа = «полный список причастных» (сигнал reconcile
    для ядра). Плоский LEGACY-ключ ``assignee_slugs`` больше не кладётся.
    """
    build = _PAYLOAD[entity_kind]
    backend_id = getattr(obj, "backend_id", None)
    payload = build(obj)
    if project is not None and entity_kind in ("task", "epic"):
        payload["project_slug"] = project.slug
        # core-id проекта-контейнера: ядро резолвит контейнер по нему (надёжно при
        # разнобое имён Atlas↔ядро), project_slug — fallback. None пока не связан.
        payload["project_backend_id"] = getattr(project, "backend_id", None)
    if entity_kind == "task":
        payload["assignees"] = list(assignees or [])
    # checklist: на проводе entity_kind = канон ядра "checklist_item" (НЕ
    # внутренний "checklist", который остаётся для policy/outbox). Родителя
    # резолвит ядро по parent_task_backend_id = backend_id задачи-родителя.
    wire_kind = entity_kind
    if entity_kind == "checklist":
        wire_kind = "checklist_item"
        if parent_task is not None:
            payload["parent_task_backend_id"] = getattr(parent_task, "backend_id", None)
    return {
        "entity_kind": wire_kind,
        "op": op,
        "entity_id": backend_id or obj.id,
        "payload_json": payload,
        "source_portal_id": portal_id,
    }


__all__ = ["to_event"]
