# F3b — доменная модель Atlas (Counterparty/Epic/Checklist/SyncPolicy/Outbox) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Steps use `- [ ]`.

**Goal:** Расширить локальную модель `atlas` сущностями для синка через хаб и для де-хардкода принадлежности: `SyncPolicy`, `Counterparty` (+ `Project.owner/customer`, `ProjectType.default_sync_policy`), `Epic`, `ChecklistItem`, `TaskMember`, `Outbox`, `SyncCursor` + поля `backend_id` — через Alembic-миграции с тестами, без потери данных.

**Architecture:** 3 последовательные миграции (down_revision цепочкой от head `ca84c1d9b54e`): (1) справочники синка + контрагенты, (2) иерархия Epic/Checklist/TaskMember, (3) sync-инфра. Все новые FK — `nullable` (дефолты резолвятся в рантайме), поэтому add_column на существующих таблицах не требует server_default и не ломает строки. Модели в `pm/models.py`, миграции через `alembic revision --autogenerate` + ручная выверка, сиды через `op.bulk_insert`/UPDATE. `bool` хранится как `Integer` 0/1 (стиль atlas — `is_archived`).

**Tech Stack:** SQLAlchemy 2.x (sync), Alembic (batch-режим для SQLite), pytest. Python 3.14 (фактическое окружение). uv.

**Соглашения (следовать существующему коду `pm/models.py`):** id = `String(36)` PK `default=_gen_uuid`; времена `default=msk_now`; `CheckConstraint`/`Index` в `__table_args__`; bool → `Integer` default 0.

---

## File Structure

- **Modify** `src/atlas/pm/models.py` — добавить 7 моделей + поля в `Project`/`ProjectType`/`Task`.
- **Create** `migrations/versions/<rev1>_f3b_sync_policy_counterparty.py`
- **Create** `migrations/versions/<rev2>_f3b_epic_checklist_taskmember.py`
- **Create** `migrations/versions/<rev3>_f3b_backend_id_outbox_cursor.py`
- **Modify** `src/atlas/pm/seeds.py` — добавить сиды `SyncPolicy` + `default_sync_policy` типам + контрагентов; включить в `seed_all`.
- **Create** `tests/test_pm_f3b_models.py`, `tests/test_pm_migration_f3b.py`, `tests/test_pm_seeds_f3b.py`.

Ветка `feat/f3-atlas-cli-sync`. Тесты: `cd <ATLAS> && uv run pytest <path> -v`. Образец миграции — `migrations/versions/d88bf4f8a629_tags_and_archive_engine.py`; образец теста миграции — `tests/test_pm_migration_007.py`.

---

### Task 1: Справочники синка + контрагенты (миграция 1)

**Files:**
- Modify: `src/atlas/pm/models.py`
- Create: `migrations/versions/<rev1>_f3b_sync_policy_counterparty.py`
- Modify: `src/atlas/pm/seeds.py`
- Test: `tests/test_pm_f3b_models.py`, `tests/test_pm_migration_f3b.py`, `tests/test_pm_seeds_f3b.py`

- [ ] **Step 1: Написать падающий тест моделей (RED)**

Create `tests/test_pm_f3b_models.py`:

```python
"""F3b: новые модели создаются и связываются в in-memory БД."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import (
    Base, Counterparty, Project, ProjectStatus, ProjectType, SyncPolicy,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _mk_type_status(s):
    t = ProjectType(slug="client-project", name="Кл", default_sync_policy="full")
    st = ProjectStatus(slug="active", name="A", order_idx=1)
    s.add_all([t, st])
    s.flush()
    return t, st


def test_sync_policy_crud(session):
    session.add(SyncPolicy(slug="full", name="Full", sync_epic=1, sync_task=1, sync_checklist=1))
    session.commit()
    p = session.get(SyncPolicy, "full")
    assert (p.sync_epic, p.sync_task, p.sync_checklist) == (1, 1, 1)


def test_counterparty_and_project_owner(session):
    session.add(SyncPolicy(slug="full", name="Full", sync_epic=1, sync_task=1, sync_checklist=1))
    t, st = _mk_type_status(session)
    owner = Counterparty(slug="cifro-pro", kind="company", name="Cifro.pro", git_namespace="cifropro1")
    session.add(owner)
    session.flush()
    proj = Project(
        slug="acme", name="Acme", type_id=t.id, status_id=st.id, priority="P2",
        one_line_summary="x", owner_id=owner.id, sync_policy="full",
    )
    session.add(proj)
    session.commit()
    got = session.get(Project, proj.id)
    assert got.owner_id == owner.id
    assert got.sync_policy == "full"


def test_counterparty_kind_constraint(session):
    from sqlalchemy.exc import IntegrityError
    session.add(Counterparty(slug="bad", kind="alien", name="X"))
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 2: Запустить — RED**

Run: `uv run pytest tests/test_pm_f3b_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'SyncPolicy'`.

- [ ] **Step 3: Добавить модели в `pm/models.py`**

В конец `src/atlas/pm/models.py` добавить:

```python
# --------------------------------------------------------------------------- #
# F3b: справочники синка + контрагенты                                        #
# --------------------------------------------------------------------------- #


class SyncPolicy(Base):
    """Политика-потолок синка: до какого уровня иерархии выгружать наружу.

    v1 — три булевых уровня (bool как Integer 0/1). Сиды: local(0,0,0),
    epics(1,0,0), media(1,1,0), full(1,1,1).
    """

    __tablename__ = "sync_policies"

    slug: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sync_epic: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sync_task: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sync_checklist: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=msk_now, nullable=False
    )


class Counterparty(Base):
    """Контрагент — владелец/заказчик проекта (бизнес-связь, НЕ адрес синка).

    От owner вытекает git-namespace; пространство синка определяет команда
    проекта (участники), не контрагент. Зеркало core-Counterparty.
    """

    __tablename__ = "counterparties"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    git_namespace: Mapped[Optional[str]] = mapped_column(String(200))
    backend_id: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=msk_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('person','company')", name="ck_counterparties_kind"
        ),
    )
```

В классе `ProjectType` добавить колонку (после `is_archived`):

```python
    default_sync_policy: Mapped[Optional[str]] = mapped_column(
        String(50), ForeignKey("sync_policies.slug")
    )
```

В классе `Project` добавить колонки (рядом с прочими nullable, перед `created_at`):

```python
    owner_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("counterparties.id")
    )
    customer_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("counterparties.id")
    )
    sync_policy: Mapped[Optional[str]] = mapped_column(
        String(50), ForeignKey("sync_policies.slug")
    )
    backend_id: Mapped[Optional[str]] = mapped_column(String(36))
```

- [ ] **Step 4: Запустить тест моделей — GREEN**

Run: `uv run pytest tests/test_pm_f3b_models.py -v`
Expected: PASS (3 passed) — модели создаются в in-memory БД.

- [ ] **Step 5: Сгенерировать миграцию автогеном и выверить**

Run: `cd <ATLAS> && uv run alembic revision --autogenerate -m "f3b sync_policy counterparty"`
Затем ОТКРЫТЬ созданный файл `migrations/versions/<rev1>_*.py` и проверить:
- `down_revision = 'ca84c1d9b54e'` (текущий head);
- созданы таблицы `sync_policies`, `counterparties` (с `ck_counterparties_kind`);
- в `projects` добавлены `owner_id`, `customer_id`, `sync_policy`, `backend_id` (все nullable, через `batch_alter_table`);
- в `project_types` добавлен `default_sync_policy` (nullable).
Если autogenerate пропустил CheckConstraint или FK — дописать вручную по образцу `d88bf4f8a629`. Убедиться, что `downgrade()` зеркально удаляет таблицы/колонки.

В КОНЕЦ `upgrade()` той же миграции дописать сид-данные (bulk_insert политик + UPDATE дефолтов типам) — по образцу `d88bf4f8a629` (ad-hoc `sa.table`):

```python
    # --- seed: sync policies + default_sync_policy типам -------------------
    sync_policies_tbl = sa.table(
        "sync_policies",
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
        sa.column("sync_epic", sa.Integer),
        sa.column("sync_task", sa.Integer),
        sa.column("sync_checklist", sa.Integer),
        sa.column("created_at", sa.DateTime),
    )
    now = datetime.utcnow()
    op.bulk_insert(sync_policies_tbl, [
        {"slug": "local", "name": "Локально (ничего наружу)", "sync_epic": 0, "sync_task": 0, "sync_checklist": 0, "created_at": now},
        {"slug": "epics", "name": "Только эпики (вехи)", "sync_epic": 1, "sync_task": 0, "sync_checklist": 0, "created_at": now},
        {"slug": "media", "name": "Эпики + задачи", "sync_epic": 1, "sync_task": 1, "sync_checklist": 0, "created_at": now},
        {"slug": "full", "name": "Полностью", "sync_epic": 1, "sync_task": 1, "sync_checklist": 1, "created_at": now},
    ])
    # дефолтная политика типам: dev → epics, client-project → full
    op.execute(sa.text("UPDATE project_types SET default_sync_policy='full' WHERE slug='client-project'"))
    op.execute(sa.text(
        "UPDATE project_types SET default_sync_policy='epics' "
        "WHERE slug IN ('business-product','personal-utility','personal-project','shared-infrastructure')"
    ))
    op.execute(sa.text("UPDATE project_types SET default_sync_policy='local' WHERE slug IN ('test','inbox')"))
    # --- seed: контрагенты из owner-тегов ---------------------------------
    counterparties_tbl = sa.table(
        "counterparties",
        sa.column("id", sa.String), sa.column("slug", sa.String),
        sa.column("kind", sa.String), sa.column("name", sa.String),
        sa.column("git_namespace", sa.String), sa.column("backend_id", sa.String),
        sa.column("created_at", sa.DateTime),
    )
    import uuid as _uuid
    cifro_id, dmitry_id = str(_uuid.uuid4()), str(_uuid.uuid4())
    op.bulk_insert(counterparties_tbl, [
        {"id": cifro_id, "slug": "cifro-pro", "kind": "company", "name": "Cifro.pro", "git_namespace": "cifropro1", "backend_id": None, "created_at": now},
        {"id": dmitry_id, "slug": "dmitry", "kind": "person", "name": "Дмитрий Семёнов", "git_namespace": None, "backend_id": None, "created_at": now},
    ])
    # best-effort: проставить owner проектам по owner-тегу (если есть данные)
    op.execute(sa.text(
        "UPDATE projects SET owner_id=:cid WHERE id IN ("
        "  SELECT pt.project_id FROM project_tags pt JOIN tags t ON t.id=pt.tag_id WHERE t.slug='cifro-pro')"
    ).bindparams(cid=cifro_id))
    op.execute(sa.text(
        "UPDATE projects SET owner_id=:did WHERE id IN ("
        "  SELECT pt.project_id FROM project_tags pt JOIN tags t ON t.id=pt.tag_id WHERE t.slug='dmitry') AND owner_id IS NULL"
    ).bindparams(did=dmitry_id))
```

Добавить вверху миграции импорт: `from datetime import datetime` и `import sqlalchemy as sa` (если autogenerate не добавил). В `downgrade()` дописать в начало удаление сидов: `op.execute(sa.text("DELETE FROM counterparties WHERE slug IN ('cifro-pro','dmitry')"))` и `op.execute(sa.text("DELETE FROM sync_policies WHERE slug IN ('local','epics','media','full')"))` (плюс автоген-удаление колонок/таблиц).

- [ ] **Step 6: Тест миграции round-trip (по образцу test_pm_migration_007)**

Create `tests/test_pm_migration_f3b.py`:

```python
"""F3b: миграции применяются и откатываются на временной БД."""
import subprocess
import sys
from pathlib import Path

ATLAS = Path(__file__).resolve().parents[1]


def _alembic(args, db_url):
    env = {"ATLAS_DB_URL": db_url}
    import os
    full_env = {**os.environ, **env}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ATLAS, env=full_env, capture_output=True, text=True,
    )


def test_upgrade_head_then_downgrade_base(tmp_path):
    db = tmp_path / "mig.db"
    url = f"sqlite:///{db}"
    up = _alembic(["upgrade", "head"], url)
    assert up.returncode == 0, up.stderr
    # таблицы и сиды на месте
    import sqlite3
    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sync_policies", "counterparties"} <= names
    n = conn.execute("SELECT COUNT(*) FROM sync_policies").fetchone()[0]
    assert n == 4
    conn.close()
    down = _alembic(["downgrade", "base"], url)
    assert down.returncode == 0, down.stderr
```

Run: `uv run pytest tests/test_pm_migration_f3b.py -v`
Expected: PASS — upgrade head + downgrade base без ошибок, 4 политики засеяны.

- [ ] **Step 7: Сиды в seeds.py + тест сидов**

В `src/atlas/pm/seeds.py` добавить константы и функции (идемпотентный upsert по образцу `_upsert`):

```python
SYNC_POLICIES_SEED = [
    {"slug": "local", "name": "Локально (ничего наружу)", "sync_epic": 0, "sync_task": 0, "sync_checklist": 0},
    {"slug": "epics", "name": "Только эпики (вехи)", "sync_epic": 1, "sync_task": 0, "sync_checklist": 0},
    {"slug": "media", "name": "Эпики + задачи", "sync_epic": 1, "sync_task": 1, "sync_checklist": 0},
    {"slug": "full", "name": "Полностью", "sync_epic": 1, "sync_task": 1, "sync_checklist": 1},
]

COUNTERPARTIES_SEED = [
    {"slug": "cifro-pro", "kind": "company", "name": "Cifro.pro", "git_namespace": "cifropro1"},
    {"slug": "dmitry", "kind": "person", "name": "Дмитрий Семёнов"},
]

DEFAULT_SYNC_POLICY_BY_TYPE = {
    "client-project": "full",
    "business-product": "epics",
    "personal-utility": "epics",
    "personal-project": "epics",
    "shared-infrastructure": "epics",
    "test": "local",
    "inbox": "local",
}
```

И функции:

```python
def seed_sync_policies(session: Session) -> list:
    from atlas.pm.models import SyncPolicy
    return [_upsert(session, SyncPolicy, "slug", sp) for sp in SYNC_POLICIES_SEED]


def seed_counterparties(session: Session) -> list:
    from atlas.pm.models import Counterparty
    return [_upsert(session, Counterparty, "slug", cp) for cp in COUNTERPARTIES_SEED]


def seed_type_default_policies(session: Session) -> int:
    from atlas.pm.models import ProjectType
    n = 0
    for slug, policy in DEFAULT_SYNC_POLICY_BY_TYPE.items():
        pt = session.execute(
            select(ProjectType).where(ProjectType.slug == slug)
        ).scalar_one_or_none()
        if pt is not None:
            pt.default_sync_policy = policy
            n += 1
    return n
```

В `seed_all` добавить вызовы перед `session.commit()`:

```python
    policies = seed_sync_policies(session)
    counterparties = seed_counterparties(session)
    type_defaults = seed_type_default_policies(session)
```

и в возвращаемый dict: `"sync_policies": len(policies), "counterparties": len(counterparties), "type_defaults": type_defaults`.

Create `tests/test_pm_seeds_f3b.py`:

```python
"""F3b: сиды политик/контрагентов идемпотентны, дефолты проставлены типам."""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from atlas.pm.models import Base, Counterparty, ProjectType, SyncPolicy
from atlas.pm.seeds import seed_all


def _fresh_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_seed_all_includes_f3b():
    s = _fresh_session()
    seed_all(s)
    assert s.get(SyncPolicy, "full").sync_checklist == 1
    cp = s.execute(select(Counterparty).where(Counterparty.slug == "cifro-pro")).scalar_one()
    assert cp.git_namespace == "cifropro1"
    ct = s.execute(select(ProjectType).where(ProjectType.slug == "client-project")).scalar_one()
    assert ct.default_sync_policy == "full"


def test_seed_all_idempotent():
    s = _fresh_session()
    seed_all(s)
    seed_all(s)  # повторный вызов не должен падать/дублировать
    n = s.execute(select(SyncPolicy)).scalars().all()
    assert len(n) == 4
```

Run: `uv run pytest tests/test_pm_seeds_f3b.py -v`
Expected: PASS (2 passed).

- [ ] **Step 8: Полный прогон + commit**

Run: `uv run pytest -q`
Expected: всё PASS, без регрессий.

```bash
git add src/atlas/pm/models.py "migrations/versions/" src/atlas/pm/seeds.py tests/test_pm_f3b_models.py tests/test_pm_migration_f3b.py tests/test_pm_seeds_f3b.py
git commit -m "feat(f3b): SyncPolicy + Counterparty + Project.owner/customer/sync_policy + сиды"
```

---

### Task 2: Иерархия Epic / ChecklistItem / TaskMember (миграция 2)

**Files:**
- Modify: `src/atlas/pm/models.py`
- Create: `migrations/versions/<rev2>_f3b_epic_checklist_taskmember.py`
- Test: `tests/test_pm_f3b_hierarchy.py`

- [ ] **Step 1: Написать падающий тест (RED)**

Create `tests/test_pm_f3b_hierarchy.py`:

```python
"""F3b: Epic/ChecklistItem/TaskMember и связь Task.epic_id."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import (
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
```

- [ ] **Step 2: Запустить — RED**

Run: `uv run pytest tests/test_pm_f3b_hierarchy.py -v`
Expected: FAIL — `ImportError: cannot import name 'Epic'`.

- [ ] **Step 3: Добавить модели в `pm/models.py`**

```python
# --------------------------------------------------------------------------- #
# F3b: иерархия Epic → Task → ChecklistItem + TaskMember                       #
# --------------------------------------------------------------------------- #


class Epic(Base):
    """Эпик = спринт (крупная веха, опц. даты). Уровень, синкаемый наружу."""

    __tablename__ = "epics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    slug: Mapped[Optional[str]] = mapped_column(String(100), unique=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    goal: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    backend_id: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=msk_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=msk_now, onupdate=msk_now, nullable=False
    )

    __table_args__ = (Index("idx_epics_project", "project_id"),)


class ChecklistItem(Base):
    """Чек-лист задачи (шаги ИИ-агента). По умолчанию локален."""

    __tablename__ = "checklist_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    backend_id: Mapped[Optional[str]] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=msk_now, nullable=False)

    __table_args__ = (Index("idx_checklist_task", "task_id"),)


class TaskMember(Base):
    """Участник задачи с ролью (расширение одиночного assignee_id)."""

    __tablename__ = "task_members"

    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    participant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("participants.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=msk_now, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "role IN ('responsible','executor','watcher')", name="ck_task_members_role"
        ),
    )
```

В классе `Task` добавить колонку (рядом с `sprint_id`):

```python
    epic_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("epics.id"))
```

- [ ] **Step 4: GREEN — тест моделей**

Run: `uv run pytest tests/test_pm_f3b_hierarchy.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Миграция автогеном + выверка**

Run: `cd <ATLAS> && uv run alembic revision --autogenerate -m "f3b epic checklist taskmember"`
Проверить: `down_revision` = ревизия Task 1; созданы `epics`, `checklist_items`, `task_members` (с `ck_task_members_role`, индексами); в `tasks` добавлена `epic_id` (nullable, через batch). `downgrade()` зеркально удаляет. Если FK на `epics.id` в `tasks` создаёт цикл в batch-режиме — добавить `epic_id` без FK-constraint (только колонка), FK не критичен для SQLite-приложения; отметить в комментарии.

- [ ] **Step 6: Тест миграции — расширить round-trip**

В `tests/test_pm_migration_f3b.py` в `test_upgrade_head_then_downgrade_base` после проверки sync_policies добавить:

```python
    assert {"epics", "checklist_items", "task_members"} <= names
```

Run: `uv run pytest tests/test_pm_migration_f3b.py tests/test_pm_f3b_hierarchy.py -v`
Expected: PASS.

- [ ] **Step 7: Полный прогон + commit**

Run: `uv run pytest -q`

```bash
git add src/atlas/pm/models.py "migrations/versions/" tests/test_pm_f3b_hierarchy.py tests/test_pm_migration_f3b.py
git commit -m "feat(f3b): Epic + ChecklistItem + TaskMember + Task.epic_id"
```

---

### Task 3: Sync-инфра — backend_id + Outbox + SyncCursor (миграция 3)

**Files:**
- Modify: `src/atlas/pm/models.py`
- Create: `migrations/versions/<rev3>_f3b_backend_id_outbox_cursor.py`
- Test: `tests/test_pm_f3b_sync_infra.py`

- [ ] **Step 1: Написать падающий тест (RED)**

Create `tests/test_pm_f3b_sync_infra.py`:

```python
"""F3b: Outbox/SyncCursor + backend_id на Task."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import (
    Base, Outbox, Project, ProjectStatus, ProjectType, SyncCursor, Task,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_outbox_defaults(session):
    o = Outbox(op="create", entity_kind="task", entity_id="x", payload_json="{}")
    session.add(o); session.commit()
    got = session.get(Outbox, o.id)
    assert got.status == "pending"
    assert got.attempts == 0


def test_sync_cursor(session):
    session.add(SyncCursor(channel="atlas", cursor="2026-06-14T00:00:00"))
    session.commit()
    assert session.get(SyncCursor, "atlas").cursor.startswith("2026")


def test_task_backend_id(session):
    t = ProjectType(slug="t", name="t"); st = ProjectStatus(slug="a", name="a", order_idx=1)
    session.add_all([t, st]); session.flush()
    p = Project(slug="p", name="P", type_id=t.id, status_id=st.id, priority="P2", one_line_summary="x")
    session.add(p); session.flush()
    task = Task(project_id=p.id, title="T", cpp_description="ц", priority="P2", backend_id="be-1")
    session.add(task); session.commit()
    assert session.get(Task, task.id).backend_id == "be-1"


def test_outbox_op_constraint(session):
    from sqlalchemy.exc import IntegrityError
    session.add(Outbox(op="explode", entity_kind="task", entity_id="x", payload_json="{}"))
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 2: Запустить — RED**

Run: `uv run pytest tests/test_pm_f3b_sync_infra.py -v`
Expected: FAIL — `ImportError: cannot import name 'Outbox'`.

- [ ] **Step 3: Добавить модели + backend_id в `pm/models.py`**

```python
# --------------------------------------------------------------------------- #
# F3b: sync-инфра — outbox + курсор pull                                       #
# --------------------------------------------------------------------------- #


class Outbox(Base):
    """Очередь исходящих операций (локальное изменение → событие на хаб)."""

    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    op: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=msk_now, nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_error: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("op IN ('create','update','delete')", name="ck_outbox_op"),
        CheckConstraint(
            "status IN ('pending','sent','failed')", name="ck_outbox_status"
        ),
        Index("idx_outbox_status", "status"),
    )


class SyncCursor(Base):
    """Курсор pull-канала (ISO occurred_at последнего применённого события)."""

    __tablename__ = "sync_cursors"

    channel: Mapped[str] = mapped_column(String(50), primary_key=True)
    cursor: Mapped[Optional[str]] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=msk_now, onupdate=msk_now, nullable=False
    )
```

В класс `Task` добавить (рядом с `notion_page_id`):

```python
    backend_id: Mapped[Optional[str]] = mapped_column(String(36))
```

(Поля `backend_id` для `Project`/`Epic`/`ChecklistItem`/`Counterparty` уже добавлены в Task 1-2.)

- [ ] **Step 4: GREEN**

Run: `uv run pytest tests/test_pm_f3b_sync_infra.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Миграция автогеном + выверка**

Run: `cd <ATLAS> && uv run alembic revision --autogenerate -m "f3b backend_id outbox cursor"`
Проверить: `down_revision` = ревизия Task 2; созданы `outbox` (с `ck_outbox_op`, `ck_outbox_status`, `idx_outbox_status`), `sync_cursors`; в `tasks` добавлен `backend_id` (nullable). `downgrade()` зеркальный.

- [ ] **Step 6: Тест миграции — финальная проверка**

В `tests/test_pm_migration_f3b.py` добавить в проверку таблиц: `assert {"outbox", "sync_cursors"} <= names`.

Run: `uv run pytest tests/test_pm_migration_f3b.py tests/test_pm_f3b_sync_infra.py -v`
Expected: PASS.

- [ ] **Step 7: Полный прогон + commit**

Run: `uv run pytest -q`

```bash
git add src/atlas/pm/models.py "migrations/versions/" tests/test_pm_f3b_sync_infra.py tests/test_pm_migration_f3b.py
git commit -m "feat(f3b): backend_id (Task) + Outbox + SyncCursor (sync-инфра)"
```

---

## Self-Review — покрытие спеки F3b (§4)

| Сущность спеки | Задача |
|---|---|
| `SyncPolicy` + сиды local/epics/media/full | Task 1 |
| `Counterparty` + `Project.owner_id/customer_id` | Task 1 |
| `ProjectType.default_sync_policy` + сиды (dev→epics, client→full) | Task 1 |
| `Project.sync_policy` + `Project.backend_id` | Task 1 |
| `Epic` + `Task.epic_id` | Task 2 |
| `ChecklistItem` | Task 2 |
| `TaskMember` (responsible/executor/watcher) | Task 2 |
| `backend_id` на Task | Task 3 (Counterparty/Project — T1, Epic/ChecklistItem — T2) |
| `Outbox` | Task 3 |
| `SyncCursor` | Task 3 |

**Граница:** F3b создаёт модели/миграции/сиды (код в git). Применение на боевой `~/.atlas/atlas.db` (`alembic upgrade head`) — отдельный осознанный шаг с бэкапом (бэкап уже сделан); миграции в тестах гоняются на tmp-БД.

**Нюансы:** все новые FK nullable (нет server_default-проблем); `bool` как `Integer`; `epic_id` без FK-constraint допустим, если batch-цикл (SQLite). Сиды — и в миграции (для боевой БД через upgrade), и в `seeds.py` (для `seed_all` на свежей БД), согласованы.

**Placeholder-скан:** код моделей/сидов/тестов дословный; миграции — autogenerate + явная выверка по образцу `d88bf4f8a629` (реальный рецепт, не «TODO»).

**Type consistency:** `SyncPolicy.slug` PK (строковый FK из Project/ProjectType); `Counterparty.id` UUID (FK owner_id/customer_id); `Epic.id`/`Task.epic_id`; `TaskMember` составной PK (task_id, participant_id, role). Имена таблиц: `sync_policies`, `counterparties`, `epics`, `checklist_items`, `task_members`, `outbox`, `sync_cursors`.
