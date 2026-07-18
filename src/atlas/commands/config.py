"""CLI-команды `atlas config ...` — показать/задать конфиг Atlas (онбординг).

Конфиг слоистый (clikit AppConfig): дефолты < global config.toml < project
.atlas.toml < env ATLAS_*. ``set`` пишет в global config.toml. Секрет ``api_key``
ЗДЕСЬ не задаётся — он живёт в env ``ATLAS_API_KEY`` / защищённом secret-store.
"""
from __future__ import annotations

from pathlib import Path
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
    "github_owner",
    "team_owner",
    "base_url",
    "portal_id",
    "scope",
    "timezone",
    "projects_root",
    "default_priority",
    "default_review",
    "default_reviewer",
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
    # СЕКРЕТ не пишем в plaintext config.toml: api_key живёт в env/keystore
    # (без этого env ATLAS_API_KEY «промоутился» бы в global-файл — leak).
    data["api_key"] = ""
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


@config_app.command("setup")
@command
def init_cmd() -> None:
    """Интерактивный онбординг конфига: вопросы по ключевым полям + дефолтам задач.

    Для человека (терминал). Агент задаёт значения через ``atlas config set``.
    Enter — оставить текущее значение. После — `atlas project init` (создать БД).
    """
    cfg = load_config()
    console.print("[bold magenta]Atlas — онбординг конфига[/bold magenta] "
                  "[grey50](Enter — оставить текущее)[/grey50]\n")
    answers: dict[str, Any] = {}

    def _ask(key: str, prompt: str) -> None:
        answers[key] = typer.prompt(prompt, default=str(getattr(cfg, key) or ""))

    _ask("owner", "Твой member-slug (владелец стора, дефолтный actor аудита)")
    _ask("timezone", "Часовой пояс (offset, напр. +03:00)")
    answers["projects_root"] = typer.prompt(
        "Корневая папка портфеля проектов (где репозитории + layout)",
        default=str(cfg.projects_root or str(Path.home() / "Documents" / "PROJECT")),
    )
    _ask("default_priority", "Приоритет задач по умолчанию (P0|P1|P2|P3)")
    review = typer.confirm("Заводить reviewer по умолчанию (приёмка задач)?",
                           default=bool(cfg.default_review))
    answers["default_review"] = review
    if review:
        _ask("default_reviewer", "Reviewer по умолчанию (slug; пусто — создатель)")
    console.print("[grey50]— git-namespacing (опционально, Enter чтобы пропустить) —[/grey50]")
    _ask("org_namespace", "Орг git-namespace (GitLab)")
    _ask("personal_namespace", "Личный git-namespace (GitLab)")
    _ask("github_owner", "GitHub owner по умолчанию (user/org)")
    _ask("team_owner", "Владелец командных (--team) проектов")

    # — выбор AI-агентов, которым прописать Atlas-дисциплину (механизм — agentskit) —
    from agentskit import agent_registry, resolve_agent_keys

    console.print("[grey50]— AI-агенты (куда писать Atlas-дисциплину) —[/grey50]")
    agents_raw = typer.prompt(
        f"Каким агентам прописать дисциплину? CSV [{', '.join(agent_registry())}] или 'all'",
        default="all",
    )
    try:
        agent_keys = resolve_agent_keys(agents_raw)
    except ValueError as exc:
        console.print(f"[yellow]{exc} — пропускаю выбор агентов.[/yellow]")
        agent_keys = []

    data = cfg.model_dump()
    data.update(answers)
    data["api_key"] = ""  # секрет не в plaintext config.toml (env/keystore)
    AtlasConfig(**data).save("atlas")
    default_actor.cache_clear()

    saved = [k for k in answers if str(answers[k]) != str(getattr(cfg, k) or "")]
    init_hint = (
        f"atlas init --agents {','.join(agent_keys)} --create"
        if agent_keys else "atlas init"
    )
    emit_data(
        {"saved": saved, "config_keys": list(answers.keys()), "agents": agent_keys},
        text_renderer=lambda d: console.print(
            f"\n[green]✓ Конфиг сохранён.[/green] Изменено: "
            f"{', '.join(d['saved']) or '—'}.\nДалее: [bold]atlas project init[/bold] "
            f"(создать БД + сиды), затем [bold]{init_hint}[/bold] (дисциплина в агентов)."
        ),
    )
