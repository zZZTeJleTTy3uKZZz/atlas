# atlas — синхронизация с хабом и профили

Atlas — **локальный портал** в трёхсторонней системе синка. Он НЕ ходит в Notion/Б24 напрямую —
он общается только с **ядром-хабом** `notion-api-b24`, а ядро уже фанаутит в Б24 и Notion.

```
Atlas (локальный SQLite) ──push (POST /events)──▶ ядро-хаб ──фанаут──▶ Б24 / Notion
Atlas ◀──pull (long-poll GET /events/poll)── ядро-хаб ◀──вебхуки── Б24 / Notion
```

Хаб: `https://auto.pragmati.ru/automatization/notion-b24-task`, аутентификация `X-API-Key`.

## Профиль = отдельный стор

Профиль — это **самостоятельный Atlas-стор**: своя `atlas.db` + свой API-ключ от ядра + свой
`scope`. Один человек может держать несколько сторов (например `atlas-admin` и `atlas-dmitry`).

- `atlas --profile <slug> <команда>` — работать в конкретном сторе (БД `profiles/<slug>/atlas.db`).
- Без `--profile` — основной стор `~/.atlas/atlas.db`.
- `scope` стора (на стороне ядра, по ключу):
  - `all` — стор видит ВСЕ события (профиль admin);
  - `personal` — только задачи, где владелец стора — ответственный/исполнитель, либо lead проекта.

Завести стор: `atlas profile register --name "…" [--scope all|personal --member <slug>]`.
Команда дёргает ядро `POST /api/v1/admin/profiles` (нужен admin-ключ), атомарно создаёт
Member+Portal(atlas)+ApiKey и сохраняет `profiles/<slug>/config.toml` (base_url + api_key +
portal_id + scope) и `profiles/<slug>/atlas.db`. Сырой ключ показывается один раз.

Конфиг хаба (`AtlasConfig` на clikit): `base_url`, `api_key`, `portal_id`, `scope`. Слои:
env (`ATLAS_DB_URL`, `ATLAS_PROFILE`) > `profiles/<p>/config.toml` > `~/.atlas/`.

## Команды синка

| Команда | Что делает |
|---|---|
| `atlas sync push` | выгрузить pending-операции из локального **Outbox** на хаб (`POST /events`). |
| `atlas sync pull [--timeout 25]` | один цикл входящего синка: long-poll `/events/poll` → применить локально по backend_id. |
| `atlas sync watch [--timeout 25]` | устойчивый фоновый long-poll (исключения не валят цикл, exp-backoff). Лог в `cache_dir/sync-watch.log`. |
| `atlas sync up` | подключиться к хабу: install + start фонового демона (= `daemon install` + запуск). |
| `atlas sync daemon install\|uninstall\|status` | Windows Scheduled Task `atlas-sync-watch` (автостарт при входе, авто-рестарт). |

Двусторонний синк: **push** (исходящее, через Outbox+policy) + **pull/watch** (входящее, идемпотентный
upsert по `backend_id`). Идемпотентность + подавление эха: upsert по backend_id + ядро исключает
источник из фанаута. **apply (входящее) НЕ кладёт в Outbox** — иначе была бы петля.

## Что синкается (policy)

Решение «синкать ли» — через `SyncPolicy` (потолок на проект): поля `sync_epic` / `sync_task` /
`sync_checklist`. Дефолт берётся из `ProjectType.default_sync_policy`. Справочник политик:
`local` (ничего) / `epics` (только вехи) / `media` / `full` (всё). ИИ-агент (`kind=ai_agent`) не
присутствует в порталах → его задачи не фанаутятся (эмерджентно).

Граница синка по сути: задача уходит в портал, если владелец портала — её ответственный/исполнитель
(MemberPortal + роли), либо он lead проекта.

## Синк пунктов чек-листа (двунаправленный)

`atlas checklist add/check/delete` → enqueue в Outbox → `sync push` → ядро → Б24/Notion. Обратно:
правка пункта в Б24/Notion → ядро → `sync pull` → локальный ChecklistItem обновляется.

Контракт на проводе = язык ядра: `entity_kind="checklist_item"`, payload
`{title, done, due, order_idx, parent_task_backend_id}`. Atlas транслирует ↔ свои поля
(`text/is_done/position/due_date`). Дедлайн пункта (`due_date`) и порядок (`position`) сохраняются
в обе стороны. Родитель резолвится по `backend_id` задачи.

## Типовой сценарий настройки (делает Дмитрий в своей сессии)

```bash
# 1. Завести стор(ы) — нужен admin-ключ от ядра
atlas profile register --name "Атлас (всё)" --member dmitry --scope all      # → atlas-admin
atlas profile register --name "Атлас (моё)" --member dmitry --scope personal # → atlas-dmitry

# 2. Подключить фоновый синк
atlas --profile atlas-dmitry sync up
atlas --profile atlas-dmitry sync daemon status

# 3. Разовый прогон вручную
atlas sync push && atlas sync pull --timeout 5
```

> Регистрация Scheduled Task требует пользовательской сессии Windows — в изолированном
> окружении Claude `Register-ScheduledTask` падает с PermissionDenied. Демон ставит Дмитрий сам.
