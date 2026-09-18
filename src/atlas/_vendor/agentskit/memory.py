"""Движок ПАМЯТИ агента: адрес рабочего контекста как данные, а не константа.

Это была единственная поверхность из аудита, которой кит не знал вовсе, — при
том что его собственный рабочий контекст живёт именно там. Пока адрес не
назван данными, инструменты собирают его строкой у себя внутри, и смена
раскладки у агента ломает их молча: файл пишется, никто не жалуется, а агент
его не читает.

Что здесь есть и чего нет:

* **есть** — где лежит память, как зовётся папка проекта, что читать первым;
* **нет** — разбора содержимого. Формат заметки принадлежит тому, кто её
  пишет, и кит не вправе решать за него, где у него заголовок.

Имя папки проекта — не путь и не хеш, а путь с заменой каждого не
буквенно-цифрового символа дефисом. Правило снято с живой машины и сверено на
трёх путях: по одному примеру его не вывести, потому что двойной дефис
возникает и от двоеточия с разделителем, и от разделителя с подчёркиванием.
"""
from __future__ import annotations

import re
from pathlib import Path

from .hooks import _spec_of
from .spec import AgentSpec, MemoryLayout

#: Известные способы получить имя папки проекта.
PROJECT_KEYS = ("slug-dashes",)


def memory_layout_for(agent: str | AgentSpec) -> MemoryLayout | None:
    """Раскладка памяти агента. ``None`` — агент её не держит либо не изучен."""
    return _spec_of(agent).memory


def project_key(path: str | Path, *, kind: str = "slug-dashes") -> str:
    """Имя папки проекта по его пути.

    ``slug-dashes`` — каждый не буквенно-цифровой символ становится дефисом.
    Проверено на живой машине: ``C:\\Users\\example\\Documents\\PROJECT\\_storage\\atlas``
    даёт ``C--Users-example-Documents-PROJECT--storage-atlas``.
    """
    if kind not in PROJECT_KEYS:
        raise ValueError(
            f"неизвестный способ имени папки проекта {kind!r} — "
            f"известны: {', '.join(PROJECT_KEYS)}"
        )
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def memory_dir(
    agent: str | AgentSpec,
    *,
    home: Path,
    project: str | Path | None = None,
    root_override: str | Path | None = None,
) -> Path | None:
    """Папка памяти агента. ``None`` — агент память не держит.

    ``root_override`` — значение настройки, переопределяющей корень целиком
    (у Claude это ``autoMemoryDirectory``). Отдаём его аргументом, а не читаем
    настройки сами: кит не знает, где у потребителя лежит конфиг, и гадать об
    этом хуже, чем принять готовое значение.
    """
    spec = _spec_of(agent)
    layout = spec.memory
    if layout is None:
        return None

    base = Path(root_override).expanduser() if root_override else home.joinpath(
        spec.config_dir, *layout.root
    )
    if layout.per_project:
        if project is None:
            return None  # без проекта адрес не собрать — это не ошибка, а «нечего сказать»
        base = base / project_key(project, kind=layout.project_key)
    return base.joinpath(*layout.subdir)


def memory_index(
    agent: str | AgentSpec,
    *,
    home: Path,
    project: str | Path | None = None,
    root_override: str | Path | None = None,
) -> Path | None:
    """Головной файл памяти — тот, который агент читает первым."""
    spec = _spec_of(agent)
    layout = spec.memory
    directory = memory_dir(agent, home=home, project=project, root_override=root_override)
    if layout is None or directory is None:
        return None
    return directory / layout.index_file


def memory_files(
    agent: str | AgentSpec,
    *,
    home: Path,
    project: str | Path | None = None,
    root_override: str | Path | None = None,
) -> list[Path]:
    """Что лежит в памяти. Пусто — либо папки нет, либо агент память не держит.

    Содержимое не разбираем: формат заметки принадлежит тому, кто её писал.
    """
    directory = memory_dir(agent, home=home, project=project, root_override=root_override)
    if directory is None or not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file())


__all__ = [
    "PROJECT_KEYS",
    "memory_dir",
    "memory_files",
    "memory_index",
    "memory_layout_for",
    "project_key",
]
