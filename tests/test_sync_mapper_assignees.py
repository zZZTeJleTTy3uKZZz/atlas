"""PART A: резолв ответственных/исполнителей задачи → participant-slug'и.

mapper.assignee_slugs(session, task) собирает slug'и из TaskMember задачи
(responsible/executor — НЕ watcher: наблюдатель не исполнитель) плюс
denormalized Task.assignee_id (главный исполнитель из `--assignee`).
Ноль хардкода имён: всё через Participant.slug.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.models import (
    Base, Participant, Project, ProjectStatus, ProjectType, Task, TaskMember,
)
from atlas.sync import mapper


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _project(s):
    t = ProjectType(slug="client-project", name="Кл")
    st = ProjectStatus(slug="active", name="A", order_idx=1)
    s.add_all([t, st]); s.flush()
    p = Project(slug="acme", name="Acme", type_id=t.id, status_id=st.id,
                priority="P2", one_line_summary="x")
    s.add(p); s.flush()
    return p


def _task(s, project, **over):
    base = dict(project_id=project.id, title="T", cpp_description="ц", priority="P2")
    base.update(over)
    t = Task(**base)
    s.add(t); s.flush()
    return t


def test_assignee_slugs_from_denormalized_assignee_id(session):
    """`task create --assignee dmitry` пишет только Task.assignee_id — slug
    должен доехать даже без строк TaskMember."""
    p = _project(session)
    dm = Participant(kind="human", slug="dmitry", name="Дмитрий")
    session.add(dm); session.flush()
    task = _task(session, p, assignee_id=dm.id)
    assert mapper.assignee_slugs(session, task) == ["dmitry"]


def test_assignee_slugs_from_task_members_responsible_and_executor(session):
    """`member add --role responsible|executor` → оба попадают в assignee_slugs."""
    p = _project(session)
    dm = Participant(kind="human", slug="dmitry", name="Дмитрий")
    cl = Participant(kind="ai_agent", slug="claude", name="Claude")
    session.add_all([dm, cl]); session.flush()
    task = _task(session, p)
    session.add_all([
        TaskMember(task_id=task.id, participant_id=dm.id, role="responsible"),
        TaskMember(task_id=task.id, participant_id=cl.id, role="executor"),
    ])
    session.flush()
    assert set(mapper.assignee_slugs(session, task)) == {"dmitry", "claude"}


def test_assignee_slugs_excludes_watcher(session):
    """Наблюдатель (watcher) — не исполнитель, в assignee_slugs не входит."""
    p = _project(session)
    dm = Participant(kind="human", slug="dmitry", name="Дмитрий")
    w = Participant(kind="human", slug="observer", name="Наблюдатель")
    session.add_all([dm, w]); session.flush()
    task = _task(session, p)
    session.add_all([
        TaskMember(task_id=task.id, participant_id=dm.id, role="responsible"),
        TaskMember(task_id=task.id, participant_id=w.id, role="watcher"),
    ])
    session.flush()
    assert mapper.assignee_slugs(session, task) == ["dmitry"]


def test_assignee_slugs_dedups_denormalized_and_member(session):
    """Если dmitry и в assignee_id, и в TaskMember(responsible) — slug один раз."""
    p = _project(session)
    dm = Participant(kind="human", slug="dmitry", name="Дмитрий")
    session.add(dm); session.flush()
    task = _task(session, p, assignee_id=dm.id)
    session.add(TaskMember(task_id=task.id, participant_id=dm.id, role="responsible"))
    session.flush()
    assert mapper.assignee_slugs(session, task) == ["dmitry"]


def test_assignee_slugs_empty_when_no_assignee(session):
    p = _project(session)
    task = _task(session, p)
    assert mapper.assignee_slugs(session, task) == []


# --------------------------------------------------------------------------- #
# mapper.assignees — slug + role (НЕ плоский список): responsible vs executor #
# --------------------------------------------------------------------------- #


def test_assignees_from_denormalized_assignee_id_is_responsible(session):
    """Task.assignee_id — «главный исполнитель», его роль responsible."""
    p = _project(session)
    dm = Participant(kind="human", slug="dmitry", name="Дмитрий")
    session.add(dm); session.flush()
    task = _task(session, p, assignee_id=dm.id)
    assert mapper.assignees(session, task) == [{"slug": "dmitry", "role": "responsible"}]


def test_assignees_keeps_responsible_and_executor_roles_distinct(session):
    """member add responsible|executor → роли сохранены раздельно (не схлопнуты)."""
    p = _project(session)
    dm = Participant(kind="human", slug="dmitry", name="Дмитрий")
    cl = Participant(kind="ai_agent", slug="claude", name="Claude")
    session.add_all([dm, cl]); session.flush()
    task = _task(session, p)
    session.add_all([
        TaskMember(task_id=task.id, participant_id=dm.id, role="responsible"),
        TaskMember(task_id=task.id, participant_id=cl.id, role="executor"),
    ])
    session.flush()
    assert {(a["slug"], a["role"]) for a in mapper.assignees(session, task)} == {
        ("dmitry", "responsible"), ("claude", "executor"),
    }


def test_assignees_excludes_watcher(session):
    """Наблюдатель (watcher) — не исполнитель, в assignees не входит."""
    p = _project(session)
    dm = Participant(kind="human", slug="dmitry", name="Дмитрий")
    w = Participant(kind="human", slug="observer", name="Наблюдатель")
    session.add_all([dm, w]); session.flush()
    task = _task(session, p)
    session.add_all([
        TaskMember(task_id=task.id, participant_id=dm.id, role="responsible"),
        TaskMember(task_id=task.id, participant_id=w.id, role="watcher"),
    ])
    session.flush()
    assert mapper.assignees(session, task) == [{"slug": "dmitry", "role": "responsible"}]


def test_assignees_dedups_slug_with_responsible_priority(session):
    """dmitry в assignee_id (responsible) и в TaskMember(executor) — один раз,
    с приоритетом responsible (источник assignee_id первый и побеждает)."""
    p = _project(session)
    dm = Participant(kind="human", slug="dmitry", name="Дмитрий")
    session.add(dm); session.flush()
    task = _task(session, p, assignee_id=dm.id)
    session.add(TaskMember(task_id=task.id, participant_id=dm.id, role="executor"))
    session.flush()
    assert mapper.assignees(session, task) == [{"slug": "dmitry", "role": "responsible"}]


def test_assignees_empty_when_no_assignee(session):
    p = _project(session)
    task = _task(session, p)
    assert mapper.assignees(session, task) == []
