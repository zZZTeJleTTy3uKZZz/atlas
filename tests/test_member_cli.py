"""F3e: atlas member add/list/rm — участники задачи (TaskMember)."""
import json
import os

from typer.testing import CliRunner

from atlas.cli import app
from atlas.db import make_engine, make_session
from atlas.models import (
    Base, Outbox, Participant, Project, ProjectStatus, ProjectType, SyncPolicy,
    Task, TaskMember,
)

runner = CliRunner()


def _prep(tmp_path, *, sync_policy=None):
    url = f"sqlite:///{tmp_path / 'atlas.db'}"
    os.environ["ATLAS_DB_URL"] = url
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    with make_session(engine) as s:
        if sync_policy:
            s.add(SyncPolicy(slug="full", name="f", sync_epic=1, sync_task=1,
                             sync_checklist=1))
        t = ProjectType(slug="cp", name="Кл")
        st = ProjectStatus(slug="act", name="A", order_idx=30)
        s.add_all([t, st]); s.flush()
        p = Project(slug="acme", name="Acme", type_id=t.id, status_id=st.id,
                    priority="P2", one_line_summary="x", prefix="ACM",
                    sync_policy=sync_policy)
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


def _task_update_outbox(url):
    """Outbox-строки op=update entity_kind=task (через payload_json)."""
    with make_session(make_engine(url)) as s:
        rows = s.query(Outbox).filter(
            Outbox.op == "update", Outbox.entity_kind == "task"
        ).all()
        return [json.loads(r.payload_json) for r in rows]


def test_member_add_enqueues_task_update(tmp_path):
    """После member add смена состава должна уехать сразу: в outbox появляется
    op=update entity_kind=task для этой задачи (policy=full разрешает синк task)."""
    url, _ = _prep(tmp_path, sync_policy="full")
    try:
        res = runner.invoke(app, ["member", "add", "--task", "ACM-1", "--participant", "claude", "--role", "executor"])
        assert res.exit_code == 0, res.stdout
        events = _task_update_outbox(url)
        assert len(events) == 1, events
        assert events[0]["payload_json"]["slug"] == "ACM-1"
    finally:
        os.environ.pop("ATLAS_DB_URL", None)


def test_member_rm_enqueues_task_update(tmp_path):
    """После member rm состав тоже выгружается: ещё одно op=update task."""
    url, _ = _prep(tmp_path, sync_policy="full")
    try:
        runner.invoke(app, ["member", "add", "--task", "ACM-1", "--participant", "claude", "--role", "executor"])
        res = runner.invoke(app, ["member", "rm", "--task", "ACM-1", "--participant", "claude", "--role", "executor"])
        assert res.exit_code == 0, res.stdout
        events = _task_update_outbox(url)
        # add + rm = два update-события
        assert len(events) == 2, events
    finally:
        os.environ.pop("ATLAS_DB_URL", None)


def test_member_add_enqueue_best_effort_no_policy(tmp_path):
    """Без sync_policy enqueue зарежет policy (вернёт None) — но это best-effort:
    CLI всё равно exit_code==0 и TaskMember создан. Outbox пуст."""
    url, _ = _prep(tmp_path)  # sync_policy=None → should_sync=False
    try:
        res = runner.invoke(app, ["member", "add", "--task", "ACM-1", "--participant", "claude", "--role", "executor"])
        assert res.exit_code == 0, res.stdout
        with make_session(make_engine(url)) as s:
            assert len(s.query(TaskMember).all()) == 1
        assert _task_update_outbox(url) == []
    finally:
        os.environ.pop("ATLAS_DB_URL", None)
