"""Детект AI-агентов по файловой системе (перенос ui-ux-pro-max ``detectAIType``).

Структурный детект: для каждого ``AgentSpec`` проверяем наличие его ``config_dir``
(и дополнительных ``detect_paths``) в проекте (cwd) и/или в $HOME. Пусто → caller
трактует как 'all'.

Опыт 2026-07-21: одного ``config_dir`` МАЛО. Пользовательский корень opencode —
``~/.config/opencode`` (а не ``~/.opencode``), домашняя папка antigravity —
``~/.gemini/config`` (а не ``~/.agents``): по одному ``config_dir`` установленный
агент в глобальном скоупе просто не находился. Ложноотрицательный детект тише
ложноположительного и потому опаснее — «агента нет» никто не перепроверяет.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .spec import AgentSpec, list_agents


@dataclass(frozen=True)
class DetectedAgent:
    """Найденный агент + где (project/global/both)."""

    key: str
    label: str
    present: bool          # config_dir существует (cwd и/или $HOME)
    scope: str             # "project" | "global" | "both" | "none"


def detect_dirs_of(spec: AgentSpec) -> tuple[str, ...]:
    """Все папки-приметы агента: ``config_dir`` + ``detect_paths`` (данные реестра).

    Публичная: потребителю кита нужно уметь спросить, ПО ЧЕМУ агента искали, —
    иначе «не найден» неотличим от «искали не там» (opencode до 2026-07-21).
    """
    return (spec.config_dir, *spec.detect_paths)


def _scope_of(spec: AgentSpec, cwd: Path, home: Path) -> str:
    dirs = detect_dirs_of(spec)
    in_proj = any((cwd / d).exists() for d in dirs)
    in_home = any((home / d).exists() for d in dirs)
    if in_proj and in_home:
        return "both"
    if in_proj:
        return "project"
    if in_home:
        return "global"
    return "none"


def detect_agents(
    cwd: Path | None = None,
    *,
    home: Path | None = None,
    include_absent: bool = False,
) -> list[DetectedAgent]:
    """Просканировать ФС по реестру. ``include_absent=False`` — только найденные.

    Возвращает список в порядке реестра. Пустой список найденных → caller
    трактует как 'all' (как ``suggested='all'`` в ui-ux-pro-max).
    """
    base = Path(cwd) if cwd is not None else Path.cwd()
    h = Path(home) if home is not None else Path.home()
    out: list[DetectedAgent] = []
    for spec in list_agents():
        scope = _scope_of(spec, base, h)
        present = scope != "none"
        if present or include_absent:
            out.append(DetectedAgent(spec.key, spec.label, present, scope))
    return out
