"""Оркестратор автономного онбординга — главная точка роли A.

Для каждой цели (``resolve_targets``) применяет managed-блок плагина:
- **reference** (MVP): идемпотентная инъекция блока в memory-файл агента
  (перенос Atlas ``_apply_one``: детект exists, granular-verbs, dry-run);
- **full** (фаза 2): материализация контента в per-agent layout — пока заглушка.

``uninstall`` снимает managed-блок namespace (чужие блоки не трогает).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .inject import has_managed_block, inject_managed_block, strip_managed_block
from .targets import Target, resolve_targets


@dataclass(frozen=True)
class ApplyResult:
    """Результат применения к одной цели."""

    path: str
    action: str   # created|appended|updated|unchanged|skipped|removed|would-<verb>
    agent_key: str
    reason: str = ""


def _apply_one(
    t: Target, body: str, *, namespace: str, dry_run: bool
) -> ApplyResult:
    if t.mode != "reference":
        # Материализация навыка (запись SKILL.md) — фаза 2. Пути под неё уже
        # резолвятся из реестра: ``targets.resolve_skill_targets`` /
        # ``skill_path_for`` (находка 2026-07-21 — раскладка обязана жить в
        # одном месте, в данных, а не появляться заново в установщике).
        return ApplyResult(
            str(t.path), "skipped", t.agent_key,
            reason="full-режим пока не реализован (фаза 2); пути — resolve_skill_targets",
        )
    exists = t.path.exists()
    if not exists and not t.create_if_missing:
        return ApplyResult(str(t.path), "skipped", t.agent_key, reason="файла нет")
    existing = t.path.read_text(encoding="utf-8") if exists else ""
    updated = inject_managed_block(existing, body, namespace=namespace)
    if updated == existing:
        return ApplyResult(str(t.path), "unchanged", t.agent_key)
    had = has_managed_block(existing, namespace=namespace)
    verb = ("updated" if had else "appended") if exists else "created"
    if dry_run:
        return ApplyResult(str(t.path), f"would-{verb}", t.agent_key)
    t.path.parent.mkdir(parents=True, exist_ok=True)
    t.path.write_text(updated, encoding="utf-8")
    return ApplyResult(str(t.path), verb, t.agent_key)


def onboard(
    *,
    namespace: str,
    body: str,
    scope: str = "all",
    agents: list[str] | None = None,
    mode: str = "reference",
    create: bool = False,
    dry_run: bool = False,
    force: bool = False,          # full-режим (фаза 2); reference идемпотентен и так
    cwd: Path | None = None,
) -> list[ApplyResult]:
    """Прописать managed-блок ``namespace`` (тело ``body``) выбранным агентам.

    ``agents=None`` → легаси (существующие агентские файлы); список ключей →
    точечный выбор (``resolve_agent_keys`` на стороне caller). Идемпотентно:
    повторный вызов с тем же ``body`` → ``unchanged``.
    """
    targets = resolve_targets(
        scope=scope, agents=agents, mode=mode, create=create, cwd=cwd,
    )
    return [_apply_one(t, body, namespace=namespace, dry_run=dry_run) for t in targets]


def uninstall(
    *,
    namespace: str,
    scope: str = "all",
    agents: list[str] | None = None,
    dry_run: bool = False,
    cwd: Path | None = None,
) -> list[ApplyResult]:
    """Снять managed-блок ``namespace`` (reference). Чужие блоки не трогаются."""
    targets = resolve_targets(
        scope=scope, agents=agents, mode="reference", create=False, cwd=cwd,
    )
    results: list[ApplyResult] = []
    for t in targets:
        if not t.path.exists():
            results.append(ApplyResult(str(t.path), "skipped", t.agent_key, reason="файла нет"))
            continue
        existing = t.path.read_text(encoding="utf-8")
        if not has_managed_block(existing, namespace=namespace):
            results.append(ApplyResult(str(t.path), "skipped", t.agent_key, reason="нет блока"))
            continue
        stripped = strip_managed_block(existing, namespace=namespace)
        if dry_run:
            results.append(ApplyResult(str(t.path), "would-removed", t.agent_key))
            continue
        t.path.write_text(stripped, encoding="utf-8")
        results.append(ApplyResult(str(t.path), "removed", t.agent_key))
    return results
