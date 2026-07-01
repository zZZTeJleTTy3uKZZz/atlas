"""Тесты для atlas.git_backend.

Покрывает:
- run() helper — единая обёртка subprocess.run.
- LocalGitOps — тонкая обёртка вокруг git CLI (init/commit/remote/push/status).
- GitLabBackend — обёртка вокруг `glab` CLI: create_remote, transfer_to_group,
  get_remote_status.
- GitBackend Protocol — статическая совместимость GitLabBackend.

ВАЖНО: ВСЕ subprocess вызовы мокаются. Никаких реальных git/glab команд
не выполняется. Реальный fs касаемся только в `tmp_path`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# run() helper
# ---------------------------------------------------------------------------


class TestRun:
    def test_run_returns_returncode_stdout_stderr_tuple(self, monkeypatch):
        from atlas import git_backend

        captured: dict[str, Any] = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="hello",
                stderr="",
            )

        monkeypatch.setattr(git_backend.subprocess, "run", fake_run)
        rc, out, err = git_backend.run(["git", "status"], cwd="/tmp/x")
        assert rc == 0
        assert out == "hello"
        assert err == ""
        assert captured["cmd"] == ["git", "status"]
        assert captured["kwargs"]["cwd"] == "/tmp/x"
        # Должен использовать text mode + capture_output.
        assert captured["kwargs"]["text"] is True

    def test_run_propagates_nonzero_returncode_without_raising(self, monkeypatch):
        from atlas import git_backend

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=128, stdout="", stderr="fatal"
            )

        monkeypatch.setattr(git_backend.subprocess, "run", fake_run)
        rc, out, err = git_backend.run(["git", "fail"])
        assert rc == 128
        assert err == "fatal"


# ---------------------------------------------------------------------------
# LocalGitOps
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_runner(monkeypatch):
    """Перехватываем git_backend.run, возвращаем подменяемые ответы."""
    from atlas import git_backend

    calls: list[dict[str, Any]] = []
    queue: list[tuple[int, str, str]] = []

    def _set_responses(*items: tuple[int, str, str]) -> None:
        queue.extend(items)

    def fake_run(cmd, cwd=None):
        # Сохраняем cwd как str (имитируем реальный run()), чтобы тесты
        # сравнивали со str(tmp_path).
        cwd_str = str(cwd) if cwd is not None else None
        calls.append({"cmd": list(cmd), "cwd": cwd_str})
        if queue:
            return queue.pop(0)
        return (0, "", "")

    monkeypatch.setattr(git_backend, "run", fake_run)
    return {"calls": calls, "set": _set_responses}


class TestLocalGitOps:
    def test_init_runs_git_init(self, tmp_path, fake_runner):
        from atlas.git_backend import LocalGitOps

        ops = LocalGitOps()
        ops.init(tmp_path)

        assert any(c["cmd"][:2] == ["git", "init"] for c in fake_runner["calls"])
        # cwd должен быть tmp_path (как str).
        cmd_call = next(c for c in fake_runner["calls"] if c["cmd"][:2] == ["git", "init"])
        assert cmd_call["cwd"] == str(tmp_path)

    def test_add_all_commit_returns_sha(self, tmp_path, fake_runner):
        from atlas.git_backend import LocalGitOps

        # Программа: git add -A → ok; git commit → ok; git rev-parse HEAD → sha.
        fake_runner["set"](
            (0, "", ""),  # add
            (0, "", ""),  # commit
            (0, "abc1234deadbeef\n", ""),  # rev-parse
        )

        ops = LocalGitOps()
        sha = ops.add_all_commit(tmp_path, "feat: initial")

        assert sha == "abc1234deadbeef"
        cmds = [c["cmd"] for c in fake_runner["calls"]]
        assert ["git", "add", "-A"] in cmds
        # commit с -m
        assert any(
            c[:3] == ["git", "commit", "-m"] and "feat: initial" in c
            for c in cmds
        )
        assert any(
            c[:3] == ["git", "rev-parse", "HEAD"] for c in cmds
        )

    def test_add_remote_invokes_git_remote_add(self, tmp_path, fake_runner):
        from atlas.git_backend import LocalGitOps

        ops = LocalGitOps()
        ops.add_remote(tmp_path, "origin", "git@gitlab.com:example-org/clients/cifro.git")

        cmds = [c["cmd"] for c in fake_runner["calls"]]
        assert any(
            c[:3] == ["git", "remote", "add"]
            and "origin" in c
            and "git@gitlab.com:example-org/clients/cifro.git" in c
            for c in cmds
        )

    def test_set_default_branch_uses_symbolic_ref(self, tmp_path, fake_runner):
        from atlas.git_backend import LocalGitOps

        ops = LocalGitOps()
        ops.set_default_branch(tmp_path, "main")

        # должен либо init.defaultBranch / либо checkout -B / либо
        # symbolic-ref HEAD refs/heads/main. Конкретику не пинаем, проверим
        # хотя бы, что было вызвано что-то с 'main' в команде.
        flat = [" ".join(c["cmd"]) for c in fake_runner["calls"]]
        assert any("main" in s for s in flat)

    def test_push_default_origin_main(self, tmp_path, fake_runner):
        from atlas.git_backend import LocalGitOps

        ops = LocalGitOps()
        ops.push(tmp_path)

        cmds = [c["cmd"] for c in fake_runner["calls"]]
        # Ожидаем git push -u origin main (default).
        assert any(
            c[0:2] == ["git", "push"] and "origin" in c and "main" in c
            for c in cmds
        )

    def test_push_with_custom_branch(self, tmp_path, fake_runner):
        from atlas.git_backend import LocalGitOps

        ops = LocalGitOps()
        ops.push(tmp_path, branch="develop")

        cmds = [c["cmd"] for c in fake_runner["calls"]]
        assert any(
            c[0:2] == ["git", "push"] and "develop" in c for c in cmds
        )

    def test_status_parses_branch_and_dirty(self, tmp_path, fake_runner):
        """status() парсит вывод git status --branch --porcelain=v2 +
        git rev-parse HEAD для last_sha."""
        from atlas.git_backend import LocalGitOps

        # branch.head main
        # branch.upstream origin/main
        # branch.ab +2 -1
        porcelain = (
            "# branch.oid abc123def\n"
            "# branch.head main\n"
            "# branch.upstream origin/main\n"
            "# branch.ab +2 -1\n"
            "1 .M N... 100644 100644 100644 sha sha file.txt\n"
        )
        fake_runner["set"](
            (0, porcelain, ""),
            (0, "abc123def\n", ""),  # rev-parse HEAD
        )

        ops = LocalGitOps()
        result = ops.status(tmp_path)

        assert result["branch"] == "main"
        assert result["ahead"] == 2
        assert result["behind"] == 1
        assert result["dirty"] is True
        assert result["last_sha"] == "abc123def"

    def test_status_clean_repo(self, tmp_path, fake_runner):
        from atlas.git_backend import LocalGitOps

        porcelain = (
            "# branch.oid sha1\n"
            "# branch.head main\n"
            "# branch.upstream origin/main\n"
            "# branch.ab +0 -0\n"
        )
        fake_runner["set"](
            (0, porcelain, ""),
            (0, "sha1\n", ""),
        )
        ops = LocalGitOps()
        result = ops.status(tmp_path)
        assert result["dirty"] is False
        assert result["ahead"] == 0
        assert result["behind"] == 0


# ---------------------------------------------------------------------------
# GitLabBackend
# ---------------------------------------------------------------------------


class TestGitLabBackend:
    def test_create_remote_invokes_glab_api_post_projects(self, fake_runner):
        """W45-32a: create_remote использует `glab api groups/...` +
        `glab api -X POST projects` (не `glab repo create` с full path)."""
        from atlas.git_backend import GitLabBackend

        # 1) glab api groups/example-org%2Fclients → JSON с id.
        # 2) glab api -X POST projects → JSON с http_url_to_repo.
        fake_runner["set"](
            (0, '{"id": 12345, "full_path": "example-org/clients"}', ""),
            (
                0,
                '{"id": 67890, "http_url_to_repo": "https://gitlab.com/example-org/clients/cifro.git"}',
                "",
            ),
        )

        backend = GitLabBackend()
        url = backend.create_remote("example-org/clients", "cifro", private=True)

        assert "example-org/clients/cifro" in url
        assert url.endswith(".git")
        cmds = [c["cmd"] for c in fake_runner["calls"]]
        # должен быть glab api groups/...
        assert any(
            c[0] == "glab" and c[1] == "api" and c[2].startswith("groups/")
            for c in cmds
        )
        # и POST projects
        assert any(
            c[0] == "glab" and "POST" in c and "projects" in c for c in cmds
        )

    def test_create_remote_passes_private_flag(self, fake_runner):
        from atlas.git_backend import GitLabBackend

        fake_runner["set"](
            (0, '{"id": 1}', ""),
            (
                0,
                '{"http_url_to_repo": "https://gitlab.com/example-org/clients/cifro.git"}',
                "",
            ),
        )
        backend = GitLabBackend()
        backend.create_remote("example-org/clients", "cifro", private=True)

        flat = [" ".join(c["cmd"]) for c in fake_runner["calls"]]
        assert any("visibility=private" in s for s in flat)

    def test_create_remote_passes_public_flag(self, fake_runner):
        from atlas.git_backend import GitLabBackend

        fake_runner["set"](
            (0, '{"id": 1}', ""),
            (
                0,
                '{"http_url_to_repo": "https://gitlab.com/example-org/clients/cifro.git"}',
                "",
            ),
        )
        backend = GitLabBackend()
        backend.create_remote("example-org/clients", "cifro", private=False)

        flat = [" ".join(c["cmd"]) for c in fake_runner["calls"]]
        assert any("visibility=public" in s for s in flat)

    def test_create_remote_raises_runtime_on_glab_failure(self, fake_runner):
        from atlas.git_backend import GitLabBackend

        # Первый вызов (groups) валится — это RuntimeError.
        fake_runner["set"]((1, "", "auth failed"))

        backend = GitLabBackend()
        with pytest.raises(RuntimeError, match="glab"):
            backend.create_remote("example-org/clients", "cifro")

    def test_transfer_to_group_uses_api_put(self, fake_runner):
        """W45-32j: transfer_to_group использует `glab api PUT
        /projects/<id>/transfer`, не `glab repo transfer --group`."""
        from atlas.git_backend import GitLabBackend

        # 1) glab api projects/<encoded> → JSON с id проекта.
        # 2) glab api groups/<encoded> → JSON с namespace id.
        # 3) glab api -X PUT projects/<id>/transfer → JSON с new URL.
        fake_runner["set"](
            (0, '{"id": 100, "path_with_namespace": "example-org/clients/cifro"}', ""),
            (0, '{"id": 200, "full_path": "example-org/archive/clients"}', ""),
            (
                0,
                '{"http_url_to_repo": "https://gitlab.com/example-org/archive/clients/cifro.git"}',
                "",
            ),
        )
        backend = GitLabBackend()
        new_url = backend.transfer_to_group(
            "example-org/clients/cifro", "example-org/archive/clients"
        )

        assert "archive/clients/cifro" in new_url
        cmds = [c["cmd"] for c in fake_runner["calls"]]
        # PUT /projects/100/transfer
        assert any(
            c[0] == "glab" and "PUT" in c and "/transfer" in " ".join(c)
            for c in cmds
        )

    def test_get_remote_status_returns_dict(self, fake_runner):
        from atlas.git_backend import GitLabBackend

        # `glab repo view example-org/clients/cifro -F json` → JSON.
        fake_runner["set"](
            (
                0,
                '{"web_url":"https://gitlab.com/example-org/clients/cifro","default_branch":"main","visibility":"private"}',
                "",
            ),
        )
        backend = GitLabBackend()
        info = backend.get_remote_status("example-org/clients/cifro")

        assert info["web_url"].endswith("cifro")
        assert info["default_branch"] == "main"
        assert info["visibility"] == "private"


# ---------------------------------------------------------------------------
# GitHubBackend (#301)
# ---------------------------------------------------------------------------


class TestGitHubBackend:
    def test_create_remote_invokes_gh_repo_create(self, fake_runner):
        """create_remote → `gh repo create <owner>/<repo> --private`."""
        from atlas.git_backend import GitHubBackend

        # gh печатает URL созданного репо в stdout.
        fake_runner["set"](
            (0, "✓ Created repository acme/cifro on GitHub\n"
                "https://github.com/acme/cifro\n", ""),
        )
        backend = GitHubBackend()
        url = backend.create_remote("acme", "cifro", private=True)

        assert url == "https://github.com/acme/cifro.git"
        cmds = [c["cmd"] for c in fake_runner["calls"]]
        assert any(
            c[:3] == ["gh", "repo", "create"] and "acme/cifro" in c
            and "--private" in c
            for c in cmds
        )

    def test_create_remote_constructs_url_when_stdout_silent(self, fake_runner):
        """Если gh не печатает URL — конструируем из owner/repo."""
        from atlas.git_backend import GitHubBackend

        fake_runner["set"]((0, "", ""))
        backend = GitHubBackend()
        url = backend.create_remote("acme", "cifro", private=False)
        assert url == "https://github.com/acme/cifro.git"

    def test_create_remote_passes_public_flag(self, fake_runner):
        from atlas.git_backend import GitHubBackend

        fake_runner["set"]((0, "https://github.com/acme/cifro\n", ""))
        backend = GitHubBackend()
        backend.create_remote("acme", "cifro", private=False)
        flat = [" ".join(c["cmd"]) for c in fake_runner["calls"]]
        assert any("--public" in s for s in flat)

    def test_create_remote_raises_on_gh_failure(self, fake_runner):
        from atlas.git_backend import GitHubBackend

        fake_runner["set"]((1, "", "HTTP 401: Bad credentials"))
        backend = GitHubBackend()
        with pytest.raises(RuntimeError, match="gh repo create"):
            backend.create_remote("acme", "cifro")

    def test_get_remote_status_normalizes_to_web_url(self, fake_runner):
        """gh repo view --json → dict с web_url (provider-agnostic ключ)."""
        from atlas.git_backend import GitHubBackend

        fake_runner["set"]((
            0,
            '{"name":"cifro","url":"https://github.com/acme/cifro",'
            '"defaultBranchRef":{"name":"main"},"visibility":"PRIVATE"}',
            "",
        ))
        backend = GitHubBackend()
        info = backend.get_remote_status("acme/cifro")
        assert info["web_url"] == "https://github.com/acme/cifro"
        assert info["default_branch"] == "main"
        assert info["visibility"] == "private"  # нормализован в lower

    def test_transfer_to_group_uses_gh_api_transfer(self, fake_runner):
        from atlas.git_backend import GitHubBackend

        fake_runner["set"]((0, '{"id": 1}', ""))
        backend = GitHubBackend()
        new_url = backend.transfer_to_group("acme/cifro", "newowner")
        assert new_url == "https://github.com/newowner/cifro.git"
        flat = [" ".join(c["cmd"]) for c in fake_runner["calls"]]
        assert any(
            "transfer" in s and "new_owner=newowner" in s for s in flat
        )


# ---------------------------------------------------------------------------
# Backend factory (#301)
# ---------------------------------------------------------------------------


class TestGetBackend:
    def test_gitlab(self):
        from atlas.git_backend import GitLabBackend, get_backend

        assert isinstance(get_backend("gitlab"), GitLabBackend)

    def test_github(self):
        from atlas.git_backend import GitHubBackend, get_backend

        assert isinstance(get_backend("github"), GitHubBackend)

    def test_default_empty_is_gitlab(self):
        from atlas.git_backend import GitLabBackend, get_backend

        assert isinstance(get_backend(""), GitLabBackend)

    def test_case_insensitive(self):
        from atlas.git_backend import GitHubBackend, get_backend

        assert isinstance(get_backend("GitHub"), GitHubBackend)

    def test_unknown_raises_valueerror(self):
        from atlas.git_backend import get_backend

        with pytest.raises(ValueError, match="bitbucket"):
            get_backend("bitbucket")


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_gitlab_backend_satisfies_git_backend_protocol():
    """GitLabBackend должен иметь все методы Protocol GitBackend."""
    from atlas.git_backend import GitBackend, GitLabBackend

    backend: GitBackend = GitLabBackend()  # type: ignore[assignment]
    assert hasattr(backend, "create_remote")
    assert hasattr(backend, "transfer_to_group")
    assert hasattr(backend, "get_remote_status")


def test_github_backend_satisfies_git_backend_protocol():
    """GitHubBackend должен иметь все методы Protocol GitBackend."""
    from atlas.git_backend import GitBackend, GitHubBackend

    backend: GitBackend = GitHubBackend()  # type: ignore[assignment]
    assert hasattr(backend, "create_remote")
    assert hasattr(backend, "transfer_to_group")
    assert hasattr(backend, "get_remote_status")
