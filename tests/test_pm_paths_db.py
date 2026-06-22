"""type_slug_to_group читает storage_group из БД + fallback products (не ValueError)."""
from __future__ import annotations


def _seeded_session(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_TYPES_FILE", str(tmp_path / "nope.toml"))
    from atlas.pm.db import make_engine, make_session
    from atlas.pm.models import Base
    from atlas.pm.seeds import seed_project_types, seed_sync_policies

    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    ctx = make_session(engine)
    s = ctx.__enter__()
    seed_sync_policies(s)
    seed_project_types(s)
    s.commit()
    return ctx, s


def test_group_from_db_known_type(monkeypatch, tmp_path):
    from atlas.pm.paths import type_slug_to_group

    ctx, s = _seeded_session(monkeypatch, tmp_path)
    try:
        assert type_slug_to_group("client-project", session=s) == "clients"
        assert type_slug_to_group("kit", session=s) == "products"
        assert type_slug_to_group("test", session=s) == "tests"
        assert type_slug_to_group("inbox", session=s) == "inbox"
    finally:
        ctx.__exit__(None, None, None)


def test_group_unknown_type_falls_back_products(monkeypatch, tmp_path):
    """Неизвестный slug → products (НЕ ValueError)."""
    from atlas.pm.paths import type_slug_to_group

    ctx, s = _seeded_session(monkeypatch, tmp_path)
    try:
        assert type_slug_to_group("totally-unknown", session=s) == "products"
    finally:
        ctx.__exit__(None, None, None)


def test_group_type_without_storage_group_falls_back_products(monkeypatch, tmp_path):
    """Тип есть, но storage_group=NULL → products."""
    from atlas.pm.models import ProjectType
    from atlas.pm.paths import type_slug_to_group

    ctx, s = _seeded_session(monkeypatch, tmp_path)
    try:
        s.add(ProjectType(slug="nogroup", name="No Group", storage_group=None))
        s.commit()
        assert type_slug_to_group("nogroup", session=s) == "products"
    finally:
        ctx.__exit__(None, None, None)


def test_group_no_session_uses_bootstrap_default():
    """Без сессии — аварийный bootstrap-дефолт (обратная совместимость)."""
    from atlas.pm.paths import type_slug_to_group

    assert type_slug_to_group("client-project") == "clients"
    # неизвестный без сессии — тоже products (а не ValueError)
    assert type_slug_to_group("totally-unknown") == "products"
