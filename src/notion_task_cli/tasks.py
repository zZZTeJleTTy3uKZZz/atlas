"""Задачи: чтение, фильтры, операции. Бизнес-логика здесь, не в CLI."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from dateutil import tz

from .client import NotionClient
from .config import DS_TASKS
from .props import (
    DateValue,
    read_date,
    read_multi_select,
    read_number,
    read_relation_ids,
    read_status,
    read_title,
    w_date,
    w_relation,
    w_status,
    w_title,
)


ACTIVE_STATUSES = {"В планах", "В работе", "На паузе"}
TERMINAL_STATUSES = {"Выполнена", "Отмена"}

# Канонические ключи → notion status names
STATUS_ALIAS = {
    "planned": "В планах",
    "working": "В работе",
    "paused": "На паузе",
    "done": "Выполнена",
    "cancelled": "Отмена",
    "в планах": "В планах",
    "в работе": "В работе",
    "на паузе": "На паузе",
    "выполнена": "Выполнена",
    "отмена": "Отмена",
}


@dataclass
class Task:
    id: str
    title: str
    status: str | None
    date_value: DateValue | None
    types: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    subprojects: list[str] = field(default_factory=list)
    responsible: list[str] = field(default_factory=list)
    executors: list[str] = field(default_factory=list)
    b24_task_id: int | None = None
    b24_item_id: int | None = None
    url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def date_only(self) -> date | None:
        if not self.date_value or self.date_value.start is None:
            return None
        s = self.date_value.start
        return s.date() if isinstance(s, datetime) else s


def task_from_page(page: dict[str, Any]) -> Task:
    return Task(
        id=page["id"],
        title=read_title(page, "Задача"),
        status=read_status(page, "Готово?"),
        date_value=read_date(page, "Дата"),
        types=read_multi_select(page, "Тип"),
        projects=read_relation_ids(page, "👾 Проекты"),
        subprojects=read_relation_ids(page, "👾 Под-Проекты"),
        responsible=read_relation_ids(page, "Ответственный"),
        executors=read_relation_ids(page, "Исполнители"),
        b24_task_id=_as_int(read_number(page, "b24_task_id")),
        b24_item_id=_as_int(read_number(page, "b24_checklist_item_id")),
        url=page.get("url", ""),
        raw=page,
    )


def _as_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ---- queries ----


def list_active(
    client: NotionClient,
    *,
    responsible_id: str | None = None,
    project_id: str | None = None,
) -> list[Task]:
    """Активные задачи (не Выполнена / не Отмена). Фильтрация по
    ответственному или проекту — опциональна."""
    and_: list[dict[str, Any]] = [
        {"property": "Готово?", "status": {"does_not_equal": "Выполнена"}},
        {"property": "Готово?", "status": {"does_not_equal": "Отмена"}},
    ]
    if responsible_id:
        and_.append(
            {"property": "Ответственный", "relation": {"contains": responsible_id}}
        )
    if project_id:
        # одна из двух relation-колонок — проект или подпроект
        pages = _query_with_project_filter(client, and_, project_id)
    else:
        pages = client.query_all(
            DS_TASKS,
            filter={"and": and_},
            sorts=[{"property": "Дата", "direction": "ascending"}],
        )
    return [task_from_page(p) for p in pages]


def _query_with_project_filter(
    client: NotionClient, base_and: list[dict[str, Any]], project_id: str
) -> list[dict[str, Any]]:
    flt = {
        "and": base_and + [{
            "or": [
                {"property": "👾 Проекты", "relation": {"contains": project_id}},
                {"property": "👾 Под-Проекты", "relation": {"contains": project_id}},
            ]
        }],
    }
    return client.query_all(
        DS_TASKS, filter=flt,
        sorts=[{"property": "Дата", "direction": "ascending"}],
    )


def list_today(
    client: NotionClient, *, responsible_id: str | None = None,
    tz_name: str = "Europe/Moscow",
) -> list[Task]:
    today_str = datetime.now(tz.gettz(tz_name)).strftime("%Y-%m-%d")
    tomorrow = (
        datetime.now(tz.gettz(tz_name)) + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    and_: list[dict[str, Any]] = [
        {"property": "Готово?", "status": {"does_not_equal": "Выполнена"}},
        {"property": "Готово?", "status": {"does_not_equal": "Отмена"}},
        {"property": "Дата", "date": {"on_or_after": today_str}},
        {"property": "Дата", "date": {"before": tomorrow}},
    ]
    if responsible_id:
        and_.append(
            {"property": "Ответственный", "relation": {"contains": responsible_id}}
        )
    pages = client.query_all(
        DS_TASKS, filter={"and": and_},
        sorts=[{"property": "Дата", "direction": "ascending"}],
    )
    return [task_from_page(p) for p in pages]


def list_overdue(
    client: NotionClient, *, responsible_id: str | None = None,
    tz_name: str = "Europe/Moscow",
) -> list[Task]:
    today_str = datetime.now(tz.gettz(tz_name)).strftime("%Y-%m-%d")
    and_: list[dict[str, Any]] = [
        {"property": "Готово?", "status": {"does_not_equal": "Выполнена"}},
        {"property": "Готово?", "status": {"does_not_equal": "Отмена"}},
        {"property": "Дата", "date": {"before": today_str}},
    ]
    if responsible_id:
        and_.append(
            {"property": "Ответственный", "relation": {"contains": responsible_id}}
        )
    pages = client.query_all(
        DS_TASKS, filter={"and": and_},
        sorts=[{"property": "Дата", "direction": "ascending"}],
    )
    return [task_from_page(p) for p in pages]


def list_no_date(
    client: NotionClient, *, responsible_id: str | None = None,
) -> list[Task]:
    and_: list[dict[str, Any]] = [
        {"property": "Готово?", "status": {"does_not_equal": "Выполнена"}},
        {"property": "Готово?", "status": {"does_not_equal": "Отмена"}},
        {"property": "Дата", "date": {"is_empty": True}},
    ]
    if responsible_id:
        and_.append(
            {"property": "Ответственный", "relation": {"contains": responsible_id}}
        )
    pages = client.query_all(DS_TASKS, filter={"and": and_})
    return [task_from_page(p) for p in pages]


def list_by_project(
    client: NotionClient, project_id: str, *, responsible_id: str | None = None,
) -> list[Task]:
    and_: list[dict[str, Any]] = [
        {"property": "Готово?", "status": {"does_not_equal": "Выполнена"}},
        {"property": "Готово?", "status": {"does_not_equal": "Отмена"}},
    ]
    if responsible_id:
        and_.append(
            {"property": "Ответственный", "relation": {"contains": responsible_id}}
        )
    return [
        task_from_page(p)
        for p in _query_with_project_filter(client, and_, project_id)
    ]


# ---- writes ----


def create_task(
    client: NotionClient,
    *,
    title: str,
    project_id: str | None = None,
    subproject_id: str | None = None,
    date_value: date | datetime | None = None,
    types: list[str] | None = None,
    responsible_id: str | None = None,
    status: str = "В планах",
    tz_name: str = "Europe/Moscow",
) -> dict[str, Any]:
    props: dict[str, Any] = {
        "Задача": w_title(title),
        "Готово?": w_status(status),
    }
    if date_value is not None:
        props["Дата"] = w_date(date_value, tz_name=tz_name)
    if project_id:
        props["👾 Проекты"] = w_relation([project_id])
    if subproject_id:
        props["👾 Под-Проекты"] = w_relation([subproject_id])
    if responsible_id:
        props["Ответственный"] = w_relation([responsible_id])
    if types:
        props["Тип"] = {"multi_select": [{"name": t} for t in types]}
    return client.create_page(data_source_id=DS_TASKS, properties=props)


def set_status(client: NotionClient, page_id: str, status_key: str) -> dict[str, Any]:
    canonical = STATUS_ALIAS.get(status_key.lower())
    if not canonical:
        raise ValueError(
            f"неизвестный статус {status_key!r}. Допустимы: "
            f"{sorted(set(STATUS_ALIAS.values()))}"
        )
    return client.update_page(page_id, {"Готово?": w_status(canonical)})


def set_date(
    client: NotionClient,
    page_id: str,
    d: date | datetime | None,
    *,
    tz_name: str = "Europe/Moscow",
) -> dict[str, Any]:
    return client.update_page(page_id, {"Дата": w_date(d, tz_name=tz_name)})
