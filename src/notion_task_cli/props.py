"""Чтение/запись Notion-свойств в человекочитаемом виде."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from dateutil import parser as date_parser
from dateutil import tz


def _text(chunks: list[dict[str, Any]] | None) -> str:
    if not chunks:
        return ""
    return "".join(c.get("plain_text", "") for c in chunks)


def read_title(page: dict[str, Any], prop: str) -> str:
    p = (page.get("properties") or {}).get(prop) or {}
    return _text(p.get("title"))


def read_rich_text(page: dict[str, Any], prop: str) -> str:
    p = (page.get("properties") or {}).get(prop) or {}
    return _text(p.get("rich_text"))


def read_status(page: dict[str, Any], prop: str) -> str | None:
    p = (page.get("properties") or {}).get(prop) or {}
    s = p.get("status")
    return s.get("name") if s else None


def read_multi_select(page: dict[str, Any], prop: str) -> list[str]:
    p = (page.get("properties") or {}).get(prop) or {}
    return [opt.get("name") for opt in p.get("multi_select", []) if opt.get("name")]


def read_number(page: dict[str, Any], prop: str) -> int | float | None:
    p = (page.get("properties") or {}).get(prop) or {}
    return p.get("number")


def read_checkbox(page: dict[str, Any], prop: str) -> bool:
    p = (page.get("properties") or {}).get(prop) or {}
    return bool(p.get("checkbox"))


def read_relation_ids(page: dict[str, Any], prop: str) -> list[str]:
    p = (page.get("properties") or {}).get(prop) or {}
    return [r["id"] for r in p.get("relation", []) if r.get("id")]


@dataclass
class DateValue:
    start: datetime | date | None
    end: datetime | date | None
    has_time: bool  # True если start содержит время

    @property
    def as_date(self) -> date | None:
        if self.start is None:
            return None
        if isinstance(self.start, datetime):
            return self.start.date()
        return self.start


def read_date(page: dict[str, Any], prop: str) -> DateValue | None:
    p = (page.get("properties") or {}).get(prop) or {}
    d = p.get("date")
    if not d:
        return None
    return _parse_date(d)


def _parse_date(d: dict[str, Any]) -> DateValue:
    def _one(raw: str | None) -> tuple[datetime | date | None, bool]:
        if not raw:
            return None, False
        if "T" in raw:
            return date_parser.isoparse(raw), True
        return date_parser.isoparse(raw).date(), False

    s_val, s_has = _one(d.get("start"))
    e_val, _ = _one(d.get("end"))
    return DateValue(start=s_val, end=e_val, has_time=s_has)


# ---- writers ----


def w_title(text: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": text}}]}


def w_rich_text(text: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": text}}] if text else []}


def w_status(name: str) -> dict[str, Any]:
    return {"status": {"name": name}}


def w_multi_select(names: list[str]) -> dict[str, Any]:
    return {"multi_select": [{"name": n} for n in names]}


def w_relation(ids: list[str]) -> dict[str, Any]:
    return {"relation": [{"id": i} for i in ids]}


def w_checkbox(value: bool) -> dict[str, Any]:
    return {"checkbox": bool(value)}


def w_date(start: date | datetime | None, end: date | datetime | None = None,
           *, tz_name: str = "Europe/Moscow") -> dict[str, Any]:
    """Форматируем дату в Notion-совместимый вид.
    Для datetime — ISO 8601 с таймзоной, для date — YYYY-MM-DD.
    """
    def _fmt(v: date | datetime | None) -> str | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=tz.gettz(tz_name))
            return v.isoformat()
        return v.isoformat()

    if start is None:
        return {"date": None}
    return {"date": {"start": _fmt(start), "end": _fmt(end)}}


def w_number(value: int | float | None) -> dict[str, Any]:
    return {"number": value}
