"""CLI `atlas upgrade` — обновление без skillery (pipx/editable)."""
from __future__ import annotations

import json

from typer.testing import CliRunner

runner = CliRunner()


def _app():
    from atlas.cli import app
    return app


def test_upgrade_check_shows_version_and_method():
    r = runner.invoke(_app(), ["upgrade", "--check"])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    assert "current" in d and "method" in d


def test_upgrade_editable_skips_reinstall(monkeypatch):
    monkeypatch.setattr("atlas.commands.upgrade._install_method", lambda: "editable")
    called = []
    monkeypatch.setattr("atlas.commands.upgrade.subprocess.run",
                        lambda *a, **k: called.append(a))
    r = runner.invoke(_app(), ["upgrade"])
    assert r.exit_code == 0, r.stdout
    assert json.loads(r.stdout)["method"] == "editable"
    assert called == []  # editable — reinstall не запускается


def test_upgrade_runs_pipx(monkeypatch):
    monkeypatch.setattr("atlas.commands.upgrade._install_method", lambda: "pipx-git")
    monkeypatch.setattr("atlas.commands.upgrade.shutil.which", lambda x: "/usr/bin/pipx")
    calls = []

    class _R:
        returncode = 0

    monkeypatch.setattr("atlas.commands.upgrade.subprocess.run",
                        lambda cmd, check: calls.append(cmd) or _R())
    r = runner.invoke(_app(), ["upgrade"])
    assert r.exit_code == 0, r.stdout
    assert calls and "upgrade" in calls[0]


def test_upgrade_no_pipx_errors(monkeypatch):
    monkeypatch.setattr("atlas.commands.upgrade._install_method", lambda: "pipx-git")
    monkeypatch.setattr("atlas.commands.upgrade.shutil.which", lambda x: None)
    r = runner.invoke(_app(), ["upgrade"])
    assert r.exit_code != 0
