# PRD — NP-005 Personal PM Infrastructure

**Версия**: v0.3 (2026-04-22, после pivot на DB-first + Superpowers + multi-agent readiness)
**Автор**: Дмитрий (видение), Claude (формализация)
**Статус**: 📝 Черновик; полноценный PRD готовится после второй волны deep research (см. [RESEARCH_QUESTIONS_V2.md](./RESEARCH_QUESTIONS_V2.md))

---

## 0. Связанные документы

- [OVERVIEW.md](./OVERVIEW.md) — контекст, проблема, границы (v0.1)
- [METHODOLOGY.md](./METHODOLOGY.md) — статус гипотез
- [ARCHITECTURE.md](./ARCHITECTURE.md) — техническая реализация
- [MODEL.md](./MODEL.md) — схема БД
- [BACKLOG.md](./BACKLOG.md) — задачи Sprint 1
- [RESEARCH_QUESTIONS_V2.md](./RESEARCH_QUESTIONS_V2.md) — **ultraplan** для второй волны deep research (IT PM, Scrum, multi-agent). Блокнот `9f109c5e-312f-4058-9c98-aee59853c58e`, 10 блоков запущены 2026-04-22, ждём завершения
- [research/](./research/) — ответы первой волны research v1 + запускалка v2 (`v2_launch_research.py`)

---

## 1. Видение (North Star)

**Построить персональную операционную систему для всех проектов Дмитрия, которая**:
1. Хранит портфель в **реляционной БД** (не в markdown-файлах), с возможностью расширять поля и делать миграции.
2. Работает с AI-агентами как с **первоклассными исполнителями** через плагин `superpowers` и AGENTS.md-канонизацию.
3. **Масштабируется на мультиагентную команду** в будущем: когда Дмитрий подключит платформу (OpenClaw / paperclip / аналог), в системе уже будут роли агентов (CEO, PM, Marketing, Knowledge, QA, Developer), и PM-агент будет координировать работу через ту же самую PM-систему.
4. Использует **Scrum-подход**: спринты, бэклоги, ceremonies, velocity. Superpowers применяется на уровне отдельной задачи, Scrum — на уровне спринта/эпика.
5. Связывает проект с **git-репозиторием**, **локальной папкой**, **подписками/расходами**, **участниками (людьми и агентами)**, **ЦКП (Ценными Конечными Продуктами)**.

**Итоговое назначение**: когда Дмитрий подключает мультиагентную команду, **эта система становится операционкой координации всех участников — людей, AI-агентов, внешних подрядчиков**.

---

## 2. Пользователи и stakeholders

### 2.1 Primary (сейчас)

- **Дмитрий** — Оркестратор-одиночка, основной пользователь. Использует систему для планирования, трекинга прогресса, onboarding новых проектов, рефлексии.
- **Claude Code** — AI-партнёр Дмитрия. Читает AGENTS.md проектов, берёт задачи из BACKLOG, исполняет через Superpowers workflow, обновляет PM-систему по завершении.

### 2.2 Secondary (в ближайшее время)

- **NotebookLM** — research-engine, интегрирован через CLI. Хранит ссылки на блокноты в PM.
- **Notion** — личный зеркально-инбокс UI Дмитрия (он уже привык). PM-система умеет push/pull.

### 2.3 Future (через 1-3 месяца)

- **Multi-agent команда** через OpenClaw / paperclip / Agent Zero или аналог:
  - **AI-CEO** — стратегические решения по портфелю (приоритизация, pivot-решения).
  - **AI-PM** — ведёт все проекты, периодически опрашивает как идёт каждый, корректирует планы, управляет спринтами, проводит ceremonies.
  - **AI-Marketing** — работа с маркетинговыми материалами, SEO, контент.
  - **AI-Knowledge** (= NP-004 Knowledge Conveyor) — обработка входящей информации в skills и knowledge base.
  - **AI-QA** / **AI-Reviewer** — Adversarial review, ispec compliance, code quality (уже есть в Superpowers).
  - **AI-Developer** (Claude Code или Codex) — исполняет tasks.
  - Могут быть и другие роли по мере надобности.
- **Внешние подрядчики (люди)** — разработчики, дизайнеры, маркетологи, которых Дмитрий нанимает под конкретные проекты.
- **Клиенты Cifro.pro** — косвенные stakeholders (их задачи попадают в проекты, но они сами не пользуются PM-системой Дмитрия).

### 2.4 Access model

- **Дмитрий** — полный доступ: чтение, запись, конфигурация, миграции.
- **AI-агенты** — доступ по ролям (RBAC): PM-агент пишет задачи, QA-агент читает + пишет ревью, Developer-агент закрывает задачи.
- **Подрядчики** — ограниченный доступ к своим проектам, read-only на остальное.

---

## 3. Принципы

1. **DB-first, не markdown-first.** Основной источник правды — реляционная БД (SQLite для MVP, миграция на PostgreSQL когда 2+ агента параллельно пишут).
2. **Schema-migration-ready.** Любое изменение схемы — через Alembic миграции. Никаких ручных правок БД.
3. **AGENTS.md — канонический файл контекста** (не CLAUDE.md). Универсальный стандарт, независимый от конкретного AI.
4. **Superpowers на уровне задачи.** Каждый task из BACKLOG, который требует написания кода, проходит полный workflow `brainstorming → writing-plans → using-git-worktrees → subagent-driven-development → finishing-a-development-branch`.
5. **Scrum на уровне спринта.** 2-недельные спринты, sprint goal, sprint review, retrospective, refinement. Burndown чарт derived из статуса tasks.
6. **ЦКП как обязательное поле каждой задачи.** Не «сделать X», а «какую измеримую ценность это даёт». Термин из менеджмент-технологии Хаббарда — «Ценный Конечный Продукт».
7. **API-first для multi-agent readiness.** Сразу закладываем REST API (или GraphQL) для программного доступа. Сейчас читаем только Дмитрий и Claude Code — завтра подключатся другие агенты.
8. **Append-only audit log.** Все изменения фиксируются в `action_log` таблицу. Чтобы AI-PM мог задним числом реконструировать «кто что когда делал».
9. **Notion как surface-слой, не SSOT.** Дмитрий работает в Notion глазами (личный инбокс, due-dates), но канон — в БД.
10. **Git как исполнительный слой.** Репозиторий привязан к проекту. Статусы PR / merge / branch влияют на состояние задач.

---

## 4. Функциональные требования (MVP)

### 4.1 Сущность Project — минимальный набор полей (из промпта Дмитрия)

| Поле | Тип | Обязат | Описание |
|---|---|---|---|
| `id` | UUID | ✅ | — |
| `name` | string | ✅ | Название проекта |
| `slug` | string (unique) | ✅ | Машинный идентификатор (cifro-porta, np-005, docs-parsing) |
| `type_id` | FK → `project_types` | ✅ | Тип проекта; **связь**, не enum — чтобы добавлять новые типы без миграции |
| `description` | text | ✅ | Что за проект, для чего, контекст |
| `estimated_deadline` | date | — | Примерный срок реализации |
| `status_id` | FK → `project_statuses` | ✅ | Lifecycle: experiment / active / maintained / dormant / archived / graduating |
| `priority` | enum (P0/P1/P2/P3) | ✅ | — |
| `git_repo_url` | string | — | Ссылка на git-репо (локальный или удалённый) |
| `local_path` | string | — | Абсолютный путь на локальной машине Дмитрия |
| `one_line_summary` | string | ✅ | 1-строчное описание для листингов и реестра |
| `created_at` / `updated_at` | timestamp | ✅ | — |

### 4.2 Сущность `PRD_Snapshot` — сводка PRD (из промпта)

Отдельная таблица, связанная с Project 1:N (версионирование PRD).

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID | — |
| `project_id` | FK | — |
| `version` | string | v1, v2, ... |
| `pain` | text | Какую боль решает |
| `features` | json | Список ключевых фич |
| `primary_user` | text | Главный пользователь / ICP |
| `metrics` | json | Метрики успеха (список) |
| `created_at` | timestamp | — |

### 4.3 Сущность `Stack` и `ProjectStack`

Нужна М:N связь проектов и технологий.

| Таблица `stacks` | | |
|---|---|---|
| `id`, `name` (Python 3.11 / Bitrix24 REST / Notion API / Claude API / LangGraph / …), `category` (language / framework / service / db / integration), `official_docs_url` | | |

| Таблица `project_stacks` | | |
|---|---|---|
| `project_id`, `stack_id`, `role` (core / dependency / integration / optional), `notes` | | |

### 4.4 Сущность `Expense` — траты (из промпта)

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID | — |
| `project_id` | FK | — |
| `description` | string | Что оплачивается (Motion subscription, Anthropic API, Vercel hosting) |
| `amount_monthly` | decimal | Ежемесячная цена (если подписка) |
| `amount_one_time` | decimal | Единоразовая цена (если покупка) |
| `currency` | string (RUB/USD/EUR) | — |
| `category` | enum (subscription / api-usage / hosting / hardware / contractor-fee / other) | — |
| `started_at` / `ended_at` | date | Период действия |

Агрегат: Дмитрий должен видеть **общий ежемесячный burn** по всем проектам и по каждому отдельно.

### 4.5 Сущности `Participant` и `ProjectParticipant`

Расширяется на multi-agent будущее.

| Таблица `participants` | | |
|---|---|---|
| `id`, `kind` (human / ai_agent / contractor), `name`, `role_default` (CEO / PM / Developer / Marketing / Knowledge / QA / Designer / Owner), `metadata_json` (для AI — модель, платформа; для контрактника — email, ставка) | | |

| Таблица `project_participants` | | |
|---|---|---|
| `project_id`, `participant_id`, `role_in_project` (может отличаться от `role_default`), `allocated_time_weekly_hours` | | |

**Seed data для MVP**:
- `('human', 'Дмитрий', 'Orchestrator')` — Primary role
- `('ai_agent', 'Claude Code', 'Developer/PM')` — через AGENTS.md выполняет задачи
- Остальные роли добавятся с подключением мультиагентной платформы.

### 4.6 Сущности `Sprint` и `Task`

Обязательно для Scrum-подхода.

| Таблица `sprints` | | |
|---|---|---|
| `id`, `name` (Sprint 1), `start_date`, `end_date`, `goal`, `status` (planning / active / review / done), `velocity_story_points` | | |

| Таблица `tasks` | | |
|---|---|---|
| `id`, `project_id` (FK), `sprint_id` (FK, nullable), `title`, `description`, `assignee_id` (FK → participants), `status` (backlog / todo / in_progress / review / done / blocked), `priority` (P0-P3), `due_date`, `story_points` (int), **`cpp_description` (text — ЦКП этой задачи)**, `notion_page_id`, `git_branch`, `git_pr_url`, `superpowers_spec_path`, `superpowers_plan_path`, `created_at`, `updated_at` | | |

### 4.7 Audit log

| Таблица `action_log` (append-only) | | |
|---|---|---|
| `id`, `timestamp`, `actor_id` (FK → participants), `entity_type` (project / task / sprint / ...), `entity_id`, `action` (created / updated / status_changed / assigned / ...), `details_json` | | |

### 4.8 Операции (CLI / API)

- `portfolio list` — все проекты с фильтрами по типу / статусу / участнику
- `portfolio show <slug>` — полная карточка проекта
- `portfolio create` / `update` / `archive`
- `sprint plan <name> --goal "..." --start ... --end ...`
- `sprint add-task <sprint> --project <slug> --title "..." --cpp "..."`
- `sprint show <name>` — список задач + burndown
- `task done <id>` / `task block <id>` / `task assign <id> <participant>`
- `expense add --project <slug> ...`
- `expense report [--month YYYY-MM] [--project <slug>]` — агрегат расходов
- `action-log tail --project <slug>` — последние N событий

### 4.9 Интеграции

- **Notion** — две команды `notion mirror-push` / `notion mirror-pull-inbox` (см. ARCHITECTURE §3). Зеркалит pending tasks и pull'ает inbox-идеи.
- **Git** — pre-commit hook обновляет `task.git_branch`; post-merge hook двигает задачу в `done`.
- **Superpowers** — при запуске `superpowers:writing-plans` пишет в `task.superpowers_plan_path`; при `finishing-a-development-branch` обновляет статус задачи.
- **NotebookLM** — `research_finding` записи указывают на `notebook_id`.

---

## 5. Нефункциональные требования

| NF | Решение |
|---|---|
| **Хранилище** | SQLite (MVP, offline, single-player). PostgreSQL (когда 2+ агента параллельно) |
| **ORM** | SQLAlchemy 2.x (async-ready) |
| **Миграции** | Alembic |
| **CLI** | Python 3.11+, расширение `atlas` (обратная совместимость команд) |
| **API** | FastAPI (optional в MVP, обязателен перед подключением OpenClaw) |
| **Auth** | MVP — single-user. Future — API-токены per-agent + RBAC |
| **Бэкап** | Git-репо самой БД (SQLite-файл коммитится в отдельный `portfolio-db` репо), ежедневный dump |
| **Portability** | Windows 11, Mac — без разницы (Python) |
| **Audit** | append-only `action_log`, не редактируется |

---

## 6. Мультиагентность (future-proofing)

### 6.1 Архитектурный принцип

**Вся схема БД должна поддерживать подключение агентов БЕЗ переработки.** Поэтому:
- `participants` с полем `kind` (human / ai_agent / contractor) — уже готово.
- `task.assignee_id` — любой participant, не только человек.
- `action_log.actor_id` — фиксирует кто (человек или агент) сделал изменение.
- API-first подход — агенты ходят через REST/MCP, а не через CLI (хотя CLI тоже работает).

### 6.2 Предполагаемые роли AI-агентов

| Роль | Обязанности | Когда подключаем |
|---|---|---|
| **AI-CEO** | Стратегические решения портфеля, pivot-моменты, reorg приоритетов | Q3 2026 |
| **AI-PM** | Ведёт все проекты, опрашивает статус, корректирует sprint plans, проводит ceremonies | Q2-Q3 2026 |
| **AI-Marketing** | Контент, SEO, конкурентная разведка, ценностное предложение | Q3 2026 |
| **AI-Knowledge** | = NP-004 (Knowledge Conveyor) — обработка входящей информации в skills | Уже в плане NP-004 |
| **AI-QA / Reviewer** | Adversarial review — уже в Superpowers (`code-quality-reviewer`) | Уже доступно |
| **AI-Developer** | = Claude Code / Codex — исполняет tasks | Уже подключён |
| **AI-Designer** | UI/UX артефакты, figma, mockups | По мере надобности |

### 6.3 Платформа мультиагентности

**Дмитрий рассматривает**: OpenClaw, paperclip, или аналог. Выбор — после deep research (Блок D в [RESEARCH_QUESTIONS_V2.md](./RESEARCH_QUESTIONS_V2.md)).

### 6.4 Inter-agent протокол

- **MVP**: REST API + прямая БД (агенты ходят через FastAPI).
- **V2**: MCP (Model Context Protocol) — если платформа поддерживает.
- **V3**: Agent2Agent (A2A) / ACP — если появится зрелый стандарт.

---

## 7. Scrum-слой

### 7.1 Терминология

- **Epic** — крупная фича, перекрывающая несколько спринтов. Привязан к `project`.
- **Sprint** — 2-недельный цикл работы. Содержит много `tasks` из одного или нескольких проектов.
- **Task** — минимальная единица работы, 1-3 дня max. Одна задача = одна ЦКП.
- **Story point** — оценка сложности задачи (Fibonacci: 1, 2, 3, 5, 8, 13).

### 7.2 Sprint ceremonies (для Orchestrator-одиночки + AI-команды)

| Ceremony | Когда | Участники | Длительность |
|---|---|---|---|
| **Sprint Planning** | Первый день спринта | Дмитрий + AI-PM | 30-60 мин |
| **Daily Standup** | Каждый день | Дмитрий + AI-агенты (письменно, через action_log) | 5 мин |
| **Sprint Review** | Последний день | Дмитрий + AI-PM + все агенты | 30 мин |
| **Retrospective** | Последний день | Дмитрий + AI-PM | 30 мин |
| **Backlog Refinement** | Middle of sprint | Дмитрий + AI-PM | 30 мин |

Ceremonies выполняются через CLI-команды `sprint plan` / `sprint review` / `sprint retro` с тем, чтобы результат был structured в БД.

### 7.3 Velocity / burndown

- `sprint.velocity_story_points` — derived: сумма `story_points` всех задач в статусе `done` за спринт.
- Burndown: агрегат по дням — сколько story points осталось.
- После 3 спринтов — стабильная velocity, можно прогнозировать ёмкость.

---

## 8. Superpowers integration (dev-слой)

Каждая `task`, требующая написания кода, проходит Superpowers workflow:

1. `brainstorming` → spec → файл в `<repo>/_project/docs/SCALING_PRODUCT/<project>/specs/YYYY-MM-DD-<task-slug>.md`.
2. `writing-plans` → plan → `<repo>/_project/docs/SCALING_PRODUCT/<project>/plans/YYYY-MM-DD-<task-slug>.md`.
3. `using-git-worktrees` → `.worktrees/<task-slug>/`.
4. `subagent-driven-development` (T1) или `executing-plans` (T2) → код + тесты + 2-stage review.
5. `finishing-a-development-branch` → merge / PR.
6. Post-hook: PM-система получает update:
   - `task.status = done`
   - `task.git_pr_url = ...`
   - `action_log` добавляет запись

### Tier per project (AGENTS.md frontmatter)

```yaml
---
type: "client_project" | "business_product" | "personal_utility" | "personal_project" | "shared_infrastructure"
pm_project_slug: "cifro-pro"
pm_project_id: "a1b2c3d4-..."
quality_tier: "T1" | "T2" | "T3"
superpowers_enabled: true
---
```

- **T1** (full Superpowers) — business-products, critical clients
- **T2** (writing-plans + TDD + verification, без subagent-driven) — maintained utilities, non-critical clients
- **T3** (только verification) — experiments, spikes

---

## 9. Roadmap

| Версия | Deliverables | Окно | Статус |
|---|---|---|---|
| v0.3 | PRD + MODEL + ARCHITECTURE v2 + research questions v2 | 2026-04-22 (сегодня) | ✅ В работе |
| **v0.4 (Spike)** | SQLite MVP: `projects`, `project_types`, `participants`, `tasks`, `action_log`. 4-6 CLI команд. 1 pilot project onboarded через Superpowers. | 2026-04-23 → 2026-04-30 | ⏳ Ждёт утверждения |
| v0.5 (Sprint 1) | `sprints`, `expenses`, `prd_snapshots`, `stacks`. Notion mirror (push + pull-inbox). 3 проекта onboarded. Scrum ceremonies в CLI. | 2026-04-30 → 2026-05-14 | 📋 Запланировано |
| v0.6 (Sprint 2) | Все 10 клиентов + 7 утилит + 5 бизнес-продуктов onboarded. API (FastAPI). AGENTS.md шаблоны устоялись. | 2026-05-14 → 2026-05-28 | 📋 |
| v0.7 | Multi-agent API groundwork: роли, RBAC, token auth. Первые эксперименты с AI-PM агентом (читает БД, генерит sprint plan draft). | Q3 2026 | 🔮 |
| v1.0 | OpenClaw / paperclip интеграция (или аналог). Все 7 AI-ролей активны. Burn rate под контролем. Дмитрий больше не пишет код вручную. | Q4 2026 | 🔮 |

---

## 10. Success metrics (для самой PM-системы)

| Метрика | Цель v0.4 (Spike) | Цель v0.6 (Sprint 2) | Цель v1.0 |
|---|---|---|---|
| **Portfolio coverage** (% активных проектов в БД) | 10% (1/10+14+5) | 90% | 100% |
| **Agent time-to-context** (секунд от `cd <repo>` до «я знаю что делать») | < 3 мин | < 2 мин | < 1 мин |
| **Task acceptance rate** (% PR'ов Claude Code без правок) | ≥ 60% | ≥ 75% | ≥ 85% |
| **Sprint velocity stability** (CV между 3 спринтами) | N/A | < 30% | < 15% |
| **Expense forecast accuracy** (актуал vs прогноз) | N/A | ± 20% | ± 10% |
| **Cognitive load** (время Дмитрия на админку в неделю) | < 2 ч | < 1 ч | < 30 мин |

---

## 11. Anti-goals (что НЕ является целью)

- ❌ **Публичный SaaS.** Сейчас для себя. Graduating в продукт — только если появится external demand (см. критерии NP-005 §8 в ARCHITECTURE.md).
- ❌ **Jira/Asana клон.** Не пытаемся сделать generic PM-tool. Специализация — solo Orchestrator + AI-агенты.
- ❌ **Полностью автоматический AI-agent без человека.** Дмитрий остаётся final approver (Human-on-the-loop).
- ❌ **UI-first.** Не строим сложный web-dashboard. CLI + Notion mirror — достаточно. Dashboard — если появится реальная боль.

---

## 12. Open questions → Deep Research v2

Зафиксированы в [RESEARCH_QUESTIONS_V2.md](./RESEARCH_QUESTIONS_V2.md). Ключевые:

- Как устроено управление IT-проектами в разных моделях (agency / product / consulting / studio)?
- Как работает PM-роль детально — ceremonies, артефакты, мostly-используемый toolkit?
- Scrum vs Kanban vs hybrid — что для solo + AI-команда?
- Multi-agent orchestration: OpenClaw vs paperclip vs Agent Zero vs AutoGPT vs CrewAI?
- Inter-agent protocols — MCP, A2A, ACP — что выбирать?
- Database schema патenrs для PM — что используют Jira/Linear/ClickUp/Notion под капотом?
- ЦКП (Ценный Конечный Продукт) / OKR / KPI — как правильно формулировать на уровне task?
- Migration from markdown/Notion to custom PM — кейсы, подводные камни?

## 13. Riscks

- **Over-engineering БД**: схема из 10+ таблиц для одного человека — избыточна. Митигация: начать с 5 таблиц в MVP, расширять по мере надобности.
- **Multi-agent lock-in**: выбрать не тот фреймворк — переделка. Митигация: API-first, не использовать vendor-specific features в MVP.
- **Notion drift**: бизнес-логика PM-системы vs ручные правки Дмитрия в Notion → расхождение. Митигация: canonical-field-per-concept (как в ARCHITECTURE §3).
- **Burn rate**: больше подписок и API-usage чем эффект. Митигация: `expenses` таблица + monthly review ритуал.
