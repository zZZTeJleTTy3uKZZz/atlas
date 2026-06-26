"""Provenance + портфельный режим в CLI `atlas epic` (add/get/list).

Стиль — как test_epic_cli.py: sqlite-файл, ATLAS_DB_URL, seed_all, CliRunner.
Покрываем:
- epic add с --source-project → origin авто 'injected' + injected_at;
- self-inject (source == target) → warning, origin остаётся 'native';
- epic list БЕЗ --project → портфель (все эпики всех проектов) + поле project;
- epic list --project → фильтрует по проекту;
- epic list --source-project → фильтр по источнику;
- epic get → печатает description + блок Provenance.
"""
import json
import os

from typer.testing import CliRunner

from atlas.cli import app
from atlas.db import make_engine, make_session
from atlas.models import (
    Base, Epic, Participant, Project, ProjectStatus, ProjectType,
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
        acme = Project(slug="acme", name="Acme", type_id=t.id, status_id=st.id,
                       priority="P2", one_line_summary="x", prefix="ACM",
                       sync_policy="full")
        beta = Project(slug="beta", name="Beta", type_id=t.id, status_id=st.id,
                       priority="P2", one_line_summary="y", prefix="BET",
                       sync_policy="full")
        actor = Participant(kind="human", slug="dima", name="Owner")
        s.add_all([acme, beta, actor])
        s.commit()
    return url


def _cleanup():
    os.environ.pop("ATLAS_DB_URL", None)


def test_epic_add_with_source_sets_injected(tmp_path):
    url = _prep(tmp_path)
    try:
        res = runner.invoke(app, [
            "epic", "add", "--project", "acme", "--title", "Инжект",
            "--source-project", "beta", "--rationale", "по принципу зеркала",
            "--injected-by", "dima",
        ])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            beta = s.query(Project).filter(Project.slug == "beta").one()
            ep = s.query(Epic).filter(Epic.title == "Инжект").one()
            assert ep.origin == "injected"
            assert ep.source_project_id == beta.id
            assert ep.rationale == "по принципу зеркала"
            assert ep.injected_at is not None
            assert ep.injected_by is not None
    finally:
        _cleanup()


def test_epic_add_self_inject_warns_and_stays_native(tmp_path):
    url = _prep(tmp_path)
    try:
        res = runner.invoke(app, [
            "epic", "add", "--project", "acme", "--title", "Само",
            "--source-project", "acme",
        ])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            ep = s.query(Epic).filter(Epic.title == "Само").one()
            assert ep.origin == "native"
            assert ep.source_project_id is None
            assert ep.injected_at is None
    finally:
        _cleanup()


def test_epic_add_explicit_origin_override(tmp_path):
    url = _prep(tmp_path)
    try:
        res = runner.invoke(app, [
            "epic", "add", "--project", "acme", "--title", "Импорт",
            "--source-project", "beta", "--origin", "imported",
        ])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            ep = s.query(Epic).filter(Epic.title == "Импорт").one()
            assert ep.origin == "imported"
    finally:
        _cleanup()


def test_epic_list_portfolio_without_project(tmp_path):
    url = _prep(tmp_path)
    try:
        runner.invoke(app, ["epic", "add", "--project", "acme", "--title", "EA"])
        runner.invoke(app, ["epic", "add", "--project", "beta", "--title", "EB"])
        res = runner.invoke(app, ["epic", "list"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        titles = {d["title"] for d in data}
        assert titles == {"EA", "EB"}
        by_title = {d["title"]: d for d in data}
        assert by_title["EA"]["project"] == "acme"
        assert by_title["EB"]["project"] == "beta"
    finally:
        _cleanup()


def test_epic_list_filters_by_project(tmp_path):
    url = _prep(tmp_path)
    try:
        runner.invoke(app, ["epic", "add", "--project", "acme", "--title", "EA"])
        runner.invoke(app, ["epic", "add", "--project", "beta", "--title", "EB"])
        res = runner.invoke(app, ["epic", "list", "--project", "acme"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        titles = {d["title"] for d in data}
        assert titles == {"EA"}
    finally:
        _cleanup()


def test_epic_list_filters_by_source_project(tmp_path):
    url = _prep(tmp_path)
    try:
        runner.invoke(app, ["epic", "add", "--project", "acme", "--title", "Native"])
        runner.invoke(app, [
            "epic", "add", "--project", "acme", "--title", "FromBeta",
            "--source-project", "beta",
        ])
        res = runner.invoke(app, ["epic", "list", "--source-project", "beta"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        titles = {d["title"] for d in data}
        assert titles == {"FromBeta"}
    finally:
        _cleanup()


def test_epic_get_prints_description_and_provenance(tmp_path):
    url = _prep(tmp_path)
    try:
        runner.invoke(app, [
            "epic", "add", "--project", "acme", "--title", "Карточка",
            "--slug", "card-e", "--description", "это описание эпика",
            "--source-project", "beta", "--rationale", "обоснование завода",
        ])
        res = runner.invoke(app, ["--text", "epic", "get", "card-e"])
        assert res.exit_code == 0, res.stdout
        assert "это описание эпика" in res.stdout
        assert "Provenance" in res.stdout
        assert "injected" in res.stdout
        assert "обоснование завода" in res.stdout
    finally:
        _cleanup()
