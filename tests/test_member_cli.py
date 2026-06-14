"""F3e: atlas member add/list/rm — участники задачи (TaskMember)."""
import os

from typer.testing import CliRunner

from atlas.cli import app
from atlas.pm.db import make_engine, make_session
from atlas.pm.models import (
    Base, Participant, Project, ProjectStatus, ProjectType, Task, TaskMember,
)

runner = CliRunner()


def _prep(tmp_path):
    url = f"sqlite:///{tmp_path / 'atlas.db'}"
    os.environ["ATLAS_DB_URL"] = url
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    with make_session(engine) as s:
        t = ProjectType(slug="cp", name="Кл")
        st = ProjectStatus(slug="act", name="A", order_idx=30)
        s.add_all([t, st]); s.flush()
        p = Project(slug="acme", name="Acme", type_id=t.id, status_id=st.id,
                    priority="P2", one_line_summary="x", prefix="ACM")
        ag = Participant(kind="ai_agent", slug="claude", name="Claude")
        s.add_all([p, ag]); s.flush()
        task = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2",
                    slug="ACM-1", number=1)
        s.add(task); s.commit()
        return url, task.id


def test_member_add_list_rm(tmp_path):
    url, task_id = _prep(tmp_path)
    try:
        res = runner.invoke(app, ["member", "add", "--task", "ACM-1", "--participant", "claude", "--role", "executor"])
        assert res.exit_code == 0, res.stdout
        with make_session(make_engine(url)) as s:
            assert len(s.query(TaskMember).all()) == 1
        res2 = runner.invoke(app, ["--text", "member", "list", "--task", "ACM-1"])
        assert "claude" in res2.stdout
        res3 = runner.invoke(app, ["member", "rm", "--task", "ACM-1", "--participant", "claude", "--role", "executor"])
        assert res3.exit_code == 0
        with make_session(make_engine(url)) as s:
            assert s.query(TaskMember).all() == []
    finally:
        os.environ.pop("ATLAS_DB_URL", None)
