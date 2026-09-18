"""Рендер пустого шаблона жалобы по виду (bug/feature/handoff)."""
from __future__ import annotations

from .spec import get_kind

#: Маркер секции — по нему же ``lint`` находит секции в тексте.
SECTION_PREFIX = "## "


def section_header(label: str) -> str:
    """Заголовок секции (единый формат для render + lint)."""
    return f"{SECTION_PREFIX}{label}"


def new_template(kind: str, *, title: str | None = None) -> str:
    """Пустой markdown-шаблон жалобы вида ``kind`` с подсказками в секциях."""
    spec = get_kind(kind)
    lines: list[str] = [
        f"# [{spec.label}] {title or spec.title_hint}",
        "",
    ]
    for s in spec.sections:
        req = "" if s.required else "  _(опционально)_"
        lines.append(section_header(s.label) + req)
        lines.append(f"<!-- {s.hint} -->" if s.hint else "")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
