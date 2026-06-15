# F3d — Pull + Long-poll (хаб → atlas) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Steps use `- [ ]`.

**Goal:** Реализовать входящий синк хаб → Atlas: применение событий локально по `backend_id` (`apply_event`: upsert/delete), цикл `pull_once` поверх long-poll (`BackendClient.poll_events`) с продвижением `SyncCursor`, и CLI `atlas sync pull` / `atlas sync watch`.

**Architecture:** `apply.py` — чистое применение одного события к локальному стору (резолв по `backend_id`, идемпотентный upsert; create best-effort с резолвом родителя; delete = soft `archived_at`). `cursor.py` — get/set `SyncCursor` по каналу. `pull.py` — `pull_once`: `poll_events(since=cursor)` → `apply_event` каждого → продвинуть курсор. CLI: `pull` (один цикл), `watch` (бесконечный, с обработкой Ctrl+C). Локальный стор sync, HTTP async.

**Tech Stack:** SQLAlchemy 2.x sync, clikit (`async_command`/`emit_data`), pytest + pytest-asyncio. Python 3.14, uv.

**Контракт входящего события** (симметричен `mapper.to_event` F3c; при выкате согласуется с реальным core-DomainEvent): `{entity_kind, op, entity_id (=backend_id в ядре), payload_json: dict, source_portal_id}`. Идемпотентность — по `backend_id == entity_id`.

---

## File Structure

- **Create** `src/atlas/pm/sync/cursor.py` — `get_cursor` / `set_cursor`.
- **Create** `src/atlas/pm/sync/apply.py` — `apply_event(session, event)`.
- **Create** `src/atlas/pm/sync/pull.py` — `pull_once(session, client, *, channel, timeout)`.
- **Modify** `src/atlas/pm/commands/sync.py` — добавить команды `pull` и `watch`.
- **Create** tests: `tests/test_sync_cursor.py`, `tests/test_sync_apply.py`, `tests/test_sync_pull.py`.

Ветка `feat/f3-atlas-cli-sync`. `cd <ATLAS> && uv run pytest <path> -v`.

---

### Task 1: cursor get/set

**Files:** Create `src/atlas/pm/sync/cursor.py`; Test `tests/test_sync_cursor.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_sync_cursor.py`:

```python
"""F3d: SyncCursor get/set по каналу."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import Base
from atlas.pm.sync import cursor


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_get_none_initially(session):
    assert cursor.get_cursor(session, "atlas") is None


def test_set_then_get(session):
    cursor.set_cursor(session, "atlas", "2026-06-14T10:00:00")
    session.commit()
    assert cursor.get_cursor(session, "atlas") == "2026-06-14T10:00:00"


def test_set_overwrites(session):
    cursor.set_cursor(session, "atlas", "2026-06-14T10:00:00")
    cursor.set_cursor(session, "atlas", "2026-06-14T11:00:00")
    session.commit()
    assert cursor.get_cursor(session, "atlas") == "2026-06-14T11:00:00"
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_sync_cursor.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Реализация**

Create `src/atlas/pm/sync/cursor.py`:

```python
"""Курсор pull-канала (SyncCursor): ISO occurred_at последнего применённого."""
from __future__ import annotations

from sqlalchemy.orm import Session

from atlas.pm.models import SyncCursor


def get_cursor(session: Session, channel: str) -> str | None:
    sc = session.get(SyncCursor, channel)
    return sc.cursor if sc is not None else None


def set_cursor(session: Session, channel: str, value: str | None) -> None:
    sc = session.get(SyncCursor, channel)
    if sc is None:
        sc = SyncCursor(channel=channel, cursor=value)
        session.add(sc)
    else:
        sc.cursor = value


__all__ = ["get_cursor", "set_cursor"]
```

- [ ] **Step 4: GREEN**

Run: `uv run pytest tests/test_sync_cursor.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/atlas/pm/sync/cursor.py tests/test_sync_cursor.py
git commit -m "feat(f3d): SyncCursor get/set по каналу"
```

---

### Task 2: apply_event (upsert/delete по backend_id)

**Files:** Create `src/atlas/pm/sync/apply.py`; Test `tests/test_sync_apply.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_sync_apply.py`:

```python
"""F3d: apply_event — идемпотентный upsert/delete по backend_id."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from atlas.pm.models import (
    Base, Project, ProjectStatus, ProjectType, Task,
)
from atlas.pm.sync import apply


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _project(s, backend_id="proj-be"):
    t = ProjectType(slug="t", name="t"); st = ProjectStatus(slug="a", name="a", order_idx=1)
    s.add_all([t, st]); s.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2",
                one_line_summary="x", backend_id=backend_id)
    s.add(p); s.flush()
    return p


def test_update_existing_task_by_backend_id(session):
    p = _project(session)
    task = Task(project_id=p.id, title="old", cpp_description="ц", priority="P2", backend_id="task-be")
    session.add(task); session.commit()
    ev = {"entity_kind": "task", "op": "update", "entity_id": "task-be",
          "payload_json": {"title": "new", "status": "done"}}
    res = apply.apply_event(session, ev)
    session.commit()
    assert res["updated"] == "task"
    got = session.execute(select(Task).where(Task.backend_id == "task-be")).scalar_one()
    assert got.title == "new"
    assert got.status == "done"


def test_create_task_when_project_resolved(session):
    _project(session, backend_id="proj-be")
    ev = {"entity_kind": "task", "op": "create", "entity_id": "task-be2",
          "payload_json": {"title": "T", "project_backend_id": "proj-be", "cpp": "ЦКП"}}
    res = apply.apply_event(session, ev)
    session.commit()
    assert res["created"] == "task"
    got = session.execute(select(Task).where(Task.backend_id == "task-be2")).scalar_one()
    assert got.title == "T"


def test_create_skipped_without_project(session):
    ev = {"entity_kind": "task", "op": "create", "entity_id": "x",
          "payload_json": {"title": "T"}}
    res = apply.apply_event(session, ev)
    assert "skipped" in res


def test_idempotent_update_twice(session):
    p = _project(session)
    task = Task(project_id=p.id, title="a", cpp_description="ц", priority="P2", backend_id="be")
    session.add(task); session.commit()
    ev = {"entity_kind": "task", "op": "update", "entity_id": "be",
          "payload_json": {"status": "done"}}
    apply.apply_event(session, ev); session.commit()
    apply.apply_event(session, ev); session.commit()
    rows = session.execute(select(Task).where(Task.backend_id == "be")).scalars().all()
    assert len(rows) == 1  # без дублей


def test_delete_soft_archives(session):
    p = _project(session)
    task = Task(project_id=p.id, title="a", cpp_description="ц", priority="P2", backend_id="be")
    session.add(task); session.commit()
    ev = {"entity_kind": "task", "op": "delete", "entity_id": "be", "payload_json": {}}
    res = apply.apply_event(session, ev); session.commit()
    assert res["deleted"] == "task"
    got = session.execute(select(Task).where(Task.backend_id == "be")).scalar_one()
    assert got.archived_at is not None


def test_unknown_kind_skipped(session):
    ev = {"entity_kind": "widget", "op": "update", "entity_id": "x", "payload_json": {}}
    assert "skipped" in apply.apply_event(session, ev)
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_sync_apply.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Реализация**

Create `src/atlas/pm/sync/apply.py`:

```python
"""Применение входящего события (хаб → Atlas) к локальному стору (F3d).

Идемпотентно по backend_id: update существующих, create best-effort (с
резолвом родителя по backend_id/slug), delete = soft archived_at. Неизвестные
сущности/без родителя — skip (не плодим кривые записи).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.pm._time import msk_now
from atlas.pm.models import ChecklistItem, Epic, Project, Task


def _by_backend(session: Session, model, backend_id: str):
    return session.execute(
        select(model).where(model.backend_id == backend_id)
    ).scalar_one_or_none()


def _resolve_project(session: Session, payload: dict) -> Project | None:
    pbid = payload.get("project_backend_id")
    if pbid:
        p = _by_backend(session, Project, pbid)
        if p is not None:
            return p
    pslug = payload.get("project_slug")
    if pslug:
        return session.execute(
            select(Project).where(Project.slug == pslug)
        ).scalar_one_or_none()
    return None


def _upsert_task(session: Session, bid: str, payload: dict) -> dict:
    task = _by_backend(session, Task, bid)
    if task is None:
        proj = _resolve_project(session, payload)
        if proj is None:
            return {"skipped": "no_project"}
        task = Task(
            backend_id=bid, project_id=proj.id,
            title=payload.get("title") or "(no title)",
            cpp_description=payload.get("cpp") or "—",
            priority=payload.get("priority") or "P2",
            status=payload.get("status") or "backlog",
            slug=payload.get("slug"),
        )
        session.add(task)
        return {"created": "task"}
    for key in ("title", "status", "priority"):
        if payload.get(key) is not None:
            setattr(task, "cpp_description" if key == "cpp" else key, payload[key])
    if payload.get("cpp"):
        task.cpp_description = payload["cpp"]
    return {"updated": "task"}


def _upsert_epic(session: Session, bid: str, payload: dict) -> dict:
    epic = _by_backend(session, Epic, bid)
    if epic is None:
        proj = _resolve_project(session, payload)
        if proj is None:
            return {"skipped": "no_project"}
        epic = Epic(
            backend_id=bid, project_id=proj.id,
            title=payload.get("title") or "(epic)",
            status=payload.get("status") or "active",
            slug=payload.get("slug"),
        )
        session.add(epic)
        return {"created": "epic"}
    if payload.get("title") is not None:
        epic.title = payload["title"]
    if payload.get("status") is not None:
        epic.status = payload["status"]
    return {"updated": "epic"}


def _upsert_checklist(session: Session, bid: str, payload: dict) -> dict:
    ci = _by_backend(session, ChecklistItem, bid)
    if ci is None:
        tbid = payload.get("task_backend_id")
        task = _by_backend(session, Task, tbid) if tbid else None
        if task is None:
            return {"skipped": "no_task"}
        ci = ChecklistItem(
            backend_id=bid, task_id=task.id,
            text=payload.get("text") or "",
            is_done=int(payload.get("is_done") or 0),
            position=int(payload.get("position") or 0),
        )
        session.add(ci)
        return {"created": "checklist"}
    if payload.get("text") is not None:
        ci.text = payload["text"]
    if payload.get("is_done") is not None:
        ci.is_done = int(payload["is_done"])
    return {"updated": "checklist"}


def _delete(session: Session, kind: str, bid: str) -> dict:
    model = {"task": Task, "epic": Epic, "checklist": ChecklistItem}.get(kind)
    if model is None:
        return {"skipped": f"kind:{kind}"}
    obj = _by_backend(session, model, bid)
    if obj is None:
        return {"skipped": "not_found"}
    if hasattr(obj, "archived_at"):
        obj.archived_at = msk_now()
    else:
        session.delete(obj)
    return {"deleted": kind}


_UPSERT = {"task": _upsert_task, "epic": _upsert_epic, "checklist": _upsert_checklist}


def apply_event(session: Session, event: dict[str, Any]) -> dict:
    """Применить одно событие к локальному стору. Идемпотентно по backend_id."""
    kind = event.get("entity_kind", "")
    op = event.get("op", "")
    bid = event.get("entity_id")
    payload = event.get("payload_json") or {}
    if not bid:
        return {"skipped": "no_entity_id"}
    if op == "delete":
        return _delete(session, kind, bid)
    handler = _UPSERT.get(kind)
    if handler is None:
        return {"skipped": f"kind:{kind}"}
    return handler(session, bid, payload)


__all__ = ["apply_event"]
```

> Примечание: `ChecklistItem`/`Epic` не имеют `archived_at` — `_delete` для них делает `session.delete`. `Task` имеет `archived_at` → soft. Это поведение зафиксировано тестом `test_delete_soft_archives` (task).

- [ ] **Step 4: GREEN**

Run: `uv run pytest tests/test_sync_apply.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/atlas/pm/sync/apply.py tests/test_sync_apply.py
git commit -m "feat(f3d): apply_event — upsert/delete по backend_id (идемпотентно)"
```

---

### Task 3: pull_once (poll → apply → advance cursor)

**Files:** Create `src/atlas/pm/sync/pull.py`; Test `tests/test_sync_pull.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_sync_pull.py`:

```python
"""F3d: pull_once применяет события из long-poll и двигает курсор."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from atlas.pm.models import (
    Base, Project, ProjectStatus, ProjectType, Task,
)
from atlas.pm.sync import cursor, pull


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def poll_events(self, since=None, *, timeout=25.0):
        self.calls.append({"since": since, "timeout": timeout})
        return self._response

    async def aclose(self):
        pass


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _task(s, backend_id):
    t = ProjectType(slug="t", name="t"); st = ProjectStatus(slug="a", name="a", order_idx=1)
    s.add_all([t, st]); s.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2", one_line_summary="x")
    s.add(p); s.flush()
    task = Task(project_id=p.id, title="old", cpp_description="ц", priority="P2", backend_id=backend_id)
    s.add(task); s.commit()


async def test_pull_applies_and_advances_cursor(session):
    _task(session, "be-1")
    client = _FakeClient({
        "events": [
            {"entity_kind": "task", "op": "update", "entity_id": "be-1",
             "payload_json": {"status": "done"}, "occurred_at": "2026-06-14T12:00:00"},
        ],
        "cursor": "2026-06-14T12:00:00",
    })
    result = await pull.pull_once(session, client, channel="atlas", timeout=1.0)
    assert result["applied"] == 1
    got = session.execute(select(Task).where(Task.backend_id == "be-1")).scalar_one()
    assert got.status == "done"
    assert cursor.get_cursor(session, "atlas") == "2026-06-14T12:00:00"


async def test_pull_empty_keeps_cursor(session):
    cursor.set_cursor(session, "atlas", "2026-06-14T00:00:00")
    session.commit()
    client = _FakeClient({"events": [], "cursor": None})
    result = await pull.pull_once(session, client, channel="atlas", timeout=1.0)
    assert result["applied"] == 0
    # курсор не сбрасывается в None при пустом ответе
    assert cursor.get_cursor(session, "atlas") == "2026-06-14T00:00:00"


async def test_pull_passes_since_from_cursor(session):
    cursor.set_cursor(session, "atlas", "2026-06-14T09:00:00")
    session.commit()
    client = _FakeClient({"events": [], "cursor": None})
    await pull.pull_once(session, client, channel="atlas", timeout=2.0)
    assert client.calls[0]["since"] == "2026-06-14T09:00:00"
    assert client.calls[0]["timeout"] == 2.0
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_sync_pull.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Реализация**

Create `src/atlas/pm/sync/pull.py`:

```python
"""Входящий синк через long-poll (хаб → Atlas): poll → apply → курсор."""
from __future__ import annotations

from sqlalchemy.orm import Session

from atlas.pm.sync import apply, cursor


async def pull_once(
    session: Session, client, *, channel: str = "atlas", timeout: float = 25.0
) -> dict:
    """Один цикл: long-poll событий позже курсора → применить → продвинуть курсор.

    ``client`` — объект с async ``poll_events(since, *, timeout)`` (BackendClient),
    возвращающим ``{events: [...], cursor: str|None}``. → {applied, cursor}.
    """
    since = cursor.get_cursor(session, channel)
    resp = await client.poll_events(since, timeout=timeout)
    events = resp.get("events") or []
    applied = 0
    for ev in events:
        apply.apply_event(session, ev)
        applied += 1
    new_cursor = resp.get("cursor")
    if new_cursor:
        cursor.set_cursor(session, channel, new_cursor)
    session.commit()
    return {"applied": applied, "cursor": cursor.get_cursor(session, channel)}


__all__ = ["pull_once"]
```

- [ ] **Step 4: GREEN**

Run: `uv run pytest tests/test_sync_pull.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/atlas/pm/sync/pull.py tests/test_sync_pull.py
git commit -m "feat(f3d): pull_once — long-poll → apply → курсор"
```

---

### Task 4: CLI `atlas sync pull` / `watch`

**Files:** Modify `src/atlas/pm/commands/sync.py`; Test `tests/test_sync_pull.py` (доп.)

- [ ] **Step 1: Падающий smoke-тест (RED)**

Добавить в `tests/test_sync_pull.py`:

```python
def test_sync_pull_and_watch_in_cli_help():
    from typer.testing import CliRunner
    from atlas.cli import app
    result = CliRunner().invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "pull" in result.stdout
    assert "watch" in result.stdout
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_sync_pull.py::test_sync_pull_and_watch_in_cli_help -v`
Expected: FAIL — в выводе `sync --help` нет `pull`/`watch`.

- [ ] **Step 3: Добавить команды в `pm/commands/sync.py`**

В `src/atlas/pm/commands/sync.py` добавить импорты:

```python
from atlas.pm.sync import pull as pull_mod
```

И команды (после `push_cmd`):

```python
@sync_app.command("pull")
@async_command
async def pull_cmd(
    timeout: float = typer.Option(25.0, "--timeout", help="Таймаут long-poll, сек."),
) -> None:
    """Один цикл входящего синка: применить события с хаба локально."""
    cfg = load_config()
    client = BackendClient(cfg.base_url, cfg.api_key)
    engine = make_engine(_db_url())
    try:
        with make_session(engine) as session:
            result = await pull_mod.pull_once(session, client, timeout=timeout)
    finally:
        await client.aclose()
    emit_data(result, text_renderer=lambda r: print(f"applied: {r['applied']}"))


@sync_app.command("watch")
@async_command
async def watch_cmd(
    timeout: float = typer.Option(25.0, "--timeout", help="Таймаут long-poll, сек."),
) -> None:
    """Бесконечный входящий синк (long-poll цикл). Ctrl+C для остановки."""
    import asyncio

    cfg = load_config()
    client = BackendClient(cfg.base_url, cfg.api_key)
    engine = make_engine(_db_url())
    try:
        while True:
            with make_session(engine) as session:
                result = await pull_mod.pull_once(session, client, timeout=timeout)
            emit_data(result, text_renderer=lambda r: print(f"applied: {r['applied']}"))
            await asyncio.sleep(0.1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        emit_data({"stopped": True}, text_renderer=lambda r: print("watch остановлен"))
    finally:
        await client.aclose()
```

- [ ] **Step 4: GREEN + полный прогон**

Run: `uv run pytest tests/test_sync_pull.py -v && uv run pytest -q`
Expected: PASS, без регрессий.

- [ ] **Step 5: Commit**

```bash
git add src/atlas/pm/commands/sync.py tests/test_sync_pull.py
git commit -m "feat(f3d): CLI atlas sync pull/watch (long-poll)"
```

---

## Self-Review — покрытие спеки F3d (§5–6)

| Компонент спеки | Задача |
|---|---|
| `SyncCursor` курсор канала | Task 1 |
| `apply_event` (upsert по backend_id, idempotent, delete) | Task 2 |
| `pull_once` (long-poll → apply → курсор) | Task 3 |
| CLI `atlas sync pull` / `watch` | Task 4 |
| идемпотентность / эхо-подавление | Task 2 (upsert по backend_id; эхо «минус источник» — на бэке) |

**Граница F3d:** create через apply — best-effort (резолв родителя по `project_backend_id`/`project_slug` или `task_backend_id` в payload); без родителя — skip (не плодим кривьё). Для полного дерева-создания payload должен нести ссылки на родителя — это согласуется при выкате с реальным core-событием (mapper в F3e можно дополнить ссылками родителя). v1 надёжно покрывает обратный синк ИЗМЕНЕНИЙ (update статусов/полей) — главный кейс.

**Placeholder-скан:** код модулей/тестов дословный; `watch` — реальный цикл с обработкой прерывания.

**Type consistency:** `cursor.get_cursor/set_cursor(session, channel[, value])`; `apply.apply_event(session, event)`; `pull.pull_once(session, client, *, channel, timeout)`. Событие: `{entity_kind, op, entity_id, payload_json}`. Имена согласованы между тестами и реализацией.
