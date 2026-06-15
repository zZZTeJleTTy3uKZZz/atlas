"""F3g: daemon — установка/снятие Scheduled Task (powershell мокается)."""
import subprocess
import sys
import types

import pytest

from atlas.pm.sync import daemon


def _fake_run(rc=0, out="installed", err=""):
    def run(script):
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)
    return run


def test_install_registers_atlogon_task(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "_bash_exe", lambda: r"C:\Git\bin\bash.exe")
    monkeypatch.setattr(daemon, "_atlas_root", lambda: tmp_path)
    captured = {}

    def run(script):
        captured["script"] = script
        return types.SimpleNamespace(returncode=0, stdout="installed", stderr="")

    res = daemon.install(run=run)
    assert res["ok"] is True
    assert res["task"] == daemon.TASK_NAME
    # ключевое: триггер при входе + рестарт + регистрация
    assert "-AtLogOn" in captured["script"]
    assert "RestartCount" in captured["script"]
    assert "Register-ScheduledTask" in captured["script"]
    # headless VBS создан
    assert (tmp_path / "scripts" / "sync_watch_headless.vbs").exists()


def test_install_no_bash(monkeypatch):
    monkeypatch.setattr(daemon, "_bash_exe", lambda: None)
    res = daemon.install(run=_fake_run())
    assert res["ok"] is False


def test_uninstall_calls_unregister(monkeypatch):
    captured = {}
    def run(script):
        captured["script"] = script
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    res = daemon.uninstall(run=run)
    assert res["ok"] is True
    assert "Unregister-ScheduledTask" in captured["script"]
