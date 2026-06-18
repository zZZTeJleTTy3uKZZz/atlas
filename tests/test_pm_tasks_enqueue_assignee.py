"""PART A: `task add --assignee <slug>` кладёт slug в outbox payload.

Сквозной путь: CLI → Task.assignee_id → enqueue → mapper.assignee_slugs →
payload_json["assignee_slugs"]. Без этого ядро получает task без участников,
TaskMember=0, Notion «Ответственный» пуст.
"""
import json
import os

from typer.testing import CliRunner

from atlas.cli import app
from atlas.pm.db import make_engine, make_session
from atlas.pm.models import Base, Outbox, Project, ProjectStatus, ProjectType
from atlas.pm.seeds import seed_all

runner = CliRunner()


def _prep_db(tmp_path):
    db = tmp_path / "atlas.db"
    url = f"sqlite:///{db}"
    os.environ["ATLAS_DB_URL"] = url
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    with make_session(engine) as s:
        seed_all(s)  # сидит participant slug=dmitry
        t = ProjectType(slug="cp", name="Кл", default_sync_policy="full")
        st = ProjectStatus(slug="act", name="A", order_idx=20)
        s.add_all([t, st]); s.flush()
        p = Project(slug="acme", name="Acme", type_id=t.id, status_id=st.id,
                    priority="P2", one_line_summary="x", prefix="ACM", sync_policy="full")
        s.add(p); s.commit()
    return url


def test_task_add_with_assignee_enqueues_slug(tmp_path):
    url = _prep_db(tmp_path)
    try:
        res = runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "Сделать X",
            "--cpp", "ЦКП", "--assignee", "dmitry",
        ])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            ob = s.query(Outbox).one()
            payload = json.loads(ob.payload_json)["payload_json"]
            assert payload["assignee_slugs"] == ["dmitry"]
    finally:
        os.environ.pop("ATLAS_DB_URL", None)


def test_task_add_without_assignee_enqueues_empty(tmp_path):
    url = _prep_db(tmp_path)
    try:
        res = runner.invoke(app, [
            "task", "add", "--project", "acme", "--title", "Без исполнителя",
            "--cpp", "ЦКП",
        ])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            ob = s.query(Outbox).one()
            payload = json.loads(ob.payload_json)["payload_json"]
            assert payload["assignee_slugs"] == []
    finally:
        os.environ.pop("ATLAS_DB_URL", None)
