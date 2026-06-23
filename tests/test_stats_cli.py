"""CLI-тесты для группы `atlas stats` + `atlas dashboard` (эпик Dashboard).

Стиль — как test_epic_cli.py / test_pm_tasks_provenance_cli.py: sqlite-файл,
ATLAS_DB_URL, seed_all, CliRunner. Проверяем оба режима вывода (--json дефолт
и --text). git-режим (#131) мокает atlas.stats.run, чтобы не дёргать
реальный git.

Команды/режимы:
- `atlas stats` (overview по умолчанию) — counts (#128);
- `atlas stats --period 7d` — активность в окне (#129);
- `atlas stats --provenance` — provenance-аналитика (#130);
- `atlas stats --project <ref>` — git-статистика проекта (#131);
- `atlas dashboard` — объединённый обзор (#132).
"""
import json
import os

import pytest
from typer.testing import CliRunner

from atlas.cli import app
from atlas.db import make_engine, make_session
from atlas.models import (
    Base,
    Counterparty,
    Project,
    ProjectStatus,
    ProjectType,
    Task,
)
from atlas.seeds import seed_all

runner = CliRunner()


def _prep(tmp_path):
    url = f"sqlite:///{tmp_path / 'atlas.db'}"
    os.environ["ATLAS_DB_URL"] = url
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    with make_session(engine) as s:
        seed_all(s)
        t = ProjectType(slug="cp", name="Кл", default_sync_policy="full")
        st = ProjectStatus(slug="act", name="A", order_idx=30)
        s.add_all([t, st])
        s.flush()
        cp = s.query(Counterparty).filter(Counterparty.slug == "cifro-pro").one()
        acme = Project(slug="acme", name="Acme", type_id=t.id, status_id=st.id,
                       priority="P2", one_line_summary="x", prefix="ACM",
                       sync_policy="full", owner_id=cp.id,
                       local_path=str(tmp_path / "acme-repo"))
        beta = Project(slug="beta", name="Beta", type_id=t.id, status_id=st.id,
                       priority="P2", one_line_summary="y", prefix="BET",
                       sync_policy="full")
        s.add_all([acme, beta])
        s.flush()
        # инжектированная задача beta→acme, реализована
        inj = Task(project_id=acme.id, title="Инж", cpp_description="c",
                   priority="P2", status="done", origin="injected",
                   source_project_id=beta.id, number=1)
        s.add(inj)
        s.commit()
    return url


def _cleanup():
    os.environ.pop("ATLAS_DB_URL", None)


# --------------------------------------------------------------------------- #
# #128 overview (default)                                                      #
# --------------------------------------------------------------------------- #


def test_stats_overview_json_default(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["stats"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        assert data["total"] == 2
        by_type = {r["key"]: r["count"] for r in data["by_type"]}
        assert by_type["cp"] == 2
    finally:
        _cleanup()


def test_stats_overview_text(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["--text", "stats"])
        assert res.exit_code == 0, res.stdout
        assert "2" in res.stdout
    finally:
        _cleanup()


def test_stats_overview_counterparty_breakdown(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["stats"])
        data = json.loads(res.stdout)
        by_owner = {r["key"]: r["count"] for r in data["by_owner"]}
        assert by_owner.get("cifro-pro") == 1
    finally:
        _cleanup()


# --------------------------------------------------------------------------- #
# #129 period                                                                  #
# --------------------------------------------------------------------------- #


def test_stats_period_json(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["stats", "--period", "365d"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        # проекты заведены сейчас → активны в окне 365d
        assert data["projects_active"] >= 2
        assert "start" in data and "end" in data
    finally:
        _cleanup()


def test_stats_period_invalid_errors(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["stats", "--period", "garbage"])
        assert res.exit_code != 0
    finally:
        _cleanup()


def test_stats_period_filter_by_type(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["stats", "--period", "365d", "--type", "cp"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        assert data["projects_active"] == 2
    finally:
        _cleanup()


def test_stats_period_filter_by_tag(tmp_path):
    """--tag реально фильтрует (раньше был silent no-op)."""
    _prep(tmp_path)
    try:
        from atlas.models import ProjectTag, Tag

        url = os.environ["ATLAS_DB_URL"]
        engine = make_engine(url)
        with make_session(engine) as s:
            acme = s.query(Project).filter(Project.slug == "acme").one()
            tag = Tag(slug="flagged", name="Flagged", category="other")
            s.add(tag)
            s.flush()
            s.add(ProjectTag(project_id=acme.id, tag_id=tag.id))
            s.commit()

        res = runner.invoke(app, ["stats", "--period", "365d", "--tag", "flagged"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        slugs = {p["slug"] for p in data["projects"]}
        assert slugs == {"acme"}
        assert data["projects_active"] == 1
    finally:
        _cleanup()


def test_stats_period_invalid_emits_json_error_on_stderr(tmp_path):
    """В --json (дефолт) ошибка периода — валидный JSON на stderr, stdout чист."""
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["stats", "--period", "garbage"])
        assert res.exit_code != 0
        assert res.stdout.strip() == ""
        err = json.loads(res.stderr.strip().splitlines()[-1])
        assert err["event"] == "error"
        assert err["code"] == "invalid_period"
    finally:
        _cleanup()


# --------------------------------------------------------------------------- #
# #130 provenance                                                              #
# --------------------------------------------------------------------------- #


def test_stats_provenance_json(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["stats", "--provenance"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        assert data["total_injected"] == 1
        assert data["realized"] == 1
        assert data["realized_share"] == pytest.approx(1.0)
        sources = {r["slug"] for r in data["top_sources"]}
        assert "beta" in sources
        sinks = {r["slug"] for r in data["top_sinks"]}
        assert "acme" in sinks
    finally:
        _cleanup()


def test_stats_provenance_text(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["--text", "stats", "--provenance"])
        assert res.exit_code == 0, res.stdout
        assert "beta" in res.stdout
    finally:
        _cleanup()


# --------------------------------------------------------------------------- #
# #131 git per-project (subprocess мокается)                                  #
# --------------------------------------------------------------------------- #


def _fake_git_run(cmd, cwd=None):
    joined = " ".join(cmd)
    if "rev-parse" in joined:
        return (0, "true\n", "")
    if "rev-list" in joined:
        return (0, "7\n", "")
    if "--reverse" in joined:
        return (0, "2026-06-01T10:00:00+03:00\n", "")
    if "log" in joined:
        return (0, "2026-06-20T10:00:00+03:00\n", "")
    return (0, "", "")


def test_stats_project_git_json(tmp_path, monkeypatch):
    _prep(tmp_path)
    try:
        from atlas import stats as stats_mod
        monkeypatch.setattr(stats_mod, "run", _fake_git_run)
        res = runner.invoke(app, ["stats", "--project", "acme"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        assert data["commits"] == 7
        assert data["is_git"] is True
        assert "2026-06-20" in data["last_commit_at"]
    finally:
        _cleanup()


def test_stats_project_unknown_errors(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["stats", "--project", "nope"])
        assert res.exit_code != 0
        assert res.stdout.strip() == ""
        err = json.loads(res.stderr.strip().splitlines()[-1])
        assert err["event"] == "error"
        assert err["code"] == "not_found"
    finally:
        _cleanup()


def test_stats_project_git_exposes_last_pushed_at(tmp_path, monkeypatch):
    """#131: last_pushed_at из Project отдаётся в git-секции."""
    _prep(tmp_path)
    try:
        from datetime import datetime

        from atlas import stats as stats_mod
        monkeypatch.setattr(stats_mod, "run", _fake_git_run)

        url = os.environ["ATLAS_DB_URL"]
        engine = make_engine(url)
        with make_session(engine) as s:
            acme = s.query(Project).filter(Project.slug == "acme").one()
            acme.git_last_pushed_at = datetime(2026, 6, 19, 8, 0, 0)
            s.commit()

        res = runner.invoke(app, ["stats", "--project", "acme"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        assert "last_pushed_at" in data
        assert "2026-06-19" in data["last_pushed_at"]
    finally:
        _cleanup()


def test_stats_project_git_text(tmp_path, monkeypatch):
    _prep(tmp_path)
    try:
        from atlas import stats as stats_mod
        monkeypatch.setattr(stats_mod, "run", _fake_git_run)
        res = runner.invoke(app, ["--text", "stats", "--project", "acme"])
        assert res.exit_code == 0, res.stdout
        assert "7" in res.stdout
    finally:
        _cleanup()


# --------------------------------------------------------------------------- #
# #132 dashboard                                                               #
# --------------------------------------------------------------------------- #


def test_dashboard_json_has_all_sections(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["dashboard"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        assert "counts" in data
        assert "provenance" in data
        assert "activity" in data
        assert data["counts"]["total"] == 2
        assert data["provenance"]["total_injected"] == 1
    finally:
        _cleanup()


def test_dashboard_text(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["--text", "dashboard"])
        assert res.exit_code == 0, res.stdout
        # читаемый обзор содержит ключевые цифры
        assert "2" in res.stdout
    finally:
        _cleanup()


def test_dashboard_period_window(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, ["dashboard", "--period", "365d"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        assert data["activity"]["projects_active"] >= 2
    finally:
        _cleanup()
