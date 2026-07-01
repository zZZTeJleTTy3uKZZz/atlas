"""CLI `atlas init` — прописать Atlas-дисциплину во все агентские инструкции.

Тонкая обёртка над китом ``agentskit`` (механизм онбординга — реестр агентов,
детект, идемпотентная инъекция managed-блока, резолв global/project). Atlas
приносит лишь КОНТЕНТ: ``DISCIPLINE_BODY`` + namespace ``"atlas"`` (см.
``atlas.discipline``). Маркеры ``ATLAS:*`` сохраняют обратную совместимость с
уже прописанными блоками.

Чужой текст вне маркеров не трогается. ``--dry-run`` — показать, что изменится.
``--agents claude,gemini`` — точечный выбор агентов (без него — легаси: все
существующие агентские файлы).
"""
from __future__ import annotations

import json as _json
import os
from typing import Any

import typer
from agentskit import onboard, resolve_agent_keys
from clikit import command
from rich.console import Console

from atlas.discipline import ATLAS_NAMESPACE, DISCIPLINE_BODY

console = Console()


def _render(d: dict[str, Any]) -> None:
    icons = {
        "created": "[green]＋[/green]", "appended": "[green]＋[/green]",
        "updated": "[cyan]↻[/cyan]", "unchanged": "[dim]＝[/dim]",
        "skipped": "[grey42]·[/grey42]", "removed": "[red]－[/red]",
    }
    mode = " [yellow](dry-run)[/yellow]" if d.get("dry_run") else ""
    console.print(f"[bold magenta]atlas init — Atlas-дисциплина в агентов[/bold magenta]{mode}")
    for r in d["results"]:
        act = r["action"].replace("would-", "")
        icon = icons.get(act, "·")
        reason = f" [dim]({r['reason']})[/dim]" if r.get("reason") else ""
        console.print(f"  {icon} {r['action']:<10} {r['path']}{reason}")
    if not d["results"]:
        console.print("  [yellow]Нет целей: агентских файлов в cwd не найдено "
                      "(--create создаст AGENTS.md).[/yellow]")


@command
def init_cmd(
    scope: str = typer.Option(
        "all", "--scope",
        help="global (~/.<agent>/…) | repo (агентские файлы cwd) | all.",
    ),
    agents: str = typer.Option(
        "", "--agents",
        help="Точечный выбор: CSV ключей (claude,codex,gemini,cursor,copilot,…) "
             "или 'all'. Пусто → легаси (все существующие агентские файлы).",
    ),
    create: bool = typer.Option(
        False, "--create",
        help="Создать файлы выбранных агентов, если их нет (репо-scope).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Показать, что изменится, без записи.",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Машинный JSON.",
    ),
) -> None:
    """Прописать Atlas-дисциплину (managed-блок) в агентские инструкции.

    Делегирует механизм в ``agentskit.onboard`` (namespace=atlas). Без
    ``--agents`` — все существующие агентские файлы; с ``--agents claude,gemini``
    — точечно (с ``--create`` создаст их файлы).
    """
    if scope not in ("global", "repo", "all"):
        console.print(f"[red]Неверный --scope '{scope}': global|repo|all.[/red]")
        raise typer.Exit(code=1)
    agent_keys: list[str] | None = None
    if agents.strip():
        try:
            agent_keys = resolve_agent_keys(agents)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc

    results = onboard(
        namespace=ATLAS_NAMESPACE, body=DISCIPLINE_BODY,
        scope=scope, agents=agent_keys, create=create, dry_run=dry_run,
    )
    data = {
        "scope": scope,
        "agents": agent_keys,
        "dry_run": dry_run,
        "results": [
            {"path": r.path, "action": r.action, "agent": r.agent_key,
             "reason": r.reason}
            for r in results
        ],
    }

    if json_out or os.environ.get("ATLAS_OUTPUT") == "json":
        print(_json.dumps(data, ensure_ascii=False))
    else:
        _render(data)
