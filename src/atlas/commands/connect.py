"""CLI `atlas backend connect / disconnect / status` — подключение к внешнему backend-сервису.

Atlas **local-first**: весь функционал (проекты/задачи/эпики/спринты/дашборд)
работает БЕЗ бэкенда. ``connect`` опционально подключает синк: задаёт ``base_url``
и кладёт admin-API-ключ в защищённый secret-store (keyring/file-fallback, НЕ в
открытый config.toml). После — `atlas sync push/pull` шлёт/тянет события.

- ``atlas backend status``            — показать статус подключения;
- ``atlas backend connect <url> [--key]`` — подключить (ключ: --key | интерактивный ввод);
- ``atlas backend disconnect``        — убрать ключ из secret-store (опц. сбросить url).

NB: знание о КОНКРЕТНЫХ внешних системах (Notion/Б24) живёт в backend-сервисе, не в
CLI. Полноценный модуль работы с бэком планируется отдельным приватным китом,
догружаемым по connect (см. docs/design) — сейчас синк встроен и опционален.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import typer
from clikit import CliError, command, emit_data
from rich.console import Console

from atlas import keystore
from atlas.appconfig import AtlasConfig, load_config, resolve_api_key

console = Console()


def _set_base_url(cfg: AtlasConfig, base_url: str) -> None:
    data = cfg.model_dump()
    data["base_url"] = base_url
    AtlasConfig(**data).save("atlas")


def _status_data(cfg: AtlasConfig) -> dict[str, Any]:
    key = resolve_api_key(cfg)
    has_url = bool(cfg.base_url and cfg.base_url != "http://localhost:8000")
    return {
        "connected": bool(key) and has_url,
        "base_url": cfg.base_url,
        "portal_id": cfg.portal_id,
        "api_key_set": bool(key),
    }


def _render_status(d: dict[str, Any]) -> None:
    mark = "[green]● подключён[/green]" if d["connected"] else "[grey50]○ не подключён[/grey50]"
    console.print(f"{mark}  backend: [cyan]{d['base_url']}[/cyan]")
    console.print(f"  portal: {d['portal_id']} · ключ: "
                  f"{'[green]задан[/green]' if d['api_key_set'] else '[grey50]нет[/grey50]'}")
    if not d["connected"]:
        console.print("  [grey50]Local-first работает и так. Подключить синк: "
                      "atlas backend connect <url> --key <admin-key>[/grey50]")


async def _verify(base_url: str, key: str) -> tuple[bool, str]:
    """Лёгкая проверка связи: короткий poll. (ok, сообщение)."""
    from atlas.sync.backend_client import BackendClient

    client = BackendClient(base_url, key)
    try:
        await client.poll_events(timeout=2.0)
        return True, "ok"
    except Exception as exc:  # noqa: BLE001 — verify best-effort, любая ошибка = не прошло
        return False, str(exc)
    finally:
        await client.aclose()


@command
def connect_cmd(
    base_url: Optional[str] = typer.Argument(
        None, help="URL backend-сервиса. Без аргумента — показать статус."
    ),
    key: Optional[str] = typer.Option(
        None, "--key", help="Admin-API-ключ (иначе — интерактивный ввод).",
    ),
    verify: bool = typer.Option(
        True, "--verify/--no-verify", help="Проверить связь коротким poll.",
    ),
    no_input: bool = typer.Option(
        False, "--no-input", help="Не спрашивать ключ интерактивно (для скриптов).",
    ),
) -> None:
    """Подключить Atlas к backend (или показать статус, если без URL)."""
    cfg = load_config()
    if base_url is None:
        emit_data(_status_data(cfg), text_renderer=_render_status)
        return

    _set_base_url(cfg, base_url)
    if key is None and not no_input:
        key = typer.prompt("Admin-API-ключ", hide_input=True, default="")
    if key:
        keystore.save_api_key(cfg.portal_id, key)

    cfg = load_config()
    resolved = resolve_api_key(cfg)
    verified, vmsg = (None, "")
    if verify and resolved:
        verified, vmsg = asyncio.run(_verify(base_url, resolved))

    data = {**_status_data(cfg), "verified": verified, "verify_message": vmsg}

    def _render(d: dict[str, Any]) -> None:
        _render_status(d)
        if d["verified"] is True:
            console.print("  [green]✓ связь с backend подтверждена[/green]")
        elif d["verified"] is False:
            console.print(f"  [yellow]⚠ сохранено, но проверка не прошла: {d['verify_message']}[/yellow]")

    emit_data(data, text_renderer=_render)


@command
def disconnect_cmd(
    reset_url: bool = typer.Option(
        False, "--reset-url", help="Также сбросить base_url на дефолт.",
    ),
) -> None:
    """Отключить backend: убрать ключ из secret-store (синк перестанет работать)."""
    cfg = load_config()
    keystore.clear_api_key(cfg.portal_id)
    if reset_url:
        _set_base_url(cfg, "http://localhost:8000")
    if cfg.api_key:  # подчистить legacy открытый ключ из config.toml, если был
        data = cfg.model_dump()
        data["api_key"] = ""
        AtlasConfig(**data).save("atlas")
    emit_data(
        {"disconnected": True, "url_reset": reset_url},
        text_renderer=lambda d: console.print(
            "[green]✓[/green] Отключено (ключ убран из secret-store). "
            "Local-first работает как обычно."
        ),
    )


@command
def status_cmd() -> None:
    """Показать статус подключения Atlas к backend."""
    cfg = load_config()
    emit_data(_status_data(cfg), text_renderer=_render_status)


#: Ресурс-группа `atlas backend` — подключение к внешнему backend-сервису (синк).
#: connect/disconnect/status собраны под одним существительным (было — два
#: плоских top-level `atlas connect`/`atlas disconnect`).
backend_app = typer.Typer(
    no_args_is_help=True,
    help="Подключение к внешнему backend-сервису (синк). Local-first работает и без него.",
)
backend_app.command("connect")(connect_cmd)
backend_app.command("disconnect")(disconnect_cmd)
backend_app.command("status")(status_cmd)


def _require_connected() -> None:
    """Гард для sync-команд: без подключения — понятная ошибка, не сетевой таймаут."""
    cfg = load_config()
    if not (resolve_api_key(cfg) and cfg.base_url):
        raise CliError(
            "not_connected",
            "Нет подключения к backend. Подключись: atlas backend connect <url> --key <admin-key> "
            "(local-first работает и без этого).",
        )
