"""CLI `atlas upgrade` — обновить Atlas без skillery (pipx/git).

Atlas ставится тремя путями (skillery / pipx из git / uv editable-dev). Обновление:
- **skillery** — через skillery (re-install + deps --upgrade); эта команда подскажет;
- **pipx из git** — ``pipx upgrade atlas`` (или ``--reinstall`` = force из git);
- **editable (dev)** — ``git pull`` в репо (код живой), reinstall не нужен.

``--check`` — только показать версию и метод установки, не обновлять.
"""
from __future__ import annotations

import json as _json
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, distribution, version
from typing import Any

import typer
from clikit import CliError, command, emit_data
from rich.console import Console

console = Console()

#: Публичный git-источник Atlas (для pipx reinstall).
DEFAULT_GIT_SOURCE = "git+https://github.com/zZZTeJleTTy3uKZZz/atlas.git"


def _current_version() -> str:
    try:
        return version("atlas")
    except PackageNotFoundError:
        return "0.0.0"


def _install_method() -> str:
    """editable | pipx-git | pipx | other — по метаданным дистрибутива."""
    try:
        d = distribution("atlas")
        durl = d.read_text("direct_url.json")
        if durl:
            info = _json.loads(durl)
            if info.get("dir_info", {}).get("editable"):
                return "editable"
            if "git" in (info.get("vcs_info", {}).get("vcs", "") or info.get("url", "")):
                return "pipx-git"
    except Exception:
        pass
    return "pipx"


@command
def upgrade_cmd(
    reinstall: bool = typer.Option(
        False, "--reinstall", help="Полная переустановка из git (force), а не pipx upgrade.",
    ),
    source: str = typer.Option(
        DEFAULT_GIT_SOURCE, "--source", help="git-источник для --reinstall.",
    ),
    check: bool = typer.Option(
        False, "--check", help="Только показать версию и метод установки.",
    ),
) -> None:
    """Обновить Atlas (pipx из git) или подсказать путь для editable/skillery."""
    method = _install_method()
    data: dict[str, Any] = {"current": _current_version(), "method": method}

    def _render(d: dict[str, Any]) -> None:
        console.print(f"Atlas [bold]{d['current']}[/bold] · установка: [cyan]{d['method']}[/cyan]")
        if d.get("hint"):
            console.print(f"  {d['hint']}")
        if d.get("ran"):
            console.print(f"  [green]✓ выполнено:[/green] {d['ran']} (проверь: atlas --version)")

    if method == "editable":
        data["hint"] = "editable (dev) — код живой; обнови `git pull` в репозитории Atlas."
        emit_data(data, text_renderer=_render)
        return

    if check:
        data["hint"] = ("обновить: `atlas upgrade` (pipx upgrade) или `--reinstall` (force из git). "
                        "skillery-установка — через skillery.")
        emit_data(data, text_renderer=_render)
        return

    if shutil.which("pipx") is None:
        raise CliError(
            "no_pipx",
            "pipx не найден. Обнови вручную: pipx install --force "
            f"\"{source}\"  (или через skillery, если ставил им).",
        )

    cmd = (
        ["pipx", "install", "--force", source]
        if reinstall
        else ["pipx", "upgrade", "atlas"]
    )
    rc = subprocess.run(cmd, check=False).returncode
    if rc != 0:
        raise CliError("upgrade_failed", f"pipx вернул код {rc}. Команда: {' '.join(cmd)}")
    data["ran"] = " ".join(cmd)
    emit_data(data, text_renderer=_render)
