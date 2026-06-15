# F3e — Команды иерархии (epic/checklist/member) + enqueue — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Steps use `- [ ]`.

**Goal:** Дать Atlas CLI команды для ведения иерархии Epic → Task → ChecklistItem + участники задач (TaskMember), на `clikit` (`--json` по умолчанию для ИИ-агентов), с автоматическим enqueue в outbox; расширить enqueue-hook на `pm-tasks update`/`delete`.

**Architecture:** Новые typer-приложения `pm/commands/epic.py`, `pm/commands/checklist.py`, `pm/commands/member.py`, подключённые в `cli.py`. Команды собраны на `clikit.command` + `emit_data` (json-дефолт, `--text` для человека). Каждая мутация локального стора → `outbox.enqueue(...)` (фильтр политики уже внутри). Резолв ссылок — через существующие `pm/slugs.py` (`resolve_project_ref`/`resolve_task_ref`) + локальные хелперы для эпиков.

**Tech Stack:** typer, clikit (`command`/`emit_data`), SQLAlchemy 2.x sync, pytest. Python 3.14, uv.

**Соглашения:** `_db_url()` = `ATLAS_DB_URL` или дефолт (как в `pm/commands/pm_tasks.py`); `portal_id = "atlas-local"`; enqueue best-effort (обёрнут try/except — синк не срывает локальную операцию). Образец команд — `pm/commands/pm_tasks.py`.

---

## File Structure

- **Create** `src/atlas/pm/commands/epic.py` — `epic_app` (add/list/get).
- **Create** `src/atlas/pm/commands/checklist.py` — `checklist_app` (add/list/check).
- **Create** `src/atlas/pm/commands/member.py` — `member_app` (add/list/rm).
- **Modify** `src/atlas/cli.py` — `add_typer` трёх групп.
- **Modify** `src/atlas/pm/commands/pm_tasks.py` — enqueue в `update`/`delete`.
- **Create** tests: `tests/test_epic_cli.py`, `tests/test_checklist_cli.py`, `tests/test_member_cli.py`, `tests/test_pm_tasks_enqueue_update.py`.

Ветка `feat/f3-atlas-cli-sync`. `cd <ATLAS> && uv run pytest <path> -v`.

**Общий тестовый хелпер** (повторяется в каждом тест-файле — вставлять дословно):

```python
import os
from atlas.pm.db import make_engine, make_session
from atlas.pm.models import Base, Project, ProjectStatus, ProjectType, SyncPolicy
from atlas.pm.seeds import seed_all


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
```

---

### Task 1: epic-команды (`atlas epic add/list/get`)

**Files:** Create `src/atlas/pm/commands/epic.py`; Modify `src/atlas/cli.py`; Test `tests/test_epic_cli.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_epic_cli.py`:

```python
"""F3e: atlas epic add/list/get + enqueue."""
import os

from typer.testing import CliRunner

from atlas.cli import app
from atlas.pm.db import make_engine, make_session
from atlas.pm.models import Base, Epic, Outbox, Project, ProjectStatus, ProjectType, SyncPolicy
from atlas.pm.seeds import seed_all

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
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_epic_cli.py -v`
Expected: FAIL — нет команды `epic` (exit_code != 0).

- [ ] **Step 3: Реализация `epic.py`**

Create `src/atlas/pm/commands/epic.py`:

```python
"""CLI `atlas epic ...` — эпики (вехи/спринты). На clikit (--json по умолчанию)."""
from __future__ import annotations

import os

import typer
from clikit import command, emit_data
from sqlalchemy import select

from atlas.pm.db import DEFAULT_DB_PATH, make_engine, make_session
from atlas.pm.models import Epic, Project
from atlas.pm.slugs import resolve_project_ref, slugify_text
from atlas.pm.sync import outbox as _outbox

epic_app = typer.Typer(no_args_is_help=True, help="Эпики (вехи/спринты).")
_PORTAL = "atlas-local"


def _db_url() -> str:
    return os.environ.get("ATLAS_DB_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


def _enqueue(session, op, obj, project):
    try:
        _outbox.enqueue(session, op, "epic", obj, project=project, portal_id=_PORTAL)
    except Exception:
        pass


@epic_app.command("add")
@command
def add_cmd(
    project: str = typer.Option(..., "--project", help="Project ref (slug | UUID)"),
    title: str = typer.Option(..., "--title"),
    slug: str | None = typer.Option(None, "--slug"),
    goal: str | None = typer.Option(None, "--goal"),
) -> None:
    """Создать эпик."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        proj = resolve_project_ref(session, project)
        if proj is None:
            raise typer.Exit(1)
        epic = Epic(
            project_id=proj.id, title=title,
            slug=slug or slugify_text(title) or None, goal=goal,
        )
        session.add(epic)
        session.flush()
        _enqueue(session, "create", epic, proj)
        session.commit()
        emit_data(
            {"id": epic.id, "slug": epic.slug, "title": epic.title, "status": epic.status},
            text_renderer=lambda d: print(f"✓ epic {d['slug'] or d['id']} — {d['title']}"),
        )


@epic_app.command("list")
@command
def list_cmd(
    project: str = typer.Option(..., "--project"),
) -> None:
    """Список эпиков проекта."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        proj = resolve_project_ref(session, project)
        if proj is None:
            raise typer.Exit(1)
        rows = session.execute(
            select(Epic).where(Epic.project_id == proj.id).order_by(Epic.created_at)
        ).scalars().all()
        data = [{"id": e.id, "slug": e.slug, "title": e.title, "status": e.status} for e in rows]
        emit_data(
            data,
            text_renderer=lambda items: [print(f"{i['slug'] or i['id']}: {i['title']} ({i['status']})") for i in items],
        )


@epic_app.command("get")
@command
def get_cmd(ref: str = typer.Argument(..., help="slug | UUID эпика")) -> None:
    """Карточка эпика по slug или id."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        epic = session.execute(
            select(Epic).where((Epic.slug == ref) | (Epic.id == ref))
        ).scalar_one_or_none()
        if epic is None:
            raise typer.Exit(1)
        emit_data({
            "id": epic.id, "slug": epic.slug, "title": epic.title,
            "status": epic.status, "goal": epic.goal, "project_id": epic.project_id,
            "backend_id": epic.backend_id,
        })
```

- [ ] **Step 4: Подключить в `cli.py`**

В `src/atlas/cli.py` добавить импорт и регистрацию рядом с прочими pm-командами:

```python
from .pm.commands.epic import epic_app as pm_epic_app
```
```python
app.add_typer(pm_epic_app, name="epic")  # F3e: эпики
```

- [ ] **Step 5: GREEN + полный прогон**

Run: `uv run pytest tests/test_epic_cli.py -v && uv run pytest -q`
Expected: PASS, без регрессий.

- [ ] **Step 6: Commit**

```bash
git add src/atlas/pm/commands/epic.py src/atlas/cli.py tests/test_epic_cli.py
git commit -m "feat(f3e): команды atlas epic add/list/get + enqueue"
```

---

### Task 2: checklist-команды (`atlas checklist add/list/check`)

**Files:** Create `src/atlas/pm/commands/checklist.py`; Modify `src/atlas/cli.py`; Test `tests/test_checklist_cli.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_checklist_cli.py`:

```python
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
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_checklist_cli.py -v`
Expected: FAIL — нет команды `checklist`.

- [ ] **Step 3: Реализация `checklist.py`**

Create `src/atlas/pm/commands/checklist.py`:

```python
"""CLI `atlas checklist ...` — чек-листы задач (шаги). На clikit."""
from __future__ import annotations

import os

import typer
from clikit import command, emit_data
from sqlalchemy import func, select

from atlas.pm.db import DEFAULT_DB_PATH, make_engine, make_session
from atlas.pm.models import ChecklistItem, Project, Task
from atlas.pm.slugs import resolve_task_ref
from atlas.pm.sync import outbox as _outbox

checklist_app = typer.Typer(no_args_is_help=True, help="Чек-листы задач (шаги ИИ-агента).")
_PORTAL = "atlas-local"


def _db_url() -> str:
    return os.environ.get("ATLAS_DB_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


def _enqueue(session, op, obj, project):
    try:
        _outbox.enqueue(session, op, "checklist", obj, project=project, portal_id=_PORTAL)
    except Exception:
        pass


@checklist_app.command("add")
@command
def add_cmd(
    task: str = typer.Option(..., "--task", help="Task ref (number | slug | UUID)"),
    text: str = typer.Option(..., "--text"),
) -> None:
    """Добавить пункт чек-листа к задаче."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        t = resolve_task_ref(session, task)
        if t is None:
            raise typer.Exit(1)
        next_pos = session.execute(
            select(func.count()).select_from(ChecklistItem).where(ChecklistItem.task_id == t.id)
        ).scalar_one()
        ci = ChecklistItem(task_id=t.id, text=text, position=next_pos)
        session.add(ci)
        session.flush()
        proj = session.get(Project, t.project_id)
        _enqueue(session, "create", ci, proj)
        session.commit()
        emit_data(
            {"id": ci.id, "text": ci.text, "is_done": ci.is_done, "position": ci.position},
            text_renderer=lambda d: print(f"☐ [{d['position']}] {d['text']}"),
        )


@checklist_app.command("list")
@command
def list_cmd(task: str = typer.Option(..., "--task")) -> None:
    """Список пунктов чек-листа задачи."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        t = resolve_task_ref(session, task)
        if t is None:
            raise typer.Exit(1)
        rows = session.execute(
            select(ChecklistItem).where(ChecklistItem.task_id == t.id).order_by(ChecklistItem.position)
        ).scalars().all()
        data = [{"id": c.id, "text": c.text, "is_done": c.is_done, "position": c.position} for c in rows]
        emit_data(
            data,
            text_renderer=lambda items: [print(f"{'☑' if i['is_done'] else '☐'} {i['text']}") for i in items],
        )


@checklist_app.command("check")
@command
def check_cmd(
    item_id: str = typer.Argument(..., help="UUID пункта"),
    uncheck: bool = typer.Option(False, "--uncheck", help="Снять отметку"),
) -> None:
    """Отметить пункт выполненным (или снять --uncheck)."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ci = session.get(ChecklistItem, item_id)
        if ci is None:
            raise typer.Exit(1)
        ci.is_done = 0 if uncheck else 1
        task = session.get(Task, ci.task_id)
        proj = session.get(Project, task.project_id) if task else None
        if proj is not None:
            _enqueue(session, "update", ci, proj)
        session.commit()
        emit_data(
            {"id": ci.id, "is_done": ci.is_done},
            text_renderer=lambda d: print(f"{'☑' if d['is_done'] else '☐'} {d['id']}"),
        )
```

- [ ] **Step 4: Подключить в `cli.py`**

```python
from .pm.commands.checklist import checklist_app as pm_checklist_app
```
```python
app.add_typer(pm_checklist_app, name="checklist")  # F3e: чек-листы
```

- [ ] **Step 5: GREEN + полный прогон**

Run: `uv run pytest tests/test_checklist_cli.py -v && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/atlas/pm/commands/checklist.py src/atlas/cli.py tests/test_checklist_cli.py
git commit -m "feat(f3e): команды atlas checklist add/list/check + enqueue"
```

---

### Task 3: member-команды (`atlas member add/list/rm`)

**Files:** Create `src/atlas/pm/commands/member.py`; Modify `src/atlas/cli.py`; Test `tests/test_member_cli.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_member_cli.py`:

```python
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
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_member_cli.py -v`
Expected: FAIL — нет команды `member`.

- [ ] **Step 3: Реализация `member.py`**

Create `src/atlas/pm/commands/member.py`:

```python
"""CLI `atlas member ...` — участники задачи (TaskMember: responsible/executor/watcher)."""
from __future__ import annotations

import os

import typer
from clikit import command, emit_data
from sqlalchemy import select

from atlas.pm.db import DEFAULT_DB_PATH, make_engine, make_session
from atlas.pm.models import Participant, TaskMember
from atlas.pm.slugs import resolve_task_ref

member_app = typer.Typer(no_args_is_help=True, help="Участники задачи (роли).")
_ROLES = {"responsible", "executor", "watcher"}


def _db_url() -> str:
    return os.environ.get("ATLAS_DB_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


def _participant(session, slug):
    return session.execute(
        select(Participant).where(Participant.slug == slug)
    ).scalar_one_or_none()


@member_app.command("add")
@command
def add_cmd(
    task: str = typer.Option(..., "--task"),
    participant: str = typer.Option(..., "--participant", help="participant slug"),
    role: str = typer.Option("executor", "--role", help="responsible|executor|watcher"),
) -> None:
    """Назначить участника на задачу с ролью."""
    if role not in _ROLES:
        raise typer.Exit(1)
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        t = resolve_task_ref(session, task)
        p = _participant(session, participant)
        if t is None or p is None:
            raise typer.Exit(1)
        exists = session.get(TaskMember, (t.id, p.id, role))
        if exists is None:
            session.add(TaskMember(task_id=t.id, participant_id=p.id, role=role))
            session.commit()
        emit_data(
            {"task_id": t.id, "participant": participant, "role": role},
            text_renderer=lambda d: print(f"✓ {d['participant']} → {d['role']}"),
        )


@member_app.command("list")
@command
def list_cmd(task: str = typer.Option(..., "--task")) -> None:
    """Список участников задачи."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        t = resolve_task_ref(session, task)
        if t is None:
            raise typer.Exit(1)
        rows = session.execute(
            select(TaskMember, Participant)
            .join(Participant, TaskMember.participant_id == Participant.id)
            .where(TaskMember.task_id == t.id)
        ).all()
        data = [{"participant": p.slug, "role": tm.role} for tm, p in rows]
        emit_data(
            data,
            text_renderer=lambda items: [print(f"{i['participant']}: {i['role']}") for i in items],
        )


@member_app.command("rm")
@command
def rm_cmd(
    task: str = typer.Option(..., "--task"),
    participant: str = typer.Option(..., "--participant"),
    role: str = typer.Option(..., "--role"),
) -> None:
    """Снять участника с роли на задаче."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        t = resolve_task_ref(session, task)
        p = _participant(session, participant)
        if t is None or p is None:
            raise typer.Exit(1)
        tm = session.get(TaskMember, (t.id, p.id, role))
        if tm is not None:
            session.delete(tm)
            session.commit()
        emit_data({"removed": tm is not None}, text_renderer=lambda d: print("✓ removed" if d["removed"] else "— нет такого"))
```

- [ ] **Step 4: Подключить в `cli.py`**

```python
from .pm.commands.member import member_app as pm_member_app
```
```python
app.add_typer(pm_member_app, name="member")  # F3e: участники задач
```

- [ ] **Step 5: GREEN + полный прогон**

Run: `uv run pytest tests/test_member_cli.py -v && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/atlas/pm/commands/member.py src/atlas/cli.py tests/test_member_cli.py
git commit -m "feat(f3e): команды atlas member add/list/rm (TaskMember)"
```

---

### Task 4: enqueue в `pm-tasks update`/`delete`

**Files:** Modify `src/atlas/pm/commands/pm_tasks.py`; Test `tests/test_pm_tasks_enqueue_update.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_pm_tasks_enqueue_update.py`:

```python
"""F3e: update/delete задачи кладут событие в outbox (policy full)."""
import os

from typer.testing import CliRunner

from atlas.cli import app
from atlas.pm.db import make_engine, make_session
from atlas.pm.models import Base, Outbox, Project, ProjectStatus, ProjectType, SyncPolicy, Task
from atlas.pm.seeds import seed_all

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


def test_update_enqueues(tmp_path):
    url = _prep(tmp_path)
    try:
        res = runner.invoke(app, ["pm-tasks", "update", "ACM-1", "--status", "in_progress"])
        assert res.exit_code == 0, res.stdout
        with make_session(make_engine(url)) as s:
            obs = s.query(Outbox).filter(Outbox.op == "update", Outbox.entity_kind == "task").all()
            assert len(obs) == 1
    finally:
        os.environ.pop("ATLAS_DB_URL", None)


def test_delete_enqueues(tmp_path):
    url = _prep(tmp_path)
    try:
        res = runner.invoke(app, ["pm-tasks", "delete", "ACM-1"])
        assert res.exit_code == 0, res.stdout
        with make_session(make_engine(url)) as s:
            obs = s.query(Outbox).filter(Outbox.op == "delete", Outbox.entity_kind == "task").all()
            assert len(obs) == 1
    finally:
        os.environ.pop("ATLAS_DB_URL", None)
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_pm_tasks_enqueue_update.py -v`
Expected: FAIL — update/delete ещё не enqueue'ят (0 записей).

- [ ] **Step 3: Добавить enqueue в `update_cmd` и `delete_cmd`**

В `src/atlas/pm/commands/pm_tasks.py` (импорт `_outbox` уже добавлен в F3c):

В `update_cmd`, ВНУТРИ `with make_session(...)`, ПОСЛЕ применения diffs и `_log_action(...)`, ПЕРЕД `session.commit()` (в ветке, где есть изменения) добавить:

```python
        # F3e: enqueue update в outbox (best-effort)
        try:
            _proj = session.get(Project, task.project_id)
            if _proj is not None:
                _outbox.enqueue(session, "update", "task", task, project=_proj, portal_id="atlas-local")
        except Exception:
            pass
```

В `delete_cmd`, в ветке soft-archive, ПОСЛЕ `task.archived_at = msk_now()` и `_log_action(...)`, ПЕРЕД `session.commit()` добавить:

```python
        # F3e: enqueue delete в outbox (best-effort)
        try:
            _proj = session.get(Project, task.project_id)
            if _proj is not None:
                _outbox.enqueue(session, "delete", "task", task, project=_proj, portal_id="atlas-local")
        except Exception:
            pass
```

(`Project` уже импортирован в `pm_tasks.py`.)

- [ ] **Step 4: GREEN + полный прогон**

Run: `uv run pytest tests/test_pm_tasks_enqueue_update.py -v && uv run pytest -q`
Expected: PASS, без регрессий.

- [ ] **Step 5: Commit**

```bash
git add src/atlas/pm/commands/pm_tasks.py tests/test_pm_tasks_enqueue_update.py
git commit -m "feat(f3e): enqueue в pm-tasks update/delete"
```

---

## Self-Review — покрытие спеки F3e (§10)

| Компонент | Задача |
|---|---|
| CRUD эпиков (`atlas epic`) | Task 1 |
| CRUD чек-листов (`atlas checklist`) | Task 2 |
| участники задачи (`atlas member`, TaskMember) | Task 3 |
| enqueue на update/delete задач | Task 4 |
| новые команды на `clikit` (`--json` по умолчанию) | все (command + emit_data) |

**Граница F3e:** перевод СУЩЕСТВУЮЩИХ команд (today/tasks/projects/...) на `emit_data` — НЕ здесь (отдельный cleanup-этап «единство вывода», не блокирует функциональность). F3e даёт новые команды иерархии сразу на clikit + замыкает enqueue на всех мутациях задач.

**Placeholder-скан:** код команд/тестов дословный.

**Type consistency:** `epic_app`/`checklist_app`/`member_app`; enqueue через `outbox.enqueue(session, op, entity_kind, obj, *, project, portal_id)` (контракт F3c); резолв через `resolve_project_ref`/`resolve_task_ref` (существующие в `pm/slugs.py`). `TaskMember` PK — `(task_id, participant_id, role)` (как в F3b), `session.get(TaskMember, (t.id, p.id, role))` — кортеж составного ключа.
