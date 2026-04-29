# CHANGELOG — NP-005 Personal PM Infrastructure

## 2026-04-24 — v0.4.1: полный CRUD MVP + переименование portfolio → projects

- Переименование: `notion-task-cli` → `atlas` (папка + CLI + env var). Отдельный ACTION_LOG про это.
- **CLI команды расширены до полного CRUD** по всем MVP-сущностям:
  - `projects add / list / get / update / delete` (soft-delete через archived_at)
  - `pm-tasks add / list / get / update / delete` (с обязательным ЦКП)
  - `participants add / list / get / update / delete` (cascade через --force, soft через --soft)
  - `types add / list`, `statuses add / list`
  - `action-log list` (read-only append-only)
- **Миграция 002** (`0d172deaa09b`): `projects.prefix`, `tasks.number`, `tasks.slug`.
- **Миграция 003** (`c55f75e76e5b`): `tasks.archived_at`.
- **Новый модуль** `src/atlas/pm/slugs.py`: `slugify_text` (с транслитерацией русского), `generate_unique_slug` (collision suffix -2/-3), `generate_prefix_from_slug`, `resolve_project_ref`, `resolve_task_ref`, `build_task_slug`, `next_task_number`.
- **Тесты**: 28 baseline → **180 passed** (+152 тестов по TDD, в том числе регрессия на flaky `resolve_task_ref` для all-digits UUID prefix).
- **Skill `atlas`** обновлён: Layer 2 переписан, slug+prefix правила для агента, ЦКП гайд, schema v0.4 MVP.
- Notion-side `projects` → `notion-projects` (для клиентов). PM-projects получил чистое имя.

### Bug fix — flaky `resolve_task_ref` (детерминирован)

- **Симптом**: `tests/test_pm_slugs.py::TestResolveTaskRef::test_resolve_by_short_uuid` периодически падал на UUID, чей short prefix состоял только из цифр (Python random UUID может выдать что-то вроде `12345678-...`).
- **Причина**: `resolve_task_ref` сначала пытался резолвить `ref.isdigit()` через `Task.number`, не находил (никакого таска с `number=12345678` нет), возвращал `None`, не успев попробовать UUID prefix.
- **Fix**: для `ref` короче `UUID_SHORT_MIN` (7) — однозначно number. Для длиннее или равной 7 и из hex-алфавита — **сначала** UUID prefix, при отсутствии match — fallback на number. Добавлен детерминированный регрессионный тест `test_resolve_by_short_uuid_when_prefix_is_all_digits` с явно сконструированным UUID `12345678-aaaa-...`.

## 2026-04-23 — v0.4 Spike выполнен (БД + CLI + Superpowers инфраструктура)

### Параллельная работа

- **Background subagent** (4 часа wall-clock): ждал завершения 10 research-блоков в NotebookLM v2. Результат: **3 из 10 блоков завершились** (B Scrum, H Capacity, E Docs), **121 source импортировано**. Остальные 7 (A, C, D, F, G, I, J) зависли на стороне NotebookLM backend (RPC POLL_RESEARCH repeatedly failing).
- **Main session** делала SP-01..SP-10.

### Создано / изменено

**`~/.claude/AGENTS.md`** — расширен блоками:
- Superpowers workflow (brainstorming → writing-plans → using-git-worktrees → subagent-driven-development → finishing-a-development-branch).
- Iron Laws: TDD + verification-before-completion.
- 5-scope AGENTS.md cascade + Compound Engineering.
- Tier system T1/T2/T3.
- Ссылки на NP-005 PM-систему.

**Шаблоны AGENTS.md**: `Metela/New Projects/_project/docs/templates/agents/` —
- `AGENTS_client_project.md` — для клиентских проектов с B24-frontmatter.
- `AGENTS_business_product.md` — для NP-XXX, quality_tier T1, TDD обязателен.
- `AGENTS_personal_utility.md` — для Tests/* утилит + graduation criteria.

**`notion-task-cli`** — git-инициализирован, добавлено:
- `.gitignore` (Python/uv/venv/`.worktrees/`/`portfolio.db`).
- `AGENTS.md` (type=shared_infrastructure, T1 tier).
- `pyproject.toml`: + sqlalchemy 2.x, alembic, python-frontmatter, pytest-cov, ruff.
- `src/notion_task_cli/pm/` — новый пакет:
  - `db.py` — engine + session factory, SQLite FK constraints, `DEFAULT_DB_PATH=~/.cifro-pm/portfolio.db`.
  - `models.py` — 7 MVP-таблиц: `ProjectType`, `ProjectStatus`, `Project` (+ check-constraint priority), `Participant` (kind ∈ human/ai_agent/contractor), `ProjectParticipant` (M:N), `Task` (с NOT NULL `cpp_description` для ЦКП), `ActionLog` (append-only). Все check-constraints и индексы согласно MODEL.md §2.
  - `seeds.py` — идемпотентные upsert: 5 project_types, 6 project_statuses, 2 participants (Дмитрий + Claude Code).
  - `commands/portfolio.py` — 6 CLI-команд (init, list, create, show, types, statuses).
- `tests/test_pm_db.py` + `tests/test_pm_seeds.py` — 9 новых тестов, **TDD RED-GREEN проверен** (6 падали ModuleNotFoundError → 9 GREEN после имплементации).
- `migrations/` — Alembic setup, `env.py` читает `Base.metadata` + env var override, `render_as_batch` для SQLite.
- `migrations/versions/0a6b3db9f107_initial_mvp_schema.py` — первая миграция автогенерирована и применена.
- `_project/docs/` scaffolding (SCALING_PRODUCT/{specs,plans}, ARCHITECTURE/decisions, PROJECT_LOG).

### Проверено (verification-before-completion)

- `pytest -v` — **28/28 тестов GREEN** (19 existing + 9 new PM). Warnings: DeprecationWarning про `datetime.utcnow()` — TODO в Sprint 1.
- `alembic upgrade head` на чистой SQLite — applies cleanly (0a6b3db9f107).
- **E2E smoke test**:
  - `notionctl portfolio init` → 5 types + 6 statuses + 2 participants засеяны.
  - `notionctl portfolio create np-005 --type business-product ...` → UUID выдан.
  - `notionctl portfolio create cifro-pro --type client-project ...` → второй проект.
  - `notionctl portfolio list` → красивая Rich-таблица 2 проекта.
  - `notionctl portfolio show np-005` → полная карточка.

### Git

- Первый коммит в `notion-task-cli` (main branch, initial).

### Что НЕ сделано в Spike (переносится)

- **SP-16 Superpowers pilot**: прогнать ОДНУ реальную фичу (`portfolio push`) через полный Superpowers workflow (brainstorming → writing-plans → worktree → subagent-driven → finishing). Отложено на старт Sprint 1.
- **SP-18 Onboarding pilot'ов**: заполнить AGENTS.md в 3 реальных проектах (cifro-pro, np-004, docs-parsing) из шаблонов. После SP-16 или параллельно.
- **SP-21 Ритуалы прожить неделю** + Monday Kickstart + Friday Wind-down + retro.
- **Research v2 — 7 зависших блоков**: перезапуск когда NotebookLM backend восстановится, либо вручную через web UI.

### Метрики Spike

| Метрика | Цель | Факт |
|---|---|---|
| Portfolio coverage | 1 проект в БД | ✅ 2 проекта (np-005 + cifro-pro в demo) |
| Agent time-to-context | < 3 мин | ⏳ не замерено (Sprint 1) |
| Task acceptance rate | ≥ 60% | ⏳ не замерено (Sprint 1) |
| Тесты GREEN | 100% | ✅ 28/28 |
| TDD compliance | RED первый | ✅ проверено на pm/ модуле |
| CLI команды работают | ≥ 4 | ✅ 6 (init/list/create/show/types/statuses) |

### Хвосты

- 7 застрявших research-блоков требуют решения (перезапуск / проверка web UI / принятие частичности).
- SQLAlchemy `datetime.utcnow()` deprecated — рефакторинг на `datetime.now(UTC)` в Sprint 1.
- `alembic.ini` содержит `sqlalchemy.url = driver://user:pass@localhost/dbname` placeholder — обойдено через env var в `env.py`, но можно убрать для чистоты.
- Не реализованы команды `task create/done/block`, `action-log tail`, `portfolio push/pull-inbox` — это Sprint 1.

## 2026-04-22 (later) — v0.3 MEGA PIVOT: DB-first + Superpowers + multi-agent readiness + AGENTS.md (не CLAUDE.md)

Дмитрий вынес 4 ключевых архитектурных решения одним сообщением:

1. **Принимаем Superpowers plugin v5.0.7 в архитектуру**. Убираем свои 4 Agent Personas и Fresh Chat Per Task — вместо этого используем отлаженный Superpowers workflow (`brainstorming → writing-plans → using-git-worktrees → subagent-driven-development → finishing-a-development-branch`) с tier-системой T1/T2/T3 в AGENTS.md frontmatter.
2. **Будем создавать/дорабатывать skill для ведения проектов и проектной документации** — на основе Superpowers + наших наработок (в backlog). Пока — через CLI-команды `notion-task-cli portfolio/sprint/task/expense`.
3. **PM-архитектура переходит на БД-first** (не markdown). SQLite для MVP → PostgreSQL когда multi-agent concurrent writes. SQLAlchemy 2.x + Alembic. Схема — в MODEL.md с таблицами: `projects`, `project_types`, `project_statuses`, `participants`, `tasks`, `sprints`, `expenses`, `prd_snapshots`, `stacks`, `project_stacks`, `action_log`, + будущие `agent_runs`, `research_findings`.
4. **AGENTS.md канонический файл (НЕ CLAUDE.md)** — для будущего multi-agent setup (OpenClaw / paperclip / аналог). Сохранено в memory feedback.

### Что создано

- **`PRD.md` v0.3** — полное видение Дмитрия: multi-agent команда будущего (AI-CEO, AI-PM, AI-Marketing, AI-Knowledge, AI-QA, AI-Developer), Scrum-слой, ЦКП на уровне задач, roadmap от Spike v0.4 до v1.0 (Q4 2026 — OpenClaw-интеграция).
- **`MODEL.md` v0.1** — схема БД с 6 MVP-таблицами (v0.4 Spike) + 5 расширений (v0.5 Sprint 1) + 2 multi-agent (v0.7). Seed data, SQL-примеры для queries, mapping на Notion.
- **`ARCHITECTURE.md` v2** — полностью переписан. 11 слоёв: Portfolio (DB), Project Standard (5-scope AGENTS.md cascade), SSOT, Agent Orchestration через Superpowers, API Drift Governance, Scrum ceremonies + personal rituals, Tooling, Utility→Product lifecycle, Multi-agent readiness, Anti-patterns (13 штук), end-to-end lifecycle пример.
- **`BACKLOG.md` v3** — волны работы: W0 (сделано), W1 (research v2), W2 (Spike v0.4, 22 задачи), W3 (Sprint 1, 14 задач), W4 (Sprint 2), W5 (v0.7 multi-agent), W6 (v1.0).
- **`RESEARCH_QUESTIONS_V2.md`** — /ultraplan с 10 блоками для второй волны deep research:
  - A. IT PM fundamentals 2026
  - B. Scrum detailed — ceremonies + AI adaptations
  - C. Multi-agent orchestration frameworks (CrewAI, AutoGen, LangGraph, OpenAI Swarm, OpenClaw, paperclip, etc.)
  - D. Inter-agent protocols (MCP, A2A, ACP, OpenAPI)
  - E. Project documentation hierarchy (PRD, MRD, ADR, spec)
  - F. Database schema patterns (Linear, Jira, Asana, ClickUp, Notion)
  - G. Metrics — ЦКП / OKR / KPI / NorthStar formulation
  - H. Capacity planning для multi-track + AI
  - I. Notion integration patterns
  - J. Migration cases: markdown → custom PM systems
  - + §K синтезирующий + §L вопросы к Дмитрию.
- **`METHODOLOGY.md` v0.3** — статус гипотез v0.2 после pivot + 7 новых гипотез (H1-H7) + список оставшихся пробелов.
- **Memory feedback** `feedback_agents_md_canonical.md` — правило AGENTS.md вместо CLAUDE.md для всех будущих сессий.

### Что изменилось в архитектуре

| Компонент | До v0.3 | После v0.3 |
|---|---|---|
| SSOT портфеля | markdown `PORTFOLIO.md` | БД `portfolio.db` (SQLite) |
| Конфигурационный файл агента | CLAUDE.md | **AGENTS.md** |
| Agent orchestration | 4 самодельных Personas | Superpowers plugin workflow с tier'ами T1/T2/T3 |
| Sprint-структура | Personal rituals only | **Scrum ceremonies** (Planning/Standup/Refinement/Review/Retro) + personal rituals |
| Multi-agent ready | Не заложено | Заложено в схеме БД (`participants.kind`, RBAC, FastAPI groundwork в v0.7) |
| ЦКП / метрики | Упомянуто косвенно | **Обязательное поле `tasks.cpp_description`** |

### Хвосты после v0.3

- Research v2 не запущен — это задача W1 в BACKLOG.
- Блокнот NotebookLM для v2 не назначен — решение Дмитрия (рекомендация: отдельный `Cifro.pro — IT PM & Multi-Agent Orchestration (NP-005 v2)`).
- После research v2 — обновления: MODEL.md → v0.2, ARCHITECTURE.md → v3, PRD.md → v0.4.
- Spike v0.4 может стартовать частично параллельно с research v2 (SP-01..05 не требуют results).
- Выбор мультиагентной платформы (OpenClaw / paperclip / CrewAI / AutoGen / Agent Zero / ...) — после Блока C research v2.
- 5 классификационно-неопределённых папок (`NL/`, `Tech/`, `Перетяжка/`, `Шуклин/`, `Спецификации/`) — остаются на Monday Kickstart.

## 2026-04-22 (late) — v0.2.1 PIVOT: PM-first (а не B24-first)

- Дмитрий уточнил: **Bitrix24 не нужен для core PM-системы**. PM может быть на чём угодно (даже самописная SQLite), главное — отвечала критериям. Notion — личный зеркальный задачник (удобный интерфейс). B24 — leaf, получает клиентские задачи из Notion существующими средствами Дмитрия.
- **`ARCHITECTURE.md §3` переписан**: core SSOT-карта теперь PM-first:
  - Канон (PM): `portfolio_entry`, `project_metadata`, `dev_task`, `ritual_record`, `ADR`, `api_drift_event`, `research_finding`, `agent_run_log`, `action_log`.
  - Canon (Notion): `personal_inbox_item` — Дмитрий ловит идеи в Notion с телефона/браузера, PM забирает pull-командой.
  - Вне core NP-005: `client_company`, `client_contact`, `deal`, `client_task_b24` — остаются в Notion → B24 через существующий setup.
- **`ARCHITECTURE.md §7` обновлён**: убраны Latenode и B24-webhooks из Sprint 1. Добавлено: расширение `notion-task-cli` 3 командами (`portfolio push`, `portfolio pull-inbox`, `sync-agents`) как основной glue PM ↔ Notion. SQLite опционален как derived cache. API Drift Governance остаётся — но относится к **проектам в портфеле** (например, NP-002 Bitrix24 API Wrapper), не к самой PM.
- **`BACKLOG.md Sprint 1` правки**:
  - `NP5-007` (Latenode) → заменён на `notion-task-cli portfolio push` (PM-first).
  - `NP5-010` (B24 openapi) → заменён на `notion-task-cli portfolio pull-inbox`. API drift отодвинут в Sprint 2 как `NP5-R05` для NP-002.
  - Sprint 2 переориентирован: все Latenode/B24-задачи в `NP5-R08` с пометкой "только если существующий setup Дмитрия ломается".
- **`NOT IN SCOPE Sprint 1`** расширен: B24 интеграция на уровне PM, API Drift для клиентских порталов, Latenode / сторонний glue.

## 2026-04-22 — v0.2: deep research выполнен, ARCHITECTURE.md v1 + BACKLOG.md Sprint 1

- Использован блокнот NotebookLM `0c2805ab-42f8-4e98-86c7-e7a618f0f850` «Эволюция ИИ-инструментов: от разработки до рабочих пространств» — 47 источников (41 ready, 6 error).
- Применён новый протокол **Progressive Inquiry** из обновлённого skill `notebooklm` (от общего к частному): 3 вопроса блокноту вместо 8 заранее заготовленных.
  - **Step 1 (Orient)**: content map — 5 кластеров + анализ сводного источника №29 + 4 пробела. Saved as note `Content Map (Orient Step 1)`.
  - **Step 2 (Focused)**: 14-day implementation protocol (6 секций) для соло-оркестратора с 10 клиентами B24 + 5 продуктами + 14 утилитами. Saved as note `14-Day Implementation Protocol`.
  - **Step 3 (Focused)**: integration sync protocol (5 пунктов: SSOT-карта, 5 triggers, conflict resolution, KV-cache append-only, YAML frontmatter). Saved as note `Integration Sync Protocol`.
- Скачан fulltext источника №29 «Operational Architectures for the Autonomous Soloist» (33 210 символов, 50+ референсов) — сохранён в `research/00_operational_architectures_soloist.md`.
- Созданы локальные markdown-синтезы всех 3 ask-ответов в `research/01_content_map.md`, `research/02_implementation_protocol_14d.md`, `research/03_integration_sync_protocol.md`.
- Написан **`ARCHITECTURE.md v1`** — 10 слоёв (Portfolio / Project Standard / SSOT / Agent Personas / API Drift / Rituals / Tooling / Utility→Product / Anti-patterns / end-to-end flow) с привязкой каждого слоя к источникам блокнота.
- Написан **`BACKLOG.md`** — Sprint 1 на 14 дней (14 задач `NP5-001..014`), Sprint 2 pre-план (7 задач `NP5-R01..R07`), 4 research follow-ups (`NP5-RF01..04`) по пробелам Orient-ответа.

### Ключевые решения v0.2

- **Таксономия портфеля**: 5 категорий × 6 lifecycle-стадий (подтверждено паттерном Standalone Capability Group из №29).
- **5-scope CLAUDE.md cascade**: Global → Project → Local Secret → Folder → Imports. Глубже scope побеждает. Файлы ≤ 200 строк, human-curated (+4% vs -3% у LLM-generated).
- **SSOT-карта**: 9 типов сущностей, 4 канона (B24 / Notion / Local Git / NotebookLM). Полный bidirectional sync запрещён (infinite loops).
- **Conflict resolution**: canonical-field-per-concept (а не last-write-wins).
- **Fresh Chat Per Task**: markdown-артефакты как API между агентами. 100% защита от Context Rot.
- **Tooling 80/20**: Claude Code + Notion + NotebookLM уже есть; добавить Motion ($34) или Amie (free) + Latenode. НЕ ставить Warp/Intent/Linear/Zapier/Optic.
- **4 Agent Personas**: Scribe/PM, Coder, QA-Critic (Adversarial Review), Researcher — через ролевое кэширование в `~/.claude/personas/`.
- **API Drift**: Contract-First + Spectral (pre-commit) + oasdiff (pre-release). Optic избыточен.
- **Utility → Product criteria**: 4 фильтра (Real Problem / Agent Gap / Efficiency ≥30 min/week / Cognitive Load @ 23:00). ≥ 3/4 → `graduating`.
- **Ритуалы**: Monday Kickstart, PR Review of One, Friday Wind-down, Monthly Metrics, Ritual Reset. НЕ внедрять ежедневные Scrum-стендапы с самим собой.

### Статус после v0.2

- [x] Идея зафиксирована.
- [x] Блокнот NotebookLM использован (`0c2805ab-...`, 47 sources).
- [x] Deep research выполнен (3 ask-сессии + fulltext источника №29).
- [x] Ответы синтезированы в `research/`.
- [x] `ARCHITECTURE.md v1` написан.
- [x] `BACKLOG.md` с Sprint 1 (14 задач) составлен.
- [ ] `METHODOLOGY.md v2` переписан с пометками подтверждений/опровержений (в работе).
- [ ] Дмитрий валидирует Sprint 1 и решает, с какого NP5-001 начинать.

### Хвосты

- 4 research follow-ups не запущены (Stories 1-100 deep-dive, Token Economics, Legal risks AI, Long-term code review).
- 5 папок в `PROJECT/` требуют классификации (NL/, Tech/, Перетяжка/, Шуклин/, Спецификации/) — нужен ответ Дмитрия.

## 2026-04-22 — v0.1 модуль создан

- Создана папка `NP-005_Personal_PM_Infrastructure/`.
- Написаны документы v0.1: `README.md` (карта), `OVERVIEW.md` (контекст + проблема + границы + целевая архитектура), `RESEARCH_QUESTIONS.md` (8 блоков + итоговые запросы), `METHODOLOGY.md` (10 разделов с гипотезами).
- Идея помечена как `personal-utility` (не на продажу сейчас), по прецеденту NP-004.
- Строка добавлена в `products/new/README.md`, запись в `PROJECT_LOG/ACTION_LOG.md`.
- Создан memory-файл `project_portfolio_index.md` с начальной сортировкой портфеля (10 клиентов, 4 продукта, 14 утилит, 1 личный, 1 инфраструктурный).

### Статус после v0.1

- [x] Идея зафиксирована.
- [ ] Блокнот NotebookLM назначен (нужно решение Дмитрия: отдельный или общий `d01b964f-...`).
- [ ] Deep research запущен.
- [ ] Ответы синтезированы в `research/` подпапку.
- [ ] `METHODOLOGY.md v2` переписан с пометками подтверждений/опровержений.
- [ ] `ARCHITECTURE.md v1` написан.
- [ ] `BACKLOG.md` составлен.

### Хвосты

- Возможно нужен Блок I про приватность и data-ownership — зависит от того, пойдёт ли NP-005 в продуктизацию.
- Неочевидно, нужно ли сразу замапить каждый из 14 проектов в `Tests/` в `PORTFOLIO.md` (через `portfolio-register`) или дождаться research.
