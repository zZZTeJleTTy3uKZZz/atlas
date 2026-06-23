"""Unit-тесты module-aware layout (эпик «Физика контейнеров-модулей» #126/#163).

Контейнеры и модули:

- У модуля задан ``parent_id`` (FK на проект-контейнер).
- Физически модуль живёт в общем `_storage/<module_slug>/` (как все проекты).
- ЛОГИЧЕСКИ модуль виден как junction
  `<container_logical>/modules/<module_slug>/` → `_storage/<module_slug>/`,
  а НЕ в type-группе (Products/Clients/...).

`get_logical_path` остаётся duck-typed и не лезет в БД: контейнерный
логический путь передаётся вызывающей стороной через `container_logical`
(CLI знает сессию и резолвит parent_id → контейнер). Если `container_logical`
не передан — поведение прежнее (type-группа), чтобы не ломать standalone.

Все «тяжёлые» операции (mklink/robocopy) мокаются — реальную ФС не трогаем.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _fake_project(
    slug: str,
    *,
    type_slug: str = "business-product",
    archived: bool = False,
    archived_group: str | None = None,
    local_path: str | None = None,
    parent_id: str | None = None,
    parent_slug: str | None = None,
):
    return SimpleNamespace(
        id="pid-" + slug,
        slug=slug,
        name=slug,
        type_slug=type_slug,
        archived=archived,
        archived_group=archived_group,
        local_path=local_path,
        parent_id=parent_id,
        parent_slug=parent_slug,
    )


# --------------------------------------------------------------------------- #
# get_logical_path module-aware                                               #
# --------------------------------------------------------------------------- #


class TestModuleLogicalPath:
    def test_module_logical_is_under_container_modules(self, monkeypatch, tmp_path):
        from atlas.layout import get_logical_path

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        container_logical = tmp_path.resolve() / "Products" / "cont"
        module = _fake_project("mod", parent_id="pid-cont")

        result = get_logical_path(module, container_logical=container_logical)
        assert result == container_logical / "modules" / "mod"

    def test_module_without_container_logical_falls_back_to_type_group(
        self, monkeypatch, tmp_path
    ):
        """Если parent_id есть, но container_logical не передан — прежний путь
        (type-группа). Защита от поломки вызовов без резолвера."""
        from atlas.layout import get_logical_path

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        module = _fake_project("mod", parent_id="pid-cont")
        result = get_logical_path(module)
        assert result == tmp_path.resolve() / "Products" / "mod"

    def test_standalone_ignores_container_logical(self, monkeypatch, tmp_path):
        """Проект без parent_id игнорирует container_logical (не модуль)."""
        from atlas.layout import get_logical_path

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        proj = _fake_project("standalone", parent_id=None)
        result = get_logical_path(
            proj, container_logical=tmp_path / "Products" / "cont"
        )
        assert result == tmp_path.resolve() / "Products" / "standalone"

    def test_container_storage_is_flat(self, monkeypatch, tmp_path):
        """Storage модуля — плоский `_storage/<slug>/`, не зависит от parent."""
        from atlas.layout import get_storage_path

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        assert (
            get_storage_path("mod").parent
            == tmp_path.resolve() / "_storage"
        )


# --------------------------------------------------------------------------- #
# resolve_container_logical helper                                            #
# --------------------------------------------------------------------------- #


class TestResolveContainerLogical:
    def test_resolver_builds_container_logical_from_parent(
        self, monkeypatch, tmp_path
    ):
        """`resolve_container_logical(project, resolver)` строит logical путь
        контейнера, вызывая resolver(parent_id) → container-view."""
        from atlas.layout import get_logical_path, resolve_container_logical

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        container = _fake_project("cont", type_slug="business-product")
        module = _fake_project("mod", parent_id="pid-cont")

        def resolver(parent_id: str):
            assert parent_id == "pid-cont"
            return container

        container_logical = resolve_container_logical(module, resolver)
        assert container_logical == get_logical_path(container)

    def test_resolver_none_for_standalone(self, monkeypatch, tmp_path):
        from atlas.layout import resolve_container_logical

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        proj = _fake_project("standalone", parent_id=None)
        assert resolve_container_logical(proj, lambda pid: None) is None
