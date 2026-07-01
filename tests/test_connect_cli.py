"""CLI `atlas connect / disconnect` + гейт sync (local-first → опц. подключение)."""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Изолируем config.toml + мокаем keystore (без реального keyring)."""
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path / "cfg"))
    for k in ("ATLAS_API_KEY", "ATLAS_OWNER", "ATLAS_ORG_NAMESPACE",
              "ATLAS_PERSONAL_NAMESPACE", "ATLAS_PERSONAL_OWNER", "ATLAS_TEAM_OWNER"):
        monkeypatch.delenv(k, raising=False)
    store: dict[str, str] = {}
    monkeypatch.setattr("atlas.keystore.save_api_key", lambda pid, key: store.__setitem__(pid, key))
    monkeypatch.setattr("atlas.keystore.load_api_key", lambda pid: store.get(pid))
    monkeypatch.setattr("atlas.keystore.clear_api_key", lambda pid: store.pop(pid, None))
    from atlas.appconfig import default_actor
    default_actor.cache_clear()
    yield


def _app():
    from atlas.cli import app
    return app


def test_status_disconnected_by_default():
    r = runner.invoke(_app(), ["connect"])
    assert r.exit_code == 0, r.stdout
    assert json.loads(r.stdout)["connected"] is False


def test_connect_sets_url_and_key():
    from atlas.appconfig import load_config

    r = runner.invoke(_app(), ["connect", "https://api.example.com",
                               "--key", "secret-admin-key", "--no-verify"])
    assert r.exit_code == 0, r.stdout
    cfg = load_config()
    assert cfg.base_url == "https://api.example.com"
    # ключ — в (мок-)secret-store, НЕ в открытом config.toml
    assert cfg.api_key == ""
    st = json.loads(runner.invoke(_app(), ["connect"]).stdout)
    assert st["connected"] is True and st["api_key_set"] is True


def test_disconnect_clears_key():
    runner.invoke(_app(), ["connect", "https://api.example.com", "--key", "k", "--no-verify"])
    r = runner.invoke(_app(), ["disconnect"])
    assert r.exit_code == 0, r.stdout
    st = json.loads(runner.invoke(_app(), ["connect"]).stdout)
    assert st["api_key_set"] is False and st["connected"] is False


def test_sync_requires_connection():
    # local-first: без подключения sync даёт понятную ошибку, не сетевой таймаут
    r = runner.invoke(_app(), ["sync", "push"])
    assert r.exit_code != 0  # понятная ошибка вместо сетевого таймаута
    assert "connect" in r.output.lower() or "подключ" in r.output.lower()


def test_local_first_works_without_connect(tmp_path, monkeypatch):
    # ключевая гарантия: основной функционал не требует backend
    monkeypatch.setenv("ATLAS_DB_URL", f"sqlite:///{tmp_path / 'lf.db'}")
    from atlas.db import make_engine, make_session
    from atlas.models import Base
    from atlas.seeds import seed_all
    eng = make_engine(f"sqlite:///{tmp_path / 'lf.db'}")
    Base.metadata.create_all(eng)
    with make_session(eng) as s:
        seed_all(s); s.commit()
    # dashboard работает без подключения (human-вывод по умолчанию)
    r = runner.invoke(_app(), ["dashboard"])
    assert r.exit_code == 0, r.stdout
    assert "Atlas" in r.output
