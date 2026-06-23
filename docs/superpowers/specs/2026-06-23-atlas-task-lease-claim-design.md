# Дизайн: Task Lease/Claim — блокировка задач для мультиагентности (Волна 8)

**Дата**: 2026-06-23
**Статус**: дизайн согласован, готов к плану реализации
**Бэклог**: [BACKLOG.md](../../../_project/docs/SCALING_PRODUCT/products/new/NP-005_Personal_PM_Infrastructure/BACKLOG.md) → ВОЛНА 8 (W8-01…16)
**Провенанс**: намайнено из [gastownhall/beads](https://github.com/gastownhall/beads), gap-анализ против Atlas.

## 1. Проблема

В Atlas нет механизма контроля одновременной работы нескольких агентов над одной задачей (подтверждено по коду):

- `pm/commands/pm_tasks.py::update_cmd` при переходе в `in_progress` проверяет только `started_at IS None` — два агента переведут одну задачу в работу без конфликта.
- `assignee_id` перезаписывается вслепую.
- `sync/apply.py::_upsert_task` делает `setattr` без сравнения времён (blind last-applied-wins).

Это блокер дорожной карты (V07/V10 — «7 AI-ролей одновременно пишут в PM»). Нужен механизм: агент **берёт** задачу (claim) → остальные видят, что она занята, и не дублируют/не затирают; видно **кто взял, откуда и когда закончил**; упавший агент не блокирует задачу навсегда.

## 2. Согласованные решения (brainstorming 2026-06-23)

| Вопрос | Решение |
|---|---|
| Идентичность держателя | Богатый контекст: **actor** (participant) + **session-id** (Claude Code) + **origin** (проект/cwd). Передача — флаги `--actor/--session/--from` с фолбэком на env `ATLAS_ACTOR/ATLAS_SESSION/ATLAS_FROM`. |
| Семантика claim | `claim` → `status='in_progress'` + `assignee_id=actor` (как в beads). |
| Optimistic-lock | На **все** апдейты задачи (не только claim). |
| Реализация атомарности | **SQLAlchemy `version_id_col`** (подход B). |
| TTL / живучесть | По умолчанию **2ч** + `renew` (heartbeat) + ленивый авто-reaper + `take --force`. |

## 3. Модель данных + миграция

Новая Alembic-ревизия, `down_revision = e5f6a7b8c9d0` (текущий head). Down дропает всё.

Колонки в `tasks`:

| Колонка | Тип | Назначение |
|---|---|---|
| `lease_owner` | `String(36)` FK→`participants.id`, NULL | **кто** держит (роль/агент) |
| `lease_session_id` | `String(200)` NULL | **кто конкретно** — id сессии Claude Code |
| `lease_origin` | `String(200)` NULL | **откуда** взято (проект/cwd) |
| `claimed_at` | `DateTime` NULL | **когда взял** |
| `lease_expires_at` | `DateTime` NULL | TTL-дедлайн (протухание) |
| `lock_version` | `Integer NOT NULL, server_default '0'` | optimistic-lock |

- `Task.__mapper_args__ = {"version_id_col": Task.lock_version}` — версия бампается и проверяется (`WHERE lock_version=?`) на каждом ORM-flush задачи; рассинхрон → `sqlalchemy.orm.exc.StaleDataError`.
- Индексы: `idx_tasks_lease (lease_owner, lease_expires_at)`, `idx_tasks_lease_expires (lease_expires_at)`.
- Backfill не нужен: `server_default '0'` даёт существующим строкам валидную версию (требование `version_id_col`); lease-поля NULL.
- «Когда закончил» — событие в `ActionLog` (`task_released` / `task_updated→done`) + существующий `completed_at`.
- Lease-поля и `lock_version` ортогональны `status` — в `ck_tasks_status` НЕ добавляются.

## 4. Модуль `src/atlas/pm/lease.py` (pure-logic, без typer)

**Резолв контекста** (precedence): actor = `--actor` › env `ATLAS_ACTOR` › `DEFAULT_ACTOR_SLUG` (`dmitry`); session = `--session` › `ATLAS_SESSION` › `None`; origin = `--from` › `ATLAS_FROM` › `basename(cwd)`. `DEFAULT_TTL = timedelta(hours=2)`. `parse_ttl("2h"|"30m"|"90s"|"1d") → timedelta` (invalid → ValueError).

**Ошибки**: `LeaseHeldError(holder_slug, expires_at)`, `LeaseNotOwnedError(holder_slug)`, `OptimisticLockError` (обёртка над `StaleDataError`).

**Lease «свободен»** для actor, если: `lease_owner IS NULL` **OR** `lease_expires_at < now` **OR** `lease_owner == actor.id`.

**Операции** (все логируют в `ActionLog`, `now=msk_now()`):

- `claim_task(session, task, actor, *, session_id, origin, ttl, now)`:
  1. Если lease занят другим (по правилу выше) → `LeaseHeldError`.
  2. Иначе: `lease_owner=actor.id`, `lease_session_id`, `lease_origin`, `lease_expires_at=now+ttl`; `claimed_at`: сохранить если re-claim тем же живым owner, иначе `now`; `status='in_progress'`, `assignee_id=actor.id`, `started_at=COALESCE(started_at, now)`. `session.flush()`.
  3. `version_id_col` даёт `WHERE lock_version=?`. На `StaleDataError` → `session.rollback()`/re-read + retry (≤3). Если после re-read lease занят другим → `LeaseHeldError`.
  4. Идемпотентность: повторный claim тем же actor (lease не протух) → success (опц. продлевает expiry).
  - Возвращает `LeaseResult(task, previous_holder)`.
- `release_task(session, task, actor)`: если `lease_owner != actor.id` → `LeaseNotOwnedError`; иначе чистит `lease_*` (статус НЕ трогает).
- `renew_lease(session, task, actor, ttl)`: только владелец; продлевает `lease_expires_at=now+ttl`.
- `take_task(session, task, actor, *, session_id, origin, ttl)`: принудительный отбор занятого/протухшего lease; прежний держатель → `ActionLog` (`task_taken`, details `previous_holder`).
- `expire_stale_leases(session, now)`: чистит `lease_*` где `lease_expires_at < now AND lease_owner IS NOT NULL`; возвращает список освобождённых; логирует `lease_expired`. Статус не трогает (lease свободен → задача реклеймуема).

`ActionLog`-события: `task_claimed | task_released | lease_renewed | task_taken | lease_expired`; `actor_id` + `details_json {previous_holder, session_id, origin, expires_at}`. (`_log_action` уже использует `_actor_id(session)`; для lease-операций actor передаётся явно.)

## 5. CLI (`atlas task …`, ед. число, `--json` по умолчанию)

- `atlas task claim <ref> [--ttl 2h] [--actor <slug>] [--session <id>] [--from <origin>]`
- `atlas task release <ref> [--actor <slug>]`
- `atlas task renew <ref> [--ttl 2h] [--actor <slug>]`
- `atlas task take <ref> --force [--ttl] [--actor] [--session] [--from]`
- `atlas task stale [--reap]` — без `--reap` отчёт о протухших; с `--reap` освобождает.
- **Отображение**: `task get` — блок lease (держатель · сессия · откуда · до HH:MM); `task list` — колонка lease (`ai-backend·до 14:30` / `—`).
- **Ленивый reaper**: `expire_stale_leases()` в начале `claim` и `list` (без демона — single-file SQLite).

## 6. Optimistic-lock на все апдейты + поведение синка

- `version_id_col` на `Task` автоматически защищает каждый ORM-flush. `update_cmd` / `add` / `delete` оборачивают `session.commit()`: `StaleDataError` → `OptimisticLockError` с понятным сообщением («задача изменена параллельно — перечитайте и повторите»).
- `update --status done|cancelled` → авто-снятие lease (clear `lease_*`) как часть завершения.
- `sync/apply.py::_upsert_task`:
  - **LWW-по-occurred_at**: перед `setattr` сравнивать `occurred_at` входящего события с `task.updated_at` — не перезаписывать более свежее локальное. Реализуется, **если** pull-контракт ядра несёт `occurred_at`/`updated_at`; иначе fallback на текущее поведение + `log.warning("LWW pending: core payload lacks occurred_at")`. (Зависимость от контракта ядра — отметить в плане.)
  - **Retry на StaleDataError**: обернуть apply-запись в bounded-retry (re-read + повтор), чтобы входящий синк не падал жёстко на параллельном локальном бампе версии. Apply НЕ применяет interactive-guard семантику (он авторитетный incoming).

## 7. Инвариант синка + аудит

- `sync/mapper.py::_task_payload` сериализует только доменные поля (title/status/priority/cpp/slug/…) и **НЕ** включает `lease_owner/lease_session_id/lease_origin/claimed_at/lease_expires_at/lock_version`. Lease — локальная координация портала, наружу не уходит (иначе протухание на одной машине затрёт другую через LWW).
- `atlas task claim/release/renew/take` **не** enqueue-ят в outbox (в отличие от `update`). Явный тест: claim не создаёт outbox-запись.
- Все lease-операции пишут в существующий append-only `ActionLog`.

## 8. Обработка ошибок и краевые случаи

| Случай | Поведение |
|---|---|
| claim занятой другим | `LeaseHeldError(holder, expires)` → ненулевой exit + сообщение (кто/сессия/откуда/до когда) |
| re-claim тем же actor (retry) | идемпотентный success |
| гонка двух claim | `version_id_col` → один выигрывает; проигравший после re-read → `LeaseHeldError` |
| release/renew не-владельцем | `LeaseNotOwnedError` (кроме `take --force`) |
| claim протухшего lease | success, прежний держатель в `ActionLog` |
| `take --force` свободной задачи | просто claim |
| параллельный `update` (устаревшая версия) | `OptimisticLockError` |
| невалидный `--ttl` | ошибка парсинга |
| reaper повторно | идемпотентно |

## 9. План тестов (TDD, ~25–35)

- unit: `parse_ttl`; precedence `resolve_actor/session/origin`; правило «lease свободен».
- `claim` happy-path: ставит `status/assignee/started_at/lease_*` + `ActionLog`.
- гонка двух `claim` (через таммперинг `lock_version` / два сеанса) → один выигрывает, второй `LeaseHeldError`.
- идемпотентный re-claim тем же actor.
- `claim` протухшего lease → success + previous_holder в `ActionLog`.
- `release` владельцем / не-владельцем (`LeaseNotOwnedError`).
- `renew` продлевает; не-владелец → ошибка.
- `take --force` отбирает + пишет `task_taken`.
- `expire_stale_leases` освобождает протухшие, не трогает свежие, логирует `lease_expired`.
- `update` бампает версию; параллельный `update` устаревшей версией → `OptimisticLockError`.
- `update --status done` снимает lease.
- `mapper._task_payload` не содержит lease/version-полей; `claim` не создаёт outbox-запись.
- apply: устаревшее incoming не затирает свежее локальное (если есть `occurred_at`); apply переживает `StaleDataError` (retry).

## 10. Вне скоупа Волны 8 (→ Волна 9 и далее)

- Таблица `task_dependencies` + `ready`-очередь (`atlas task ready --claim`) — Волна 9.
- Синхронизация lease/зависимостей в ядро — не делаем (локальная координация).
- Фоновый демон-reaper — не нужен (ленивый reaper достаточно для single-file SQLite).
