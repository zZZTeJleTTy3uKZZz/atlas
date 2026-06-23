"""F3g: daemon — установка/снятие Scheduled Task (powershell мокается)."""
import subprocess
import sys
import types

import pytest

from atlas.sync import daemon


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


def test_install_profile_aware(monkeypatch, tmp_path):
    """С профилем: своя задача (суффикс), свой VBS, и --profile в команде watch."""
    monkeypatch.setattr(daemon, "_bash_exe", lambda: r"C:\Git\bin\bash.exe")
    monkeypatch.setattr(daemon, "_atlas_root", lambda: tmp_path)
    monkeypatch.delenv("ATLAS_PROFILE", raising=False)
    captured = {}

    def run(script):
        captured["script"] = script
        return types.SimpleNamespace(returncode=0, stdout="installed", stderr="")

    res = daemon.install(profile="dmitry", run=run)
    assert res["ok"] is True
    assert res["task"] == "atlas-sync-watch-dmitry"
    assert res["profile"] == "dmitry"
    assert "atlas-sync-watch-dmitry" in captured["script"]
    # профиль-специфичный VBS, в команде watch стоит --profile dmitry
    vbs = tmp_path / "scripts" / "sync_watch_headless-dmitry.vbs"
    assert vbs.exists()
    assert "--profile dmitry --text sync watch" in vbs.read_text(encoding="ascii")


def test_install_reads_profile_from_env(monkeypatch, tmp_path):
    """Без явного profile берём ATLAS_PROFILE (его ставит корневой --profile)."""
    monkeypatch.setattr(daemon, "_bash_exe", lambda: r"C:\Git\bin\bash.exe")
    monkeypatch.setattr(daemon, "_atlas_root", lambda: tmp_path)
    monkeypatch.setenv("ATLAS_PROFILE", "admin")

    def run(script):
        return types.SimpleNamespace(returncode=0, stdout="installed", stderr="")

    res = daemon.install(run=run)
    assert res["task"] == "atlas-sync-watch-admin"
    assert (tmp_path / "scripts" / "sync_watch_headless-admin.vbs").exists()
