"""Тесты для atlas.git_paths.

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
        from atlas.git_paths import derive_group_path

        # Тип активный, но archived_group задан → archive путь.
        assert (
            derive_group_path("client-project", "active", "clients")
            == "cifropro1/archive/clients"
        )

    def test_archived_group_products_for_business_product(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path("business-product", "active", "products")
            == "cifropro1/archive/products"
        )

    def test_archived_group_tests(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path("test", "active", "tests")
            == "cifropro1/archive/tests"
        )

    def test_archived_group_inbox(self):
        from atlas.git_paths import derive_group_path

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
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path("client-project", status, None)
            == "cifropro1/archive/clients"
        )

    def test_client_project_paused_is_not_archived(self):
        """paused — это активная папка clients (rule даёт archived/frozen/completed only)."""
        from atlas.git_paths import derive_group_path

        assert derive_group_path("client-project", "paused", None) == "cifropro1/clients"


# ----------------------------------------------------------------------
# Активные маппинги по типам
# ----------------------------------------------------------------------


class TestActiveTypeMapping:
    def test_client_project_active(self):
        from atlas.git_paths import derive_group_path

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
        from atlas.git_paths import derive_group_path

        assert derive_group_path(type_slug, "active", None) == "cifropro1/products"

    def test_test_type(self):
        from atlas.git_paths import derive_group_path

        assert derive_group_path("test", "active", None) == "cifropro1/tests"

    def test_inbox_type(self):
        from atlas.git_paths import derive_group_path

        assert derive_group_path("inbox", "active", None) == "cifropro1/inbox"


# ----------------------------------------------------------------------
# Edge cases: неизвестный тип
# ----------------------------------------------------------------------


class TestUnknownType:
    def test_unknown_type_raises_value_error(self):
        from atlas.git_paths import derive_group_path

        with pytest.raises(ValueError, match="Неизвестный project_type"):
            derive_group_path("frozen-bananas", "active", None)


# ----------------------------------------------------------------------
# TOP_LEVEL_GROUP константа
# ----------------------------------------------------------------------


def test_top_level_group_is_cifropro1():
    from atlas.git_paths import TOP_LEVEL_GROUP

    assert TOP_LEVEL_GROUP == "cifropro1"


def test_personal_top_level_group_constant():
    from atlas.git_paths import PERSONAL_TOP_LEVEL_GROUP

    assert PERSONAL_TOP_LEVEL_GROUP == "zzztejletty3ukzzz"


# ----------------------------------------------------------------------
# Owner-aware namespace selection
# ----------------------------------------------------------------------


class TestOwnerAwareNamespace:
    """Если у проекта есть тег `owner:dmitry` — namespace = личный
    (`zzztejletty3ukzzz/...`), иначе по умолчанию `cifropro1/...`.

    Передавать список owner_tags явно (slug-ов из category=owner): как из
    проекта они «прилетают».
    """

    def test_owner_dmitry_personal_namespace_for_personal_utility(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path(
                "personal-utility", "active", None, owner_tags=["dmitry"]
            )
            == "zzztejletty3ukzzz/products"
        )

    def test_owner_dmitry_personal_namespace_for_client_project(self):
        """У Дмитрия есть собственные клиентские проекты (личные клиенты)."""
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path(
                "client-project", "active", None, owner_tags=["dmitry"]
            )
            == "zzztejletty3ukzzz/clients"
        )

    def test_owner_dmitry_archive(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path(
                "client-project", "archived", "clients", owner_tags=["dmitry"]
            )
            == "zzztejletty3ukzzz/archive/clients"
        )

    def test_owner_cifro_pro_uses_business_namespace(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path(
                "client-project", "active", None, owner_tags=["cifro-pro"]
            )
            == "cifropro1/clients"
        )

    def test_owner_dmitry_wins_over_cifro_pro_when_both_present(self):
        """Если у проекта оба owner-тега — личный приоритетнее.

        (Этого почти не должно быть, но защищаемся.)
        """
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path(
                "client-project", "active", None,
                owner_tags=["cifro-pro", "dmitry"],
            )
            == "zzztejletty3ukzzz/clients"
        )

    def test_no_owner_tags_defaults_to_cifropro1(self):
        """Без owner-тегов — fallback на бизнес-namespace.

        Так работает legacy-вызов без kwarg owner_tags.
        """
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path("business-product", "active", None)
            == "cifropro1/products"
        )

    def test_empty_owner_tags_list_defaults_to_cifropro1(self):
        from atlas.git_paths import derive_group_path

        assert (
            derive_group_path(
                "business-product", "active", None, owner_tags=[]
            )
            == "cifropro1/products"
        )
