"""CLI `atlas setup` — turnkey-онбординг atlas в агента одной командой.

Делает две вещи (идемпотентно, повторный запуск безопасен):

1. **Правила** — прописывает Atlas-дисциплину (managed-блок) в агентские файлы,
   как ``atlas init`` (та же ``agentskit.onboard``, namespace=atlas).
2. **Хук** (Claude Code) — регистрирует SessionStart-хук в
   ``~/.claude/settings.json`` как встроенную команду ``atlas session-hook``
   (без внешнего файла-скрипта — не зависит от ``python`` в PATH, #877). Хук
   впрыскивает компактную сводку портфеля (триаж) в начало сессии. Мёрж
   settings.json **не трогает чужие хуки** и мигрирует со старого файла-хука.

Флаги: ``--dry-run`` (показать без записи), ``--no-hooks`` / ``--no-rules``
(частично), ``--uninstall`` (снять хук из settings.json), ``--scope`` (как у
``atlas init``). Хук — специфичен для Claude Code (settings.json + hooks-папка).

Установщик (``install/install.ps1`` / ``install.sh``) вызывает ``atlas setup``
после установки CLI — но команда самодостаточна и запускается вручную.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import typer
from clikit import CliError, command
from rich.console import Console

console = Console()

_HOOK_STATUS_MESSAGE = "Atlas triage…"
_HOOK_TIMEOUT_SEC = 15
#: Legacy-имя внешнего файла-хука (старый способ — python-скрипт). Теперь хук —
#: встроенная команда `atlas session-hook`; этот файл при setup удаляется (#877).
_HOOK_FILENAME = "session_atlas.py"
#: Маркеры «нашего» хука в settings.json — новая команда И legacy-файл-скрипт.
#: Нужны, чтобы idempotency/uninstall распознавали ОБА (миграция без дублей).
_HOOK_MARKERS = ("session-hook", _HOOK_FILENAME)


def _is_our_command(cmd: Any) -> bool:
    """True, если команда в settings.json — наш SessionStart-хук (новый или legacy)."""
    return isinstance(cmd, str) and any(m in cmd for m in _HOOK_MARKERS)


# ── пути / команда хука (чистые, без тяжёлых импортов — тестируемы) ──────────

def _claude_dir() -> Path:
    """Корень настроек Claude Code (``~/.claude``; override ``CLAUDE_CONFIG_DIR``)."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".claude"


def _hook_path(claude_dir: Path) -> Path:
    """Путь legacy-файла-хука (для очистки при миграции на команду)."""
    return claude_dir / "hooks" / _HOOK_FILENAME


def _settings_path(claude_dir: Path) -> Path:
    return claude_dir / "settings.json"


def _hook_command() -> str:
    """Команда SessionStart-хука = сам `atlas` (в PATH после install) → подкоманда
    `session-hook`. НЕ зависит от `python`/`python3` в PATH (uv-tool его не кладёт)
    и от внешнего файла-скрипта (#877). Абсолютный путь к atlas надёжнее PATH хука."""
    import shutil

    atlas_exe = shutil.which("atlas") or "atlas"
    return f'"{atlas_exe}" session-hook' if " " in atlas_exe else f"{atlas_exe} session-hook"


# ── идемпотентный мёрж settings.json (риск клоббера — покрыто тестами) ───────

def _merge_session_hook(settings: dict[str, Any], command_str: str) -> tuple[dict[str, Any], bool]:
    """Нормализует SessionStart: снимает ВСЕ наши хуки (legacy-файл + дубли) и
    ставит ровно один — ``command_str`` (``atlas session-hook``). Чужие хуки/группы
    сохраняются как есть.

    Возвращает ``(settings, changed)``. Идемпотентно: если результат совпал с
    исходным SessionStart — ``changed=False`` (сравниваем состояние до/после).
    """
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise CliError("settings_broken", "settings.json: 'hooks' не объект")
    session_start = hooks.setdefault("SessionStart", [])
    if not isinstance(session_start, list):
        raise CliError("settings_broken", "settings.json: 'hooks.SessionStart' не список")

    before = json.dumps(session_start, sort_keys=True, ensure_ascii=False)
    # 1. выкинуть все НАШИ хуки (по маркерам: legacy-файл + дубли новой команды)
    new_groups: list[Any] = []
    for group in session_start:
        inner = (group or {}).get("hooks", []) or []
        kept = [h for h in inner if not (isinstance(h, dict) and _is_our_command(h.get("command")))]
        if kept:
            g = dict(group)
            g["hooks"] = kept
            new_groups.append(g)
    # 2. добавить ровно один наш (matcher startup|resume — не на каждый clear/compact)
    new_groups.append(
        {
            "matcher": "startup|resume",
            "hooks": [
                {
                    "type": "command",
                    "command": command_str,
                    "timeout": _HOOK_TIMEOUT_SEC,
                    "statusMessage": _HOOK_STATUS_MESSAGE,
                }
            ],
        }
    )
    after = json.dumps(new_groups, sort_keys=True, ensure_ascii=False)
    hooks["SessionStart"] = new_groups
    return settings, before != after


def _remove_session_hook(settings: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Убирает ВСЕ наши SessionStart-хуки (новый + legacy) из settings.json (``--uninstall``)."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings, False
    session_start = hooks.get("SessionStart")
    if not isinstance(session_start, list):
        return settings, False
    changed = False
    new_groups = []
    for group in session_start:
        inner = (group or {}).get("hooks", []) or []
        kept = [h for h in inner if not (isinstance(h, dict) and _is_our_command(h.get("command")))]
        if len(kept) != len(inner):
            changed = True
        if kept:
            new_group = dict(group)
            new_group["hooks"] = kept
            new_groups.append(new_group)
        # пустую группу (после удаления) отбрасываем
    if changed:
        hooks["SessionStart"] = new_groups
    return settings, changed


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        # utf-8-sig снимает BOM (Notepad на Windows пишет его) — иначе json падает.
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        raise CliError("settings_broken", f"settings.json нечитаем/битый: {path}") from None
    if not isinstance(data, dict):
        # Не затираем молча чужой валидный-но-не-объект settings.json.
        raise CliError("settings_broken", f"settings.json не JSON-объект: {path}")
    return data


def _dump_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ── установка/снятие Claude-хука ─────────────────────────────────────────────

def install_claude_hook(*, dry_run: bool = False) -> list[str]:
    """Регистрирует `atlas session-hook` в settings.json (без внешнего файла-скрипта).

    Мигрирует со старого способа: чистит legacy-регистрацию и удаляет осиротевший
    файл ``session_atlas.py`` — чтобы не осталось двойного хука (#877)."""
    claude = _claude_dir()
    settings_path = _settings_path(claude)
    cmd = _hook_command()
    log: list[str] = []

    settings = _load_settings(settings_path)
    settings, changed = _merge_session_hook(settings, cmd)

    # cleanup: legacy-файл-хук больше не нужен (хук — команда `atlas`).
    legacy = _hook_path(claude)
    if legacy.exists():
        if dry_run:
            log.append(f"would-remove {legacy} (legacy-скрипт)")
        else:
            legacy.unlink(missing_ok=True)
            log.append(f"legacy-hook  {legacy} (удалён — теперь `atlas session-hook`)")

    if not changed:
        log.append(f"settings     {settings_path} (хук уже стоит — без изменений)")
    elif dry_run:
        log.append(f"would-register {settings_path} SessionStart -> {cmd}")
    else:
        _dump_settings(settings_path, settings)
        log.append(f"settings     {settings_path} (SessionStart -> {cmd})")
    return log


def uninstall_claude_hook(*, dry_run: bool = False) -> list[str]:
    claude = _claude_dir()
    settings_path = _settings_path(claude)
    settings = _load_settings(settings_path)
    settings, changed = _remove_session_hook(settings)  # снимает и новый, и legacy
    log: list[str] = []

    legacy = _hook_path(claude)
    if legacy.exists() and not dry_run:
        legacy.unlink(missing_ok=True)
        log.append(f"legacy-hook  {legacy} (удалён)")

    if not changed:
        log.append(f"settings     {settings_path} (нашего хука нет — нечего снимать)")
        return log
    if not dry_run:
        _dump_settings(settings_path, settings)
    log.append(f"settings     {settings_path} (-SessionStart)")
    return log


# ── команда ──────────────────────────────────────────────────────────────────

@command
def setup_cmd(
    scope: str = typer.Option(
        "all", "--scope", help="global | repo | all — как у `atlas init`."
    ),
    agents: str = typer.Option(
        "", "--agents", help="CSV агентов (claude,gemini,…) или 'all'. Пусто → все существующие."
    ),
    no_rules: bool = typer.Option(False, "--no-rules", help="Не прописывать правила (только хук)."),
    no_hooks: bool = typer.Option(False, "--no-hooks", help="Не ставить хук (только правила)."),
    uninstall: bool = typer.Option(False, "--uninstall", help="Снять Claude-хук из settings.json."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Показать, что изменится, без записи."),
) -> None:
    """Turnkey-онбординг atlas в агента: правила (`atlas init`) + SessionStart-хук."""
    mode = " (dry-run)" if dry_run else ""
    console.print(f"[bold magenta]atlas setup — правила + хук в агента[/bold magenta]{mode}")

    if uninstall:
        for line in uninstall_claude_hook(dry_run=dry_run):
            console.print(f"  {line}")
        return

    if not no_rules:
        # Ленивый импорт: тяжёлые зависимости onboard не грузим на уровне модуля
        # (чтобы merge-хелперы оставались чисто-stdlib и тестировались отдельно).
        from agentskit import onboard, resolve_agent_keys

        from atlas.discipline import ATLAS_NAMESPACE, DISCIPLINE_BODY

        agent_keys = resolve_agent_keys(agents) if agents.strip() else None
        results = onboard(
            namespace=ATLAS_NAMESPACE, body=DISCIPLINE_BODY,
            scope=scope, agents=agent_keys, create=False, dry_run=dry_run,
        )
        console.print(f"  [cyan]правила[/cyan]: {len(results)} агентских файл(ов) обработано")

    if not no_hooks:
        for line in install_claude_hook(dry_run=dry_run):
            console.print(f"  {line}")

    console.print("[dim]  Проверь: новая сессия агента покажет блок «[ATLAS — состояние портфеля…]».[/dim]")


# ── встроенный SessionStart-хук: команда `atlas session-hook` ────────────────
# Заменяет прежний внешний python-скрипт (#877): не зависит от `python` в PATH,
# версионируется/тестируется вместе с CLI, триаж берётся НАПРЯМУЮ (без subprocess).

_STALE_DAYS = 7
_MAX_IN_PROGRESS = 8
_PRIO_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _hook_age(t: dict[str, Any]) -> int:
    try:
        return int(t.get("age_days") or 0)
    except Exception:
        return 0


def format_triage_summary(data: dict[str, Any]) -> str:
    """Компактная сводка портфеля для SessionStart-хука (числа + в работе + забытые)."""
    counts = data.get("counts", {}) or {}
    ip = data.get("in_progress", []) or []
    ip_sorted = sorted(
        ip, key=lambda t: (_PRIO_ORDER.get(t.get("priority") or "P3", 3), -_hook_age(t))
    )
    lines = ["[ATLAS — состояние портфеля на старте сессии]"]
    lines.append(
        "Открыто: {tot} · в работе {ip} · на ревью {rv} · todo {td} · заблокировано {bl}".format(
            tot=data.get("total_open", "?"),
            ip=counts.get("in_progress", 0),
            rv=counts.get("review", 0),
            td=counts.get("todo", 0),
            bl=counts.get("blocked", 0),
        )
    )
    if ip_sorted:
        lines.append("▶ В работе (важные и забытые первыми):")
        for t in ip_sorted[:_MAX_IN_PROGRESS]:
            a = _hook_age(t)
            flag = f" ⚠️{a}д" if a > _STALE_DAYS else ""
            pr = t.get("priority") or ""
            proj = t.get("project") or "?"
            tail = f" · {pr}" if pr else ""
            lines.append(f"  · #{t.get('number')} {t.get('title')} [{proj}{tail}]{flag}")
    stale = [t for t in ip if _hook_age(t) > _STALE_DAYS]
    if stale:
        lines.append(
            f"⏳ Забытых в работе (>{_STALE_DAYS}д): {len(stale)} — доведи, "
            "сдай на ревью или сними в blocked/backlog."
        )
    if counts.get("review", 0):
        lines.append(
            f"✅ На ревью {counts.get('review')} — если ты reviewer, разбери approve/reject."
        )
    lines.append(
        "Не начинай новое, не сверившись со списком; для новой работы — `atlas task add` с ЦКП."
    )
    return "\n".join(lines)


def _triage_data() -> dict[str, Any] | None:
    """Срез триажа НАПРЯМУЮ (build_triage, без subprocess) — быстро и без atlas в PATH."""
    try:
        from atlas.db import make_engine, make_session, resolve_db_url
        from atlas.triage import build_triage

        with make_session(make_engine(resolve_db_url())) as session:
            return build_triage(session)
    except Exception:
        return None


@command
def session_hook_cmd() -> None:
    """[скрытая] SessionStart-хук Claude Code: печатает сводку портфеля в
    additionalContext. Ставится `atlas setup`. НИКОГДА не ломает старт сессии —
    при любой ошибке/недоступности БД тихо отдаёт пустой ответ."""
    try:
        try:
            sys.stdin.read()  # Claude Code передаёт payload в stdin
        except Exception:
            pass
        data = _triage_data()
        if not data:
            sys.stdout.write(json.dumps({"suppressOutput": True}))
            return
        out = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": format_triage_summary(data),
            }
        }
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
    except Exception:
        sys.stdout.write("{}")
