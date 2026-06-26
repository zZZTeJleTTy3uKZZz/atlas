"""Тесты утилит путей для archive engine.

Проверяют:
- type_slug_to_group: маппинг project_type.slug → группа (clients/products/tests).
- archive_path / group_path / expected_project_path: вычисление физического
  размещения проекта.
- get_projects_root: env ATLAS_PROJECTS_ROOT → путь, иначе default.

TDD: пишется ДО реализации src/atlas/pm/paths.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# type_slug_to_group                                                          #
# --------------------------------------------------------------------------- #


class TestTypeSlugToGroup:
    def test_type_slug_to_group_client(self):
        from atlas.paths import type_slug_to_group
        assert type_slug_to_group("client-project") == "clients"

    def test_type_slug_to_group_business_product(self):
        from atlas.paths import type_slug_to_group
        assert type_slug_to_group("business-product") == "products"

    def test_type_slug_to_group_personal_utility(self):
        from atlas.paths import type_slug_to_group
        assert type_slug_to_group("personal-utility") == "products"

    def test_type_slug_to_group_personal_project(self):
        from atlas.paths import type_slug_to_group
        assert type_slug_to_group("personal-project") == "products"

    def test_type_slug_to_group_shared_infrastructure(self):
        from atlas.paths import type_slug_to_group
        assert type_slug_to_group("shared-infrastructure") == "products"

    def test_type_slug_to_group_test(self):
        from atlas.paths import type_slug_to_group
        assert type_slug_to_group("test") == "tests"

    def test_type_slug_to_group_inbox(self):
        from atlas.paths import type_slug_to_group
        assert type_slug_to_group("inbox") == "inbox"

    def test_type_slug_unknown_falls_back_products(self):
        """Канон: неизвестный slug → products (а не ValueError)."""
        from atlas.paths import type_slug_to_group
        assert type_slug_to_group("unknown-type-slug") == "products"


# --------------------------------------------------------------------------- #
# archive_path / group_path                                                   #
# --------------------------------------------------------------------------- #


class TestPathBuilders:
    def test_archive_path_clients(self):
        from atlas.paths import archive_path
        root = Path("C:/PROJECT")
        result = archive_path(root, "clients", "cifro")
        assert result == Path("C:/PROJECT/_Archive/clients/cifro")

    def test_archive_path_products(self):
        from atlas.paths import archive_path
        root = Path("C:/PROJECT")
        assert archive_path(root, "products", "atlas-demo") == Path(
            "C:/PROJECT/_Archive/products/atlas-demo"
        )

    def test_archive_path_tests(self):
        from atlas.paths import archive_path
        root = Path("C:/PROJECT")
        assert archive_path(root, "tests", "spike") == Path(
            "C:/PROJECT/_Archive/tests/spike"
        )

    def test_archive_path_with_inbox_group(self):
        from atlas.paths import archive_path
        root = Path("C:/PROJECT")
        assert archive_path(root, "inbox", "cifro") == Path(
            "C:/PROJECT/_Archive/inbox/cifro"
        )

    def test_group_path_client_project(self):
        from atlas.paths import group_path
        root = Path("C:/PROJECT")
        assert group_path(root, "client-project", "cifro") == Path(
            "C:/PROJECT/Clients/cifro"
        )

    def test_group_path_business_product(self):
        from atlas.paths import group_path
        root = Path("C:/PROJECT")
        assert group_path(root, "business-product", "atlas-demo") == Path(
            "C:/PROJECT/Products/atlas-demo"
        )

    def test_group_path_personal_utility(self):
        from atlas.paths import group_path
        root = Path("C:/PROJECT")
        assert group_path(root, "personal-utility", "utility") == Path(
            "C:/PROJECT/Products/utility"
        )

    def test_group_path_test(self):
        from atlas.paths import group_path
        root = Path("C:/PROJECT")
        assert group_path(root, "test", "spike") == Path(
            "C:/PROJECT/Tests/spike"
        )

    def test_group_path_inbox(self):
        """inbox project_type → физическая папка PROJECT/_Inbox/<slug>/."""
        from atlas.paths import group_path
        root = Path("C:/PROJECT")
        assert group_path(root, "inbox", "cifro") == Path(
            "C:/PROJECT/_Inbox/cifro"
        )


# --------------------------------------------------------------------------- #
# expected_project_path                                                       #
# --------------------------------------------------------------------------- #


class TestExpectedProjectPath:
    def test_active_client_project(self):
        from atlas.paths import expected_project_path
        root = Path("C:/PROJECT")
        assert expected_project_path(
            root, "client-project", "cifro", archived=False
        ) == Path("C:/PROJECT/Clients/cifro")

    def test_active_business_product(self):
        from atlas.paths import expected_project_path
        root = Path("C:/PROJECT")
        assert expected_project_path(
            root, "business-product", "atlas-demo", archived=False
        ) == Path("C:/PROJECT/Products/atlas-demo")

    def test_active_test(self):
        from atlas.paths import expected_project_path
        root = Path("C:/PROJECT")
        assert expected_project_path(
            root, "test", "spike", archived=False
        ) == Path("C:/PROJECT/Tests/spike")

    def test_archived_with_archived_group(self):
        from atlas.paths import expected_project_path
        root = Path("C:/PROJECT")
        # В архиве — игнорируем type_slug, используем archived_group
        assert expected_project_path(
            root, "client-project", "cifro",
            archived=True, archived_group="clients",
        ) == Path("C:/PROJECT/_Archive/clients/cifro")

    def test_archived_products(self):
        from atlas.paths import expected_project_path
        root = Path("C:/PROJECT")
        assert expected_project_path(
            root, "business-product", "atlas-demo",
            archived=True, archived_group="products",
        ) == Path("C:/PROJECT/_Archive/products/atlas-demo")

    def test_archived_without_group_falls_back_to_type(self):
        """archived=True но archived_group не задан → падёт на type_slug mapping."""
        from atlas.paths import expected_project_path
        root = Path("C:/PROJECT")
        # Если archived_group None — используем type для определения группы
        assert expected_project_path(
            root, "client-project", "cifro",
            archived=True, archived_group=None,
        ) == Path("C:/PROJECT/_Archive/clients/cifro")

    def test_expected_project_path_inbox_active(self):
        """inbox type, active → root / _Inbox / <slug>."""
        from atlas.paths import expected_project_path
        root = Path("C:/PROJECT")
        assert expected_project_path(
            root, "inbox", "cifro", archived=False,
        ) == Path("C:/PROJECT/_Inbox/cifro")

    def test_expected_project_path_inbox_archived(self):
        """inbox type, archived → root / _Archive / inbox / <slug>."""
        from atlas.paths import expected_project_path
        root = Path("C:/PROJECT")
        assert expected_project_path(
            root, "inbox", "cifro",
            archived=True, archived_group="inbox",
        ) == Path("C:/PROJECT/_Archive/inbox/cifro")


# --------------------------------------------------------------------------- #
# get_projects_root                                                           #
# --------------------------------------------------------------------------- #


class TestGetProjectsRoot:
    def test_get_projects_root_from_env(self, monkeypatch, tmp_path):
        from atlas.paths import get_projects_root
        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        result = get_projects_root()
        assert result == tmp_path.resolve()

    def test_get_projects_root_default(self, monkeypatch):
        """Без env ATLAS_PROJECTS_ROOT → default путь (Path.home()/Documents/PROJECT)."""
        from atlas.paths import get_projects_root
        monkeypatch.delenv("ATLAS_PROJECTS_ROOT", raising=False)
        result = get_projects_root()
        # Путь должен оканчиваться на "PROJECT"
        assert result.name == "PROJECT"

    def test_get_projects_root_env_expanded(self, monkeypatch, tmp_path):
        """Env содержит ~ → должен expand."""
        from atlas.paths import get_projects_root
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", "~/my_projects")
        result = get_projects_root()
        # expanduser должен раскрыть ~ в $HOME
        assert str(result).endswith("my_projects")
