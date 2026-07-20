#!/usr/bin/env python3
"""Извлекает секцию версии из CHANGELOG.md — единственный источник release notes.

Одним и тем же текстом наполняются: аннотация git-тега (publish_public_github.sh),
GitHub Release (.github/workflows/publish.yml) и проверка CI-гарда «секция для
версии существует». Ручной формат CHANGELOG при этом не меняется — механизируется
только доставка.

    python scripts/release_notes.py 0.3.4 [CHANGELOG.md]

Печатает секцию в stdout; если секции нет — пишет в stderr и выходит с кодом 1
(молчаливый «пустой релиз» хуже упавшего пайплайна).
"""
from __future__ import annotations

import sys
from pathlib import Path

HEADING_PREFIX = "## "


def extract(changelog: Path | str, version: str) -> str:
    """Возвращает секцию версии (вместе со строкой заголовка).

    Заголовки в CHANGELOG.md имеют вид `## 0.3.4 — описание`, версия — второе
    слово. Допускается и `## [0.3.4]` (Keep a Changelog). Префикс `v` у версии
    снимается: наверх приходит имя тега.
    """
    wanted = version.lstrip("vV").strip()
    text = Path(changelog).read_text(encoding="utf-8")

    section: list[str] = []
    for line in text.splitlines():
        if line.startswith(HEADING_PREFIX):
            if section:  # начался следующий раздел — наш закончился
                break
            parts = line[len(HEADING_PREFIX):].split()
            found = parts[0].strip("[]") if parts else ""
            if found == wanted:
                section.append(line)
            continue
        if section:
            section.append(line)

    if not section:
        raise LookupError(f"в {changelog} нет секции для версии {wanted}")
    return "\n".join(section).strip() + "\n"


def main(argv: list[str]) -> int:
    if not 1 <= len(argv) <= 2:
        print(__doc__, file=sys.stderr)
        return 2
    version = argv[0]
    changelog = Path(argv[1]) if len(argv) == 2 else Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    try:
        sys.stdout.write(extract(changelog, version))
    except (LookupError, OSError) as exc:
        print(f"release_notes: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
