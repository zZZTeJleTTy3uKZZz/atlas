"""Epic worktree-циклы (#300): логика epic_worktree + CLI `atlas epic worktree`.

ВСЕ git-вызовы мокаются (atlas.epic_worktree.run) — реальных git-команд нет;
fs трогаем только в tmp_path.
"""
from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

from atlas.cli import app
from atlas.db import make_engine, make_session
from atlas.models import Base, Epic, Project, ProjectStatus, ProjectType
from atlas.seeds import seed_all

runner = CliRunner()


# --------------------------------------------------------------------------- #
# Гибкий git-мок: динамика для rev-parse/status/show-ref, остальное настраиваемо
# --------------------------------------------------------------------------- #


@pytest.fixture()
def gitmock(monkeypatch):
    from atlas import epic_worktree as W

    state = {
        "branch": "main",            # current_branch
        "clean": True,               # is_clean
        "branches": set(),           # существующие ветки (show-ref)
        "worktree_porcelain": "",    # вывод `worktree list --porcelain`
        "fail": {},                  # sub → (rc, out, err) принудительная ошибка
    }
    calls: list[list[str]] = []

    def fake(cmd, cwd=None):
        calls.append(list(cmd))
        sub = cmd[1] if len(cmd) > 1 else ""
        if sub in state["fail"]:
            return state["fail"][sub]
        if sub == "rev-parse" and "--abbrev-ref" in cmd:
            return (0, state["branch"] + "\n", "")
        if sub == "status" and "--porcelain" in cmd:
            return (0, "" if state["clean"] else " M file.txt\n", "")
        if sub == "show-ref":
            br = cmd[-1].replace("refs/heads/", "")
            return (0, "", "") if br in state["branches"] else (1, "", "")
        if sub == "worktree" and len(cmd) > 2 and cmd[2] == "list":
            return (0, state["worktree_porcelain"], "")
        return (0, "", "")

    monkeypatch.setattr(W, "run", fake)
    return {"state": state, "calls": calls}


# --------------------------------------------------------------------------- #
# Логика (pure-ish over run)                                                   #
# --------------------------------------------------------------------------- #


def test_epic_branch_deterministic():
    from atlas.epic_worktree import epic_branch
    assert epic_branch("billing-v2") == "epic/billing-v2"


def test_default_worktree_path_is_sibling(tmp_path):
    from atlas.epic_worktree import default_worktree_path
    repo = tmp_path / "myrepo"
    p = default_worktree_path(repo, "foo")
    assert p.name == "epic-foo"
    assert p.parent.name == "myrepo.worktrees"
    assert p.parent.parent == tmp_path  # рядом с репо, не внутри


def test_list_worktrees_parses_porcelain(gitmock):
    from atlas.epic_worktree import list_worktrees
    gitmock["state"]["worktree_porcelain"] = (
        "worktree /repo\nHEAD abc123\nbranch refs/heads/main\n\n"
        "worktree /repo.worktrees/epic-foo\nHEAD def456\nbranch refs/heads/epic/foo\n\n"
    )
    wts = list_worktrees("/repo")
    assert len(wts) == 2
    assert wts[0]["branch"] == "main" and wts[0]["is_epic"] is False
    assert wts[1]["branch"] == "epic/foo" and wts[1]["is_epic"] is True


def test_add_worktree_new_branch_uses_dash_b(gitmock, tmp_path):
    from atlas.epic_worktree import add_worktree
    add_worktree(tmp_path / "repo", tmp_path / "wt", "epic/foo", "main")
    # ветки нет → должен быть `worktree add <path> -b epic/foo main`
    add_calls = [c for c in gitmock["calls"] if c[1:3] == ["worktree", "add"]]
    assert add_calls and "-b" in add_calls[0] and "epic/foo" in add_calls[0]


def test_add_worktree_existing_branch_reuses(gitmock, tmp_path):
    from atlas.epic_worktree import add_worktree
    gitmock["state"]["branches"].add("epic/foo")
    add_worktree(tmp_path / "repo", tmp_path / "wt", "epic/foo", "main")
    add_calls = [c for c in gitmock["calls"] if c[1:3] == ["worktree", "add"]]
    assert add_calls and "-b" not in add_calls[0]  # ветка есть → без -b


def test_merge_into_happy_path(gitmock):
    from atlas.epic_worktree import merge_into
    gitmock["state"]["branches"].add("epic/foo")
    gitmock["state"]["branch"] = "main"
    gitmock["state"]["clean"] = True
    res = merge_into("/repo", "epic/foo", "main", no_ff=True)
    assert res["merged"] is True
    merge_calls = [c for c in gitmock["calls"] if c[1] == "merge"]
    assert merge_calls and "--no-ff" in merge_calls[0]


def test_merge_into_wrong_branch_errors(gitmock):
    from atlas.epic_worktree import WorktreeError, merge_into
    gitmock["state"]["branches"].add("epic/foo")
    gitmock["state"]["branch"] = "dev"  # не main
    with pytest.raises(WorktreeError, match="нужно 'main'"):
        merge_into("/repo", "epic/foo", "main")


def test_merge_into_dirty_tree_errors(gitmock):
    from atlas.epic_worktree import WorktreeError, merge_into
    gitmock["state"]["branches"].add("epic/foo")
    gitmock["state"]["clean"] = False
    with pytest.raises(WorktreeError, match="грязн"):
        merge_into("/repo", "epic/foo", "main")


def test_merge_into_missing_branch_errors(gitmock):
    from atlas.epic_worktree import WorktreeError, merge_into
    with pytest.raises(WorktreeError, match="не найдена"):
        merge_into("/repo", "epic/foo", "main")


def test_merge_conflict_aborts(gitmock):
    from atlas.epic_worktree import WorktreeError, merge_into
    gitmock["state"]["branches"].add("epic/foo")
    gitmock["state"]["fail"]["merge"] = (1, "", "CONFLICT")
    with pytest.raises(WorktreeError, match="откатил"):
        merge_into("/repo", "epic/foo", "main")
    # должен был вызвать merge --abort
    assert any(c[1] == "merge" and "--abort" in c for c in gitmock["calls"])


def test_merge_with_push(gitmock):
    from atlas.epic_worktree import merge_into
    gitmock["state"]["branches"].add("epic/foo")
    res = merge_into("/repo", "epic/foo", "main", push=True)
    assert res["pushed"] is True
    assert any(c[1] == "push" and "origin" in c and "main" in c for c in gitmock["calls"])


# --------------------------------------------------------------------------- #
# CLI `atlas epic worktree …`                                                  #
# --------------------------------------------------------------------------- #


def _prep(tmp_path, *, with_git=True):
    url = f"sqlite:///{tmp_path / 'atlas.db'}"
    os.environ["ATLAS_DB_URL"] = url
    repo = tmp_path / "acme_repo"
    repo.mkdir()
    if with_git:
        (repo / ".git").mkdir()
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    with make_session(engine) as s:
        seed_all(s)
        t = ProjectType(slug="cp", name="Кл", default_sync_policy="full")
        st = ProjectStatus(slug="act", name="A", order_idx=30)
        s.add_all([t, st]); s.flush()
        p = Project(slug="acme", name="Acme", type_id=t.id, status_id=st.id,
                    priority="P2", one_line_summary="x", prefix="ACM",
                    sync_policy="full", local_path=str(repo),
                    git_default_branch="main")
        s.add(p); s.flush()
        s.add(Epic(slug="billing", project_id=p.id, title="Billing", status="active"))
        s.commit()
    return url, repo


def test_cli_worktree_create(tmp_path, gitmock):
    url, repo = _prep(tmp_path)
    try:
        gitmock["state"]["worktree_porcelain"] = ""  # нет существующих
        res = runner.invoke(app, ["epic", "worktree", "create", "billing"])
        assert res.exit_code == 0, res.stdout
        import json
        d = json.loads(res.stdout)
        assert d["created"] is True and d["branch"] == "epic/billing"
        assert any(c[1:3] == ["worktree", "add"] for c in gitmock["calls"])
    finally:
        os.environ.pop("ATLAS_DB_URL", None)


def test_cli_worktree_create_idempotent(tmp_path, gitmock):
    url, repo = _prep(tmp_path)
    try:
        gitmock["state"]["worktree_porcelain"] = (
            f"worktree {repo}.worktrees/epic-billing\nHEAD a1\nbranch refs/heads/epic/billing\n\n"
        )
        res = runner.invoke(app, ["epic", "worktree", "create", "billing"])
        assert res.exit_code == 0, res.stdout
        import json
        assert json.loads(res.stdout)["created"] is False  # уже есть
    finally:
        os.environ.pop("ATLAS_DB_URL", None)


def test_cli_worktree_merge(tmp_path, gitmock):
    url, repo = _prep(tmp_path)
    try:
        gitmock["state"]["branches"].add("epic/billing")
        gitmock["state"]["branch"] = "main"
        res = runner.invoke(app, ["epic", "worktree", "merge", "billing"])
        assert res.exit_code == 0, res.stdout
        import json
        d = json.loads(res.stdout)
        assert d["merged"] is True and d["into"] == "main"
    finally:
        os.environ.pop("ATLAS_DB_URL", None)


def test_cli_worktree_create_needs_git(tmp_path, gitmock):
    url, repo = _prep(tmp_path, with_git=False)
    try:
        res = runner.invoke(app, ["epic", "worktree", "create", "billing"])
        assert res.exit_code != 0
    finally:
        os.environ.pop("ATLAS_DB_URL", None)
