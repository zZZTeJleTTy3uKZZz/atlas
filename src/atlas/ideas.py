"""Idea management — utility functions для W45-38.

Idea = Project с entity_kind='idea'. Живёт как один MD-файл в `_Ideas/<slug>.md`.

Этот модуль содержит:
- ``IDEA_TEMPLATE`` / ``IDEAS_BACKLOG_TEMPLATE`` — шаблоны MD.
- ``render_idea_md()`` — генерация контента `_Ideas/<slug>.md` из метаданных.
- ``extract_idea_backlog()`` — regex-парсер: извлечь секцию `### #<slug>`
  из общего `_Ideas/BACKLOG.md`. При promote эта секция переезжает в
  `_storage/<slug>/BACKLOG.md`, а из исходника удаляется.
- ``ensure_ideas_root()`` — создать `_Ideas/` + `README.md` + `BACKLOG.md`
  с template'ами, если отсутствуют.

Дизайн: всё, что трогает физику, — в этом модуле; CLI (commands/ideas.py) —
только тонкая обёртка над ним.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------- #
# Templates                                                                   #
# --------------------------------------------------------------------------- #


IDEAS_README_TEMPLATE = """\
# _Ideas/ — incubator для idea-stage записей

> Сюда попадают **сформулированные мысли** о новых проектах/продуктах,
> которые ещё не решено делать. Каждая идея = один MD-файл.

## Канон

- 1 идея = 1 файл `_Ideas/<slug>.md` (slug совпадает с `Project.slug` в
  Atlas-БД, `entity_kind='idea'`).
- Когда идея созревает — `atlas ideas promote <slug>` переводит её в
  полноценный проект (`_storage/<slug>/` + junction).
- Когда идея отброшена — `atlas ideas update <slug> --status cancelled`.

## Backlog

`BACKLOG.md` — общий список задач по всем идеям. Convention:

```markdown
### #<slug-идеи>
- [ ] **P0** Задача 1
- [ ] **P1** Задача 2

### #<другая-идея>
- [ ] ...
```

Строки `### #<slug>` обязательны для группировки. При `atlas ideas promote
<slug>` соответствующая секция перенесётся в `_storage/<slug>/BACKLOG.md` и
удалится из этого файла.

## Команды

```sh
atlas ideas add --slug <s> --name <n> --type <t> [--priority P2] [--tag ...]
atlas ideas list                # фильтры --type / --tag / --status
atlas ideas show <slug>         # карточка БД + содержимое .md
atlas ideas promote <slug> [--status active] [--init-git]
atlas ideas demote <slug>       # обратно в idea (если решили что не время)
```
"""


IDEAS_BACKLOG_TEMPLATE = """\
# Backlog по идеям (incubator)

> Convention: каждая идея — отдельная секция `### #<slug>`. При
> `atlas ideas promote <slug>` секция переезжает в новый
> `_storage/<slug>/BACKLOG.md`.

## По идеям

(пусто — секции добавляются по мере появления идей)

## Общее (не привязано к конкретной идее)

(idea-cross-cutting задачи)
"""


IDEA_MD_TEMPLATE = """\
# {name}

> {one_line}

## Метаданные

- **Slug**: `{slug}`
- **Type-hint** (на промоут): `{type_slug}`
- **Priority**: {priority}
- **Status**: {status_slug}
- **Owner**: {owner_str}
- **Tags**: {tags_str}
- Создано: {created_date}

## Проблема / гипотеза

(заполнить — что за проблема, у кого, как сейчас решают, наша гипотеза)

## Целевой ICP

(кто пользователь, какой сегмент рынка, размер)

## MVP scope

(минимальный продукт-обещание для проверки problem-fit)

## Decision criteria (что должно произойти, чтобы промоутнуть в project)

- [ ] (например, 3 кастомера сказали «дам предзаказ»)
- [ ] (или хотя бы 1 разговор с pilot-клиентом подтвердил problem-fit)

## Ресурсы

- (ссылки, NotebookLM blocks, конкуренты, etc.)
"""


# --------------------------------------------------------------------------- #
# Rendering                                                                   #
# --------------------------------------------------------------------------- #


def render_idea_md(
    *,
    name: str,
    slug: str,
    type_slug: str,
    priority: str,
    status_slug: str,
    one_line: str,
    owner_tags: Optional[list[str]] = None,
    all_tags: Optional[list[str]] = None,
    created_at: Optional[datetime] = None,
) -> str:
    """Сгенерировать содержимое `_Ideas/<slug>.md` из метаданных проекта.

    Не пишет на диск — caller сам решает.
    """
    created = created_at or datetime.now()
    owner_str = ", ".join(f"`{o}`" for o in (owner_tags or [])) or "—"
    tags_str = (
        ", ".join(f"`{t}`" for t in (all_tags or [])) if all_tags else "—"
    )
    return IDEA_MD_TEMPLATE.format(
        name=name,
        slug=slug,
        type_slug=type_slug,
        priority=priority,
        status_slug=status_slug,
        one_line=one_line or "(заполнить one-line)",
        owner_str=owner_str,
        tags_str=tags_str,
        created_date=created.strftime("%Y-%m-%d"),
    )


# --------------------------------------------------------------------------- #
# Layout setup                                                                #
# --------------------------------------------------------------------------- #


def ensure_ideas_root(root: Path) -> Path:
    """Создать `<root>/_Ideas/` + `README.md` + `BACKLOG.md` если не существуют.

    Возвращает путь к `_Ideas/`. Идемпотентно: уже существующие файлы не
    перезаписываются.
    """
    ideas_dir = root / "_Ideas"
    ideas_dir.mkdir(parents=True, exist_ok=True)
    readme = ideas_dir / "README.md"
    if not readme.exists():
        readme.write_text(IDEAS_README_TEMPLATE, encoding="utf-8")
    backlog = ideas_dir / "BACKLOG.md"
    if not backlog.exists():
        backlog.write_text(IDEAS_BACKLOG_TEMPLATE, encoding="utf-8")
    return ideas_dir


def write_idea_md(
    ideas_dir: Path, slug: str, content: str, *, overwrite: bool = False
) -> Path:
    """Записать `_Ideas/<slug>.md`.

    Если файл существует и `overwrite=False` — raise FileExistsError.
    """
    path = ideas_dir / f"{slug}.md"
    if path.exists() and not overwrite:
        raise FileExistsError(f"Idea file already exists: {path}")
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Backlog extraction (W45-38d)                                                #
# --------------------------------------------------------------------------- #


# Заголовок секции по идее: `### #<slug>` (с возможным trailing whitespace).
# Завершение секции: следующий `### #...` или `## ...`.
_IDEA_SECTION_RE = re.compile(
    r"^### #(?P<slug>[a-z0-9-]+)\s*$", re.MULTILINE
)


def extract_idea_backlog(
    backlog_text: str,
    slug: str,
) -> tuple[Optional[str], str]:
    """Вырезать секцию `### #<slug>` из текста ``backlog_text``.

    Возвращает кортеж ``(extracted_block, remaining_text)``:
    - ``extracted_block`` — строки секции (заголовок + тело до следующего
      ###/## или EOF). ``None`` если секция не найдена.
    - ``remaining_text`` — исходный текст с удалённой секцией.

    Сохраняет одну пустую строку на месте удалённой секции (для читаемости
    оставшегося файла).
    """
    lines = backlog_text.splitlines(keepends=True)

    # Найти строку начала секции.
    start_idx: Optional[int] = None
    for i, line in enumerate(lines):
        m = _IDEA_SECTION_RE.match(line)
        if m and m.group("slug") == slug:
            start_idx = i
            break

    if start_idx is None:
        return None, backlog_text

    # Найти строку конца секции (следующий ### или ## или EOF).
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        line = lines[j]
        # Любая ### #<other-slug> или просто ### / ## завершает секцию.
        if line.startswith("### ") or line.startswith("## "):
            end_idx = j
            break

    extracted_block = "".join(lines[start_idx:end_idx]).rstrip() + "\n"
    # Удаляем секцию + одну ведущую пустую строку (если есть).
    before = lines[:start_idx]
    after = lines[end_idx:]

    # Trim trailing blank line из `before` (чтобы не накапливать).
    while before and before[-1].strip() == "":
        before.pop()

    if before and after:
        # Поставить одну пустую строку как разделитель.
        remaining_text = "".join(before) + "\n" + "".join(after)
    else:
        remaining_text = "".join(before + after)

    return extracted_block, remaining_text


def render_promoted_backlog(extracted_block: str, source_date: str) -> str:
    """Сформировать содержимое `_storage/<slug>/BACKLOG.md` из извлечённой
    секции.

    Добавляет заголовок-сноску про источник и дату переноса.
    """
    return (
        f"# Backlog\n"
        f"\n"
        f"> Перенесено из `_Ideas/BACKLOG.md` {source_date} при promote.\n"
        f"\n"
        f"{extracted_block.rstrip()}\n"
    )


# --------------------------------------------------------------------------- #
# Public API summary (для тестов и CLI)                                       #
# --------------------------------------------------------------------------- #


__all__ = [
    "IDEAS_README_TEMPLATE",
    "IDEAS_BACKLOG_TEMPLATE",
    "IDEA_MD_TEMPLATE",
    "render_idea_md",
    "ensure_ideas_root",
    "write_idea_md",
    "extract_idea_backlog",
    "render_promoted_backlog",
]
