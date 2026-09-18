"""Managed-маркеры онбординг-блока, параметризованные namespace плагина.

Один движок инъекции обслуживает N плагинов в ОДНОМ файле без коллизий: каждый
плагин владеет своей парой маркеров, выведенной из его namespace. Например
``atlas init`` пишет ``<!-- ATLAS:BEGIN managed -->``, гипотетический ``uipro
init`` — ``<!-- UIPRO:BEGIN managed -->``; оба блока мирно сосуществуют в одном
``CLAUDE.md``.

Обратная совместимость: ``begin_marker("atlas")`` == исторический Atlas-маркер
``<!-- ATLAS:BEGIN managed -->`` (был хардкодом ``BEGIN_MARKER``), поэтому уже
прописанные ATLAS-блоки находятся и обновляются как раньше.
"""
from __future__ import annotations


def begin_marker(namespace: str) -> str:
    """Открывающий маркер managed-блока для ``namespace`` (UPPER-регистр)."""
    return f"<!-- {namespace.upper()}:BEGIN managed -->"


def end_marker(namespace: str) -> str:
    """Закрывающий маркер managed-блока для ``namespace`` (UPPER-регистр)."""
    return f"<!-- {namespace.upper()}:END -->"
