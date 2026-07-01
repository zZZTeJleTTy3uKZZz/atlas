"""Provenance + фикс --json в CLI `atlas task` (add/get/list).

Стиль — как test_pm_tasks_cli.py / test_epic_provenance_cli.py: sqlite-файл,
ATLAS_DB_URL, seed_all, CliRunner. Покрываем:
- task add с/без --source-project (origin injected/native, injected_at);
- self-inject (source == target) → warning, origin остаётся 'native';
- нерезолвимый --source-project / --injected-by → ошибка;
- явный --origin перебивает авто-injected;
- action_log task_created содержит source_project + rationale;
- task get печатает блок Provenance (текст) + provenance-поля в JSON;
- task list --source-project фильтрует + маркер origin;
- --json реально отдаёт JSON для list и get.
"""
import json
import os

from typer.testing import CliRunner

from atlas.cli import app
from atlas.db import make_engine, make_session
from atlas.models import (
    ActionLog,
    Base,
    Participant,
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


# --------------------------------------------------------------------------- #
# add — provenance / инвариант origin↔source                                  #
# --------------------------------------------------------------------------- #


def test_task_add_with_source_sets_injected(tmp_path):
    url = _prep(tmp_path)
    try:
        res = runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "Инжект", "--cpp", "ЦКП",
            "--source-project", "beta", "--rationale", "по принципу зеркала",
            "--injected-by", "dima",
        ])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            beta = s.query(Project).filter(Project.slug == "beta").one()
            task = s.query(Task).filter(Task.title == "Инжект").one()
            assert task.origin == "injected"
            assert task.source_project_id == beta.id
            assert task.rationale == "по принципу зеркала"
            assert task.injected_at is not None
            assert task.injected_by is not None
    finally:
        _cleanup()


def test_task_add_without_source_is_native(tmp_path):
    url = _prep(tmp_path)
    try:
        res = runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "Натив", "--cpp", "x",
        ])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            task = s.query(Task).filter(Task.title == "Натив").one()
            assert task.origin == "native"
            assert task.source_project_id is None
            assert task.injected_at is None
            assert task.injected_by is None
    finally:
        _cleanup()


def test_task_add_self_inject_warns_and_stays_native(tmp_path):
    url = _prep(tmp_path)
    try:
        res = runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "Само", "--cpp", "x",
            "--source-project", "acme",
        ])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            task = s.query(Task).filter(Task.title == "Само").one()
            assert task.origin == "native"
            assert task.source_project_id is None
            assert task.injected_at is None
    finally:
        _cleanup()


def test_task_add_explicit_origin_override(tmp_path):
    url = _prep(tmp_path)
    try:
        res = runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "Импорт", "--cpp", "x",
            "--source-project", "beta", "--origin", "imported",
        ])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            task = s.query(Task).filter(Task.title == "Импорт").one()
            assert task.origin == "imported"
            assert task.source_project_id is not None
    finally:
        _cleanup()


def test_task_add_unresolvable_source_errors(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "X", "--cpp", "x",
            "--source-project", "nope",
        ])
        assert res.exit_code != 0
    finally:
        _cleanup()


def test_task_add_unresolvable_injected_by_errors(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "X", "--cpp", "x",
            "--source-project", "beta", "--injected-by", "ghost",
        ])
        assert res.exit_code != 0
    finally:
        _cleanup()


def test_task_add_invalid_origin_errors(tmp_path):
    _prep(tmp_path)
    try:
        res = runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "X", "--cpp", "x",
            "--origin", "garbage",
        ])
        assert res.exit_code != 0
    finally:
        _cleanup()


def test_task_add_action_log_has_source_and_rationale(tmp_path):
    url = _prep(tmp_path)
    try:
        runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "Logged", "--cpp", "x",
            "--source-project", "beta", "--rationale", "обоснование",
        ])
        engine = make_engine(url)
        with make_session(engine) as s:
            entry = s.query(ActionLog).filter(
                ActionLog.action == "task_created"
            ).one()
            details = json.loads(entry.details_json)
            assert details["source_project"] == "beta"
            assert details["rationale"] == "обоснование"
    finally:
        _cleanup()


# --------------------------------------------------------------------------- #
# get — Provenance блок + JSON                                                 #
# --------------------------------------------------------------------------- #


def test_task_get_prints_provenance_text(tmp_path):
    _prep(tmp_path)
    try:
        runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "Карточка", "--cpp", "x",
            "--slug", "card", "--source-project", "beta",
            "--rationale", "обоснование завода",
        ])
        res = runner.invoke(app, ["--text", "task", "get", "ACM-card"])
        assert res.exit_code == 0, res.stdout
        assert "Provenance" in res.stdout
        assert "injected" in res.stdout
        assert "обоснование завода" in res.stdout
        assert "beta" in res.stdout
    finally:
        _cleanup()


def test_task_get_native_omits_provenance_text(tmp_path):
    _prep(tmp_path)
    try:
        runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "Натив", "--cpp", "x",
            "--slug", "nat",
        ])
        res = runner.invoke(app, ["--text", "task", "get", "ACM-nat"])
        assert res.exit_code == 0, res.stdout
        assert "Provenance" not in res.stdout
    finally:
        _cleanup()


def test_task_get_json_has_provenance(tmp_path):
    _prep(tmp_path)
    try:
        runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "JSONget", "--cpp", "x",
            "--slug", "jg", "--source-project", "beta", "--rationale", "обос",
        ])
        res = runner.invoke(app, ["task", "get", "ACM-jg"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        assert data["origin"] == "injected"
        assert data["source_project"] == "beta"
        assert data["rationale"] == "обос"
        assert data["title"] == "JSONget"
    finally:
        _cleanup()


# --------------------------------------------------------------------------- #
# list — фильтр --source-project + JSON                                       #
# --------------------------------------------------------------------------- #


def test_task_list_filters_by_source_project(tmp_path):
    _prep(tmp_path)
    try:
        runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "Native", "--cpp", "x",
        ])
        runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "FromBeta", "--cpp", "x",
            "--source-project", "beta",
        ])
        res = runner.invoke(app, ["task", "list", "--source-project", "beta"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        titles = {d["title"] for d in data}
        assert titles == {"FromBeta"}
    finally:
        _cleanup()


def test_task_list_json_has_origin(tmp_path):
    _prep(tmp_path)
    try:
        runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "Inj", "--cpp", "x",
            "--source-project", "beta",
        ])
        res = runner.invoke(app, ["task", "list"])
        assert res.exit_code == 0, res.stdout
        data = json.loads(res.stdout)
        assert isinstance(data, list)
        by_title = {d["title"]: d for d in data}
        assert by_title["Inj"]["origin"] == "injected"
        assert by_title["Inj"]["source_project"] == "beta"
    finally:
        _cleanup()


def test_task_list_text_marks_injected(tmp_path):
    _prep(tmp_path)
    try:
        runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "Inj", "--cpp", "x",
            "--source-project", "beta",
        ])
        res = runner.invoke(app, ["--text", "task", "list"])
        assert res.exit_code == 0, res.stdout
        # маркер происхождения: либо «injected», либо «←beta»
        assert "injected" in res.stdout.lower() or "beta" in res.stdout.lower()
    finally:
        _cleanup()
