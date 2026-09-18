"""agentskit — AI-агенты как сущности: автономный онбординг инструкций.

Переиспользуемый слой поверх ``librarykit``. Делает любой навык/инструмент
**автономным онбордером**: сам прописывает свои инструкции (managed-блок) в
конфиг-файлы любого AI-агента (``.claude``/``.cursor``/``.codex``/``.agents``/
``.github``/…) идемпотентно и аддитивно — чужой текст не затирается.

Механизм generic: реестр агент→файл как ДАННЫЕ (``data/agents.json``), детект по
ФС, инъекция managed-блока между параметризуемыми маркерами, резолв global/project.
Контент (тело инструкции + namespace) приносит потребитель — это НЕ код кита.

Второй механизм — ХУКИ (``agentskit.hooks``): регистрация встроенной команды
плагина на событие агента (``SessionStart`` у Claude Code) в его настройках,
идемпотентно и не трогая чужие хуки. Тоже данные: механизм хуков описан в
реестре (``AgentSpec.hooks``), у большинства агентов его нет — и это нормальный
ответ, а не ошибка.

Роль «агент-как-адаптер» (драйв агента из комбайна) — зона ``adapterkit``, не
этого кита (agentskit отвечает только за онбординг-данные и инъекцию).
"""
from __future__ import annotations

from .detect import DetectedAgent, detect_agents, detect_dirs_of
from .hooks import (
    HookError,
    HookResult,
    HookSpec,
    HookStatus,
    SettingsIO,
    agents_with_hooks,
    find_hook_entries,
    hook_layout_for,
    hook_settings_path,
    hook_status,
    install_hook,
    merge_hook,
    remove_hook,
    supports_hooks,
    uninstall_hook,
)
from .inject import (
    has_managed_block,
    inject_managed_block,
    managed_block,
    strip_managed_block,
)
from .markers import begin_marker, end_marker
from .onboard import ApplyResult, onboard, uninstall
from .spec import (
    EVIDENCE_DOCS,
    EVIDENCE_EMPIRICAL,
    EVIDENCE_KINDS,
    EVIDENCE_UNVERIFIED,
    UNVERIFIED,
    AgentSpec,
    Evidence,
    HookLayout,
    SkillLayout,
    agent_registry,
    get_agent_spec,
    list_agents,
    register_agent_spec,
    resolve_agent_keys,
)
from .targets import (
    Target,
    global_path_for,
    legacy_skill_paths_for,
    readonly_skill_paths_for,
    resolve_legacy_skill_targets,
    resolve_readonly_skill_targets,
    resolve_skill_targets,
    resolve_targets,
    skill_evidence_for,
    skill_layout_for,
    skill_path_for,
    skill_paths_for,
    skill_root_for,
    skill_roots_for,
)

__all__ = [
    "__version__",
    # инъекция / маркеры
    "begin_marker",
    "end_marker",
    "managed_block",
    "has_managed_block",
    "inject_managed_block",
    "strip_managed_block",
    # реестр агентов (данные)
    "AgentSpec",
    "SkillLayout",
    "HookLayout",
    "agent_registry",
    "get_agent_spec",
    "list_agents",
    "register_agent_spec",
    "resolve_agent_keys",
    # доказательность записи реестра (машиночитаемо, а не глазами в JSON)
    "Evidence",
    "EVIDENCE_EMPIRICAL",
    "EVIDENCE_DOCS",
    "EVIDENCE_UNVERIFIED",
    "EVIDENCE_KINDS",
    "UNVERIFIED",
    "skill_evidence_for",
    # детект / резолв целей / онбординг
    "DetectedAgent",
    "detect_agents",
    "detect_dirs_of",
    "Target",
    "global_path_for",
    "resolve_targets",
    # раскладка навыков (full-режим): данные реестра → пути
    "skill_layout_for",
    "skill_root_for",
    "skill_roots_for",
    "skill_path_for",
    "skill_paths_for",
    "resolve_skill_targets",
    # места ТОЛЬКО ДЛЯ ЧТЕНИЯ: агент оттуда читает, кит туда не пишет
    "readonly_skill_paths_for",
    "resolve_readonly_skill_targets",
    # устаревшие раскладки — только распознать уже лежащее (см. 2026-07-21)
    "legacy_skill_paths_for",
    "resolve_legacy_skill_targets",
    "ApplyResult",
    "onboard",
    "uninstall",
    # хуки агента (событие + встроенная команда плагина; namespace как у блоков)
    "HookSpec",
    "HookResult",
    "HookStatus",
    "HookError",
    "SettingsIO",
    "supports_hooks",
    "agents_with_hooks",
    "hook_layout_for",
    "hook_settings_path",
    "install_hook",
    "uninstall_hook",
    "hook_status",
    "merge_hook",
    "remove_hook",
    "find_hook_entries",
]

# ЗАМОРОЖЕННАЯ vendored-копия кита ``agentskit`` (s-agentskit) — единственный
# потребитель (Atlas) больше не тянет его с PyPI, код физически перенесён внутрь
# ``atlas`` (Atlas #2710, волна 5). Оригинал остаётся на диске в репозитории
# ``agentskit`` в замороженном виде (см. его README). Версия зафиксирована на
# момент вендоринга — дистрибутив ``s-agentskit`` в окружении atlas больше не
# установлен, поэтому больше не читаем её из importlib.metadata.
_FROZEN_VERSION = "0.2.0"


def _resolve_version() -> str:
    return _FROZEN_VERSION


def __getattr__(name: str):
    if name == "__version__":
        value = _resolve_version()
        globals()["__version__"] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
