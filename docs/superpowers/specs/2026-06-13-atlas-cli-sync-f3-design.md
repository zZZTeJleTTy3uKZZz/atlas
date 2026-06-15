# Atlas Framework — F3: Atlas CLI как локальный портал (sync через хаб) + clikit

**Дата:** 2026-06-13
**Статус:** design (брейнсторм закрыт, развилки подтверждены Дмитрием)
**Репозиторий:** `_storage/atlas` (пакет `atlas`), ветка `feat/f3-atlas-cli-sync`
**Связанные:** Atlas Framework design v2.6 (`notion-api-b24`), F2 adapters/orchestrator design, `clikit` README

---

## 0. Цель одной фразой

Превратить существующий автономный PM-CLI `atlas` в полноценный **портал Atlas Framework**: локальный стор (SQLite) + sync-адаптер, который по hub-and-spoke синкается с backend-хабом (`notion-api-b24`) через **события (push)** и **long-poll (pull)**; параллельно перевести весь CLI на твою библиотеку **`clikit`** (единый стандарт построения CLI). Граница «что синкать наружу» решается **без хардкода**.

## 1. Контекст

- `atlas` (NP-005, «Personal PM Infrastructure») — зрелый CLI: Typer + SQLAlchemy 2.x (sync) + Alembic + Rich + httpx(Notion). БД `~/.atlas/atlas.db`. Богатая локальная модель: `Project`/`Participant`(kind=human|ai_agent|contractor)/`Task`(ЦКП `cpp_description`, `quality_tier`, `superpowers_spec_path`/`plan_path`)/`ActionLog`(append-only)/`Tag`. 7 миграций.
- **`AGENTS.md` atlas — обязательные правила:** TDD Iron Law (нет кода без падающего теста), миграции **только Alembic + Ask First**, data-model-FIRST, `action_log` append-only, не коммитить `atlas.db`/`.env`, feature-ветка (не `master`).
- `clikit` (`_storage/clikit`) — стандарт-библиотека построения CLI: `HttpClient` (async httpx + stamina-retry + 401-refresh + разбор FastAPI `{"detail"}`), `AppConfig` (слои global/project/local + env-интерполяция секретов + MCP), `SecretStore` (keyring), `AppPaths` (platformdirs), `build_root_app`/`command`/`async_command`, `emit_data`/`emit_error` (**`--json` по умолчанию — под ИИ-агентов**), `gated` (RBAC), `scaffold_cli`.
- backend-хаб (`notion-api-b24`): core-модель `Epic → Task(+TaskMember) → ChecklistItem`, `entity_links` (hub-and-spoke), `/api/v1/events` (POST приём + GET `/poll` long-poll, **готов**), оркестратор (resolve→intake→core→targeting→fanout), адаптеры Notion/Б24 (F2, готовы, выкат отложен).

## 2. Ключевые решения брейнсторма (подтверждены Дмитрием)

| # | Развилка | Решение |
|---|---|---|
| 1 | Чем Atlas становится для хаба | **A — локальный портал-адаптер.** Своя богатая модель остаётся; адаптер маппит *синкаемое подмножество* в core. Backend держит `backend_id` Atlas-портала в `entity_links` (как для Notion/Б24). Сохраняет автономность и данные. |
| 2 | Где граница «синкать наружу» | **C — присутствие исполнителя × политика-потолок проекта.** Ноль хардкода (DIP): движок спрашивает абстракцию, не тип проекта. |
| 3 | Дефолт политики | Явно на проекте (`Project.sync_policy`), **преднаполняется** из `ProjectType.default_sync_policy`. Override первоклассный. |
| 4 | Механика синка | **push = события** (`POST /events`, чтобы прошло через оркестратор → фанаут), **pull = long-poll**, между ними — локальный **outbox**. |
| 5 | Глубина adoption `clikit` | **Полная, инкрементально** (модуль за модулем, TDD): результат — единство, процесс — безопасный. |
| 6 | Де-хардкод классов «клиент/мой/Cifro» | **C — гибрид; НЕ путать контрагента и порталы синка:** (а) контрагент `owner`/`customer` (`Counterparty`, данные) = принадлежность/заказчик + git-namespace; (б) **куда синкать задачи = КОМАНДА проекта** (участники Я/Артём → их физ-порталы через присутствие `MemberPortal`, фанаут `sync_targets` на бэке) — НЕ owner (заказчик может не иметь портала); (в) инфра-раскладка → `clikit.AppConfig`. Тип ортогонален всему. |

## 3. Иерархия и граница human/AI

```
Epic        ← «человеческий», крупный/понятный. Прилетел из Б24/Notion ИЛИ создан локально.
 │            СИНКАЕТСЯ наружу (команда видит одну понятную единицу).
 ├─ Task         ← для ИИ-агента (assignee kind=ai_agent). Живёт ЛОКАЛЬНО.
 │   ├─ ChecklistItem  ← шаги для агента. Локально.
 │   └─ …
 └─ Task …
              ↑ под-дерево по умолчанию НЕ покидает Atlas → команда не захламляется,
                на каждую ступень можно посадить своего ИИ-агента.
```

Это **ровно иерархия superpowers**: `spec(эпик) → plan(задачи) → steps(чеклист)`. Граница синка возникает из **двух независимых факторов**:

1. **Присутствие исполнителя** (механизм `MemberPortal`/`sync_targets`, уже на бэке): ИИ-агент-исполнитель **не присутствует** ни в одном командном портале → его задачу физически некуда фанаутить. «ИИ-кухня наружу не идёт» — эмерджентно.
2. **Политика-потолок проекта** (`SyncPolicy`, новое): до какого *уровня иерархии* класс проекта вообще *разрешает* выгрузку. Отсекает «человек делает dev-задачу» (исполнитель присутствует, но политика dev-проекта = `local`).

Элемент уходит наружу ⇔ **оба** «за». `if project.type == 'dev'` нигде нет.

## 4. Доменная модель — расширение локального atlas

> Все изменения схемы — миграции Alembic, **Ask First** (отдельный gate с Дмитрием), бэкап `atlas.db` перед применением.

**Новые сущности:**
- `Counterparty` (`id`, `slug`, `kind` ∈ person|company, `name`, `git_namespace?`, `backend_id?`) — контрагент-владелец/заказчик (Цифро.Про / Дмитрий / клиент / Артём); зеркало core-`Counterparty`. Owner-теги `cifro-pro`/`dmitry` мигрируют сюда.
- `Epic` (`id`, `slug`, `project_id`, `title`, `goal?`, `status`, `starts_at?`/`ends_at?` — эпик=спринт с опц. датами, `backend_id?`, timestamps).
- `ChecklistItem` (`id`, `task_id`, `text`, `is_done`, `position`, `backend_id?`).
- `TaskMember` (`task_id`, `participant_id`, `role` ∈ responsible|executor|watcher) — расширение одиночного `assignee_id` до мульти-членства (нужно для синкаемых клиентских задач). `Task.assignee_id` оставляем как denormalized «главный исполнитель» (обратная совместимость существующих команд), новый слой пишет и в `TaskMember`.
- `SyncPolicy` (`slug` PK, `name`, `sync_epic` bool, `sync_task` bool, `sync_checklist` bool). v1 — три булевых уровня (не матрица портал×уровень — YAGNI). Сиды: `local`(F,F,F), `epics`(T,F,F), `media`(T,T,F), `full`(T,T,T).
- `Outbox` (`id`, `op` ∈ create|update|delete, `entity_kind`, `entity_id`, `payload_json`, `status` ∈ pending|sent|failed, `attempts`, `created_at`, `sent_at?`, `last_error?`).
- `SyncCursor` (`channel` PK, `cursor` — ISO `occurred_at` последнего применённого pull-события).

**Изменения существующих:**
- `Task`: `+ epic_id?` (FK Epic; `sprint_id` мигрирует в него), `+ backend_id?`.
- `Project`: `+ sync_policy` (FK `SyncPolicy.slug`, default из типа), `+ owner_id?` / `+ customer_id?` (FK `Counterparty`: принадлежность + заказчик; **owner задаёт пространство синка и git-namespace**), `+ backend_id?`.
- `ProjectType`: `+ default_sync_policy` (FK `SyncPolicy.slug`). **Сиды дефолтов (данные, §12.6):** dev-типы (`personal-utility`/`personal-project`/`shared-infrastructure`/`business-product`) → **`epics`** (вехи наружу, ИИ-кухня скрыта); `client-project` → **`full`**. Политика `media` есть в справочнике, назначается вручную (отдельного типа `media` нет — YAGNI).
- `Participant`: `kind=ai_agent` уже есть. Присутствие в порталах atlas **не хранит** — это знает бэк (`MemberPortal`); фактор присутствия применяется на бэке.

**Принцип `backend_id` (модель A):** Atlas — портал, поэтому хранит **одно поле `backend_id`** на каждой синкаемой сущности (= id сущности в ядре хаба), а НЕ таблицу `entity_links` (она живёт на бэке и хранит external_id всех порталов). Привязка/дедуп — по `backend_id`.

**Де-хардкод классов «клиент / мой / Cifro» (решение C). Ключевое: НЕ путать контрагента и порталы синка — это РАЗНЫЕ измерения.**
Сейчас принадлежность зашита косвенно: `TYPE_TO_GROUP` (`pm/paths.py:39`), `TOP_LEVEL_GROUP="cifropro1"` (`pm/git_paths.py:33`), `GROUP_FOLDER_NAMES`, owner-теги. Разводим на отдельные измерения:
- **Контрагент (данные, на проекте):** `Project.owner` = принадлежность (чей проект), `customer` = заказчик. От `owner` вытекает **git-namespace** (где репо: `cifropro1` / namespace Артёма). Контрагент-заказчик может вообще **не иметь портала** — это бизнес-связь, НЕ адрес синка.
- **Куда синкать задачи (вычисляется, НЕ хранится на проекте):** определяется **командой проекта** (`ProjectParticipant`: Я, Артём, ИИ-агенты). Каждый участник присутствует в своих физических порталах (`MemberPortal`: Я → Notion + Б24 Цифро; Артём → Б24 Артёма). Фанаут идёт в порталы участников (`sync_targets` = команда × присутствие) — **на бэке**; Atlas лишь шлёт событие с участниками, порталы сам не вычисляет.
- **Инфра-раскладка (конфиг):** зашитые маппинги групп/namespace → `clikit.AppConfig` (`config.toml`, слои). Группа/namespace выводятся из `(type, owner)`; дефолты сохраняют `cifropro1`, junction-физику НЕ ломаем.
- **Тип проекта** — ось «характер работы» (client/product/utility), ортогональная owner, команде и политике. Принадлежность ≠ тип: клиентский Артёма и клиентский Цифро — один `type`, разные owner.

## 5. Архитектура sync-движка (atlas-сторона)

Новый пакет `src/atlas/pm/sync/` — модули с одной ответственностью (SOLID):

| Модуль | Ответственность | Зависит от |
|---|---|---|
| `backend_client.py` | доменная обёртка над `clikit.HttpClient`: `push_events(list)`, `poll_events(since, timeout)`. Auth `X-API-Key`. | clikit transport |
| `policy.py` | `should_sync(level, project) -> bool` по `SyncPolicy`. Чистая функция. | модели |
| `mapper.py` | atlas-сущность ↔ core DomainEvent payload (epic/task/checklist, с `backend_id`). Аналог `notion_map`/`bitrix_map`. | модели, контракт events |
| `outbox.py` | enqueue при локальной операции (если `policy.should_sync`), чтение pending, mark sent/failed. | модели, policy |
| `push.py` | `atlas sync push`: pending outbox → `backend_client.push_events` → сохранить `backend_id` из ответа на сущности → mark sent. | outbox, mapper, backend_client |
| `pull.py` | `atlas sync pull`: long-poll → применить события (upsert по `backend_id`, create/update/delete) → продвинуть `SyncCursor`. Идемпотентно. | mapper, backend_client |
| `daemon.py` (опц.) | `atlas sync watch`: цикл long-poll pull + flush push. Постоянный синк для ИИ-агентов. | push, pull |

**DIP:** `outbox.enqueue` зависит от `policy.should_sync` (абстракция), не от типов проектов. Новый класс проектов = строка в `SyncPolicy`-сидах, не правка кода.

## 6. Поток данных

**Локальное изменение → наружу (push):**
```
atlas pm-tasks add … 
  └─ создать Task локально (+ TaskMember)
  └─ outbox.enqueue("create","task",id, payload)   ← ТОЛЬКО если policy.should_sync("task", project)
atlas sync push
  └─ POST /api/v1/events  [{op, entity_kind, backend_id?, fields}]   (X-API-Key)
       backend: оркестратор apply_event → core upsert → targeting(присутствие×scope, минус Atlas) → fanout Notion/Б24
       ← ответ: backend_id'ы созданных сущностей
  └─ сохранить backend_id на локальной сущности, outbox mark sent
```

**Снаружи → локально (pull):**
```
atlas sync pull   (или watch)
  └─ GET /api/v1/events/poll?since=<cursor>&timeout=25   (держит соединение, мгновенная доставка)
  └─ для каждого события: upsert локальной сущности ПО backend_id (create/update/delete)
  └─ продвинуть SyncCursor.cursor = max(occurred_at)
```

**Идемпотентность / эхо-подавление:**
- привязка строго по `backend_id`; pull-upsert идемпотентен (повторное событие не плодит дубль);
- backend в фанауте исключает источник (`targeting` «минус Atlas-портал») → своё же изменение не возвращается петлёй;
- `slug` — локальный физический якорь (как в framework); `name` синкается.

## 7. clikit-интеграция (полная, инкрементально)

| Сейчас в atlas | Становится на clikit |
|---|---|
| `config.py` (dataclass + load_dotenv) | `AtlasConfig(clikit.AppConfig)`: `base_url`, `api_key` (`${ATLAS_API_KEY}`/SecretStore), `portal_id="atlas-local"`, `notion_token`… Слои global/project/local. |
| `~/.atlas` хардкод | `clikit.AppPaths("atlas")` (platformdirs). **Совместимость:** дефолт оставить `~/.atlas/atlas.db`; миграция путей — отдельный безопасный шаг. |
| `cli.py` root `typer.Typer()` | `clikit.build_root_app("atlas", version=…)` (+`--json`/`--text`/`--profile`/`--version`). |
| `console.print(...)` по командам | `emit_data`/`emit_error` (**`--json` по умолчанию** — для ИИ-агентов; `--text` = Rich для человека). |
| ad-hoc try/except | `@command`/`@async_command` (единый контракт ошибок). |
| — | `gated(permission=…)` для RBAC-команд (позже). |

**Трение auth-header:** бэк ждёт `X-API-Key`, `clikit.HttpClient` шлёт `Authorization: Bearer`. **Решение v1:** слать `X-API-Key` через `extra headers` в `backend_client`. **Лучше (обкатка clikit):** расширить `clikit.HttpClient` опцией `auth_header_name`/`auth_scheme` (контракт «расширяется, не ломается») — F3 станет первым реальным потребителем и выявит, чего clikit не хватает.

**sync↔async:** существующие команды sync (Typer + sync SQLAlchemy) не трогаем сразу; sync-слой — `@async_command` (`asyncio.run` внутри). Локальный стор остаётся sync.

## 8. Аутентификация и провижн

- admin-API-ключ Дмитрия (выпущен на бэке, F1b) хранится в `SecretStore` (keyring) или `${ATLAS_API_KEY}`; в git/`.env`-в-репо не кладём.
- На бэке — seed портала `atlas-local` (Portal) + `portal_id` в конфиге atlas.
- `X-API-Key` → бэк резолвит principal (admin-bypass на старте).

## 9. Зависимости от backend (что доделать на стыке, при выкате)

- **seed Portal `atlas-local`** на бэке.
- **intake «прилетело → эпик»**: внешняя верхнеуровневая задача (Б24/Notion) трактуется бэком как core `Epic`, а не `Task` (доработка F2-intake). Для F3 *само по себе не блокирует*: atlas создаёт эпики локально и шлёт `epic.create`.
- **контракт payload `/events`**: свериться с `EventIn`-схемой бэка; зафиксировать публичный формат полей (epic/task/checklist) — единый словарь статусов/ролей (часть «унификации»).
- фанаут эпиков/задач atlas в Notion/Б24 — адаптеры F2 (готовы).

## 10. Под-этапы F3 (декомпозиция эпик→задачи, в духе superpowers)

- **F3a — clikit-фундамент** (без изменения модели): `AtlasConfig`, `backend_client` (HttpClient + X-API-Key), root на `build_root_app`, `--json` по умолчанию. Тесты.
- **F3b — доменная модель** (Ask First, бэкап): миграции Alembic (Epic, ChecklistItem, TaskMember, **`Counterparty` + `Project.owner`/`customer`**, `backend_id`-поля, SyncPolicy, Outbox, SyncCursor) + сиды (политики + контрагенты из owner-тегов) + `ProjectType.default_sync_policy`. Тесты миграций.
- **F3b-infra — де-хардкод раскладки**: `TYPE_TO_GROUP`/`TOP_LEVEL_GROUP`/namespace → `clikit.AppConfig` (слои); группа/namespace выводятся из `(type, owner)` с дефолтами текущего поведения. Junction-физика не ломается (тесты раскладки зелёные). `owner` → порталы пространства синка.
- **F3c — outbox + push**: enqueue на локальных операциях через `policy.should_sync`, `atlas sync push` → `/events`, сохранение `backend_id`. Тесты на фейк-бэке.
- **F3d — pull + long-poll**: `atlas sync pull`/`watch`, применение событий, курсор, идемпотентность. Тесты.
- **F3e — миграция команд на clikit + CRUD иерархии**: перевод существующих групп на `emit_data`/`@command` (по одной, TDD); новые команды `atlas epic …`, `atlas checklist …`, мульти-member.
- **F3f — skill `atlas`** (superpowers-канон): `SKILL.md` ≤500 слов (frontmatter `description` = «Use when…» триггеры) + `references/` (полный список команд) + при нужде `scripts/`. Под ИИ-агентов.
- **Выкат F3** (отдельно, с Дмитрием): seed `atlas-local` на бэке, согласование сидов политик atlas↔backend, e2e против реального бэка.

## 11. Тестирование (TDD Iron Law)

- pytest + фейк-бэк (DI `http_client` в `HttpClient` / `httpx.MockTransport`).
- **policy**: `local` не кладёт `task` в outbox; `full` кладёт; `epics` кладёт только эпик.
- **idempotency**: повторный pull одного события — без дублей (upsert по `backend_id`).
- **push**: outbox pending → sent, `backend_id` сохранён.
- **миграции**: как существующие `test_pm_migration_*`.
- coverage ≥80% для `pm/sync`.

## 12. Открытые развилки (зафиксировать, не блокеры v1)

1. **Конфликт-резолюция** (изменено и локально, и на хабе): v1 — last-write-wins по `occurred_at`; field-level merge — позже.
2. **Override синка на конкретной задаче** (force-sync ИИ-задачи человеку): v1 опускаем (YAGNI); при нужде — `Task.sync_override` + первоклассная команда.
3. **Согласование справочника `SyncPolicy` atlas↔backend**: один список в сидах обоих; при расхождении — backfill. (atlas и backend — разные БД, как уже есть для статусов/типов.)
4. **Пути стора**: остаёмся на `~/.atlas`; переход на `AppPaths`/platformdirs — отдельный безопасный шаг.
5. **Гранулярность события дерева**: эпик+дети одним батчем vs поэлементно — уточнить при реализации push (батч `/events` уже поддержан параметром).
6. **Маппинг тип→дефолт-политика + «медийка»** — **РЕШЕНО (2026-06-14, дефолты; Дмитрий поправит данными):** (а) эпики dev-проектов синкаются → дефолт dev-типов = **`epics`** (вехи видны команде/клиенту, ИИ-задачи и чеклисты скрыты); (б) **отдельный тип `media` НЕ заводим** — политика `media` (эпик+задача, без чеклистов) есть в справочнике и назначается проекту вручную (`Project.sync_policy`); станет частой — добавим тип позже (данные, без миграции структуры).

---

**Терминальное состояние брейнсторма:** после ревью этой спеки Дмитрием → `writing-plans` для F3a (clikit-фундамент) как первого под-этапа.
