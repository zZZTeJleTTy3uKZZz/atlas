"""Индексы id→name для проектов и сотрудников.
Строятся по одному запросу на data_source и используются для обогащения
JSON-вывода именами клиентов и людей."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .client import NotionClient
from .config import DS_EMPLOYEES, DS_PROJECTS
from .props import read_title


@dataclass
class Index:
    data: dict[str, str]  # page_id -> title

    def name(self, page_id: str | None) -> str | None:
        if not page_id:
            return None
        return self.data.get(page_id)

    def pairs(self, ids: list[str]) -> list[dict[str, str]]:
        return [{"id": i, "title": self.data.get(i) or ""} for i in ids]


def _build(client: NotionClient, data_source_id: str, title_prop: str) -> Index:
    m: dict[str, str] = {}
    for page in client.query_data_source(data_source_id):
        title = read_title(page, title_prop)
        if title:
            m[page["id"]] = title
    return Index(m)


def projects_index(client: NotionClient) -> Index:
    return _build(client, DS_PROJECTS, "Название")


def employees_index(client: NotionClient) -> Index:
    return _build(client, DS_EMPLOYEES, "Имя")
