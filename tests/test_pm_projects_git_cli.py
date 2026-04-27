"""Тесты для CLI `atlas projects git ...`.

Группа команд под sub-typer'ом git_app:
- `init`        — создать локальный repo + remote в GitLab + push.
- `status`      — показать состояние remote/local репо.
- `push`        — `git push origin <branch>` + update last_pushed_at.
- `link`        — привязать существующий remote (без create_remote / push).
- `move`        — `glab repo transfer` + обновить URL в локальном remote и БД.
- `status-all`  — массовый обзор по фильтрам.
- `sync-from-remote` — найти расхождения между БД и реальными URL в GitLab.

ВАЖНО: subprocess (`glab`/`git`) ВСЕГДА мокается — никаких реальных вызовов.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from typer.testing import CliRunner


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "PROJECT"
    root.mkdir()
    (root / "Clients").mkdir()
    (root / "Products").mkdir()
    (root / "Tests").mkdir()
    (root / "_Inbox").mkdir()
    (root / "_Archive").mkdir()
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture()
def fresh_engine(tmp_path, monkeypatch):
    """Чистая SQLite БД на диске + ATLAS_DB_URL в env."""
    from atlas.pm.db import make_engine
    from atlas.pm.models import Base

    db_path = tmp_path / "atlas.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def seeded_engine(fresh_engine):
    """Чистая БД + полный seed."""
    from atlas.pm.db import make_session
    from atlas.pm.seeds import seed_all

    with make_session(fresh_engine) as session:
        seed_all(session)
    return fresh_engine


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def app():
    """Sub-app `atlas projects` (включая зарегистрированный git_app)."""
    from atlas.pm.commands.projects import projects_app
    return projects_app


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _combined(result) -> str:
    out = result.stdout or ""
    try:
        err = result.stderr or ""
    except (ValueError, AttributeError):
        err = ""
    return out + err


def _make_local_path(projects_root: Path, slug: str, group: str = "Clients") -> Path:
    p = projects_root / group / slug
    p.mkdir(parents=True, exist_ok=True)
    (p / "README.md").write_text(f"project {slug}\n", encoding="utf-8")
    return p


def _add_project(
    runner,
    app,
    *,
    name: str,
    slug: str,
    type_slug: str = "client-project",
    local_path: Path | None = None,
    extra_args: list[str] | None = None,
):
    args = ["add", "--name", name, "--slug", slug, "--type", type_slug]
    if local_path is not None:
        args.extend(["--local-path", str(local_path)])
    if extra_args:
        args.extend(extra_args)
    result = runner.invoke(app, args)
    assert result.exit_code == 0, _combined(result)


def _get_project(engine, slug):
    from atlas.pm.db import make_session
    from atlas.pm.models import Project

    with make_session(engine) as session:
        return session.execute(
            select(Project).where(Project.slug == slug)
        ).scalar_one()


def _set_git_remote_url(engine, slug: str, url: str) -> None:
    """Установить git_remote_url напрямую в БД (CLI `add` не принимает этот флаг).

    Эмулирует уже инициализированный через `atlas projects git init` проект.
    """
    from atlas.pm.db import make_session
    from atlas.pm.models import Project

    with make_session(engine) as session:
        proj = session.execute(
            select(Project).where(Project.slug == slug)
        ).scalar_one()
        proj.git_remote_url = url
        session.commit()


@pytest.fixture()
def fake_run(monkeypatch):
    """Перехватываем `atlas.pm.git_backend.run` — все subprocess через него."""
    from atlas.pm import git_backend

    calls: list[dict[str, Any]] = []
    queue: list[tuple[int, str, str]] = []

    def _enqueue(*items: tuple[int, str, str]) -> None:
        queue.extend(items)

    def fake(cmd, cwd=None):
        calls.append({"cmd": list(cmd), "cwd": str(cwd) if cwd is not None else None})
        if queue:
            return queue.pop(0)
        return (0, "", "")

    monkeypatch.setattr(git_backend, "run", fake)
    return {"calls": calls, "set": _enqueue}


# --------------------------------------------------------------------------- #
# `atlas projects git` group is registered                                    #
# --------------------------------------------------------------------------- #


class TestGroupRegistration:
    def test_git_subapp_help_listed(self, runner, app):
        result = runner.invoke(app, ["git", "--help"])
        assert result.exit_code == 0, _combined(result)
        out = _combined(result)
        # Все ключевые команды должны фигурировать в help.
        for cmd in ("init", "status", "push", "link", "move", "status-all", "sync-from-remote"):
            assert cmd in out, f"command '{cmd}' missing in `atlas projects git --help`"


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #


class TestGitInit:
    def test_init_creates_remote_and_updates_db(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local = _make_local_path(projects_root, "cifro")
        _add_project(
            runner, app,
            name="Cifro Portal", slug="cifro", type_slug="client-project",
            local_path=local,
        )

        # Поток вызовов:
        # LocalGitOps.init: ["git", "init"]
        # LocalGitOps.add_all_commit: add(0,"",""); commit(0,"",""); rev-parse(0,"sha\n","")
        # LocalGitOps.set_default_branch: symbolic-ref
        # GitLabBackend.create_remote: glab repo create -> URL on stdout
        # LocalGitOps.add_remote: git remote add origin URL
        # LocalGitOps.push: git push -u origin main
        fake_run["set"](
            (0, "", ""),                                              # git init
            (0, "", ""),                                              # git add -A
            (0, "", ""),                                              # git commit
            (0, "abc123\n", ""),                                      # git rev-parse HEAD
            (0, "", ""),                                              # git symbolic-ref
            (0, "https://gitlab.com/cifropro1/clients/cifro\n", ""),  # glab repo create
            (0, "", ""),                                              # git remote add
            (0, "", ""),                                              # git push -u
        )

        result = runner.invoke(app, ["git", "init", "cifro"])
        assert result.exit_code == 0, _combined(result)

        # Проверяем БД
        proj = _get_project(seeded_engine, "cifro")
        assert proj.git_remote_url == "https://gitlab.com/cifropro1/clients/cifro"
        assert proj.git_default_branch == "main"
        assert proj.git_provider == "gitlab"
        assert proj.git_initialized_at is not None
        assert proj.git_last_pushed_at is not None

        # Проверяем что glab вызывался хоть раз
        cmds = [c["cmd"] for c in fake_run["calls"]]
        assert any(c[0] == "glab" for c in cmds)
        assert any(c[:2] == ["git", "push"] for c in cmds)

    def test_init_uses_personal_namespace_for_owner_dmitry(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local = _make_local_path(projects_root, "myutil", group="Products")
        _add_project(
            runner, app,
            name="My utility", slug="myutil", type_slug="personal-utility",
            local_path=local,
            extra_args=["--tag", "owner:dmitry"],
        )

        fake_run["set"](
            (0, "", ""), (0, "", ""), (0, "", ""), (0, "abc\n", ""), (0, "", ""),
            (0, "https://gitlab.com/zzztejletty3ukzzz/products/myutil\n", ""),
            (0, "", ""), (0, "", ""),
        )
        result = runner.invoke(app, ["git", "init", "myutil"])
        assert result.exit_code == 0, _combined(result)

        # Должен быть вызов glab с zzztejletty3ukzzz/products/...
        flat = [" ".join(c["cmd"]) for c in fake_run["calls"]]
        assert any(
            "glab" in s and "zzztejletty3ukzzz/products" in s for s in flat
        ), f"expected personal namespace, got: {flat}"

    def test_init_aborts_when_local_path_missing(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        # Создаём проект БЕЗ local_path (не передаём флаг — None в БД).
        _add_project(
            runner, app,
            name="No path", slug="nopath", type_slug="business-product",
        )
        result = runner.invoke(app, ["git", "init", "nopath"])
        assert result.exit_code != 0
        assert "local_path" in _combined(result).lower()

    def test_init_aborts_when_already_has_remote(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local = _make_local_path(projects_root, "already")
        _add_project(
            runner, app,
            name="Already", slug="already", type_slug="client-project",
            local_path=local,
        )
        _set_git_remote_url(
            seeded_engine, "already",
            "https://gitlab.com/cifropro1/clients/already",
        )

        result = runner.invoke(app, ["git", "init", "already"])
        assert result.exit_code != 0
        out = _combined(result).lower()
        assert "remote" in out or "уже" in out

    def test_init_with_explicit_group(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local = _make_local_path(projects_root, "explicit")
        _add_project(
            runner, app,
            name="E", slug="explicit", type_slug="client-project",
            local_path=local,
        )
        fake_run["set"](
            (0, "", ""), (0, "", ""), (0, "", ""), (0, "abc\n", ""), (0, "", ""),
            (0, "https://gitlab.com/foo/bar/explicit\n", ""),
            (0, "", ""), (0, "", ""),
        )
        result = runner.invoke(
            app, ["git", "init", "explicit", "--group", "foo/bar"]
        )
        assert result.exit_code == 0, _combined(result)
        flat = [" ".join(c["cmd"]) for c in fake_run["calls"]]
        assert any("foo/bar/explicit" in s for s in flat)


# --------------------------------------------------------------------------- #
# status                                                                      #
# --------------------------------------------------------------------------- #


class TestGitStatus:
    def test_status_prints_remote_and_local_info(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local = _make_local_path(projects_root, "stat")
        # init the .git directory marker so LocalGitOps.status will be called
        (local / ".git").mkdir()
        _add_project(
            runner, app,
            name="St", slug="stat", type_slug="client-project",
            local_path=local,
        )
        _set_git_remote_url(
            seeded_engine, "stat",
            "https://gitlab.com/cifropro1/clients/stat",
        )
        # status() = git status + rev-parse
        porcelain = (
            "# branch.oid sha\n"
            "# branch.head main\n"
            "# branch.upstream origin/main\n"
            "# branch.ab +0 -0\n"
        )
        fake_run["set"]((0, porcelain, ""), (0, "abcdef\n", ""))

        result = runner.invoke(app, ["git", "status", "stat"])
        assert result.exit_code == 0, _combined(result)
        out = _combined(result)
        assert "https://gitlab.com/cifropro1/clients/stat" in out
        assert "main" in out

    def test_status_no_git_dir_warns(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local = _make_local_path(projects_root, "nogit")
        # без .git/ — local section должен сказать "не инициализирован"
        _add_project(
            runner, app,
            name="N", slug="nogit", type_slug="client-project",
            local_path=local,
        )
        _set_git_remote_url(
            seeded_engine, "nogit",
            "https://gitlab.com/cifropro1/clients/nogit",
        )
        result = runner.invoke(app, ["git", "status", "nogit"])
        assert result.exit_code == 0, _combined(result)
        out = _combined(result).lower()
        assert "не инициализиров" in out or "no .git" in out


# --------------------------------------------------------------------------- #
# push                                                                        #
# --------------------------------------------------------------------------- #


class TestGitPush:
    def test_push_calls_git_push_and_updates_timestamp(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local = _make_local_path(projects_root, "push1")
        (local / ".git").mkdir()
        _add_project(
            runner, app,
            name="P", slug="push1", type_slug="client-project",
            local_path=local,
        )
        _set_git_remote_url(
            seeded_engine, "push1",
            "https://gitlab.com/cifropro1/clients/push1",
        )
        before = _get_project(seeded_engine, "push1")
        assert before.git_last_pushed_at is None

        fake_run["set"]((0, "", ""))
        result = runner.invoke(app, ["git", "push", "push1"])
        assert result.exit_code == 0, _combined(result)

        cmds = [c["cmd"] for c in fake_run["calls"]]
        assert any(c[:2] == ["git", "push"] for c in cmds)

        after = _get_project(seeded_engine, "push1")
        assert after.git_last_pushed_at is not None

    def test_push_aborts_when_no_local_path(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        _add_project(
            runner, app,
            name="X", slug="nopush", type_slug="client-project",
        )
        _set_git_remote_url(
            seeded_engine, "nopush",
            "https://gitlab.com/cifropro1/clients/nopush",
        )
        result = runner.invoke(app, ["git", "push", "nopush"])
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# link                                                                        #
# --------------------------------------------------------------------------- #


class TestGitLink:
    def test_link_attaches_existing_remote(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local = _make_local_path(projects_root, "linkme")
        (local / ".git").mkdir()
        _add_project(
            runner, app,
            name="L", slug="linkme", type_slug="client-project",
            local_path=local,
        )
        url = "https://gitlab.com/cifropro1/clients/linkme"
        fake_run["set"]((0, "", ""))  # git remote add
        result = runner.invoke(
            app,
            ["git", "link", "linkme", "--url", url, "--branch", "main"],
        )
        assert result.exit_code == 0, _combined(result)

        cmds = [c["cmd"] for c in fake_run["calls"]]
        assert any(
            c[:3] == ["git", "remote", "add"] and url in c for c in cmds
        )

        proj = _get_project(seeded_engine, "linkme")
        assert proj.git_remote_url == url
        assert proj.git_default_branch == "main"
        assert proj.git_provider == "gitlab"
        assert proj.git_initialized_at is not None
        assert proj.git_last_pushed_at is None

    def test_link_aborts_on_invalid_url(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local = _make_local_path(projects_root, "badurl")
        (local / ".git").mkdir()
        _add_project(
            runner, app,
            name="B", slug="badurl", type_slug="client-project",
            local_path=local,
        )
        result = runner.invoke(
            app,
            ["git", "link", "badurl", "--url", "not-a-url"],
        )
        assert result.exit_code != 0
        assert "url" in _combined(result).lower()

    def test_link_aborts_when_no_dot_git(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local = _make_local_path(projects_root, "nogitdir")
        # без .git
        _add_project(
            runner, app,
            name="N", slug="nogitdir", type_slug="client-project",
            local_path=local,
        )
        result = runner.invoke(
            app,
            ["git", "link", "nogitdir", "--url",
             "https://gitlab.com/foo/bar/nogitdir"],
        )
        assert result.exit_code != 0
        out = _combined(result).lower()
        assert ".git" in out or "init" in out


# --------------------------------------------------------------------------- #
# move                                                                        #
# --------------------------------------------------------------------------- #


class TestGitMove:
    def test_move_invokes_glab_transfer_and_updates_local_remote(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local = _make_local_path(projects_root, "mv1")
        (local / ".git").mkdir()
        _add_project(
            runner, app,
            name="M", slug="mv1", type_slug="client-project",
            local_path=local,
        )
        _set_git_remote_url(
            seeded_engine, "mv1",
            "https://gitlab.com/cifropro1/clients/mv1",
        )
        # glab transfer → URL; git remote set-url → ok
        fake_run["set"](
            (0, "https://gitlab.com/cifropro1/archive/clients/mv1\n", ""),  # glab
            (0, "", ""),  # git remote set-url
        )
        result = runner.invoke(
            app,
            [
                "git", "move", "mv1",
                "--to-group", "cifropro1/archive/clients",
            ],
        )
        assert result.exit_code == 0, _combined(result)

        cmds = [c["cmd"] for c in fake_run["calls"]]
        assert any(c[0] == "glab" and "transfer" in c for c in cmds)
        # Локальный remote обновлён.
        assert any(
            c[:3] == ["git", "remote", "set-url"] for c in cmds
        )

        proj = _get_project(seeded_engine, "mv1")
        assert "archive/clients/mv1" in proj.git_remote_url

    def test_move_aborts_when_no_remote(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local = _make_local_path(projects_root, "noremote")
        _add_project(
            runner, app,
            name="N", slug="noremote", type_slug="client-project",
            local_path=local,
        )
        result = runner.invoke(
            app, ["git", "move", "noremote", "--to-group", "foo/bar"],
        )
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# status-all                                                                  #
# --------------------------------------------------------------------------- #


class TestGitStatusAll:
    def test_status_all_lists_all_projects(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local1 = _make_local_path(projects_root, "p1")
        _add_project(
            runner, app,
            name="P1", slug="p1", type_slug="client-project",
            local_path=local1,
        )
        _set_git_remote_url(
            seeded_engine, "p1",
            "https://gitlab.com/cifropro1/clients/p1",
        )
        local2 = _make_local_path(projects_root, "p2", group="Products")
        _add_project(
            runner, app,
            name="P2", slug="p2", type_slug="business-product",
            local_path=local2,
        )

        result = runner.invoke(app, ["git", "status-all"])
        assert result.exit_code == 0, _combined(result)
        out = _combined(result)
        assert "p1" in out and "p2" in out

    def test_status_all_filters_by_type(
        self, runner, app, seeded_engine, projects_root, fake_run
    ):
        local1 = _make_local_path(projects_root, "c1")
        _add_project(
            runner, app,
            name="C1", slug="c1", type_slug="client-project",
            local_path=local1,
        )
        _set_git_remote_url(
            seeded_engine, "c1",
            "https://gitlab.com/cifropro1/clients/c1",
        )
        local2 = _make_local_path(projects_root, "b1", group="Products")
        _add_project(
            runner, app,
            name="B1", slug="b1", type_slug="business-product",
            local_path=local2,
        )
        result = runner.invoke(
            app, ["git", "status-all", "--type", "client-project"],
        )
        assert result.exit_code == 0, _combined(result)
        out = _combined(result)
        assert "c1" in out
        assert "b1" not in out


# --------------------------------------------------------------------------- #
# sync-from-remote                                                            #
# --------------------------------------------------------------------------- #


class TestGitSyncFromRemote:
    def test_sync_dry_run_does_not_modify_db(
        self, runner, app, seeded_engine, projects_root, fake_run, monkeypatch
    ):
        local = _make_local_path(projects_root, "sync1")
        _add_project(
            runner, app,
            name="S1", slug="sync1", type_slug="client-project",
            local_path=local,
        )
        _set_git_remote_url(
            seeded_engine, "sync1",
            "https://gitlab.com/cifropro1/clients/sync1",
        )
        # Заглушим backend.get_remote_status → URL не изменился.
        from atlas.pm import git_backend

        def fake_status(self, repo_full_path):
            return {
                "web_url": f"https://gitlab.com/{repo_full_path}",
                "default_branch": "main",
                "visibility": "private",
            }

        monkeypatch.setattr(
            git_backend.GitLabBackend, "get_remote_status", fake_status
        )

        result = runner.invoke(app, ["git", "sync-from-remote", "--dry-run"])
        assert result.exit_code == 0, _combined(result)

        # БД не изменена
        proj = _get_project(seeded_engine, "sync1")
        assert proj.git_remote_url == "https://gitlab.com/cifropro1/clients/sync1"

    def test_sync_updates_url_when_remote_changed(
        self, runner, app, seeded_engine, projects_root, fake_run, monkeypatch
    ):
        local = _make_local_path(projects_root, "moved")
        _add_project(
            runner, app,
            name="Moved", slug="moved", type_slug="client-project",
            local_path=local,
        )
        _set_git_remote_url(
            seeded_engine, "moved",
            "https://gitlab.com/cifropro1/clients/moved",
        )
        from atlas.pm import git_backend

        def fake_status(self, repo_full_path):
            # сервер возвращает URL из новой группы.
            return {
                "web_url": "https://gitlab.com/cifropro1/archive/clients/moved",
                "default_branch": "main",
                "visibility": "private",
            }

        monkeypatch.setattr(
            git_backend.GitLabBackend, "get_remote_status", fake_status
        )

        result = runner.invoke(app, ["git", "sync-from-remote"])
        assert result.exit_code == 0, _combined(result)

        proj = _get_project(seeded_engine, "moved")
        assert "archive/clients/moved" in proj.git_remote_url
