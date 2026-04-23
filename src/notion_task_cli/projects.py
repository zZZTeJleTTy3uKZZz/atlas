"""Чтение проектов/клиентов."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import NotionClient
from .config import DS_PROJECTS
from .props import read_number, read_status, read_title


@dataclass
class Project:
    id: str
    title: str
    status: str | None
    b24_company_id: int | None
    b24_contact_id: int | None
    url: str


def project_from_page(page: dict[str, Any]) -> Project:
    def _n(name: str) -> int | None:
        v = read_number(page, name)
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return Project(
        id=page["id"],
        title=read_title(page, "Название"),
        status=read_status(page, "Статус"),
        b24_company_id=_n("b24_company_id"),
        b24_contact_id=_n("b24_contact_id"),
        url=page.get("url", ""),
    )


def list_projects(
    client: NotionClient, *, status: str | None = None
) -> list[Project]:
    flt: dict[str, Any] | None = None
    if status:
        flt = {"property": "Статус", "status": {"equals": status}}
    pages = client.query_all(DS_PROJECTS, filter=flt)
    return [project_from_page(p) for p in pages]
