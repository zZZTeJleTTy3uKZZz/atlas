"""Низкоуровневая обёртка над Windows junction-ссылками.

Junction (NTFS reparse point типа `IO_REPARSE_TAG_MOUNT_POINT`) — это
символическая ссылка на директорию, которая:

- Создаётся через ``cmd /c mklink /J <link> <target>`` (без админских прав на
  локальном NTFS).
- Удаляется через ``cmd /c rmdir <link>`` — удаляется ТОЛЬКО ссылка, таргет
  не трогается. Это критически важная семантика: при `remove_junction` мы
  гарантируем, что `<target>` остаётся нетронутым.
- Похожа на Unix-симлинк, но без поддержки UNC и без кросс-платформенности.

Этот модуль:

- Знает только про junction. Файловые операции (move/copy) — в `layout.py`.
- Никогда не удаляет реальные папки: `remove_junction` всегда проверяет,
  что путь — это junction, иначе кидает `SafetyError`.
- Кросс-платформенно компилируется (импорт работает на любой ОС), но
  фактические operations возможны только на Windows.

Используется PM-слоем (Atlas, §3.14 SKILL.md «Layout & junction architecture»).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #


class SafetyError(RuntimeError):
    """SAFETY: пытались выполнить опасную операцию (например, удалить через
    `remove_junction` реальный каталог, а не junction).

    Никогда не должна реально срабатывать в продакшене — она существует именно
    как защита от программных ошибок, а не как штатное поведение.
    """


class JunctionError(RuntimeError):
    """Generic ошибка при работе с junction (mklink/rmdir вернули non-zero и т.д.)."""


# --------------------------------------------------------------------------- #
# OS detection                                                                #
# --------------------------------------------------------------------------- #


def is_windows() -> bool:
    """True на Windows, False на любой другой ОС."""
    return sys.platform == "win32"


# --------------------------------------------------------------------------- #
# Detection: is_junction / junction_target                                    #
# --------------------------------------------------------------------------- #


# Reparse-tag для junction (mount point) — IO_REPARSE_TAG_MOUNT_POINT.
# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fscc/c8e77b37-3909-4fe6-a4ea-2b9d423b1ee4
_REPARSE_TAG_MOUNT_POINT = 0xA0000003
# FILE_ATTRIBUTE_REPARSE_POINT
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def is_junction(p: Path) -> bool:
    """True если путь существует и является NTFS junction (reparse point).

    Реализация:
    1. Путь должен существовать как директория.
    2. На Windows — проверяем атрибут REPARSE_POINT в st_file_attributes.
       Это покрывает symlink и junction; для нашей задачи различать их не
       обязательно (управляем мы только junction'ами).
    3. На не-Windows — используем `os.path.islink`.

    Не существующий путь / файл / обычная директория → False.
    """
    p = Path(p)
    try:
        if not p.exists():
            # Симлинк на несуществующий target тоже бывает; islink проверяет
            # сам линк независимо от target.
            if os.path.islink(str(p)):
                return True
            return False
    except OSError:
        return False

    if not is_windows():
        # На POSIX используем стандартный islink.
        return os.path.islink(str(p))

    try:
        st = p.lstat()
    except OSError:
        return False

    file_attrs = getattr(st, "st_file_attributes", None)
    if file_attrs is None:
        return False
    if not (file_attrs & _FILE_ATTRIBUTE_REPARSE_POINT):
        return False

    # Различаем symlink (тег MS_SYMLINK = 0xA000000C) и junction
    # (MOUNT_POINT = 0xA0000003). Если есть st_reparse_tag — используем его.
    reparse_tag = getattr(st, "st_reparse_tag", None)
    if reparse_tag is not None:
        return reparse_tag == _REPARSE_TAG_MOUNT_POINT

    # Если st_reparse_tag недоступен (старая версия Python) — считаем любую
    # директорию-reparse-point junction'ом. Этого достаточно для нашей задачи,
    # так как мы создаём только junction.
    return True


def junction_target(p: Path) -> Optional[Path]:
    """Прочитать таргет junction'а.

    На Python 3.12+ `Path.readlink()` поддерживает junction. На более старых
    версиях используем fallback через ``fsutil reparsepoint query``.

    Returns ``None`` если ``p`` не junction.
    """
    p = Path(p)
    if not is_junction(p):
        return None

    try:
        target = os.readlink(str(p))
        return _strip_unc_prefix(Path(target))
    except (OSError, NotImplementedError):
        # Fallback на fsutil — он печатает строку «Substitute Name: \??\C:\...»
        if not is_windows():
            return None
        result = _junction_target_via_fsutil(p)
        if result is None:
            return None
        return _strip_unc_prefix(result)


def _strip_unc_prefix(p: Path) -> Path:
    r"""Срезать префикс ``\\?\`` (extended-length path), если он есть.

    `os.readlink` на Windows для junction может вернуть путь вида
    ``\\?\C:\foo\bar``. Для сравнения с обычными ``Path`` этот префикс мешает,
    поэтому приводим к нормальной форме.
    """
    s = str(p)
    if s.startswith("\\\\?\\"):
        return Path(s[4:])
    return p


def _junction_target_via_fsutil(p: Path) -> Optional[Path]:
    """Парсит вывод ``fsutil reparsepoint query`` для извлечения таргета."""
    try:
        result = subprocess.run(
            ["fsutil", "reparsepoint", "query", str(p)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        # «Substitute Name: \??\C:\Users\...» или локализованные варианты —
        # ищем символы «\??\»
        if "\\??\\" in line:
            idx = line.find("\\??\\")
            target = line[idx + 4:]
            return Path(target)
    return None


# --------------------------------------------------------------------------- #
# create_junction / remove_junction                                           #
# --------------------------------------------------------------------------- #


def create_junction(link: Path, target: Path) -> None:
    """Создать ссылку-директорию `link` → `target` (kross-platform).

    Порядок проверок (до создания, общий для всех ОС):
    1. ``link.parent`` существует.
    2. ``link`` ещё не существует.
    3. ``target`` существует и является директорией.

    На Windows используется ``cmd /c mklink /J "<link>" "<target>"`` (NTFS
    junction, не требует админ-прав на локальном томе). На POSIX используется
    ``os.symlink(target, link, target_is_directory=True)`` — обычный
    directory-симлинк (``target_is_directory`` важен только для сигнатуры
    Windows-совместимости, на POSIX игнорируется).

    Любая ошибка (non-zero subprocess или исключение) превращается в
    `JunctionError` с деталями.
    """
    link = Path(link)
    target = Path(target)

    # --- pre-проверки, общие для всех ОС (ДО ветвления) ---
    if not link.parent.exists():
        raise JunctionError(
            f"Parent directory не существует: {link.parent}. "
            f"Сначала создайте её."
        )
    if link.exists() or os.path.islink(str(link)):
        raise JunctionError(
            f"Link уже существует: {link}. Сначала удалите/переместите."
        )
    if not target.exists():
        raise JunctionError(
            f"Target не существует: {target}. Создание ссылки требует "
            f"существующую директорию-таргет."
        )
    if not target.is_dir():
        raise JunctionError(
            f"Target не является директорией: {target}. Junction/symlink "
            f"требует директорию."
        )

    if is_windows():
        cmd = ["cmd", "/c", "mklink", "/J", str(link), str(target)]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise JunctionError(f"mklink subprocess failed: {exc}") from exc

        if result.returncode != 0:
            raise JunctionError(
                f"mklink /J вернул {result.returncode}. "
                f"stdout: {result.stdout.strip()!r}; "
                f"stderr: {result.stderr.strip()!r}"
            )
    else:
        # POSIX: обычный directory-симлинк. `target_is_directory` нужен только
        # для совместимости сигнатуры с Windows; на POSIX он игнорируется.
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
        except OSError as exc:
            raise JunctionError(
                f"symlink({link} -> {target}) failed: {exc}"
            ) from exc


def remove_junction(link: Path) -> None:
    """Удалить junction-ссылку (НЕ таргет).

    SAFETY: перед `rmdir` проверяет, что ``link`` действительно junction.
    Если это реальная папка — поднимает `SafetyError` и НЕ трогает её.

    Команда: ``cmd /c rmdir "<link>"`` — удаляет ТОЛЬКО reparse-point.
    """
    link = Path(link)

    if not link.exists() and not os.path.islink(str(link)):
        raise JunctionError(f"Link не существует: {link}")

    if not is_junction(link):
        raise SafetyError(
            f"Отказ удалять {link}: это не junction, а реальная папка/файл. "
            f"remove_junction() предназначен ТОЛЬКО для junction-ссылок."
        )

    if is_windows():
        cmd = ["cmd", "/c", "rmdir", str(link)]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise JunctionError(f"rmdir subprocess failed: {exc}") from exc
        if result.returncode != 0:
            raise JunctionError(
                f"rmdir вернул {result.returncode}. "
                f"stdout: {result.stdout.strip()!r}; "
                f"stderr: {result.stderr.strip()!r}"
            )
    else:
        # На POSIX есть симлинки — `os.unlink` снимает только сам линк.
        try:
            os.unlink(str(link))
        except OSError as exc:
            raise JunctionError(f"unlink({link}) failed: {exc}") from exc
