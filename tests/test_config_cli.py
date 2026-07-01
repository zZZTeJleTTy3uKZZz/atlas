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
        "ATLAS_PERSONAL_OWNER", "ATLAS_TEAM_OWNER", "ATLAS_PROJECTS_ROOT",
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


def test_set_default_task_fields():
    from atlas.appconfig import load_config

    assert runner.invoke(config_app, ["set", "default_priority", "P1"]).exit_code == 0
    assert runner.invoke(config_app, ["set", "default_review", "false"]).exit_code == 0
    assert runner.invoke(config_app, ["set", "default_reviewer", "bob"]).exit_code == 0
    cfg = load_config()
    assert cfg.default_priority == "P1"
    assert cfg.default_review is False
    assert cfg.default_reviewer == "bob"


def test_init_wizard_interactive():
    from atlas.appconfig import load_config

    # Порядок промптов: owner, timezone, projects_root, priority, review?(y),
    # reviewer, org-ns, personal-ns, github_owner, team-owner, agents(all).
    inp = "alice\n+04:00\n/tmp/portfolio\nP1\ny\nbob\n\nmy-ns\nacme\n\n\n"
    r = runner.invoke(config_app, ["init"], input=inp)
    assert r.exit_code == 0, r.output
    cfg = load_config()
    assert cfg.owner == "alice"
    assert cfg.timezone == "+04:00"
    assert cfg.projects_root == "/tmp/portfolio"
    assert cfg.default_priority == "P1"
    assert cfg.default_review is True
    assert cfg.default_reviewer == "bob"
    assert cfg.personal_namespace == "my-ns"
    assert cfg.github_owner == "acme"


def test_projects_root_from_config(monkeypatch):
    # config.projects_root → get_projects_root (когда env не задан)
    monkeypatch.delenv("ATLAS_PROJECTS_ROOT", raising=False)
    assert runner.invoke(config_app, ["set", "projects_root", "/custom/portfolio"]).exit_code == 0
    from atlas.paths import get_projects_root
    assert str(get_projects_root()).replace("\\", "/").endswith("/custom/portfolio")
