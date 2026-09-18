"""Виды жалоб как ДАННЫЕ (загружаются из ``data/kinds.json``).

Добавить вид/секцию = правка JSON, без правки кода. ``kinds.json`` — единый
language-neutral источник правды (его же сможет прочитать будущий CLI на другом
языке).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources


@dataclass(frozen=True)
class Section:
    """Секция шаблона жалобы."""

    key: str
    label: str
    required: bool
    hint: str = ""


@dataclass(frozen=True)
class KindSpec:
    """Вид жалобы: bug | feature | handoff — набор секций."""

    key: str
    label: str
    title_hint: str
    sections: tuple[Section, ...]

    def required_sections(self) -> tuple[Section, ...]:
        return tuple(s for s in self.sections if s.required)


@lru_cache(maxsize=1)
def _registry() -> dict[str, KindSpec]:
    raw = resources.files(__package__).joinpath("data", "kinds.json").read_text("utf-8")
    data = json.loads(raw)
    out: dict[str, KindSpec] = {}
    for key, d in data["kinds"].items():
        sections = tuple(
            Section(s["key"], s["label"], bool(s.get("required", False)), s.get("hint", ""))
            for s in d["sections"]
        )
        out[key] = KindSpec(key, d.get("label", key), d.get("title_hint", ""), sections)
    return out


def list_kinds() -> list[str]:
    """Доступные виды жалоб (ключи)."""
    return list(_registry().keys())


def get_kind(kind: str) -> KindSpec:
    """KindSpec по ключу. Неизвестный → ValueError со списком доступных."""
    reg = _registry()
    if kind not in reg:
        raise ValueError(
            f"Неизвестный вид '{kind}'. Доступно: {', '.join(reg)}."
        )
    return reg[kind]
