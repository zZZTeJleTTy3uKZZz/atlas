# Task Lease/Claim Implementation Plan (Волна 8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Дать Atlas атомарную блокировку задач (lease/claim) для нескольких AI-агентов: взять задачу → остальные видят занятость → не дублируют/не затирают; видно кто/откуда/когда; упавший агент не блокирует навсегда (TTL).

**Architecture:** SQLAlchemy `version_id_col` на `Task` (optimistic-lock на все апдейты) + богатый контекст держателя (lease_owner/session/origin/expires) + pure-logic `pm/lease.py` + CLI `atlas task claim/release/renew/take/stale`. Lease локален — наружу в ядро не уходит.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, Alembic, Typer, Rich, pytest.

**Spec:** [2026-06-23-atlas-task-lease-claim-design.md](../specs/2026-06-23-atlas-task-lease-claim-design.md)

---

## File Structure

- Create: `src/atlas/pm/lease.py` — pure-logic lease-движок (errors, parse_ttl, resolve_*, claim/release/renew/take/expire).
- Create: `src/atlas/pm/commands/task_lease.py` — CLI-команды lease, монтируются в `pm_tasks_app`.
- Create: `migrations/versions/<rev>_task_lease_optimistic_lock.py` — миграция.
- Create: `tests/test_pm_lease.py`, `tests/test_pm_lease_cli.py`.
- Modify: `src/atlas/pm/models.py` — поля + `__mapper_args__` на `Task`.
- Modify: `src/atlas/pm/commands/pm_tasks.py` — mount lease-команд, `StaleDataError→OptimisticLockError` в update/add/delete, auto-release lease в `update --status done|cancelled`, колонка lease в `list`, блок lease в `get`, ленивый reaper.
- Modify: `src/atlas/pm/sync/apply.py` — LWW-по-occurred_at (если есть) + retry на StaleDataError.
- Verify: `src/atlas/pm/sync/mapper.py::_task_payload` НЕ содержит lease/version-полей (тест, правка только если содержит).

---

## Task 1: Миграция + модель (поля lease + version_id_col)

**Files:**
- Modify: `src/atlas/pm/models.py` (класс `Task`, ~248-329)
- Create: `migrations/versions/<rev>_task_lease_optimistic_lock.py`

- [ ] **Step 1.1: Добавить поля в `Task`** (после `archived_at`, перед `__table_args__`):

```python
    # --- Lease/Claim: блокировка задачи для мультиагентности (Волна 8) -----
    lease_owner: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("participants.id"), nullable=True
    )
    lease_session_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    lease_origin: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lock_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
```

- [ ] **Step 1.2: Включить optimistic-lock + индексы.** Добавить `__mapper_args__` в класс `Task` и индексы в `__table_args__`:

```python
    __mapper_args__ = {"version_id_col": lock_version}
```
```python
        Index("idx_tasks_lease", "lease_owner", "lease_expires_at"),
        Index("idx_tasks_lease_expires", "lease_expires_at"),
```

- [ ] **Step 1.3: Создать миграцию** `migrations/versions/<rev>_task_lease_optimistic_lock.py`, `down_revision = "e5f6a7b8c9d0"`:

```python
def upgrade() -> None:
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("lease_owner", sa.String(36), sa.ForeignKey("participants.id"), nullable=True))
        b.add_column(sa.Column("lease_session_id", sa.String(200), nullable=True))
        b.add_column(sa.Column("lease_origin", sa.String(200), nullable=True))
        b.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("idx_tasks_lease", "tasks", ["lease_owner", "lease_expires_at"])
    op.create_index("idx_tasks_lease_expires", "tasks", ["lease_expires_at"])

def downgrade() -> None:
    op.drop_index("idx_tasks_lease_expires", "tasks")
    op.drop_index("idx_tasks_lease", "tasks")
    with op.batch_alter_table("tasks") as b:
        for c in ("lock_version","lease_expires_at","claimed_at","lease_origin","lease_session_id","lease_owner"):
            b.drop_column(c)
```

- [ ] **Step 1.4:** Прогнать миграцию на тест-БД и существующий suite — `pytest -q`. Ожидание: зелёно (version_id_col не ломает существующие read-modify-write пути). Если красно — чинить до перехода к Task 2.
- [ ] **Step 1.5: Commit** `feat(model): lease/version поля в tasks + version_id_col (W8-01/02)`.

## Task 2: `pm/lease.py` — pure-logic движок

**Files:** Create `src/atlas/pm/lease.py`; Test `tests/test_pm_lease.py`.

- [ ] **Step 2.1: Тесты (RED)** — `parse_ttl`, `_lease_is_free`, claim happy/held/idempotent/expired, release owner/not-owner, renew, take, expire_stale. (Полные кейсы — см. spec §9.)
- [ ] **Step 2.2: Реализация.** Ключевое:

```python
DEFAULT_TTL = timedelta(hours=2)

class LeaseHeldError(Exception):
    def __init__(self, holder, expires_at): ...
class LeaseNotOwnedError(Exception): ...
class OptimisticLockError(Exception): ...

def parse_ttl(s: str) -> timedelta:  # "2h"/"30m"/"90s"/"1d" → timedelta; invalid → ValueError

def resolve_actor(session, actor_slug=None) -> Participant  # flag > ATLAS_ACTOR > DEFAULT_ACTOR_SLUG
def resolve_session_id(explicit=None) -> str|None           # flag > ATLAS_SESSION
def resolve_origin(explicit=None) -> str|None               # flag > ATLAS_FROM > basename(cwd)

def _lease_is_free(task, actor_id, now) -> bool:
    return task.lease_owner is None or task.lease_owner == actor_id or \
        (task.lease_expires_at is not None and task.lease_expires_at < now)

def claim_task(session, task, actor, *, session_id, origin, ttl=DEFAULT_TTL, now=None):
    now = now or msk_now()
    for _ in range(3):
        if not _lease_is_free(task, actor.id, now):
            raise LeaseHeldError(_holder_slug(session, task.lease_owner), task.lease_expires_at)
        prev = task.lease_owner
        same_live = task.lease_owner == actor.id and task.lease_expires_at and task.lease_expires_at >= now
        task.lease_owner = actor.id
        task.lease_session_id = session_id
        task.lease_origin = origin
        task.lease_expires_at = now + ttl
        if not same_live:
            task.claimed_at = now
        task.status = "in_progress"
        task.assignee_id = actor.id
        if task.started_at is None:
            task.started_at = now
        try:
            session.flush()
        except StaleDataError:
            session.rollback(); session.refresh(task); now = msk_now(); continue
        _log_lease(session, "task_claimed", task, actor, prev, session_id, origin)
        return LeaseResult(task=task, previous_holder=prev)
    raise LeaseHeldError(_holder_slug(session, task.lease_owner), task.lease_expires_at)

def release_task(session, task, actor): ...   # owner-check → LeaseNotOwnedError; clear lease_*; log task_released
def renew_lease(session, task, actor, ttl=DEFAULT_TTL): ...  # owner-check; expires=now+ttl; log lease_renewed
def take_task(session, task, actor, *, session_id, origin, ttl=DEFAULT_TTL): ...  # force; log task_taken(prev)
def expire_stale_leases(session, now=None) -> list:  # clear where expires<now & owner not null; log lease_expired
```
`_log_lease` пишет в `ActionLog(actor_id=actor.id, entity_type="task", entity_id=task.id, action=..., details_json={previous_holder, session_id, origin, expires_at})`. Lease-операции `flush`, commit делает CLI.

- [ ] **Step 2.3:** `pytest tests/test_pm_lease.py -v` → GREEN.
- [ ] **Step 2.4: Commit** `feat(lease): pure-logic движок claim/release/renew/take/expire (W8-03/04/10)`.

## Task 3: CLI команды + отображение + reaper

**Files:** Create `src/atlas/pm/commands/task_lease.py`; Modify `pm_tasks.py` (mount, list-колонка, get-блок, reaper); Test `tests/test_pm_lease_cli.py`.

- [ ] **Step 3.1: Тесты (RED)** — `claim`/`release`/`renew`/`take --force`/`stale [--reap]` через Typer runner; занятость другим → ненулевой exit; `get`/`list` показывают lease.
- [ ] **Step 3.2:** Реализовать команды в `task_lease.py` (register-функция, вызывается из pm_tasks.py: `register_lease_commands(pm_tasks_app)`), каждая: resolve ref → resolve actor/session/origin → вызов lease.* → `session.commit()` → вывод (json/text). `claim`/`list` сначала `expire_stale_leases()`.
- [ ] **Step 3.3:** `task get` — блок lease; `task list` — колонка lease (`{owner}·до {HH:MM}` / `—`).
- [ ] **Step 3.4:** `pytest tests/test_pm_lease_cli.py -v` → GREEN.
- [ ] **Step 3.5: Commit** `feat(cli): atlas task claim/release/renew/take/stale + lease в get/list (W8-06/07/11/12)`.

## Task 4: Optimistic-lock в update/add/delete + auto-release

**Files:** Modify `pm_tasks.py` (`update_cmd`, `add_cmd`, `delete_cmd`).

- [ ] **Step 4.1: Тест (RED)** — параллельный update устаревшей версией → `OptimisticLockError`; `update --status done` снимает lease.
- [ ] **Step 4.2:** Обернуть `session.commit()` в update/add/delete: `except StaleDataError: raise OptimisticLockError(...)` (CLI печатает понятное сообщение + ненулевой exit). В `update_cmd` при `status in {"done","cancelled"}` — очистить `lease_*`.
- [ ] **Step 4.3:** `pytest tests/ -k "update or lock" -v` → GREEN.
- [ ] **Step 4.4: Commit** `feat(tasks): optimistic-lock на апдейты + auto-release lease при done/cancelled (W8-05)`.

## Task 5: Синк — LWW + retry + инвариант payload

**Files:** Modify `sync/apply.py`; Test в `tests/` (apply + mapper payload).

- [ ] **Step 5.1: Тест (RED)** — `_task_payload` не содержит lease/version-полей; `claim` не создаёт outbox-запись; устаревшее incoming не затирает свежее локальное (если `occurred_at` есть); apply переживает `StaleDataError` (retry).
- [ ] **Step 5.2:** `_upsert_task`: если payload несёт `occurred_at`/`updated_at` и оно старше `task.updated_at` — skip setattr (LWW); иначе как сейчас + `log.warning` если поля нет. Обернуть запись в bounded-retry на `StaleDataError` (re-read). Проверить `_task_payload` — если в нём вдруг есть lease-поля, убрать (ожидается, что их нет).
- [ ] **Step 5.3:** `pytest tests/ -k "apply or payload or outbox" -v` → GREEN.
- [ ] **Step 5.4: Commit** `feat(sync): LWW-по-occurred_at + retry на StaleDataError; lease не в payload (W8-08/14/15)`.

## Task 6: Полный прогон + smoke

- [ ] **Step 6.1:** `pytest -q` — весь suite GREEN (старые 507+ не сломаны).
- [ ] **Step 6.2:** Smoke на тест-БД: `task add` → `task claim` (актор A) → повторный `claim` актором B (ошибка занятости) → `renew` A → `take --force` B → `release` B → `stale --reap`. Проверить `action-log list` содержит lease-события.
- [ ] **Step 6.3: Commit** (если правки) + финальный отчёт.

---

## Self-Review (coverage vs spec)

- §3 модель/миграция → Task 1 ✓
- §4 lease-движок → Task 2 ✓
- §5 CLI/отображение/reaper → Task 3 ✓
- §6 optimistic-lock/auto-release → Task 4 ✓; синк LWW/retry → Task 5 ✓
- §7 инвариант payload/outbox/audit → Task 5 (payload/outbox) + Task 2 (audit) ✓
- §8 ошибки → покрыты тестами Task 2-4 ✓
- §9 тесты → распределены по Task 2-5 ✓
