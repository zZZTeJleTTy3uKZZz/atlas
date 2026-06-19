"""F3f: CLI atlas checklist add --due / check / delete → enqueue в Outbox.

Уровень enqueue остаётся "checklist" (policy/outbox), на проводе mapper
переводит в "checklist_item". Здесь проверяем outbox-уровень + поля.
"""
import json
import os

from typer.testing import CliRunner

from atlas.cli import app
from atlas.pm.db import make_engine, make_session
from atlas.pm.models import (
    Base, ChecklistItem, Outbox, Project, ProjectStatus, ProjectType, Task,
)
from atlas.pm.seeds import seed_all

runner = CliRunner()


def _prep_with_task(tmp_path):
    url = f"sqlite:///{tmp_path / 'atlas.db'}"
    os.environ["ATLAS_DB_URL"] = url
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    with make_session(engine) as s:
        seed_all(s)
        t = ProjectType(slug="cp", name="Кл", default_sync_policy="full")
        st = ProjectStatus(slug="act", name="A", order_idx=40)
        s.add_all([t, st]); s.flush()
        p = Project(slug="acme", name="Acme", type_id=t.id, status_id=st.id,
                    priority="P2", one_line_summary="x", prefix="ACM",
                    sync_policy="full")
        s.add(p); s.flush()
        task = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2",
                    slug="ACM-1", number=1, backend_id="task-be-1")
        s.add(task); s.commit()
        return url, task.id


def test_checklist_add_with_due_enqueues_checklist_level(tmp_path):
    url, _ = _prep_with_task(tmp_path)
    try:
        res = runner.invoke(app, [
            "checklist", "add", "--task", "ACM-1", "--text", "Шаг 1",
            "--due", "2026-06-22",
        ])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            items = s.query(ChecklistItem).all()
            assert len(items) == 1
            assert items[0].due_date is not None
            obs = s.query(Outbox).all()
            assert len(obs) == 1
            assert obs[0].entity_kind == "checklist"
            assert obs[0].op == "create"
            # на проводе — канон ядра
            wire = json.loads(obs[0].payload_json)
            assert wire["entity_kind"] == "checklist_item"
            assert wire["payload_json"]["title"] == "Шаг 1"
            assert wire["payload_json"]["due"] == "2026-06-22"
            assert wire["payload_json"]["parent_task_backend_id"] == "task-be-1"
    finally:
        os.environ.pop("ATLAS_DB_URL", None)


def test_checklist_delete_enqueues_delete(tmp_path):
    url, _ = _prep_with_task(tmp_path)
    try:
        runner.invoke(app, ["checklist", "add", "--task", "ACM-1", "--text", "Шаг 1"])
        engine = make_engine(url)
        with make_session(engine) as s:
            ci_id = s.query(ChecklistItem).one().id
        res = runner.invoke(app, ["checklist", "delete", ci_id])
        assert res.exit_code == 0, res.stdout
        with make_session(make_engine(url)) as s:
            assert s.get(ChecklistItem, ci_id) is None
            ops = [o.op for o in s.query(Outbox).all()]
            assert "delete" in ops
    finally:
        os.environ.pop("ATLAS_DB_URL", None)
