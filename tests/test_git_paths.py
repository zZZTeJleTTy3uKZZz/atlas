"""Тесты для atlas.git_paths.

derive_group_path(project_type, status, archived_group) → строковый GitLab path
относительно top-level группы `example-org`.

Маппинг:
- archived_group is not None → `example-org/archive/{archived_group}`
- status ∈ ('archived','frozen','completed') и тип client-project →
  `example-org/archive/clients`
- type 'client-project' → `example-org/clients`
- type 'business-product' / 'personal-utility' / 'personal-project'
  / 'shared-infrastructure' → `example-org/products`
- type 'test' → `example-org/tests`
- type 'inbox' → `example-org/inbox`

TDD: пишется до реализации.
"""
from __future__ import annotations

import pytest


# ----------------------------------------------------------------------
# archived_group приоритет
# ----------------------------------------------------------------------


class TestArchivedGroupTakesPrecedence:
    def test_archived_group_clients_overrides_active_type(self):
        from atlas.git_paths import derive_group_path

        # Тип активный, но archived_group задан → archive путь.
        assert (
            derive_group_path("client-project", "active", "clients")
            == "example-org/archive/clients"
        )

    def test_archived_group_products_for_business_product(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path("business-product", "active", "products")
            == "example-org/archive/products"
        )

    def test_archived_group_tests(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path("test", "active", "tests")
            == "example-org/archive/tests"
        )

    def test_archived_group_inbox(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path("inbox", "active", "inbox")
            == "example-org/archive/inbox"
        )


# ----------------------------------------------------------------------
# status archived/frozen/completed для client-project → archive/clients
# ----------------------------------------------------------------------


class TestStatusBasedArchive:
    @pytest.mark.parametrize("status", ["archived", "frozen", "completed"])
    def test_client_project_with_archive_status_maps_to_archive_clients(self, status):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path("client-project", status, None)
            == "example-org/archive/clients"
        )

    def test_client_project_paused_is_not_archived(self):
        """paused — это активная папка clients (rule даёт archived/frozen/completed only)."""
        from atlas.git_paths import derive_group_path

        assert derive_group_path("client-project", "paused", None) == "example-org/clients"


# ----------------------------------------------------------------------
# Активные маппинги по типам
# ----------------------------------------------------------------------


class TestActiveTypeMapping:
    def test_client_project_active(self):
        from atlas.git_paths import derive_group_path

        assert derive_group_path("client-project", "active", None) == "example-org/clients"

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
        from atlas.git_paths import derive_group_path

        assert derive_group_path(type_slug, "active", None) == "example-org/products"

    def test_test_type(self):
        from atlas.git_paths import derive_group_path

        assert derive_group_path("test", "active", None) == "example-org/tests"

    def test_inbox_type(self):
        from atlas.git_paths import derive_group_path

        assert derive_group_path("inbox", "active", None) == "example-org/inbox"


# ----------------------------------------------------------------------
# Edge cases: неизвестный тип
# ----------------------------------------------------------------------


class TestUnknownType:
    def test_unknown_type_raises_value_error(self):
        from atlas.git_paths import derive_group_path

        with pytest.raises(ValueError, match="Неизвестный project_type"):
            derive_group_path("frozen-bananas", "active", None)


# ----------------------------------------------------------------------
# Namespaces из конфига (раньше — модульные константы-хардкоды)
# ----------------------------------------------------------------------


def test_namespaces_from_config():
    """org/personal namespaces + personal_owner берутся из конфига (conftest)."""
    from atlas.git_paths import _namespaces

    org, personal, personal_owner = _namespaces()
    assert org == "example-org"
    assert personal == "example-personal"
    assert personal_owner == "owner"


# ----------------------------------------------------------------------
# Owner-aware namespace selection
# ----------------------------------------------------------------------


class TestOwnerAwareNamespace:
    """Если у проекта есть тег `owner:owner` — namespace = личный
    (`example-personal/...`), иначе по умолчанию `example-org/...`.

    Передавать список owner_tags явно (slug-ов из category=owner): как из
    проекта они «прилетают».
    """

    def test_owner_owner_personal_namespace_for_personal_utility(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path(
                "personal-utility", "active", None, owner_tags=["owner"]
            )
            == "example-personal/products"
        )

    def test_owner_owner_personal_namespace_for_client_project(self):
        """У владельца есть собственные клиентские проекты (личные клиенты)."""
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path(
                "client-project", "active", None, owner_tags=["owner"]
            )
            == "example-personal/clients"
        )

    def test_owner_owner_archive(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path(
                "client-project", "archived", "clients", owner_tags=["owner"]
            )
            == "example-personal/archive/clients"
        )

    def test_owner_cifro_pro_uses_business_namespace(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path(
                "client-project", "active", None, owner_tags=["example-org"]
            )
            == "example-org/clients"
        )

    def test_owner_owner_wins_over_cifro_pro_when_both_present(self):
        """Если у проекта оба owner-тега — личный приоритетнее.

        (Этого почти не должно быть, но защищаемся.)
        """
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path(
                "client-project", "active", None,
                owner_tags=["example-org", "owner"],
            )
            == "example-personal/clients"
        )

    def test_no_owner_tags_defaults_to_example_org(self):
        """Без owner-тегов — fallback на бизнес-namespace.

        Так работает legacy-вызов без kwarg owner_tags.
        """
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path("business-product", "active", None)
            == "example-org/products"
        )

    def test_empty_owner_tags_list_defaults_to_example_org(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path(
                "business-product", "active", None, owner_tags=[]
            )
            == "example-org/products"
        )
