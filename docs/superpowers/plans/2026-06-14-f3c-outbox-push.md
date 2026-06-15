# F3c — Outbox + Push (atlas → хаб) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Steps use `- [ ]`.

**Goal:** Реализовать исходящий синк Atlas → backend-хаб: `policy.should_sync` (потолок по `SyncPolicy`), `mapper.to_event` (сущность → `EventIn`-payload), `outbox` (enqueue/pending/mark), `push.push_pending` (отправка через `BackendClient`), CLI `atlas sync push`, и hook автоматического enqueue в командах задач.

**Architecture:** Модули в `src/atlas/pm/sync/` (одна ответственность каждый, SOLID). Локальная операция → `outbox.enqueue` (только если `policy.should_sync` для уровня сущности) → запись `Outbox` с готовым `EventIn`-payload. Команда `atlas sync push` (async, на `clikit.async_command`) читает pending, шлёт батчем на `/api/v1/events`, помечает sent. `entity_id` события = локальный id (бэк свяжет через entity_link по `source_portal_id="atlas-local"`); `backend_id` придёт при pull (F3d). Локальный стор sync (SQLAlchemy), HTTP — async.

**Tech Stack:** SQLAlchemy 2.x sync, clikit (`async_command`/`emit_data`/`HttpClient`), pytest + pytest-asyncio, pytest-httpx. Python 3.14, uv.

**Контракт события** (сверено с бэком `EventIn`): `{entity_kind, op, entity_id, payload_json: dict, source_portal_id}`. `entity_kind ∈ project|epic|task|checklist`, `op ∈ create|update|delete`.

---

## File Structure

- **Create** `src/atlas/pm/sync/policy.py` — `should_sync(session, level, project) -> bool`.
- **Create** `src/atlas/pm/sync/mapper.py` — `to_event(op, entity_kind, obj, *, portal_id) -> dict`.
- **Create** `src/atlas/pm/sync/outbox.py` — `enqueue` / `pending` / `mark_sent` / `mark_failed`.
- **Create** `src/atlas/pm/sync/push.py` — `push_pending(session, client, *, limit)`.
- **Create** `src/atlas/pm/commands/sync.py` — `sync_app` (typer) с командой `push`.
- **Modify** `src/atlas/cli.py` — `app.add_typer(sync_app, name="sync")`.
- **Modify** `src/atlas/pm/commands/pm_tasks.py` — enqueue-hook в `add`/`update`.
- **Create** tests: `tests/test_sync_policy.py`, `tests/test_sync_mapper.py`, `tests/test_sync_outbox.py`, `tests/test_sync_push.py`, `tests/test_pm_tasks_enqueue.py`.

Ветка `feat/f3-atlas-cli-sync`. `cd <ATLAS> && uv run pytest <path> -v`. Образец async-теста — `tests/test_sync_backend_client.py`. Образец команд — `pm/commands/pm_tasks.py`.

---

### Task 1: policy.should_sync

**Files:** Create `src/atlas/pm/sync/policy.py`; Test `tests/test_sync_policy.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_sync_policy.py`:

```python
"""F3c: policy.should_sync — потолок синка по SyncPolicy с дефолтом от типа."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import (
    Base, Project, ProjectStatus, ProjectType, SyncPolicy,
)
from atlas.pm.sync import policy


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _setup(s, *, type_default=None, project_policy=None):
    s.add_all([
        SyncPolicy(slug="local", name="l", sync_epic=0, sync_task=0, sync_checklist=0),
        SyncPolicy(slug="epics", name="e", sync_epic=1, sync_task=0, sync_checklist=0),
        SyncPolicy(slug="full", name="f", sync_epic=1, sync_task=1, sync_checklist=1),
    ])
    t = ProjectType(slug="t", name="t", default_sync_policy=type_default)
    st = ProjectStatus(slug="a", name="a", order_idx=1)
    s.add_all([t, st]); s.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2",
                one_line_summary="x", sync_policy=project_policy)
    s.add(p); s.flush()
    return p


def test_project_policy_wins(session):
    p = _setup(session, type_default="local", project_policy="full")
    assert policy.should_sync(session, "task", p) is True
    assert policy.should_sync(session, "checklist", p) is True


def test_falls_back_to_type_default(session):
    p = _setup(session, type_default="epics", project_policy=None)
    assert policy.should_sync(session, "epic", p) is True
    assert policy.should_sync(session, "task", p) is False


def test_no_policy_no_sync(session):
    p = _setup(session, type_default=None, project_policy=None)
    assert policy.should_sync(session, "epic", p) is False


def test_unknown_level(session):
    p = _setup(session, type_default="full", project_policy=None)
    assert policy.should_sync(session, "bogus", p) is False
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_sync_policy.py -v`
Expected: FAIL — `ImportError: cannot import name 'policy'`.

- [ ] **Step 3: Реализация**

Create `src/atlas/pm/sync/policy.py`:

```python
"""Политика-потолок синка: до какого уровня иерархии выгружать наружу.

DIP: движок outbox спрашивает should_sync(level, project), не зная типов
проектов. Резолв: Project.sync_policy → иначе ProjectType.default_sync_policy.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from atlas.pm.models import ProjectType, SyncPolicy

# project и epic — верхний уровень (поле sync_epic); task/checklist — свои.
_LEVEL_FIELD = {
    "project": "sync_epic",
    "epic": "sync_epic",
    "task": "sync_task",
    "checklist": "sync_checklist",
}


def _resolve_policy_slug(session: Session, project) -> str | None:
    if project.sync_policy:
        return project.sync_policy
    pt = session.get(ProjectType, project.type_id)
    return pt.default_sync_policy if pt is not None else None


def should_sync(session: Session, level: str, project) -> bool:
    """Синкать ли сущность уровня ``level`` проекта ``project`` наружу."""
    field = _LEVEL_FIELD.get(level)
    if field is None:
        return False
    slug = _resolve_policy_slug(session, project)
    if not slug:
        return False
    sp = session.get(SyncPolicy, slug)
    if sp is None:
        return False
    return getattr(sp, field) == 1


__all__ = ["should_sync"]
```

- [ ] **Step 4: GREEN**

Run: `uv run pytest tests/test_sync_policy.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/atlas/pm/sync/policy.py tests/test_sync_policy.py
git commit -m "feat(f3c): policy.should_sync — потолок синка (DIP)"
```

---

### Task 2: mapper.to_event

**Files:** Create `src/atlas/pm/sync/mapper.py`; Test `tests/test_sync_mapper.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_sync_mapper.py`:

```python
"""F3c: mapper.to_event — ORM-сущность → EventIn-dict."""
from types import SimpleNamespace

from atlas.pm.sync import mapper


def test_task_event_uses_local_id_when_no_backend():
    task = SimpleNamespace(
        id="loc-1", backend_id=None, slug="acme-t1", title="T", status="todo",
        priority="P2", cpp_description="ц", due_date=None,
    )
    ev = mapper.to_event("create", "task", task, portal_id="atlas-local")
    assert ev["entity_kind"] == "task"
    assert ev["op"] == "create"
    assert ev["entity_id"] == "loc-1"
    assert ev["source_portal_id"] == "atlas-local"
    assert ev["payload_json"]["title"] == "T"


def test_event_prefers_backend_id():
    epic = SimpleNamespace(id="loc", backend_id="be-9", slug="e", title="E", status="active")
    ev = mapper.to_event("update", "epic", epic, portal_id="atlas-local")
    assert ev["entity_id"] == "be-9"
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_sync_mapper.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Реализация**

Create `src/atlas/pm/sync/mapper.py`:

```python
"""ORM-сущность Atlas → EventIn-payload для backend-хаба (F3c).

Контракт EventIn бэка: {entity_kind, op, entity_id, payload_json, source_portal_id}.
entity_id = backend_id если есть, иначе локальный id (бэк свяжет через
entity_link по source_portal_id). payload_json — ключевые поля сущности.
"""
from __future__ import annotations

from typing import Any


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def _project_payload(p: Any) -> dict:
    return {"slug": p.slug, "name": p.name, "backend_id": p.backend_id}


def _epic_payload(e: Any) -> dict:
    return {
        "slug": e.slug, "title": e.title, "status": e.status,
        "backend_id": e.backend_id,
    }


def _task_payload(t: Any) -> dict:
    return {
        "slug": t.slug, "title": t.title, "status": t.status,
        "priority": t.priority, "cpp": t.cpp_description,
        "due_date": _iso(t.due_date), "backend_id": t.backend_id,
    }


def _checklist_payload(c: Any) -> dict:
    return {
        "text": c.text, "is_done": c.is_done, "position": c.position,
        "backend_id": c.backend_id,
    }


_PAYLOAD = {
    "project": _project_payload,
    "epic": _epic_payload,
    "task": _task_payload,
    "checklist": _checklist_payload,
}


def to_event(op: str, entity_kind: str, obj: Any, *, portal_id: str) -> dict:
    """Построить EventIn-dict из ORM-сущности."""
    build = _PAYLOAD[entity_kind]
    backend_id = getattr(obj, "backend_id", None)
    return {
        "entity_kind": entity_kind,
        "op": op,
        "entity_id": backend_id or obj.id,
        "payload_json": build(obj),
        "source_portal_id": portal_id,
    }


__all__ = ["to_event"]
```

- [ ] **Step 4: GREEN**

Run: `uv run pytest tests/test_sync_mapper.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/atlas/pm/sync/mapper.py tests/test_sync_mapper.py
git commit -m "feat(f3c): mapper.to_event — сущность → EventIn-payload"
```

---

### Task 3: outbox (enqueue/pending/mark)

**Files:** Create `src/atlas/pm/sync/outbox.py`; Test `tests/test_sync_outbox.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_sync_outbox.py`:

```python
"""F3c: outbox.enqueue консультируется с policy; pending/mark работают."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import (
    Base, Outbox, Project, ProjectStatus, ProjectType, SyncPolicy, Task,
)
from atlas.pm.sync import outbox


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _project(s, policy_slug):
    s.add_all([
        SyncPolicy(slug="local", name="l", sync_epic=0, sync_task=0, sync_checklist=0),
        SyncPolicy(slug="full", name="f", sync_epic=1, sync_task=1, sync_checklist=1),
    ])
    t = ProjectType(slug="t", name="t")
    st = ProjectStatus(slug="a", name="a", order_idx=1)
    s.add_all([t, st]); s.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2",
                one_line_summary="x", sync_policy=policy_slug)
    s.add(p); s.flush()
    return p


def _task(s, p):
    t = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2", slug="p-t1")
    s.add(t); s.flush()
    return t


def test_enqueue_when_policy_allows(session):
    p = _project(session, "full")
    t = _task(session, p)
    ob = outbox.enqueue(session, "create", "task", t, project=p, portal_id="atlas-local")
    session.commit()
    assert ob is not None
    payload = json.loads(ob.payload_json)
    assert payload["entity_kind"] == "task"
    assert payload["source_portal_id"] == "atlas-local"


def test_enqueue_skipped_when_policy_forbids(session):
    p = _project(session, "local")
    t = _task(session, p)
    ob = outbox.enqueue(session, "create", "task", t, project=p, portal_id="atlas-local")
    session.commit()
    assert ob is None
    assert outbox.pending(session) == []


def test_pending_and_mark(session):
    p = _project(session, "full")
    t = _task(session, p)
    ob = outbox.enqueue(session, "create", "task", t, project=p, portal_id="atlas-local")
    session.commit()
    pend = outbox.pending(session)
    assert len(pend) == 1
    outbox.mark_sent(session, ob.id)
    session.commit()
    assert outbox.pending(session) == []
    assert session.get(Outbox, ob.id).status == "sent"
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_sync_outbox.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Реализация**

Create `src/atlas/pm/sync/outbox.py`:

```python
"""Локальная очередь исходящих операций (Atlas → хаб).

enqueue консультируется с policy.should_sync (потолок проекта) и кладёт
готовый EventIn-payload в Outbox. push (F3c push.py) читает pending и шлёт.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm._time import msk_now
from atlas.pm.models import Outbox
from atlas.pm.sync import mapper, policy


def enqueue(
    session: Session, op: str, entity_kind: str, obj, *, project, portal_id: str
) -> Outbox | None:
    """Поставить операцию в outbox, ЕСЛИ политика проекта разрешает уровень.

    Возвращает созданный Outbox или None (если синк уровня запрещён политикой).
    """
    if not policy.should_sync(session, entity_kind, project):
        return None
    event = mapper.to_event(op, entity_kind, obj, portal_id=portal_id)
    ob = Outbox(
        op=op,
        entity_kind=entity_kind,
        entity_id=obj.id,
        payload_json=json.dumps(event, ensure_ascii=False, default=str),
    )
    session.add(ob)
    return ob


def pending(session: Session, *, limit: int = 100) -> list[Outbox]:
    """Невыгруженные записи (status=pending), старые первыми."""
    stmt = (
        select(Outbox)
        .where(Outbox.status == "pending")
        .order_by(Outbox.created_at)
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def mark_sent(session: Session, outbox_id: str) -> None:
    ob = session.get(Outbox, outbox_id)
    if ob is not None:
        ob.status = "sent"
        ob.sent_at = msk_now()


def mark_failed(session: Session, outbox_id: str, error: str) -> None:
    ob = session.get(Outbox, outbox_id)
    if ob is not None:
        ob.status = "failed"
        ob.attempts = (ob.attempts or 0) + 1
        ob.last_error = str(error)[:500]


__all__ = ["enqueue", "pending", "mark_sent", "mark_failed"]
```

- [ ] **Step 4: GREEN**

Run: `uv run pytest tests/test_sync_outbox.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/atlas/pm/sync/outbox.py tests/test_sync_outbox.py
git commit -m "feat(f3c): outbox enqueue/pending/mark (с policy-фильтром)"
```

---

### Task 4: push.push_pending + CLI `atlas sync push`

**Files:** Create `src/atlas/pm/sync/push.py`, `src/atlas/pm/commands/sync.py`; Modify `src/atlas/cli.py`; Test `tests/test_sync_push.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_sync_push.py`:

```python
"""F3c: push_pending шлёт pending-события и помечает sent."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import (
    Base, Project, ProjectStatus, ProjectType, SyncPolicy, Task,
)
from atlas.pm.sync import outbox, push


class _FakeClient:
    def __init__(self):
        self.sent = []

    async def push_events(self, events):
        self.sent.append(events)
        return {"accepted": len(events)}

    async def aclose(self):
        pass


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _task_in_outbox(s):
    s.add(SyncPolicy(slug="full", name="f", sync_epic=1, sync_task=1, sync_checklist=1))
    t = ProjectType(slug="t", name="t"); st = ProjectStatus(slug="a", name="a", order_idx=1)
    s.add_all([t, st]); s.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2",
                one_line_summary="x", sync_policy="full")
    s.add(p); s.flush()
    task = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2", slug="p-t1")
    s.add(task); s.flush()
    outbox.enqueue(s, "create", "task", task, project=p, portal_id="atlas-local")
    s.commit()


async def test_push_pending_sends_and_marks(session):
    _task_in_outbox(session)
    client = _FakeClient()
    result = await push.push_pending(session, client)
    assert result["sent"] == 1
    assert len(client.sent) == 1
    assert client.sent[0][0]["entity_kind"] == "task"
    assert outbox.pending(session) == []


async def test_push_pending_empty(session):
    client = _FakeClient()
    result = await push.push_pending(session, client)
    assert result["sent"] == 0
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_sync_push.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Реализация push.py**

Create `src/atlas/pm/sync/push.py`:

```python
"""Отправка pending-outbox на backend-хаб (Atlas → /events)."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from atlas.pm.sync import outbox


async def push_pending(session: Session, client, *, limit: int = 100) -> dict:
    """Выгрузить pending-события батчем; пометить sent. → {sent: N}.

    ``client`` — объект с async ``push_events(list[dict])`` (BackendClient).
    """
    items = outbox.pending(session, limit=limit)
    if not items:
        return {"sent": 0}
    events = [json.loads(o.payload_json) for o in items]
    await client.push_events(events)
    for o in items:
        outbox.mark_sent(session, o.id)
    session.commit()
    return {"sent": len(items)}


__all__ = ["push_pending"]
```

- [ ] **Step 4: GREEN (push)**

Run: `uv run pytest tests/test_sync_push.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: CLI команда `atlas sync push`**

Create `src/atlas/pm/commands/sync.py`:

```python
"""CLI-команды `atlas sync ...` — синхронизация с backend-хабом (F3c)."""
from __future__ import annotations

import os

import typer
from clikit import async_command, emit_data

from atlas.appconfig import load_config
from atlas.pm.db import DEFAULT_DB_PATH, make_engine, make_session
from atlas.pm.sync import push as push_mod
from atlas.pm.sync.backend_client import BackendClient

sync_app = typer.Typer(no_args_is_help=True, help="Синхронизация Atlas ↔ backend-хаб.")


def _db_url() -> str:
    return os.environ.get("ATLAS_DB_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


@sync_app.command("push")
@async_command
async def push_cmd() -> None:
    """Выгрузить pending-операции из локального outbox на хаб."""
    cfg = load_config()
    client = BackendClient(cfg.base_url, cfg.api_key)
    engine = make_engine(_db_url())
    try:
        with make_session(engine) as session:
            result = await push_mod.push_pending(session, client)
    finally:
        await client.aclose()
    emit_data(result, text_renderer=lambda r: print(f"sent: {r['sent']}"))
```

- [ ] **Step 6: Подключить группу в cli.py**

В `src/atlas/cli.py` добавить импорт рядом с прочими pm-командами:

```python
from .pm.commands.sync import sync_app as pm_sync_app
```

И регистрацию рядом с прочими `add_typer`:

```python
app.add_typer(pm_sync_app, name="sync")  # F3c: синхронизация с хабом
```

- [ ] **Step 7: Тест CLI (smoke) + полный прогон**

Добавить в `tests/test_sync_push.py`:

```python
def test_sync_push_in_cli_help():
    from typer.testing import CliRunner
    from atlas.cli import app
    result = CliRunner().invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "push" in result.stdout
```

Run: `uv run pytest tests/test_sync_push.py -v && uv run pytest -q`
Expected: PASS, без регрессий.

- [ ] **Step 8: Commit**

```bash
git add src/atlas/pm/sync/push.py src/atlas/pm/commands/sync.py src/atlas/cli.py tests/test_sync_push.py
git commit -m "feat(f3c): push_pending + CLI atlas sync push"
```

---

### Task 5: enqueue-hook в командах задач

**Files:** Modify `src/atlas/pm/commands/pm_tasks.py`; Test `tests/test_pm_tasks_enqueue.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_pm_tasks_enqueue.py`:

```python
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
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_pm_tasks_enqueue.py -v`
Expected: FAIL — outbox пуст (хука ещё нет), `assert len(obs) == 1`.

- [ ] **Step 3: Добавить enqueue-hook в `add_cmd`**

В `src/atlas/pm/commands/pm_tasks.py`:

В импорты добавить:

```python
from atlas.pm.sync import outbox as _outbox
```

В функции `add_cmd`, ВНУТРИ блока `with make_session(engine) as session:`, ПОСЛЕ `_log_action(...)` и ПЕРЕД `session.commit()`, вставить:

```python
        # F3c: поставить в outbox для синка наружу (если политика проекта разрешает)
        try:
            _portal_id = "atlas-local"
            _outbox.enqueue(
                session, "create", "task", task, project=proj, portal_id=_portal_id,
            )
        except Exception:
            # синк — best-effort; падение enqueue не должно срывать создание задачи
            pass
```

(`task` и `proj` уже в области видимости — `task` создан выше, `proj` = `_resolve_project_or_die`.)

- [ ] **Step 4: GREEN**

Run: `uv run pytest tests/test_pm_tasks_enqueue.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Полный прогон + commit**

Run: `uv run pytest -q`
Expected: всё PASS, без регрессий.

```bash
git add src/atlas/pm/commands/pm_tasks.py tests/test_pm_tasks_enqueue.py
git commit -m "feat(f3c): enqueue-hook в pm-tasks add (операция → outbox)"
```

---

## Self-Review — покрытие спеки F3c (§5)

| Компонент спеки | Задача |
|---|---|
| `policy.py` `should_sync` (DIP) | Task 1 |
| `mapper.py` `to_event` (сущность → EventIn) | Task 2 |
| `outbox.py` enqueue/pending/mark (policy-фильтр) | Task 3 |
| `push.py` push_pending + CLI `atlas sync push` | Task 4 |
| автоматический enqueue на локальной операции | Task 5 (hook в pm-tasks add) |

**Граница F3c:** только исходящий путь (push). Pull/long-poll — F3d. Перевод остальных команд на `clikit`/`emit_data` и enqueue в update/delete/epic/checklist — F3e. `backend_id`-резолв из ответа не делаем (бэк `/events` его не возвращает — придёт при pull).

**Контракт согласован с бэком:** payload = `EventIn{entity_kind, op, entity_id, payload_json, source_portal_id}`; `entity_id` = backend_id||локальный id; `source_portal_id="atlas-local"`.

**Placeholder-скан:** весь код модулей/тестов дословный.

**Type consistency:** `policy.should_sync(session, level, project)`; `mapper.to_event(op, entity_kind, obj, *, portal_id)`; `outbox.enqueue(session, op, entity_kind, obj, *, project, portal_id)`; `push.push_pending(session, client, *, limit)`. Уровни: `project|epic|task|checklist`. Имена согласованы между тестами и реализацией во всех задачах.
