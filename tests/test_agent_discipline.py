"""`atlas init` — делегирование онбординга в кит ``agentskit`` (механизм там же).

Чистые тесты механизма (inject/markers/resolve_agent_keys/реестр) живут в
``agentskit`` (s-agentskit). Здесь — что `atlas init` корректно прокидывает
контент Atlas (DISCIPLINE_BODY + namespace 'atlas') в кит и сохраняет прежнее
поведение CLI (scope/agents/create/dry-run) + обратную совместимость маркеров.
"""
from __future__ import annotations

import json

from agentskit import has_managed_block
from typer.testing import CliRunner

runner = CliRunner()


def _app():
    from atlas.cli import app

    return app


# --------------------------------------------------------------------------- #
# Контент Atlas остаётся «своим» (плагин поверх кита)                          #
# --------------------------------------------------------------------------- #


def test_discipline_body_and_namespace_present():
    from atlas.discipline import ATLAS_NAMESPACE, DISCIPLINE_BODY

    assert ATLAS_NAMESPACE == "atlas"
    assert "Atlas — ведение задач" in DISCIPLINE_BODY


# --------------------------------------------------------------------------- #
# CLI atlas init — делегирование в agentskit                                  #
# --------------------------------------------------------------------------- #


def test_init_repo_scope_creates_agents_md(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(_app(), ["init", "--scope", "repo", "--create", "--json"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    actions = {r["path"].split("\\")[-1].split("/")[-1]: r["action"] for r in data["results"]}
    assert actions.get("AGENTS.md") == "created"
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert has_managed_block(agents, namespace="atlas")


def test_init_repo_scope_updates_existing_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Repo rules\n\n- foo\n", encoding="utf-8")
    res = runner.invoke(_app(), ["init", "--scope", "repo", "--json"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    paths = [r["path"] for r in data["results"]]
    assert any(p.endswith("CLAUDE.md") for p in paths)
    assert not any(p.endswith("AGENTS.md") for p in paths)
    txt = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "- foo" in txt and has_managed_block(txt, namespace="atlas")


def test_init_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# rules\n", encoding="utf-8")
    res = runner.invoke(_app(), ["init", "--scope", "repo", "--dry-run", "--json"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert data["dry_run"] is True
    assert all(r["action"].startswith("would-") for r in data["results"])
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "# rules\n"


def test_init_global_scope(tmp_path, monkeypatch):
    # global-путь резолвится в ките → мокаем agentskit.targets._global_claude_md.
    fake = tmp_path / ".claude" / "CLAUDE.md"
    monkeypatch.setattr("agentskit.targets._global_claude_md", lambda: fake)
    res = runner.invoke(_app(), ["init", "--scope", "global", "--agents", "claude", "--json"])
    assert res.exit_code == 0, res.stdout
    assert fake.exists() and has_managed_block(fake.read_text(encoding="utf-8"), namespace="atlas")


def test_init_agents_selects_specific_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(
        _app(),
        ["init", "--scope", "repo", "--agents", "gemini,cursor", "--create", "--json"],
    )
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert data["agents"] == ["gemini", "cursor"]
    names = {r["path"].replace("\\", "/").split("/")[-1] for r in data["results"]}
    assert names == {"GEMINI.md", ".cursorrules"}
    assert (tmp_path / "GEMINI.md").exists() and (tmp_path / ".cursorrules").exists()
    assert not (tmp_path / "CLAUDE.md").exists() and not (tmp_path / "AGENTS.md").exists()


def test_init_agents_copilot_nested_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(
        _app(), ["init", "--scope", "repo", "--agents", "copilot", "--create", "--json"]
    )
    assert res.exit_code == 0, res.stdout
    target = tmp_path / ".github" / "copilot-instructions.md"
    assert target.exists() and has_managed_block(target.read_text(encoding="utf-8"), namespace="atlas")


def test_init_agents_unknown_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(_app(), ["init", "--scope", "repo", "--agents", "windsurf-x"])
    assert res.exit_code != 0


def test_init_agents_repo_no_create_skips_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(_app(), ["init", "--scope", "repo", "--agents", "gemini", "--json"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout)
    assert all(r["action"] == "skipped" for r in data["results"])
    assert not (tmp_path / "GEMINI.md").exists()


def test_init_writes_atlas_markers_backcompat(tmp_path, monkeypatch):
    """Golden: atlas init пишет именно ATLAS:* маркеры (обратная совместимость)."""
    monkeypatch.chdir(tmp_path)
    runner.invoke(_app(), ["init", "--scope", "repo", "--agents", "codex", "--create", "--json"])
    txt = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "<!-- ATLAS:BEGIN managed -->" in txt
    assert "<!-- ATLAS:END -->" in txt
