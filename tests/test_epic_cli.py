"""F3e: atlas epic add/list/get + enqueue."""
import os

from typer.testing import CliRunner

from atlas.cli import app
from atlas.db import make_engine, make_session
from atlas.models import Base, Epic, Outbox, Project, ProjectStatus, ProjectType, SyncPolicy
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
        s.add(p); s.commit()
    return url


def test_epic_add_creates_and_enqueues(tmp_path):
    url = _prep(tmp_path)
    try:
        res = runner.invoke(app, ["epic", "add", "--project", "acme", "--title", "Спринт 1"])
        assert res.exit_code == 0, res.stdout
        engine = make_engine(url)
        with make_session(engine) as s:
            epics = s.query(Epic).all()
            assert len(epics) == 1
            assert epics[0].title == "Спринт 1"
            obs = s.query(Outbox).filter(Outbox.entity_kind == "epic").all()
            assert len(obs) == 1
    finally:
        os.environ.pop("ATLAS_DB_URL", None)


def test_epic_list(tmp_path):
    url = _prep(tmp_path)
    try:
        runner.invoke(app, ["epic", "add", "--project", "acme", "--title", "E1"])
        res = runner.invoke(app, ["--text", "epic", "list", "--project", "acme"])
        assert res.exit_code == 0
        assert "E1" in res.stdout
    finally:
        os.environ.pop("ATLAS_DB_URL", None)
