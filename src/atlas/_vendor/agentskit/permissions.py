"""Движок ПРАВ: навык объявляет, что ему нужно, кит ставит и снимает.

Права живут в общем файле настроек — рядом с правилами, которые человек писал
для себя. Поэтому главное здесь не «уметь записать», а НЕ ПОТЕРЯТЬ чужое:
правило, заведённое владельцем руками, обязано пережить и установку навыка, и
его снятие.

Приём тот же, что у хуков: свои записи узнаются по примете (namespace
плагина), всё остальное не трогается вовсе. Непонятное возвращается
побайтово, а не «нормализуется» — нормализация чужого файла означает вернуть
владельцу другой файл, формально верный и по сути не его.

Примета — префикс в самой строке права: ``atlas:Bash(atlas *)``. Хранить её
отдельным списком нельзя: файл настроек правит и человек, и другие
инструменты, и любой сторонний список рано или поздно разойдётся с
содержимым, после чего снятие унесёт чужое.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .hooks import SettingsIO, _spec_of
from .spec import AgentSpec

KNOWN_FORMATS = ("claude-settings-json",)


@dataclass(frozen=True)
class PermissionSpec:
    """Что навык просит: списки правил по корзинам.

    Пустая корзина не пишется вовсе — пустой список в чужом файле выглядит
    как решение («здесь ничего не разрешено»), хотя означает лишь отсутствие
    просьбы.
    """

    namespace: str
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()

    def buckets(self) -> dict[str, tuple[str, ...]]:
        return {"allow": self.allow, "deny": self.deny, "ask": self.ask}


@dataclass
class PermissionResult:
    agent: str
    action: str  # "installed" | "removed" | "unchanged" | "skipped" | "failed"
    reason: str = ""
    path: str | None = None
    touched: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.action in ("installed", "removed", "unchanged", "skipped")


def _mark(namespace: str, rule: str) -> str:
    """Право с приметой владения: ``atlas:Bash(atlas *)``."""
    return f"{namespace}:{rule}"


def _is_ours(namespace: str, value: object) -> bool:
    return isinstance(value, str) and value.startswith(f"{namespace}:")


def settings_path(
    agent: str | AgentSpec, *, home: Path | None = None,
    project: Path | None = None, scope: str = "global",
) -> Path | None:
    """Файл настроек нужного скоупа. ``None`` — агент прав не держит."""
    spec = _spec_of(agent)
    layout = spec.permissions
    if layout is None or scope not in layout.scopes:
        return None
    base = home if scope == "global" else project
    if base is None:
        return None
    return base.joinpath(spec.config_dir, *layout.settings_file)


def _load(io: SettingsIO, path: Path) -> dict:
    if not io.exists(path):
        return {}
    text = io.read(path).strip()
    if not text:
        return {}
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def apply_permissions(
    agent: str | AgentSpec,
    spec_perm: PermissionSpec,
    *,
    home: Path | None = None,
    project: Path | None = None,
    scope: str = "global",
    io: SettingsIO | None = None,
) -> PermissionResult:
    """Поставить права навыка, не тронув чужие."""
    spec = _spec_of(agent)
    layout = spec.permissions
    if layout is None:
        return PermissionResult(spec.key, "skipped", "агент не держит прав")
    if layout.format not in KNOWN_FORMATS:
        return PermissionResult(
            spec.key, "failed",
            f"незнакомый формат {layout.format!r} — правка наугад опаснее отказа")

    path = settings_path(agent, home=home, project=project, scope=scope)
    if path is None:
        return PermissionResult(spec.key, "skipped", f"нет скоупа {scope!r}")

    io = io or SettingsIO()
    data = _load(io, path)
    section = data.get(layout.root_key)
    if not isinstance(section, dict):
        section = {}

    touched: dict[str, int] = {}
    changed = False
    for bucket, rules in spec_perm.buckets().items():
        if bucket not in layout.buckets or not rules:
            continue
        current = section.get(bucket)
        current = list(current) if isinstance(current, list) else []
        чужие = [v for v in current if not _is_ours(spec_perm.namespace, v)]
        наши = [_mark(spec_perm.namespace, r) for r in rules]
        новый = чужие + [n for n in наши if n not in чужие]
        if новый != current:
            section[bucket] = новый
            changed = True
        touched[bucket] = len(наши)

    if not changed:
        return PermissionResult(spec.key, "unchanged", "права уже стоят", str(path), touched)

    data[layout.root_key] = section
    io.write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return PermissionResult(spec.key, "installed", "", str(path), touched)


def remove_permissions(
    agent: str | AgentSpec,
    namespace: str,
    *,
    home: Path | None = None,
    project: Path | None = None,
    scope: str = "global",
    io: SettingsIO | None = None,
) -> PermissionResult:
    """Снять ТОЛЬКО свои права. Чужие правила остаются как были."""
    spec = _spec_of(agent)
    layout = spec.permissions
    if layout is None:
        return PermissionResult(spec.key, "skipped", "агент не держит прав")

    path = settings_path(agent, home=home, project=project, scope=scope)
    if path is None:
        return PermissionResult(spec.key, "skipped", f"нет скоупа {scope!r}")

    io = io or SettingsIO()
    data = _load(io, path)
    section = data.get(layout.root_key)
    if not isinstance(section, dict):
        return PermissionResult(spec.key, "unchanged", "прав не было", str(path))

    touched: dict[str, int] = {}
    changed = False
    for bucket in layout.buckets:
        current = section.get(bucket)
        if not isinstance(current, list):
            continue
        оставить = [v for v in current if not _is_ours(namespace, v)]
        if len(оставить) != len(current):
            touched[bucket] = len(current) - len(оставить)
            changed = True
            # пустую корзину убираем целиком: пустой список читается как
            # решение «ничего не разрешено», а это не то, что мы утверждаем
            if оставить:
                section[bucket] = оставить
            else:
                section.pop(bucket, None)

    if not changed:
        return PermissionResult(spec.key, "unchanged", "наших прав не было", str(path))

    if section:
        data[layout.root_key] = section
    else:
        data.pop(layout.root_key, None)
    io.write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return PermissionResult(spec.key, "removed", "", str(path), touched)


def list_permissions(
    agent: str | AgentSpec,
    *,
    namespace: str | None = None,
    home: Path | None = None,
    project: Path | None = None,
    scope: str = "global",
    io: SettingsIO | None = None,
) -> dict[str, list[str]]:
    """Что стоит сейчас. ``namespace`` — только свои, без него — все."""
    spec = _spec_of(agent)
    layout = spec.permissions
    path = settings_path(agent, home=home, project=project, scope=scope)
    if layout is None or path is None:
        return {}
    io = io or SettingsIO()
    section = _load(io, path).get(layout.root_key)
    if not isinstance(section, dict):
        return {}
    out: dict[str, list[str]] = {}
    for bucket in layout.buckets:
        values = section.get(bucket)
        if not isinstance(values, list):
            continue
        if namespace is not None:
            values = [v for v in values if _is_ours(namespace, v)]
        if values:
            out[bucket] = list(values)
    return out


__all__ = [
    "PermissionResult",
    "PermissionSpec",
    "apply_permissions",
    "list_permissions",
    "remove_permissions",
    "settings_path",
]
