"""CLI `atlas config show/get/set` — онбординг-конфиг."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from atlas.commands.config import config_app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Изолируем config.toml в tmp (ATLAS_CONFIG_DIR) + чистим кэш owner.

    Глобальный conftest ставит generic ATLAS_* env (owner/namespaces) на уровне
    модуля — они перебили бы config.toml. Снимаем их, чтобы тест видел именно
    записанный командой config.toml-слой.
    """
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path / "cfg"))
    for k in (
        "ATLAS_OWNER", "ATLAS_ORG_NAMESPACE", "ATLAS_PERSONAL_NAMESPACE",
        "ATLAS_PERSONAL_OWNER", "ATLAS_TEAM_OWNER",
    ):
        monkeypatch.delenv(k, raising=False)
    from atlas.appconfig import default_actor
    default_actor.cache_clear()
    yield
    default_actor.cache_clear()


def test_set_writes_to_config_and_load_sees_it():
    from atlas.appconfig import default_actor, load_config

    r = runner.invoke(config_app, ["set", "owner", "alice"])
    assert r.exit_code == 0, r.output
    default_actor.cache_clear()
    assert load_config().owner == "alice"
    assert default_actor() == "alice"


def test_set_namespaces():
    from atlas.appconfig import load_config

    assert runner.invoke(config_app, ["set", "org_namespace", "acme"]).exit_code == 0
    assert load_config().org_namespace == "acme"


def test_set_rejects_api_key():
    r = runner.invoke(config_app, ["set", "api_key", "secret"])
    assert r.exit_code != 0


def test_set_rejects_unknown_field():
    r = runner.invoke(config_app, ["set", "bogus_field", "x"])
    assert r.exit_code != 0


def test_get_unknown_field_errors():
    assert runner.invoke(config_app, ["get", "nope"]).exit_code != 0


def test_show_runs():
    r = runner.invoke(config_app, ["show"])
    assert r.exit_code == 0
