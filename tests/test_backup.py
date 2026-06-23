"""Тесты для atlas.backup — pure logic helper для backup репозиториев.

Покрывает:
- _backup_repo() — реализует ту же логику что shell-скрипт daily_backup.sh:
  snapshot working tree через TEMP index → write-tree → compare с предыдущим
  refs/heads/backup^{tree} → если различается, commit-tree + update-ref + push.

ВАЖНО: ВСЕ subprocess вызовы git мокаются. Никаких реальных push-ов.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest


# --------------------------------------------------------------------------- #
# Fixture: подмена subprocess.run в backup module                              #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def fake_runner(monkeypatch):
    """Перехватываем backup.run, возвращаем подменяемые ответы.

    Возвращает dict:
        - calls: list of {"cmd": [...], "cwd": str_or_None, "env": dict_or_None}
        - set: запушить серию (rc, stdout, stderr) в очередь ответов
        - by_cmd: задать ответы по prefix-match команды (priority over queue).
    """
    from atlas import backup

    calls: list[dict[str, Any]] = []
    queue: list[tuple[int, str, str]] = []
    by_cmd_responses: dict[tuple[str, ...], tuple[int, str, str]] = {}

    def _set_responses(*items: tuple[int, str, str]) -> None:
        queue.extend(items)

    def _set_by_cmd(prefix: tuple[str, ...], response: tuple[int, str, str]) -> None:
        by_cmd_responses[prefix] = response

    def fake_run(cmd, *, cwd=None, env=None):
        cmd_list = list(cmd)
        cwd_str = str(cwd) if cwd is not None else None
        calls.append({
            "cmd": cmd_list,
            "cwd": cwd_str,
            "env": dict(env) if env else None,
        })
        # Сначала проверяем match по prefix.
        for prefix, resp in by_cmd_responses.items():
            if tuple(cmd_list[: len(prefix)]) == prefix:
                return resp
        if queue:
            return queue.pop(0)
        return (0, "", "")

    monkeypatch.setattr(backup, "run", fake_run)
    return {
        "calls": calls,
        "set": _set_responses,
        "by_cmd": _set_by_cmd,
    }


# --------------------------------------------------------------------------- #
# run() helper                                                                #
# --------------------------------------------------------------------------- #


class TestRun:
    def test_run_returns_returncode_stdout_stderr_tuple(self, monkeypatch):
        from atlas import backup

        captured: dict[str, Any] = {}

        def fake_subproc_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="ok", stderr=""
            )

        monkeypatch.setattr(backup.subprocess, "run", fake_subproc_run)
        rc, out, err = backup.run(["git", "status"], cwd="/tmp/x")
        assert rc == 0
        assert out == "ok"
        assert captured["cmd"] == ["git", "status"]
        assert captured["kwargs"]["cwd"] == "/tmp/x"
        assert captured["kwargs"]["text"] is True

    def test_run_supports_env_param(self, monkeypatch):
        from atlas import backup

        captured: dict[str, Any] = {}

        def fake_subproc_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        monkeypatch.setattr(backup.subprocess, "run", fake_subproc_run)
        backup.run(["git", "x"], cwd="/tmp/x", env={"GIT_INDEX_FILE": "/x"})
        assert captured["env"] == {"GIT_INDEX_FILE": "/x"}


# --------------------------------------------------------------------------- #
# _backup_repo                                                                 #
# --------------------------------------------------------------------------- #


class TestBackupRepo:
    """Тесты основной функции backup_repo."""

    def test_returns_failed_if_not_git_repo(self, tmp_path, fake_runner):
        """Папка без .git → status='failed'."""
        from atlas.backup import backup_repo

        # tmp_path не содержит .git
        result = backup_repo(tmp_path)
        assert result["status"] == "failed"
        assert "git" in result.get("error", "").lower() or "repo" in result.get("error", "").lower()

    def test_returns_failed_if_no_origin_remote(self, tmp_path, fake_runner):
        """Репо без origin remote → status='failed'."""
        from atlas.backup import backup_repo

        (tmp_path / ".git").mkdir()
        # remote get-url origin вернёт ненулевой код.
        fake_runner["by_cmd"](
            ("git", "remote", "get-url", "origin"),
            (1, "", "fatal: No such remote 'origin'"),
        )

        result = backup_repo(tmp_path)
        assert result["status"] == "failed"
        assert "origin" in result.get("error", "").lower()

    def test_skipped_if_no_changes(self, tmp_path, fake_runner):
        """Если write-tree вернул тот же tree, что и refs/heads/backup^{tree} — skip."""
        from atlas.backup import backup_repo

        (tmp_path / ".git").mkdir()
        same_tree = "abc123tree"

        fake_runner["by_cmd"](
            ("git", "remote", "get-url", "origin"),
            (0, "git@example.com:repo.git", ""),
        )
        fake_runner["by_cmd"](("git", "add", "-A"), (0, "", ""))
        fake_runner["by_cmd"](("git", "write-tree"), (0, same_tree, ""))
        fake_runner["by_cmd"](
            ("git", "rev-parse", "--verify", "--quiet", "refs/heads/backup^{tree}"),
            (0, same_tree, ""),
        )

        result = backup_repo(tmp_path)
        assert result["status"] == "skipped"
        assert result.get("reason") == "no_changes"

        # Проверяем — не было push.
        cmds = [c["cmd"] for c in fake_runner["calls"]]
        assert not any("push" in c for c in cmds)

    def test_pushed_when_tree_changed(self, tmp_path, fake_runner):
        """Если tree отличается → commit-tree + update-ref + push."""
        from atlas.backup import backup_repo

        (tmp_path / ".git").mkdir()
        new_tree = "newtreeXYZ"
        prev_tree = "oldtreeABC"
        prev_commit = "prevcommit12345"
        head_commit = "headcommit67890"
        new_commit = "freshcommit_new"

        fake_runner["by_cmd"](
            ("git", "remote", "get-url", "origin"),
            (0, "git@example.com:repo.git", ""),
        )
        fake_runner["by_cmd"](("git", "add", "-A"), (0, "", ""))
        fake_runner["by_cmd"](("git", "write-tree"), (0, new_tree, ""))
        fake_runner["by_cmd"](
            ("git", "rev-parse", "--verify", "--quiet", "refs/heads/backup^{tree}"),
            (0, prev_tree, ""),
        )
        fake_runner["by_cmd"](
            ("git", "rev-parse", "--verify", "--quiet", "refs/heads/backup"),
            (0, prev_commit, ""),
        )
        fake_runner["by_cmd"](
            ("git", "rev-parse", "--verify", "--quiet", "HEAD"),
            (0, head_commit, ""),
        )
        fake_runner["by_cmd"](("git", "commit-tree"), (0, new_commit, ""))
        fake_runner["by_cmd"](("git", "update-ref", "refs/heads/backup"), (0, "", ""))
        fake_runner["by_cmd"](
            ("git", "push", "origin", "backup"),
            (0, "", "Done"),
        )

        result = backup_repo(tmp_path)
        assert result["status"] == "pushed"
        assert result.get("commit_sha") == new_commit

    def test_pushed_with_only_head_parent_when_no_prev_backup(
        self, tmp_path, fake_runner
    ):
        """Первый backup: prev backup отсутствует → parent = HEAD only."""
        from atlas.backup import backup_repo

        (tmp_path / ".git").mkdir()
        new_tree = "tree1"
        head_commit = "headFFF"
        new_commit = "commit1"

        fake_runner["by_cmd"](
            ("git", "remote", "get-url", "origin"),
            (0, "git@example.com:repo.git", ""),
        )
        fake_runner["by_cmd"](("git", "add", "-A"), (0, "", ""))
        fake_runner["by_cmd"](("git", "write-tree"), (0, new_tree, ""))
        # Нет refs/heads/backup^{tree}
        fake_runner["by_cmd"](
            ("git", "rev-parse", "--verify", "--quiet", "refs/heads/backup^{tree}"),
            (1, "", ""),
        )
        # Нет refs/heads/backup
        fake_runner["by_cmd"](
            ("git", "rev-parse", "--verify", "--quiet", "refs/heads/backup"),
            (1, "", ""),
        )
        fake_runner["by_cmd"](
            ("git", "rev-parse", "--verify", "--quiet", "HEAD"),
            (0, head_commit, ""),
        )
        fake_runner["by_cmd"](("git", "commit-tree"), (0, new_commit, ""))
        fake_runner["by_cmd"](("git", "update-ref", "refs/heads/backup"), (0, "", ""))
        fake_runner["by_cmd"](
            ("git", "push", "origin", "backup"),
            (0, "", ""),
        )

        result = backup_repo(tmp_path)
        assert result["status"] == "pushed"
        assert result.get("commit_sha") == new_commit

    def test_failed_on_write_tree_error(self, tmp_path, fake_runner):
        """Если git write-tree падает → status='failed'."""
        from atlas.backup import backup_repo

        (tmp_path / ".git").mkdir()
        fake_runner["by_cmd"](
            ("git", "remote", "get-url", "origin"),
            (0, "git@example.com:repo.git", ""),
        )
        fake_runner["by_cmd"](("git", "add", "-A"), (0, "", ""))
        fake_runner["by_cmd"](("git", "write-tree"), (1, "", "fatal"))

        result = backup_repo(tmp_path)
        assert result["status"] == "failed"
        assert "write-tree" in result.get("error", "").lower() or "fatal" in result.get("error", "").lower()

    def test_failed_on_push_error(self, tmp_path, fake_runner):
        """Если git push падает → status='failed' и error содержит stderr."""
        from atlas.backup import backup_repo

        (tmp_path / ".git").mkdir()
        new_tree = "tree2"
        head_commit = "head2"
        new_commit = "commit2"

        fake_runner["by_cmd"](
            ("git", "remote", "get-url", "origin"),
            (0, "git@example.com:repo.git", ""),
        )
        fake_runner["by_cmd"](("git", "add", "-A"), (0, "", ""))
        fake_runner["by_cmd"](("git", "write-tree"), (0, new_tree, ""))
        fake_runner["by_cmd"](
            ("git", "rev-parse", "--verify", "--quiet", "refs/heads/backup^{tree}"),
            (1, "", ""),
        )
        fake_runner["by_cmd"](
            ("git", "rev-parse", "--verify", "--quiet", "refs/heads/backup"),
            (1, "", ""),
        )
        fake_runner["by_cmd"](
            ("git", "rev-parse", "--verify", "--quiet", "HEAD"),
            (0, head_commit, ""),
        )
        fake_runner["by_cmd"](("git", "commit-tree"), (0, new_commit, ""))
        fake_runner["by_cmd"](("git", "update-ref", "refs/heads/backup"), (0, "", ""))
        fake_runner["by_cmd"](
            ("git", "push", "origin", "backup"),
            (1, "", "Permission denied"),
        )

        result = backup_repo(tmp_path)
        assert result["status"] == "failed"
        assert "permission" in result.get("error", "").lower() or "push" in result.get("error", "").lower()

    def test_uses_temp_index_for_isolation(self, tmp_path, fake_runner):
        """git add -A и git write-tree должны вызываться с GIT_INDEX_FILE в env."""
        from atlas.backup import backup_repo

        (tmp_path / ".git").mkdir()
        # настроим успешный no-changes сценарий.
        fake_runner["by_cmd"](
            ("git", "remote", "get-url", "origin"),
            (0, "git@example.com:repo.git", ""),
        )
        same_tree = "tree_same"
        fake_runner["by_cmd"](("git", "add", "-A"), (0, "", ""))
        fake_runner["by_cmd"](("git", "write-tree"), (0, same_tree, ""))
        fake_runner["by_cmd"](
            ("git", "rev-parse", "--verify", "--quiet", "refs/heads/backup^{tree}"),
            (0, same_tree, ""),
        )

        backup_repo(tmp_path)

        # Найти все calls для add/write-tree — должен быть GIT_INDEX_FILE env.
        for call in fake_runner["calls"]:
            cmd = call["cmd"]
            if cmd[:3] == ["git", "add", "-A"] or cmd[:2] == ["git", "write-tree"]:
                env = call.get("env")
                assert env is not None
                assert "GIT_INDEX_FILE" in env

    def test_temp_index_cleaned_up(self, tmp_path, fake_runner):
        """После backup_repo TEMP_INDEX-файл должен быть удалён."""
        from atlas.backup import backup_repo

        (tmp_path / ".git").mkdir()
        fake_runner["by_cmd"](
            ("git", "remote", "get-url", "origin"),
            (0, "git@example.com:repo.git", ""),
        )
        fake_runner["by_cmd"](("git", "add", "-A"), (0, "", ""))
        fake_runner["by_cmd"](("git", "write-tree"), (0, "tree1", ""))
        fake_runner["by_cmd"](
            ("git", "rev-parse", "--verify", "--quiet", "refs/heads/backup^{tree}"),
            (0, "tree1", ""),
        )

        result = backup_repo(tmp_path)
        # достанем TEMP index из envs всех calls.
        temp_paths = set()
        for call in fake_runner["calls"]:
            env = call.get("env") or {}
            if "GIT_INDEX_FILE" in env:
                temp_paths.add(env["GIT_INDEX_FILE"])

        # Файл TEMP_INDEX не должен существовать после возврата.
        for p in temp_paths:
            assert not Path(p).exists(), f"TEMP_INDEX не убран: {p}"

        assert result["status"] == "skipped"
