"""CLI `atlas update` — самообновление Atlas.

- Основной путь: версия сверяется с **PyPI** (dist ``atlas-pm``), обновление —
  авто-детект менеджера (``uv tool`` / ``pipx`` / ``pip``) по ``sys.executable``.
  На Windows launcher ``atlas.exe`` залочен, пока процесс жив, поэтому апгрейд
  запускается отдельным detached-процессом с задержкой и применяется со
  следующего запуска.
- ``--from-git`` (legacy): полная переустановка из git (``pipx install --force``)
  — для старых pipx-git-установок. Заменяет прежнюю отдельную команду ``atlas upgrade``.
- **editable (dev)** — подсказывает ``git pull`` (код живой).

``--check`` — только показать текущую/последнюю версию, не обновлять.
"""
from __future__ import annotations

import json as _json
import os
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution, version
from typing import Any

import typer
from clikit import CliError, command, emit_data
from rich.console import Console

console = Console()

#: dist-имя на PyPI (import-пакет и команда — ``atlas``; имя ``atlas`` занято).
DIST_NAME = "atlas-pm"
#: Публичный git-источник Atlas (для legacy pipx reinstall).
DEFAULT_GIT_SOURCE = "git+https://github.com/zZZTeJleTTy3uKZZz/atlas.git"
#: Страница релиза версии — что именно изменилось. CHANGELOG.md в wheel не
#: попадает, страница PyPI показывает README, поэтому ссылка — единственный
#: способ узнать содержимое обновления прямо из CLI.
RELEASES_URL = "https://github.com/zZZTeJleTTy3uKZZz/atlas/releases/tag/v{version}"
IS_WINDOWS = os.name == "nt"


def _current_version() -> str:
    for name in (DIST_NAME, "atlas"):
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    try:  # editable / не установлен — из кода
        from atlas import __version__
        return __version__
    except Exception:
        return "0.0.0"


def _install_method() -> str:
    """editable | pipx-git | uv-tool | pipx | pip — по метаданным дистрибутива."""
    exe = (sys.executable or "").replace("\\", "/").lower()
    try:
        d = distribution(DIST_NAME) if _has_dist(DIST_NAME) else distribution("atlas")
        durl = d.read_text("direct_url.json")
        if durl:
            info = _json.loads(durl)
            if info.get("dir_info", {}).get("editable"):
                return "editable"
            if "git" in (info.get("vcs_info", {}).get("vcs", "") or info.get("url", "")):
                return "pipx-git"
    except Exception:
        pass
    if "/uv/tools/" in exe:
        return "uv-tool"
    if "/pipx/" in exe:
        return "pipx"
    return "pip"


def _has_dist(name: str) -> bool:
    try:
        version(name)
        return True
    except PackageNotFoundError:
        return False


def _latest_pypi_version(dist: str = DIST_NAME, timeout: float = 6.0) -> str:
    """Последняя версия пакета с PyPI JSON API (без внешних зависимостей)."""
    import urllib.request

    url = f"https://pypi.org/pypi/{dist}/json"
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — фикс. host
        return _json.load(r)["info"]["version"]


def _parse_ver(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for p in (v or "0").split("."):
        num = "".join(c for c in p if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def _is_newer(latest: str, current: str) -> bool:
    return _parse_ver(latest) > _parse_ver(current)


def _detect_upgrade_command(dist: str = DIST_NAME) -> list[str]:
    """Команда апгрейда по менеджеру установки (эталон — skillery-cli)."""
    exe = (sys.executable or "").replace("\\", "/").lower()
    if "/uv/tools/" in exe and shutil.which("uv"):
        return ["uv", "tool", "upgrade", dist]
    if "/pipx/" in exe and shutil.which("pipx"):
        return ["pipx", "upgrade", dist]
    if shutil.which("uv"):
        return ["uv", "tool", "upgrade", dist]
    if shutil.which("pipx"):
        return ["pipx", "upgrade", dist]
    return [sys.executable, "-m", "pip", "install", "--upgrade", dist]


def _run_upgrade(cmd: list[str]) -> str:
    """Выполнить апгрейд. На Windows — detached (launcher .exe залочен, os error 32):
    ждём ~4с, пока текущий процесс выйдет, затем апгрейд; применяется со следующего
    запуска. На POSIX — синхронно."""
    if IS_WINDOWS:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        payload = "import time, subprocess; time.sleep(4); subprocess.run(%r)" % (cmd,)
        subprocess.Popen(  # noqa: S603 — фикс. аргументы
            [sys.executable, "-c", payload],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        return "scheduled"  # применится со следующего запуска atlas
    rc = subprocess.run(cmd, check=False).returncode  # noqa: S603
    return "done" if rc == 0 else f"failed(rc={rc})"


def _render_update(d: dict[str, Any]) -> None:
    console.print(
        f"Atlas [bold]{d['current']}[/bold] · установка: [cyan]{d['method']}[/cyan]"
        + (f" · PyPI: [bold]{d['latest']}[/bold]" if d.get("latest") else "")
    )
    if d.get("update_available"):
        console.print("  [yellow]доступно обновление[/yellow]")
        if d.get("release_notes_url"):
            console.print(f"  что нового: [blue]{d['release_notes_url']}[/blue]")
    elif d.get("latest"):
        console.print("  [green]актуальная версия[/green]")
    if d.get("hint"):
        console.print(f"  {d['hint']}")
    if d.get("ran"):
        tail = {
            "scheduled": "запущено в фоне, применится со следующего запуска",
            "done": "готово (проверь: atlas --version)",
        }.get(d.get("result", ""), d.get("result", ""))
        console.print(f"  [green]✓[/green] {d['ran']} — {tail}")


@command
def update_cmd(
    check: bool = typer.Option(
        False, "--check", help="Только сверить версию с PyPI, не обновлять.",
    ),
    from_git: bool = typer.Option(
        False, "--from-git",
        help="Legacy: переустановить из git (pipx install --force), а не обновлять с PyPI.",
    ),
    source: str = typer.Option(
        DEFAULT_GIT_SOURCE, "--source", help="git-источник для --from-git.",
    ),
) -> None:
    """Обновить Atlas с PyPI (авто-детект uv tool / pipx / pip); --from-git — legacy pipx-reinstall из git."""
    method = _install_method()
    current = _current_version()
    data: dict[str, Any] = {"current": current, "method": method}

    # Legacy git-reinstall (бывшая отдельная команда `atlas upgrade --reinstall`).
    if from_git:
        if shutil.which("pipx") is None:
            raise CliError(
                "no_pipx",
                f"pipx не найден. Вручную: pipx install --force \"{source}\" "
                "(или обычный `atlas update` для PyPI).",
            )
        cmd = ["pipx", "install", "--force", source]
        rc = subprocess.run(cmd, check=False).returncode  # noqa: S603
        if rc != 0:
            raise CliError("update_failed", f"pipx вернул код {rc}. Команда: {' '.join(cmd)}")
        data["ran"] = " ".join(cmd)
        emit_data(data, text_renderer=_render_update)
        return

    if method in ("editable", "pipx-git"):
        data["hint"] = (
            "git-установка — обнови `git pull` в репозитории (editable) "
            "или `atlas update --from-git` (pipx-reinstall из git)."
        )
        emit_data(data, text_renderer=_render_update)
        return

    try:
        latest = _latest_pypi_version()
    except Exception as e:  # noqa: BLE001
        raise CliError("pypi_unreachable", f"не удалось проверить PyPI ({DIST_NAME}): {e}")
    data["latest"] = latest
    data["update_available"] = _is_newer(latest, current)
    if data["update_available"]:
        data["release_notes_url"] = RELEASES_URL.format(version=latest)

    if check or not data["update_available"]:
        emit_data(data, text_renderer=_render_update)
        return

    cmd = _detect_upgrade_command()
    data["result"] = _run_upgrade(cmd)
    data["ran"] = " ".join(cmd)
    emit_data(data, text_renderer=_render_update)
