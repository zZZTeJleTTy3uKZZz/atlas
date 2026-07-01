"""F3e: update/delete задачи кладут событие в outbox (policy full)."""
import os

from typer.testing import CliRunner

from atlas.cli import app
from atlas.db import make_engine, make_session
from atlas.models import Base, Outbox, Project, ProjectStatus, ProjectType, SyncPolicy, Task
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
        s.add_all([t, st]); s.flush()
        p = Project(slug="acme", name="Acme", type_id=t.id, status_id=st.id,
                    priority="P2", one_line_summary="x", prefix="ACM", sync_policy="full")
        s.add(p); s.flush()
        task = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2",
                    slug="ACM-1", number=1)
        s.add(task); s.commit()
    return url


def test_start_enqueues(tmp_path):
    """status→in_progress теперь через `task start` (= claim) — оно тоже
    enqueue'ит update-событие (статус синкается в ядро)."""
    url = _prep(tmp_path)
    try:
        res = runner.invoke(app, ["task", "start", "ACM-1"])
        assert res.exit_code == 0, res.stdout
        with make_session(make_engine(url)) as s:
            obs = s.query(Outbox).filter(Outbox.op == "update", Outbox.entity_kind == "task").all()
            assert len(obs) == 1
    finally:
        os.environ.pop("ATLAS_DB_URL", None)


def test_delete_enqueues(tmp_path):
    url = _prep(tmp_path)
    try:
        res = runner.invoke(app, ["task", "delete", "ACM-1"])
        assert res.exit_code == 0, res.stdout
        with make_session(make_engine(url)) as s:
            obs = s.query(Outbox).filter(Outbox.op == "delete", Outbox.entity_kind == "task").all()
            assert len(obs) == 1
    finally:
        os.environ.pop("ATLAS_DB_URL", None)
