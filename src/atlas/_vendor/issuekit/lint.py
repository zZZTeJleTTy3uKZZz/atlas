"""Валидатор жалобы: проверка наличия и непустоты обязательных секций.

«Богатая обратная связь» машинно: ``lint(text, kind)`` говорит, каких полей
чеклиста не хватает (versions/repro/traceback/expected/…), и считает балл. На
этом строится блокирующая дисциплина передачи задач в Atlas (нельзя передать
неполную жалобу — симметрично обязательному ЦКП у задачи).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .render import SECTION_PREFIX
from .spec import Section, get_kind

#: HTML-комментарии (подсказки шаблона) — не считаются содержимым.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
#: Хвост-маркер опциональности в заголовке.
_OPT_RE = re.compile(r"_\(опционально\)_", re.IGNORECASE)


@dataclass(frozen=True)
class LintResult:
    """Итог проверки жалобы."""

    kind: str
    ok: bool                       # все обязательные секции заполнены
    score: float                   # доля заполненных обязательных (0..1)
    missing: list[str] = field(default_factory=list)   # labels незаполненных required
    present: list[str] = field(default_factory=list)   # labels заполненных
    empty_optional: list[str] = field(default_factory=list)


def _split_sections(text: str) -> dict[str, str]:
    """Разбить текст по ``## <label>`` на {нормализованный_label: содержимое}."""
    out: dict[str, str] = {}
    cur_label: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        if cur_label is not None:
            out[cur_label] = "\n".join(buf)

    for line in text.splitlines():
        if line.startswith(SECTION_PREFIX):
            _flush()
            header = line[len(SECTION_PREFIX):]
            header = _OPT_RE.sub("", header).strip().rstrip("#").strip()
            cur_label = header.lower()
            buf = []
        elif cur_label is not None:
            buf.append(line)
    _flush()
    return out


def _is_filled(content: str) -> bool:
    """Непусто ли содержимое секции (после вырезания подсказок-комментариев)."""
    stripped = _COMMENT_RE.sub("", content)
    # убрать пустые код-фенсы ``` ``` и whitespace
    stripped = stripped.replace("```", "").strip()
    return bool(stripped)


def lint(text: str, kind: str) -> LintResult:
    """Проверить текст жалобы вида ``kind``: какие обязательные секции пусты."""
    spec = get_kind(kind)
    sections = _split_sections(text or "")

    def _find(s: Section) -> str | None:
        key = s.label.lower()
        if key in sections:
            return sections[key]
        # мягкое совпадение: заголовок содержит label
        for hk, hv in sections.items():
            if key in hk:
                return hv
        return None

    missing: list[str] = []
    present: list[str] = []
    empty_optional: list[str] = []
    req_total = 0
    req_filled = 0
    for s in spec.sections:
        content = _find(s)
        filled = content is not None and _is_filled(content)
        if s.required:
            req_total += 1
            if filled:
                req_filled += 1
                present.append(s.label)
            else:
                missing.append(s.label)
        elif not filled:
            empty_optional.append(s.label)
        elif filled:
            present.append(s.label)

    score = (req_filled / req_total) if req_total else 1.0
    return LintResult(
        kind=kind, ok=not missing, score=round(score, 3),
        missing=missing, present=present, empty_optional=empty_optional,
    )
