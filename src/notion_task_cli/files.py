"""Файлы клиентов."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .client import NotionClient
from .config import DS_FILES
from .props import (
    DateValue,
    read_checkbox,
    read_date,
    read_relation_ids,
    read_rich_text,
    read_title,
    w_checkbox,
    w_date,
    w_relation,
    w_rich_text,
    w_title,
)


@dataclass
class FileEntry:
    id: str
    title: str
    done: bool
    client_ids: list[str] = field(default_factory=list)
    block: str = ""
    date_value: DateValue | None = None
    url: str = ""


def file_from_page(page: dict[str, Any]) -> FileEntry:
    return FileEntry(
        id=page["id"],
        title=read_title(page, "Название"),
        done=read_checkbox(page, "Сделано?"),
        client_ids=read_relation_ids(page, "👾 _Клиенты"),
        block=read_rich_text(page, "Блок"),
        date_value=read_date(page, "Дата"),
        url=page.get("url", ""),
    )


def list_for_client(
    client: NotionClient, client_page_id: str, *, only_open: bool = False
) -> list[FileEntry]:
    and_: list[dict[str, Any]] = [
        {"property": "👾 _Клиенты", "relation": {"contains": client_page_id}},
    ]
    if only_open:
        and_.append({"property": "Сделано?", "checkbox": {"equals": False}})
    pages = client.query_all(DS_FILES, filter={"and": and_})
    return [file_from_page(p) for p in pages]


def mark_done(client: NotionClient, page_id: str, done: bool = True) -> dict[str, Any]:
    return client.update_page(page_id, {"Сделано?": w_checkbox(done)})


def create_file(
    client: NotionClient,
    *,
    title: str,
    client_page_id: str,
    date_value: date | datetime | None = None,
    block: str | None = None,
    tz_name: str = "Europe/Moscow",
) -> dict[str, Any]:
    props: dict[str, Any] = {
        "Название": w_title(title),
        "👾 _Клиенты": w_relation([client_page_id]),
        "Сделано?": w_checkbox(False),
    }
    if date_value is not None:
        props["Дата"] = w_date(date_value, tz_name=tz_name)
    if block:
        props["Блок"] = w_rich_text(block)
    return client.create_page(data_source_id=DS_FILES, properties=props)
