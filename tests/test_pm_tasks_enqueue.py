"""F3c: создание задачи через CLI кладёт событие в outbox (если policy full)."""
import os

from typer.testing import CliRunner

from atlas.cli import app
from atlas.pm.db import make_engine, make_session
from atlas.pm.models import Base, Outbox, Project, ProjectStatus, ProjectType, SyncPolicy
from atlas.pm.seeds import seed_all

runner = CliRunner()


def _prep_db(tmp_path):
    db = tmp_path / "atlas.db"
    url = f"sqlite:///{db}"
    os.environ["ATLAS_DB_URL"] = url
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    with make_session(engine) as s:
        seed_all(s)
        t = ProjectType(slug="cp", name="Кл", default_sync_policy="full")
        st = ProjectStatus(slug="act", name="A", order_idx=20)
        s.add_all([t, st]); s.flush()
        p = Project(slug="acme", name="Acme", type_id=t.id, status_id=st.id,
                    priority="P2", one_line_summary="x", prefix="ACM", sync_policy="full")
        s.add(p); s.commit()
    return url


def test_task_add_enqueues_outbox(tmp_path):
    url = _prep_db(tmp_path)
    try:
        res = runner.invoke(app, [
            "pm-tasks", "add", "--project", "acme", "--title", "Сделать X", "--cpp", "ЦКП",
        ])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            obs = s.query(Outbox).all()
            assert len(obs) == 1
            assert obs[0].entity_kind == "task"
            assert obs[0].op == "create"
    finally:
        os.environ.pop("ATLAS_DB_URL", None)
