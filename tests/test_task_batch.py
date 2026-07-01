"""Batch-конструктор задач: [defaults] на батч + [[task]] override + config-fallback."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from atlas.db import make_engine, make_session
from atlas.models import Base, Project, ProjectStatus, ProjectType, Task
from atlas.seeds import seed_all

runner = CliRunner()


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'batch.db'}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    with make_session(eng) as s:
        seed_all(s)
        pt = s.execute(select(ProjectType)).scalars().first()
        ps = s.execute(select(ProjectStatus)).scalars().first()
        s.add(Project(slug="kasha", name="Kasha", type_id=pt.id, status_id=ps.id,
                      priority="P2", one_line_summary="x", prefix="KSH"))
        s.commit()
    return eng


def _app():
    from atlas.cli import app
    return app


def _batch_file(tmp_path, body):
    f = tmp_path / "batch.toml"
    f.write_text(body, encoding="utf-8")
    return str(f)


def test_batch_defaults_and_override(engine, tmp_path):
    f = _batch_file(tmp_path, """
[defaults]
project = "kasha"
priority = "P3"
no_review = true

[[task]]
title = "Первая"
cpp = "результат 1"

[[task]]
title = "Срочная"
cpp = "результат 2"
priority = "P1"
""")
    r = runner.invoke(_app(), ["task", "batch", f])
    assert r.exit_code == 0, r.stdout
    assert json.loads(r.stdout)["count"] == 2
    with make_session(engine) as s:
        tasks = s.execute(select(Task).order_by(Task.number)).scalars().all()
        assert tasks[0].priority == "P3"        # из defaults
        assert tasks[1].priority == "P1"        # override на задаче
        assert tasks[0].reviewer_id is None     # no_review из defaults
        kasha = s.execute(select(Project).where(Project.slug == "kasha")).scalar_one()
        assert tasks[0].project_id == kasha.id


def test_batch_dry_run_writes_nothing(engine, tmp_path):
    f = _batch_file(tmp_path, """
[defaults]
project = "kasha"
[[task]]
title = "X"
cpp = "y"
""")
    r = runner.invoke(_app(), ["task", "batch", f, "--dry-run"])
    assert r.exit_code == 0, r.stdout
    data = json.loads(r.stdout)
    assert data["dry_run"] is True and data["count"] == 1
    with make_session(engine) as s:
        assert s.execute(select(Task)).scalars().all() == []


def test_batch_requires_title_and_cpp(engine, tmp_path):
    f = _batch_file(tmp_path, """
[defaults]
project = "kasha"
[[task]]
title = "без ЦКП"
""")
    r = runner.invoke(_app(), ["task", "batch", f])
    assert r.exit_code != 0


def test_batch_reviewer_default_from_batch(engine, tmp_path):
    f = _batch_file(tmp_path, """
[defaults]
project = "kasha"
reviewer = "owner"
[[task]]
title = "С ревьюером"
cpp = "z"
""")
    r = runner.invoke(_app(), ["task", "batch", f])
    assert r.exit_code == 0, r.stdout
    with make_session(engine) as s:
        from atlas.models import Participant
        t = s.execute(select(Task)).scalars().first()
        rev = s.get(Participant, t.reviewer_id)
        assert rev.slug == "owner"
