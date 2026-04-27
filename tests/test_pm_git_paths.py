"""Тесты для atlas.pm.git_paths.

derive_group_path(project_type, status, archived_group) → строковый GitLab path
относительно top-level группы `cifropro1`.

Маппинг:
- archived_group is not None → `cifropro1/archive/{archived_group}`
- status ∈ ('archived','frozen','completed') и тип client-project →
  `cifropro1/archive/clients`
- type 'client-project' → `cifropro1/clients`
- type 'business-product' / 'personal-utility' / 'personal-project'
  / 'shared-infrastructure' → `cifropro1/products`
- type 'test' → `cifropro1/tests`
- type 'inbox' → `cifropro1/inbox`

TDD: пишется до реализации.
"""
from __future__ import annotations

import pytest


# ----------------------------------------------------------------------
# archived_group приоритет
# ----------------------------------------------------------------------


class TestArchivedGroupTakesPrecedence:
    def test_archived_group_clients_overrides_active_type(self):
        from atlas.pm.git_paths import derive_group_path

        # Тип активный, но archived_group задан → archive путь.
        assert (
            derive_group_path("client-project", "active", "clients")
            == "cifropro1/archive/clients"
        )

    def test_archived_group_products_for_business_product(self):
        from atlas.pm.git_paths import derive_group_path

        assert (
            derive_group_path("business-product", "active", "products")
            == "cifropro1/archive/products"
        )

    def test_archived_group_tests(self):
        from atlas.pm.git_paths import derive_group_path

        assert (
            derive_group_path("test", "active", "tests")
            == "cifropro1/archive/tests"
        )

    def test_archived_group_inbox(self):
        from atlas.pm.git_paths import derive_group_path

        assert (
            derive_group_path("inbox", "active", "inbox")
            == "cifropro1/archive/inbox"
        )


# ----------------------------------------------------------------------
# status archived/frozen/completed для client-project → archive/clients
# ----------------------------------------------------------------------


class TestStatusBasedArchive:
    @pytest.mark.parametrize("status", ["archived", "frozen", "completed"])
    def test_client_project_with_archive_status_maps_to_archive_clients(self, status):
        from atlas.pm.git_paths import derive_group_path

        assert (
            derive_group_path("client-project", status, None)
            == "cifropro1/archive/clients"
        )

    def test_client_project_paused_is_not_archived(self):
        """paused — это активная папка clients (rule даёт archived/frozen/completed only)."""
        from atlas.pm.git_paths import derive_group_path

        assert derive_group_path("client-project", "paused", None) == "cifropro1/clients"


# ----------------------------------------------------------------------
# Активные маппинги по типам
# ----------------------------------------------------------------------


class TestActiveTypeMapping:
    def test_client_project_active(self):
        from atlas.pm.git_paths import derive_group_path

        assert derive_group_path("client-project", "active", None) == "cifropro1/clients"

    @pytest.mark.parametrize(
        "type_slug",
        [
            "business-product",
            "personal-utility",
            "personal-project",
            "shared-infrastructure",
        ],
    )
    def test_product_like_types(self, type_slug):
        from atlas.pm.git_paths import derive_group_path

        assert derive_group_path(type_slug, "active", None) == "cifropro1/products"

    def test_test_type(self):
        from atlas.pm.git_paths import derive_group_path

        assert derive_group_path("test", "active", None) == "cifropro1/tests"

    def test_inbox_type(self):
        from atlas.pm.git_paths import derive_group_path

        assert derive_group_path("inbox", "active", None) == "cifropro1/inbox"


# ----------------------------------------------------------------------
# Edge cases: неизвестный тип
# ----------------------------------------------------------------------


class TestUnknownType:
    def test_unknown_type_raises_value_error(self):
        from atlas.pm.git_paths import derive_group_path

        with pytest.raises(ValueError, match="Неизвестный project_type"):
            derive_group_path("frozen-bananas", "active", None)


# ----------------------------------------------------------------------
# TOP_LEVEL_GROUP константа
# ----------------------------------------------------------------------


def test_top_level_group_is_cifropro1():
    from atlas.pm.git_paths import TOP_LEVEL_GROUP

    assert TOP_LEVEL_GROUP == "cifropro1"
