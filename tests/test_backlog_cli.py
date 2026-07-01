"""Пул backlog (Фаза B): add / list / show / edit / convert (→ task|project) / archive."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from atlas.cli import app
from atlas.db import make_engine, make_session
from atlas.models import (
    BacklogItem,
    Base,
    Project,
    ProjectStatus,
    ProjectType,
    Task,
)
from atlas.seeds import seed_all

runner = CliRunner()


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'atlas.db'}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    monkeypatch.setenv("ATLAS_DEFAULT_REVIEW", "false")  # без reviewer в convert→task
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    with make_session(eng) as s:
        seed_all(s)  # сидит типы (вкл. personal-project) + статусы (вкл. active)
        pt = s.execute(
            select(ProjectType).where(ProjectType.slug == "personal-project")
        ).scalar_one()
        st = s.execute(
            select(ProjectStatus).where(ProjectStatus.slug == "active")
        ).scalar_one()
        s.add(Project(slug="acme", name="Acme", type_id=pt.id, status_id=st.id,
                      priority="P2", one_line_summary="x", prefix="ACM"))
        # legacy idea-проект (для unified-вида)
        s.add(Project(slug="oldidea", name="Старая идея", type_id=pt.id, status_id=st.id,
                      priority="P3", one_line_summary="y", entity_kind="idea"))
        s.commit()
    return eng


def _json(r):
    return json.loads(r.stdout)


# --- add ---

def test_add_global(engine):
    r = runner.invoke(app, ["--json", "backlog", "add", "--title", "Глобальная идея"])
    assert r.exit_code == 0, r.stdout
    d = _json(r)
    assert d["scope"] == "global" and d["status"] == "open" and d["title"] == "Глобальная идея"


def test_add_project_scoped(engine):
    r = runner.invoke(app, ["--json", "backlog", "add", "--title", "Идея проекта",
                            "--project", "acme", "--priority", "P1"])
    assert r.exit_code == 0, r.stdout
    d = _json(r)
    assert d["scope"] == "project" and d["project"] == "acme" and d["priority"] == "P1"


# --- list (unified + filters) ---

def test_list_includes_items_and_legacy(engine):
    runner.invoke(app, ["backlog", "add", "--title", "Свежая идея"])
    r = runner.invoke(app, ["--json", "backlog", "list"])
    assert r.exit_code == 0, r.stdout
    rows = _json(r)
    titles = {row["title"] for row in rows}
    assert "Свежая идея" in titles
    # legacy idea-проект показан в едином виде
    assert any(row["source"] == "legacy-idea" for row in rows)


def test_list_global_filter_excludes_project_and_legacy(engine):
    runner.invoke(app, ["backlog", "add", "--title", "G"])
    runner.invoke(app, ["backlog", "add", "--title", "P", "--project", "acme"])
    r = runner.invoke(app, ["--json", "backlog", "list", "--global"])
    rows = _json(r)
    titles = {row["title"] for row in rows}
    assert "G" in titles and "P" not in titles
    assert not any(row["source"].startswith("legacy") for row in rows)


# --- convert → task ---

def test_convert_to_task_creates_todo(engine):
    runner.invoke(app, ["backlog", "add", "--title", "Сделать форму", "--project", "acme"])
    add = _json(runner.invoke(app, ["--json", "backlog", "add", "--title", "Вторая",
                                    "--project", "acme"]))
    r = runner.invoke(app, ["--json", "backlog", "convert", add["ref"], "--as", "task",
                            "--cpp", "Форма сохраняет данные"])
    assert r.exit_code == 0, r.stdout
    d = _json(r)
    assert d["as"] == "task"
    with make_session(engine) as s:
        task = s.execute(
            select(Task).where(Task.title == "Вторая")
        ).scalar_one()
        assert task.status == "todo"
        assert task.cpp_description == "Форма сохраняет данные"
        item = s.execute(
            select(BacklogItem).where(BacklogItem.slug == add["ref"])
        ).scalar_one()
        assert item.status == "converted" and item.converted_kind == "task"


def test_convert_to_task_requires_cpp(engine):
    add = _json(runner.invoke(app, ["--json", "backlog", "add", "--title", "X", "--project", "acme"]))
    r = runner.invoke(app, ["backlog", "convert", add["ref"], "--as", "task"])
    assert r.exit_code != 0


def test_convert_global_task_needs_project(engine):
    add = _json(runner.invoke(app, ["--json", "backlog", "add", "--title", "Global X"]))
    r = runner.invoke(app, ["backlog", "convert", add["ref"], "--as", "task", "--cpp", "z"])
    assert r.exit_code != 0  # global → нужен --project


def test_convert_global_task_with_project(engine):
    add = _json(runner.invoke(app, ["--json", "backlog", "add", "--title", "Global Y"]))
    r = runner.invoke(app, ["--json", "backlog", "convert", add["ref"], "--as", "task",
                            "--project", "acme", "--cpp", "ok"])
    assert r.exit_code == 0, r.stdout


# --- convert → project ---

def test_convert_to_project(engine):
    add = _json(runner.invoke(app, ["--json", "backlog", "add", "--title", "Новый продукт"]))
    r = runner.invoke(app, ["--json", "backlog", "convert", add["ref"], "--as", "project",
                            "--type", "personal-project"])
    assert r.exit_code == 0, r.stdout
    d = _json(r)
    assert d["as"] == "project"
    with make_session(engine) as s:
        proj = s.execute(
            select(Project).where(Project.name == "Новый продукт")
        ).scalar_one()
        assert proj.entity_kind == "project"


# --- archive + already-converted guard ---

def test_archive_soft(engine):
    add = _json(runner.invoke(app, ["--json", "backlog", "add", "--title", "Архивная"]))
    r = runner.invoke(app, ["--json", "backlog", "archive", add["ref"]])
    assert r.exit_code == 0, r.stdout
    # из open-вида пропала
    rows = _json(runner.invoke(app, ["--json", "backlog", "list"]))
    assert add["ref"] not in {row["ref"] for row in rows}


def test_convert_twice_rejected(engine):
    add = _json(runner.invoke(app, ["--json", "backlog", "add", "--title", "Один раз",
                                    "--project", "acme"]))
    runner.invoke(app, ["backlog", "convert", add["ref"], "--as", "task", "--cpp", "a"])
    r = runner.invoke(app, ["backlog", "convert", add["ref"], "--as", "task", "--cpp", "b"])
    assert r.exit_code != 0  # уже converted


# --- review-fixes (#3..#6) ---

def test_convert_to_project_uses_priority(engine):
    """#3: --priority доходит до проекта (раньше молча игнорировался)."""
    add = _json(runner.invoke(app, ["--json", "backlog", "add", "--title", "Приоритетный"]))
    runner.invoke(app, ["backlog", "convert", add["ref"], "--as", "project",
                        "--type", "personal-project", "--priority", "P0"])
    with make_session(engine) as s:
        proj = s.execute(select(Project).where(Project.name == "Приоритетный")).scalar_one()
        assert proj.priority == "P0"


def test_convert_task_bad_project_clean_exit(engine):
    """#4: невалидный --project → чистый exit (CliError), не traceback."""
    add = _json(runner.invoke(app, ["--json", "backlog", "add", "--title", "X"]))
    r = runner.invoke(app, ["backlog", "convert", add["ref"], "--as", "task",
                            "--project", "no-such-proj", "--cpp", "z"])
    assert r.exit_code != 0
    assert "Traceback" not in r.stdout


def test_convert_task_no_review(engine, monkeypatch):
    """#5: --no-review снимает reviewer даже при default_review=true."""
    monkeypatch.setenv("ATLAS_DEFAULT_REVIEW", "true")
    add = _json(runner.invoke(app, ["--json", "backlog", "add", "--title", "Без ревью",
                                    "--project", "acme"]))
    r = runner.invoke(app, ["--json", "backlog", "convert", add["ref"], "--as", "task",
                            "--cpp", "ok", "--no-review"])
    assert r.exit_code == 0, r.stdout
    with make_session(engine) as s:
        task = s.execute(select(Task).where(Task.title == "Без ревью")).scalar_one()
        assert task.reviewer_id is None


def test_convert_archived_rejected(engine):
    """#6: архивированную идею нельзя преобразовать."""
    add = _json(runner.invoke(app, ["--json", "backlog", "add", "--title", "Арх",
                                    "--project", "acme"]))
    runner.invoke(app, ["backlog", "archive", add["ref"]])
    r = runner.invoke(app, ["backlog", "convert", add["ref"], "--as", "task", "--cpp", "z"])
    assert r.exit_code != 0
