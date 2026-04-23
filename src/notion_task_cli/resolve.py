"""Резолвинг имён в page_id: проекты, сотрудники."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .client import NotionClient
from .config import DS_EMPLOYEES, DS_PROJECTS
from .props import read_title


@dataclass
class NamedPage:
    id: str
    title: str


class AmbiguousMatchError(RuntimeError):
    def __init__(self, query: str, matches: list[NamedPage]) -> None:
        super().__init__(
            f"По запросу {query!r} найдено {len(matches)} совпадений, уточни."
        )
        self.query = query
        self.matches = matches


class NotFoundError(RuntimeError):
    pass


def _iter_pages(
    client: NotionClient,
    data_source_id: str,
    *,
    title_prop: str,
) -> Iterable[NamedPage]:
    for page in client.query_data_source(data_source_id):
        title = read_title(page, title_prop)
        if title:
            yield NamedPage(id=page["id"], title=title)


def resolve_project(client: NotionClient, query: str) -> NamedPage:
    """Строгое: case-insensitive equal → startswith → substring."""
    q = query.strip().lower()
    pages = list(_iter_pages(client, DS_PROJECTS, title_prop="Название"))
    return _pick(q, pages, kind="проект")


def resolve_employee(client: NotionClient, query: str) -> NamedPage:
    q = query.strip().lower()
    pages = list(_iter_pages(client, DS_EMPLOYEES, title_prop="Имя"))
    return _pick(q, pages, kind="сотрудник")


def _pick(q: str, pages: list[NamedPage], *, kind: str) -> NamedPage:
    exact = [p for p in pages if p.title.strip().lower() == q]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AmbiguousMatchError(q, exact)

    starts = [p for p in pages if p.title.strip().lower().startswith(q)]
    if len(starts) == 1:
        return starts[0]
    if len(starts) > 1:
        raise AmbiguousMatchError(q, starts)

    contains = [p for p in pages if q in p.title.strip().lower()]
    if len(contains) == 1:
        return contains[0]
    if len(contains) > 1:
        raise AmbiguousMatchError(q, contains)

    raise NotFoundError(f"{kind} не найден по запросу {q!r}")


# ---- URL / id normalization ----


def normalize_page_id(raw: str) -> str:
    """
    Принимает UUID с дефисами, без дефисов, или URL notion.so/...-<32hex>.
    """
    raw = raw.strip()
    if raw.startswith("http"):
        raw = raw.rstrip("/").split("/")[-1]
        raw = raw.split("?")[0]
        raw = raw.split("-")[-1] if len(raw) > 32 else raw
    hex_only = raw.replace("-", "").lower()
    if len(hex_only) != 32:
        raise ValueError(f"не UUID: {raw!r}")
    return (
        f"{hex_only[0:8]}-{hex_only[8:12]}-{hex_only[12:16]}-"
        f"{hex_only[16:20]}-{hex_only[20:32]}"
    )
