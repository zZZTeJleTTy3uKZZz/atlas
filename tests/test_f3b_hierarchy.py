"""F3b: Epic/ChecklistItem/TaskMember и связь Task.epic_id."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.models import (
    Base, ChecklistItem, Epic, Participant, Project, ProjectStatus,
    ProjectType, Task, TaskMember,
)


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


def test_epic_task_checklist_member(session):
    p = _project(session)
    epic = Epic(slug="acme-e1", project_id=p.id, title="Эпик 1")
    session.add(epic); session.flush()
    task = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2",
                epic_id=epic.id)
    session.add(task); session.flush()
    ci = ChecklistItem(task_id=task.id, text="шаг 1", position=0)
    agent = Participant(kind="ai_agent", slug="claude", name="Claude")
    session.add(agent); session.flush()
    tm = TaskMember(task_id=task.id, participant_id=agent.id, role="executor")
    session.add_all([ci, tm]); session.commit()
    assert session.get(Task, task.id).epic_id == epic.id
    assert session.get(ChecklistItem, ci.id).is_done == 0


def test_task_member_role_constraint(session):
    from sqlalchemy.exc import IntegrityError
    p = _project(session)
    task = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2")
    ag = Participant(kind="ai_agent", slug="c", name="C")
    session.add_all([task, ag]); session.flush()
    session.add(TaskMember(task_id=task.id, participant_id=ag.id, role="boss"))
    with pytest.raises(IntegrityError):
        session.commit()
