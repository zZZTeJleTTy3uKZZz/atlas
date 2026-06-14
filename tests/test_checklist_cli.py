"""F3e: atlas checklist add/list/check + enqueue."""
import os

from typer.testing import CliRunner

from atlas.cli import app
from atlas.pm.db import make_engine, make_session
from atlas.pm.models import (
    Base, ChecklistItem, Outbox, Project, ProjectStatus, ProjectType, SyncPolicy, Task,
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
        st = ProjectStatus(slug="act", name="A", order_idx=30)
        s.add_all([t, st]); s.flush()
        p = Project(slug="acme", name="Acme", type_id=t.id, status_id=st.id,
                    priority="P2", one_line_summary="x", prefix="ACM", sync_policy="full")
        s.add(p); s.flush()
        task = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2",
                    slug="ACM-1", number=1)
        s.add(task); s.commit()
        return url, task.id


def test_checklist_add_and_check(tmp_path):
    url, task_id = _prep_with_task(tmp_path)
    try:
        res = runner.invoke(app, ["checklist", "add", "--task", "ACM-1", "--text", "Шаг 1"])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            items = s.query(ChecklistItem).all()
            assert len(items) == 1 and items[0].is_done == 0
            ci_id = items[0].id
        res2 = runner.invoke(app, ["checklist", "check", ci_id])
        assert res2.exit_code == 0
        with make_session(make_engine(url)) as s:
            assert s.get(ChecklistItem, ci_id).is_done == 1
    finally:
        os.environ.pop("ATLAS_DB_URL", None)
