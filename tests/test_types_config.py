"""Канон типов: единый источник базовых типов + types.toml override + merge.

TDD: проверяет, что
- BASE_PROJECT_TYPES содержит полные записи (5 + роли + test + inbox);
- роли kit/service/superskill заведены с верными group/policy;
- merge by slug накладывает types.toml поверх базовых (override + дополнение);
- seed_project_types(merged) заводит ВСЕ типы с верными storage_group/policy;
- идемпотентность: повторный seed не плодит дубли, обновляет поля.
"""
from __future__ import annotations

from sqlalchemy import func, select


# --------------------------------------------------------------------------- #
# BASE_PROJECT_TYPES — встроенный единый источник                             #
# --------------------------------------------------------------------------- #


def test_base_project_types_full_records():
    from atlas.seeds import BASE_PROJECT_TYPES

    by_slug = {t["slug"]: t for t in BASE_PROJECT_TYPES}

    # 5 исходных + 3 роли + test + inbox = 10
    expected = {
        "client-project", "business-product", "personal-utility",
        "personal-project", "shared-infrastructure",
        "kit", "service", "superskill",
        "test", "inbox",
    }
    assert set(by_slug) == expected

    # каждая запись — полная (все поля)
    for t in BASE_PROJECT_TYPES:
        assert set(t) >= {
            "slug", "name", "description", "color",
            "default_sync_policy", "storage_group",
        }


def test_base_types_preserve_existing_5_verbatim():
    """name/description/color исходных 5 типов сохранены дословно."""
    from atlas.seeds import BASE_PROJECT_TYPES

    by_slug = {t["slug"]: t for t in BASE_PROJECT_TYPES}

    assert by_slug["client-project"]["name"] == "Клиентские проекты"
    assert by_slug["client-project"]["description"] == (
        "Внедрения Bitrix24 + AI-агенты для клиентов Cifro.pro"
    )
    assert by_slug["client-project"]["color"] == "#F97316"
    assert by_slug["client-project"]["storage_group"] == "clients"
    assert by_slug["client-project"]["default_sync_policy"] == "full"

    assert by_slug["business-product"]["color"] == "#10B981"
    assert by_slug["business-product"]["storage_group"] == "products"
    assert by_slug["business-product"]["default_sync_policy"] == "epics"


def test_base_types_test_and_inbox_are_full():
    """Фантомы вылечены: test/inbox — полноценные типы."""
    from atlas.seeds import BASE_PROJECT_TYPES

    by_slug = {t["slug"]: t for t in BASE_PROJECT_TYPES}

    assert by_slug["test"]["storage_group"] == "tests"
    assert by_slug["test"]["default_sync_policy"] == "local"
    assert by_slug["inbox"]["storage_group"] == "inbox"
    assert by_slug["inbox"]["default_sync_policy"] == "local"


def test_base_types_roles_seeded():
    """Роли kit/service/superskill с group=products, policy=epics."""
    from atlas.seeds import BASE_PROJECT_TYPES

    by_slug = {t["slug"]: t for t in BASE_PROJECT_TYPES}

    for slug in ("kit", "service", "superskill"):
        assert by_slug[slug]["storage_group"] == "products"
        assert by_slug[slug]["default_sync_policy"] == "epics"
        assert by_slug[slug]["name"]
        assert by_slug[slug]["description"]


# --------------------------------------------------------------------------- #
# types.toml loader + merge                                                   #
# --------------------------------------------------------------------------- #


def test_load_user_types_absent_returns_empty(tmp_path, monkeypatch):
    from atlas.seeds import load_user_types

    monkeypatch.setenv("ATLAS_TYPES_FILE", str(tmp_path / "nope.toml"))
    assert load_user_types() == []


def test_merged_types_override_and_add(tmp_path, monkeypatch):
    from atlas.seeds import merged_project_types

    toml = tmp_path / "types.toml"
    toml.write_text(
        """
[[types]]
slug = "kit"
name = "Переопределённый Kit"
default_sync_policy = "full"

[[types]]
slug = "worker-kit"
name = "Worker Kit"
description = "Кастомный тип"
color = "#123456"
default_sync_policy = "epics"
storage_group = "products"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLAS_TYPES_FILE", str(toml))

    merged = merged_project_types()
    by_slug = {t["slug"]: t for t in merged}

    # override по slug
    assert by_slug["kit"]["name"] == "Переопределённый Kit"
    assert by_slug["kit"]["default_sync_policy"] == "full"
    # дополнение новым типом
    assert "worker-kit" in by_slug
    assert by_slug["worker-kit"]["storage_group"] == "products"
    # базовые не потеряны
    assert "client-project" in by_slug


# --------------------------------------------------------------------------- #
# seed_project_types(merged)                                                  #
# --------------------------------------------------------------------------- #


def _mem_session():
    from atlas.db import make_engine, make_session
    from atlas.models import Base

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return make_session(engine)


def test_seed_project_types_writes_group_and_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_TYPES_FILE", str(tmp_path / "nope.toml"))
    from atlas.models import ProjectType
    from atlas.seeds import seed_project_types, seed_sync_policies

    with _mem_session() as s:
        seed_sync_policies(s)
        seed_project_types(s)
        s.commit()

        count = s.execute(select(func.count()).select_from(ProjectType)).scalar()
        assert count == 10

        kit = s.execute(
            select(ProjectType).where(ProjectType.slug == "kit")
        ).scalar_one()
        assert kit.storage_group == "products"
        assert kit.default_sync_policy == "epics"

        test_t = s.execute(
            select(ProjectType).where(ProjectType.slug == "test")
        ).scalar_one()
        assert test_t.storage_group == "tests"
        assert test_t.default_sync_policy == "local"


def test_seed_project_types_idempotent_updates(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_TYPES_FILE", str(tmp_path / "nope.toml"))
    from atlas.models import ProjectType
    from atlas.seeds import seed_project_types, seed_sync_policies

    with _mem_session() as s:
        seed_sync_policies(s)
        seed_project_types(s)
        s.commit()
        seed_project_types(s)  # второй прогон
        s.commit()

        count = s.execute(select(func.count()).select_from(ProjectType)).scalar()
        assert count == 10
