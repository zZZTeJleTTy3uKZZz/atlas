"""CLI-команды `atlas config ...` — показать/задать конфиг Atlas (онбординг).

Конфиг слоистый (clikit AppConfig): дефолты < global config.toml < project
.atlas.toml < env ATLAS_*. ``set`` пишет в global config.toml. Секрет ``api_key``
ЗДЕСЬ не задаётся — он живёт в env ``ATLAS_API_KEY`` / защищённом secret-store.
"""
from __future__ import annotations

from typing import Any

import typer
from clikit import command, emit_data
from clikit.errors import ValidationError
from rich.console import Console

from atlas.appconfig import AtlasConfig, default_actor, load_config

config_app = typer.Typer(
    no_args_is_help=True,
    help="Конфиг Atlas: показать/задать поля (онбординг).",
)
console = Console()

# Поля, которые можно править через CLI (api_key — НЕ здесь: он в secret-store).
SETTABLE: tuple[str, ...] = (
    "owner",
    "org_namespace",
    "personal_namespace",
    "personal_owner",
    "team_owner",
    "base_url",
    "portal_id",
    "scope",
    "timezone",
)


@config_app.command("show")
@command
def show_cmd() -> None:
    """Показать текущий эффективный конфиг (слои + env). Секрет не печатается."""
    cfg = load_config()
    data: dict[str, Any] = {k: getattr(cfg, k) for k in SETTABLE}
    data["api_key_set"] = bool(cfg.api_key)  # сам ключ не показываем

    def _render(d: dict[str, Any]) -> None:
        console.print("[bold]Конфиг Atlas[/bold]:")
        for k in SETTABLE:
            console.print(f"  {k:20} = {d[k]!r}")
        console.print(
            f"  {'api_key':20} = {'<задан>' if d['api_key_set'] else '<не задан>'} "
            "(env ATLAS_API_KEY / secret-store)"
        )

    emit_data(data, text_renderer=_render)


@config_app.command("get")
@command
def get_cmd(
    key: str = typer.Argument(..., help="имя поля конфига"),
) -> None:
    """Показать значение одного поля конфига."""
    cfg = load_config()
    if key == "api_key":
        raise ValidationError("api_key не показывается; задаётся через env/secret-store.")
    if not hasattr(cfg, key):
        raise ValidationError(f"Неизвестное поле конфига: {key!r}")
    emit_data({key: getattr(cfg, key)})


@config_app.command("set")
@command
def set_cmd(
    key: str = typer.Argument(..., help=f"поле: {', '.join(SETTABLE)}"),
    value: str = typer.Argument(..., help="значение"),
) -> None:
    """Задать поле конфига (пишет в global config.toml).

    Онбординг: ``atlas config set owner alice`` — задать владельца стора перед
    ``atlas project init``.
    """
    if key == "api_key":
        raise ValidationError(
            "api_key задаётся через env ATLAS_API_KEY (или secret-store), не здесь."
        )
    if key not in SETTABLE:
        raise ValidationError(
            f"Поле {key!r} нельзя задать. Доступно: {', '.join(SETTABLE)}"
        )
    cfg = load_config()
    data = cfg.model_dump()
    data[key] = value
    AtlasConfig(**data).save("atlas")
    # сбросить кэш владельца, чтобы повторный вызов в том же процессе увидел новое
    if key in ("owner",):
        default_actor.cache_clear()

    emit_data(
        {"key": key, "value": value, "saved": True},
        text_renderer=lambda d: console.print(
            f"[green]✓[/green] {d['key']} = {d['value']!r} → config.toml"
        ),
    )
