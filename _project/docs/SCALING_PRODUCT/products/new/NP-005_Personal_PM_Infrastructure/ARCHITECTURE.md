# ARCHITECTURE — NP-005 Personal PM Infrastructure v2.2

**Версия**: v2.2 (2026-04-24, + archive engine с физическим layout + логические статусы + universal tags planning)
**Источники**: research v1 из блокнота `0c2805ab-...` (см. [research/](./research/)) + прямое видение Дмитрия (PRD v0.3). Вторая волна research — см. [RESEARCH_QUESTIONS_V2.md](./RESEARCH_QUESTIONS_V2.md).
**Статус**: Spike v0.4 + CRUD MVP завершены (см. CHANGELOG v0.4.1). W4 (tags + archive engine) в работе — см. BACKLOG.md.

**v2.2 изменения**: добавлена новая секция §2.7 (Archive: physical vs logical state) с физическим layout `PROJECT/_Archive/{clients,products,tests}/`, 4 логическими статусами (completed/paused/frozen/archived), полным набором CLI-команд (archive/unarchive/renew/move/reorganize). §2.5 дополнена note про storage тегов в БД (миграция 004).

> ⚠️ **Переименование**: все упоминания **CLAUDE.md** в предыдущих версиях ARCHITECTURE заменены на **AGENTS.md**. Канонический файл контекста агентов — `AGENTS.md`, не CLAUDE.md. См. memory feedback.

---

## 0. Портрет «Оркестратора-одиночки → Orchestrator-команды»

- **Сейчас**: Дмитрий (Orchestrator) + Claude Code (AI-partner).
- **Через 1-3 месяца**: + multi-agent команда (AI-CEO, AI-PM, AI-Marketing, AI-Knowledge = NP-004, AI-QA, AI-Developer), потенциально + внешние подрядчики (люди).
- **Портфель**: 10 клиентов + 5 бизнес-продуктов + 14+ утилит + 1+ личный проект.
- **Стек**: Windows 11, Python 3.11+, JS/TS, markdown в git, SQLite/PostgreSQL.
- **Инструменты уже подключены**: Claude Code, NotebookLM, `atlas`, Notion workspace «Прагмат», Bitrix24 порталы клиентов, superpowers plugin v5.0.7.

Orchestrator не пишет код сам — проектирует Intent, ставит границы автономности, делает final approval.

---

## 1. Слой 1 — Portfolio (DB-first) [PIVOT 2026-04-22 late]

### 1.1 Канонический источник правды

**SSOT = реляционная БД** `portfolio.db` (SQLite для MVP; миграция на PostgreSQL, когда 2+ агента параллельно пишут). Схема — в [MODEL.md](./MODEL.md). Миграции через Alembic.

**НЕ канон** — markdown-файлы и Notion-страницы. Они — зеркала для удобства чтения (человеком, агентом, UI Notion).

### 1.2 Производные представления (автогенерируются из БД)

- `PROJECT_LOG/PORTFOLIO.md` — статическое read-only зеркало для удобного чтения в git без SQL. Генерируется командой `portfolio render`.
- Notion DS_PROJECTS — зеркало для личного UI Дмитрия (через `portfolio push`).
- Memory Claude (`project_portfolio_index.md`) — индекс-ссылка на БД и `PORTFOLIO.md`.

### 1.3 Хранилище и backup

- **SQLite-файл** `<some-safe-location>/portfolio.db` (точный путь — в `AGENTS.md` глобальном).
- **Git-репо** для БД: `portfolio-db/` — SQLite файл коммитится (binary-safe для SQLite), миграции в `migrations/`, seed data в `seeds/`.
- **Ежедневный dump** → `portfolio-db/dumps/YYYY-MM-DD.sql` (для debug и восстановления).
- **Миграция на PostgreSQL** планируется в v0.7, когда multi-agent concurrent writes станут регулярными.

### 1.4 Append-only audit

Все мутации записываются в `action_log` таблицу. Никогда не делаем `UPDATE` или `DELETE` на `action_log`. Это база для multi-agent координации: AI-PM читает `action_log`, чтобы понять «кто что делал за последние 24 часа».

---

## 2. Слой 2 — Project Standard (AGENTS.md canonical) [обновлено: не CLAUDE.md]

### 2.1 5-scope AGENTS.md cascade

**Правило:** чем глубже файл, тем выше приоритет. Файлы ≤ 200 строк (иначе «Lost in the Middle»). Human-curated (+4% vs -3% у LLM-generated).

| Scope | Путь | Что внутри |
|---|---|---|
| **Global** | `~/.claude/AGENTS.md` (+ `~/.codex/AGENTS.md` / `~/.gemini/AGENTS.md` как симлинки/копии, чтобы любой агент видел) | Роль Orchestrator, дефолтный стек, правило «data model first → logic → UI», compound engineering, Fresh Chat Per Task, ссылка на Superpowers workflow |
| **Project** | `<repo>/AGENTS.md` | Архитектура проекта, exact stack versions, executable commands, coding conventions (1-line snippets), boundaries (Always / Ask First / Never), YAML frontmatter с back-links на PM-систему |
| **Local Secret** | `<repo>/AGENTS.local.md` (в `.gitignore`!) | Токены, WIP, чувствительные пути |
| **Folder** | `<repo>/src/<module>/AGENTS.md` | Специфика модуля ≤ 50 строк |
| **Imports** | `@docs/security.md` внутри любого AGENTS.md | Подгрузка общих правил |

### 2.2 Обязательный YAML frontmatter в `<repo>/AGENTS.md`

```yaml
---
type: "client_project" | "business_product" | "personal_utility" | "personal_project" | "shared_infrastructure"
pm_project_slug: "cifro-pro"
pm_project_id: "a1b2c3d4-..."
quality_tier: "T1" | "T2" | "T3"
superpowers_enabled: true
primary_owner: "dmitry"
agents_allowed: ["claude-code", "ai-pm", "ai-qa"]  # для multi-agent RBAC
# опциональные (для client_project)
client_code: "PRAGMAT_01"
b24_company_id: "784"
notion_project_id: "a1b2c3d4-..."
# опциональные (для research-heavy)
notebooklm_id: "1cdb8a3f-..."
context_rules:
  - "Do NOT modify production B24 portal without PR approval"
  - "Append ACTION_LOG entry on every commit"
---
```

Поддерживается автоматически idempotent-скриптом `atlas sync-agents --project <slug>`, который читает PM-БД и обновляет frontmatter. Pre-commit hook → нет коммита с рассинхронизованными ID.

### 2.3 Обязательные секции тела AGENTS.md

1. **Tech Stack** — exact versions, без "latest".
2. **Executable Commands** — CLI-команды с флагами (install / test / lint / deploy / format). Агент использует verbatim.
3. **Coding Conventions** — 1-line snippets неочевидных паттернов.
4. **Boundaries** — 3-уровневая иерархия:
   - **Always**: автономные действия.
   - **Ask First**: deploy, migrations, cost-heavy ops.
   - **Never**: core config, production DB.

### 2.4 Compound Engineering

Каждая ошибка ИИ в PR-review → новое правило в `AGENTS.md`. Файл — живой onboarding.

### 2.5 Tier per project

| Tier | Какие проекты | Что применяем |
|---|---|---|
| **T1 полный** | `business_product`, critical `client_project` | Полный Superpowers: brainstorming → spec → plan → worktree → subagent-driven + TDD + 2-stage review → finishing |
| **T2 облегчённый** | `personal_utility` maintained, non-critical `client_project` | writing-plans + TDD + verification. Без subagent-driven (инлайн execution). |
| **T3 spike** | `personal_utility` experiment, `personal_project` эксперимент | Только verification-before-completion. TDD — опционально. |

> **Storage note (v0.4.1)**: `quality_tier` теперь хранится **в БД** (`tasks.quality_tier` ∈ {T1, T2, T3}), а не только в `AGENTS.md` frontmatter. Это даёт возможность фильтровать tasks по tier в SQL-запросах и автоматизировать выбор workflow без чтения markdown.

> **Storage note (v2.2, planning W4)**: помимо `quality_tier`, в БД переезжает и **owner/stack/domain** как универсальные теги (таблицы `tags` + `project_tags` — см. MODEL.md §2.8/2.9, миграция 004). YAML frontmatter остаётся для зеркала, но канон — БД: `atlas projects list --tag owner:dmitry --tag stack:b24` (AND-логика). Это развязывает физическую иерархию (группа clients/products/tests — см. §2.7) и логические измерения (owner/stack/domain), позволяя менять владельца/стек без физических move'ов папки.

### 2.6 Slug + Prefix + Ref resolution [NEW v2.1]

Реализовано в `src/atlas/pm/slugs.py`. Идея — дать каждой сущности **читаемый человеком стабильный идентификатор**, который удобно набирать в CLI, и при этом сохранить UUID как канон в БД.

#### 2.6.1 Project slug

- Формат: `[a-z0-9-]{2,50}`, kebab-case ASCII.
- Глобально уникальный.
- Источник: либо явно задан Дмитрием при `projects add --slug ...`, либо сгенерирован из `--name` через `slugify_text` (поддерживает транслитерацию RU→EN).
- Коллизия → автоматически добавляется суффикс `-2`, `-3`, ... через `generate_unique_slug`.

#### 2.6.2 Project prefix

- Формат: `[a-z0-9]{1,5}`, без дефисов.
- Глобально уникальный (отдельный UNIQUE INDEX).
- Авто из slug через `generate_prefix_from_slug`:
  - `cifro` → `cif` (один сегмент букв → первые 3).
  - `np-005` → `np5` (буквы букв-сегмента + значимые цифры).
  - `docs-parsing` → `dp` (первая буква каждого alpha-сегмента).
  - `ml-model-v2` → `mmv2` (mixed-режим).
- Коллизия prefix → ошибка пользователю с предложением задать `--prefix` явно.

#### 2.6.3 Task slug

- Формат: `{project.prefix}-{task-part}`, глобально уникальный.
- `task-part` — slugify от `--title` (или явно `--slug-part`).
- Пример: `atl-fix-login`, `np5-add-migration`.
- Сборка через `build_task_slug(project.prefix, task_part)`.

#### 2.6.4 Task number

- Тип: `INTEGER UNIQUE NOT NULL`.
- Глобальный auto-increment через `next_task_number(session)` = `MAX(Task.number) + 1` (gap'ы не закрываются).
- Используется в CLI для коротких ссылок: `pm-tasks get 42`, `pm-tasks done 42`.

#### 2.6.5 Resolve-паттерны для CLI

Любой ref в командах `*-get/update/delete` принимается в одной из 4 форм. Реализация — `resolve_project_ref` / `resolve_task_ref`.

| Форма | Пример | Где работает | Логика |
|---|---|---|---|
| **slug** | `atlas`, `atl-fix-login` | projects + tasks | exact match по `slug` |
| **UUID full** | `a1b2c3d4-e5f6-7890-abcd-ef0123456789` | projects + tasks | exact match по `id` |
| **UUID short** | `a1b2c3d` (≥ 7 hex-chars) | projects + tasks | LIKE `'<ref>%'`, ambiguous → `AmbiguousRefError` |
| **number** | `42` | только tasks | exact match по `number` |

**Правило приоритета для tasks** (важно при пересечении форм): для `ref` длиной < 7 — однозначно `Task.number`. Для `ref` ≥ 7 и из hex-алфавита — **сначала** UUID prefix, потом fallback на `Task.number` (поскольку UUID-префикс может совпадать с цифровым числом, и это сильнее).

#### 2.6.6 Правило для агента при создании сущности

Аналог skill `atlas` §3.2 — при создании Project / Task агент:

1. Если пользователь не задал `--slug` — генерирует из `--name` через `slugify_text`.
2. Если коллизия — автоматически берёт `-2`/`-3`/... через `generate_unique_slug` (НЕ спрашивает пользователя).
3. Для Project — если `--prefix` не задан, генерирует через `generate_prefix_from_slug`. При коллизии prefix — **спрашивает пользователя**, какой prefix использовать (сильнее коллизии slug, потому что prefix — публичный API задач).
4. Для Task — `slug` собирается автоматически как `{project.prefix}-{slugify(title)}`. `number` берётся через `next_task_number`.
5. Возвращает в outpit как минимум: `slug`, `number` (для tasks), короткий UUID (для отладки).

### 2.7 Archive: physical vs logical state [NEW v2.2]

Архив — это не одна dimension'а, а две ортогональные: **физическое расположение папки** и **логический статус в БД**. Смешивать их не надо, каждая решает свою задачу.

#### 2.7.1 Physical layout

```
PROJECT/
├── Clients/<slug>/        ← активные клиентские проекты
├── Products/<slug>/       ← активные business-products (NP-XXX)
├── Tests/<slug>/          ← активные test-проекты (эксперименты, утилиты)
└── _Archive/              ← один архив для всего
    ├── clients/<slug>/    ← архивные клиенты
    ├── products/<slug>/   ← архивные products
    └── tests/<slug>/      ← архивные test'ы
```

**Почему один `_Archive/` (а не свой в каждой группе)**:
- one place для бэкапа / ревизии / снапшота — удобно zip'ать, grep'ать, ls'ить.
- Централизованная "холодная зона" визуально отделена от активной работы (не мешает `cd Clients/` → tab-complete).

**Почему подгруппы `{clients,products,tests}/` внутри _Archive/**:
- сохраняют информацию о типе для unarchive: мы точно знаем, куда возвращать.
- удобно для метрик: `ls _Archive/clients/` → сразу видно историю клиентских отношений.

#### 2.7.2 Logical states (внутри архива)

Архив — это не одно состояние "мёртвый", а четыре разных статуса с разными будущими действиями:

| Статус БД | Физика | Когда применять | Ожидание возврата |
|---|---|---|---|
| `completed` | `_Archive/<group>/` | Работа закончена успешно: разовая услуга клиенту, product shipped | Низкое, но для clients — возможен `renew` |
| `paused` | `_Archive/<group>/` | Временно приостановлен, есть явная причина (блокер, пауза клиента) | Высокое, вернёмся через недели/месяцы |
| `frozen` | `_Archive/<group>/` | Заморожен надолго, нет близких планов, но идея жива | Среднее, разморозка возможна через квартал+ |
| `archived` | `_Archive/<group>/` | Окончательно закрыт, history only, не вернёмся никогда | Нулевое |

Все четыре разделяют одно и то же физическое положение в `_Archive/`. Разница — только в БД-статусе + `archived_at` timestamp + metadata.

Существующие статусы (`experiment / active / maintained / dormant / graduating`) — сохраняются для активных проектов. Миграция 004 добавляет 5 новых (`idea`, `research`, `planned`, `paused`, `completed`, `frozen`) — см. MODEL.md §5.

#### 2.7.3 Atlas commands

| Команда | Что делает физически | Что делает логически |
|---|---|---|
| `atlas projects archive <slug> --status completed\|paused\|frozen` | `mv <group>/<slug>/ _Archive/<group>/<slug>/` | `projects.status_id = <status>`, `archived_at = now()`, `archived_group = '<group>'`. `action_log += (action='project_archived', details={status, group})` |
| `atlas projects unarchive <slug> [--status active]` | `mv _Archive/<archived_group>/<slug>/ <archived_group>/<slug>/` | `projects.status_id = active` (или указанный), `archived_at = NULL`. `action_log += (action='project_unarchived')` |
| `atlas projects renew <slug>` | если в архиве — unarchive; иначе no-op физики | `renewal_count++`, `status_id = active`, clear `archived_at`. `action_log += (action='project_renewed', details={new_count})`. Только для `type='client-project'` |
| `atlas projects move <slug> --to-type <new-type>` | `mv <old-group>/<slug>/ <new-group>/<slug>/` (или в `_Archive/<new-group>/` если проект в архиве) | `projects.type_id = <new-type>`. `action_log += (action='project_moved', details={from, to})` |
| `atlas projects reorganize [--dry-run] [--apply]` | читает физику + БД → находит расхождения → предлагает diff | `--apply` синхронизирует (без destructive действий, только по подтверждению) |

**Инварианты**:
- `archived_at IS NULL` ↔ проект физически в `<group>/`.
- `archived_at IS NOT NULL` ↔ проект физически в `_Archive/<archived_group>/`.
- `renewal_count` меняется **только** через `atlas projects renew` (не через обычный unarchive).

#### 2.7.4 Why this design

**Разделение dimensions**:
- **Физическая группа** (clients/products/tests) — в физике файловой системы. Cohesion по функции: удобно `cd Clients/` + `grep` только по активным клиентам, не мешая products.
- **Владелец / стек / домен** — в БД через теги (§2.5 note). Гибкость: сменить стек проекта — одно `atlas projects add-tags <slug> --tag stack:fastapi --remove-tag stack:flask`, без mv папки.
- **Архив** — комбинированно: физика (одна папка `_Archive/` для бэкапа/ревизии) + логика (4 статуса + `renewal_count` для метрик).

**Метрики, которые становятся возможны**:
- **Client health через `renewal_count`**: сколько раз клиентский проект `renew` случался → индикатор качества отношений. Клиенты с 3+ renewals — ядро портфеля; с 0 renewals и `completed` — "одноразовые", смотрим что можно улучшить.
- **Decay паттерны через action_log + статусы**: через timestamp'ы `archived_at` и `action_log` можно узнать `среднее время в статусе paused/frozen` → backlog decay. Если проект > 6 мес в `paused` → автоматический triage "переведи в frozen или reactivate".
- **Sprint health через `action_log` entries** вокруг archive/renew операций — показывают паттерны того, как портфель живёт.

**Почему не ADR-альтернативы**:
- **Flat `_Archive/` без подгрупп** — теряем информацию о типе, unarchive усложняется, нужно всегда заглядывать в БД чтобы понять куда возвращать.
- **Свой `_archive/` в каждой группе** — три отдельных архива, fragmented backup, `grep` по всем архивам раздробленный, нет unified view.
- **Только логический статус без физического mv** — активные и архивные папки смешиваются, tab-complete шумит, `cd` неудобен.

См. [ADR-001 в atlas репозитории](../../../../../../atlas/_project/docs/ARCHITECTURE/decisions/ADR-001-archive-layout.md) — подробное обоснование принятия Варианта C.

---

## 3. Слой 3 — SSOT-карта [v2, после двух pivot'ов]

| Сущность | Канон | Зеркало/integration | Направление | Частота |
|---|---|---|---|---|
| `project`, `task`, `sprint`, `participant`, `expense`, `prd_snapshot`, `stack`, `project_stack`, `action_log` | **PM-БД (`portfolio.db`)** | `PROJECT_LOG/PORTFOLIO.md` (md-view), Notion DS_PROJECTS (user surface), memory-index | PM → зеркала | On write / Daily |
| `personal_inbox_item` | **Notion DS_TASKS** (Дмитрий ловит идеи с телефона) | PM `tasks.status='backlog'` | Notion → PM (pull-inbox) | Monday Kickstart / manual |
| `task.due_date` | **Notion** (Дмитрий правит глазами) | PM | Notion → PM (pull) | На sprint planning |
| `project_metadata` (ссылки на git / local path / notion-id / b24-id) | **PM-БД `projects` + AGENTS.md frontmatter** | — | PM ↔ AGENTS.md через `sync-agents` | Pre-commit hook |
| `ritual_record`, `ADR`, `retro_notes` | **PM (в полях `sprints.retro_notes`, `action_log`) + markdown в `_project/docs/RITUALS/` `_project/docs/ARCHITECTURE/decisions/`** | — | — | — |
| `research_finding` | **NotebookLM + PM `research_findings` table** | Local `research/*.md` | NotebookLM → PM → md | Manual (при закрытии research) |
| `code_artifact`, `test_artifact`, `superpowers_spec`, `superpowers_plan` | **Local Git** (путь в `tasks.superpowers_spec_path` / `superpowers_plan_path`) | — | — | On commit |
| `client_company`, `client_contact`, `deal`, `client_task_b24` | **Вне core NP-005.** Bitrix24 — leaf, интегрируется через существующие средства Дмитрия (Notion ↔ B24) | — | — | — |
| `agent_run_log`, `cost_per_task` | **PM (v0.7 `agent_runs` table)** | — | — | Per-task (multi-agent) |

### 3.1 Правила интеграции

1. **Никогда полного bidirectional sync** одних и тех же полей — infinite loops.
2. **Canonical-field-per-concept** — каждое понятие канонично ровно в одном источнике.
3. **Append-only** для `action_log`, `research_findings`, `sprints.retro_notes`, ADR.
4. **БД читается человеком через sqlite-браузер / CLI.** Не лезем руками в файл `portfolio.db`.
5. **`action_log` — read-only через CLI**: команда `atlas action-log list` (только list, никаких add/update/delete). Запись в `action_log` происходит исключительно как side-effect других CRUD-команд.

### 3.2 Интеграционные команды (MVP)

| Команда | Что делает |
|---|---|
| `portfolio init` | Создаёт `portfolio.db`, выполняет первую миграцию, сидит project_types/statuses/participants |
| `portfolio render` | Генерирует `PORTFOLIO.md` и `memory/project_portfolio_index.md` из БД |
| `portfolio push` | PM → Notion DS_PROJECTS (обновляет зеркало) |
| `portfolio pull-inbox` | Notion inbox → PM (новые idea-items в backlog) |
| `portfolio pull-dates` | Notion due-dates → PM (синхронизация сроков после sprint planning) |
| `sync-agents --project <slug>` | Читает БД → обновляет YAML frontmatter в `<repo>/AGENTS.md` |

---

## 4. Слой 4 — Agent Orchestration через Superpowers

### 4.1 Отказ от собственных personas

Предыдущие версии ARCHITECTURE предлагали 4 самодельных Agent Personas (Scribe / Coder / QA-Critic / Researcher). **Заменяем на Superpowers plugin v5.0.7** (14 skills + агент `code-reviewer`). Superpowers даёт отлаженный workflow с готовыми prompt-templates для implementer + spec-reviewer + code-quality-reviewer.

### 4.2 Pipeline per task

```
PM-система: task создана в backlog
        ↓
  sprint planning (по Scrum): task попадает в sprint
        ↓
  task.assignee = claude-code (AI-Developer), task.quality_tier = T1
        ↓
  Дмитрий: "Claude, возьми task #NP5-007"
        ↓
┌──────────────────────────────────────────────────────────┐
│ SUPERPOWERS WORKFLOW (на стороне Claude Code)            │
│                                                           │
│  1. brainstorming  → spec.md                              │
│  2. writing-plans  → plan.md (bite-sized TDD tasks)       │
│  3. using-git-worktrees → `.worktrees/<task-slug>/`       │
│  4. subagent-driven-development                           │
│      ├─ implementer subagent (TDD)                        │
│      ├─ spec-compliance-reviewer subagent                 │
│      └─ code-quality-reviewer subagent                    │
│  5. finishing-a-development-branch                        │
│     → merge (Option 1) or PR (Option 2)                   │
└──────────────────────────────────────────────────────────┘
        ↓
  PM post-hook: task.status = done, task.git_pr_url, action_log += entry
        ↓
  AI-PM agent (в future) читает action_log → обновляет sprint-burndown → notify
```

### 4.3 Tier определяет workflow

- **T1**: все 5 шагов Superpowers (brainstorming → finishing). `subagent-driven-development`.
- **T2**: writing-plans → using-git-worktrees → executing-plans (inline, без subagent-driven) → TDD → finishing. Без brainstorming-skill (полагаемся на spec в `tasks.description`).
- **T3**: только `verification-before-completion` + `test-driven-development` опционально. Быстрый spike-режим.

### 4.4 Fresh Chat Per Task + markdown-API

Каждый шаг Superpowers — новый чат Claude Code. Артефакты (spec.md, plan.md, tests) передаются через файлы на диске, а не через контекст. Это защищает от Context Rot.

### 4.5 Интеграционные точки PM ↔ Superpowers

| Момент | Событие | PM-сторона |
|---|---|---|
| Spec создан | `writing-plans` записал spec в путь | Обновляется `tasks.superpowers_spec_path` |
| Plan создан | `writing-plans` записал plan | `tasks.superpowers_plan_path` |
| Worktree создан | `using-git-worktrees` создал ветку | `tasks.git_branch` |
| PR открыт / merge | `finishing-a-development-branch` | `tasks.git_pr_url`, `tasks.status = done`, `tasks.completed_at`, `action_log` +entry |
| Agent run finished | (multi-agent v0.7) | `agent_runs` +row с токенами и cost |

Pre-commit hook + post-merge hook в git-репозитории проекта обновляют БД автоматически.

---

## 5. Слой 5 — API Drift Governance [не core, а для proektов в портфеле]

Применяется **к проектам в портфеле, не к самой PM-инфраструктуре** (PM markdown-only + SQLite локально, без публичного API).

### 5.1 Setup

- `openapi.yaml` в каждом проекте, использующем внешние API (Bitrix24 портал клиента, Notion API, NotebookLM и т.д.).
- `spectral` в pre-commit hook.
- `oasdiff` перед релизом — сравнение со снапшотом.

### 5.2 Drift-мониторинг

`atlas drift-scan --project <slug>` — ежедневно (через cron или scheduled task) сравнивает live-API портала B24 клиента с локальным `openapi.yaml`. Drift → автоматически `tasks.insert(title='Schema Drift в B24 клиента X')` в backlog.

### 5.3 Primary applicable projects

- NP-002 Bitrix24 API Wrapper (основное применение).
- Любые client-project с кастомным B24 API usage.

Для personal-utility и PM-инфраструктуры — не применяется.

---

## 6. Слой 6 — Ритуалы (Scrum ceremonies + Solo daily)

### 6.1 Scrum-ceremonies (на sprint-уровне)

| Ceremony | Когда | Участники | Действия |
|---|---|---|---|
| **Sprint Planning** | День 1 спринта | Дмитрий + AI-PM | `sprint plan --goal "..."` + `sprint add-task ...` для каждой. Burndown ожидаемый |
| **Daily Standup** | Каждый день | Дмитрий + agents | `sprint standup` — выводит что движется, что заблокировано (derived из `tasks` и `action_log`) |
| **Backlog Refinement** | Середина спринта | Дмитрий + AI-PM | Оценка story points для будущих тасков, уточнение ЦКП |
| **Sprint Review** | Последний день | Все участники | `sprint review` — список done-тасков, демо merge-артефактов |
| **Retrospective** | Последний день | Дмитрий + AI-PM | `sprint retro` — что сработало, что театр. Заметки в `sprints.retro_notes` |

### 6.2 Personal rituals (вне Scrum)

| Ритуал | Когда | Чеклист |
|---|---|---|
| **Monday Kickstart** | Пн утро | `portfolio pull-inbox` → brain dump → AI выделяет 3 Big Rocks → Motion blocks |
| **PR Review of One** | После каждого PR Claude Code | Чтение «глазами чужака» → WTFs → правило в AGENTS.md (compound engineering) |
| **Friday Wind-down** | Пт вечер | `sprint standup` → заметки «что сработало/сломалось» → `/clear` все Claude-вкладки |
| **Monthly Metrics** | 1-я пятница месяца | `expense report` → Acceptance Rate → portfolio-wide health check |
| **Ritual Reset** | Квартально | Убрать ритуалы-театры, обновить процессы |

---

## 7. Слой 7 — Tooling (что есть, что добавить)

### 7.1 Оставить

- **Claude Code** — основной ADE.
- **NotebookLM** — research engine.
- **`atlas`** — расширяется до PM-системы (новые команды + БД слой).
- **Notion workspace** — surface-слой (personal inbox + due-dates + зеркало).
- **Git** — основа для Superpowers git-worktrees.
- **Superpowers plugin v5.0.7** — уже установлен, используем его skills.

### 7.2 Добавить в Spike v0.4

- **SQLite + SQLAlchemy 2.x + Alembic** — база PM-системы (Python-side).
- **Расширение `atlas`**:
  - Подмодуль `portfolio` с БД-layer.
  - Подмодуль `sprint` для Scrum-операций.
  - Подмодуль `expense` для расходов.
- **TDD-инфраструктура**: pytest в pilot-репо (ещё не везде).

### 7.3 Добавить в Sprint 1 / 2

- **FastAPI** (опц.) — REST API для multi-agent future. Ставится в v0.7 перед подключением OpenClaw.
- **Motion $34/мес** или **Amie (free)** — защита календаря (см. решение Дмитрия).
- **Alembic migration CLI** — `migration create / upgrade / downgrade` wrapped in atlas.

### 7.4 НЕ ставим (избыточно для нашего масштаба)

- Linear / Jira (наша PM-БД их заменяет).
- Latenode / Zapier (интеграция PM ↔ Notion живёт в `atlas`, B24 ↔ Notion — существующая цепочка Дмитрия).
- Warp 2.0 / Intent (Claude Code как ADE достаточно).
- Optic (oasdiff закрывает 95%).

---

## 8. Слой 8 — Utility → Product lifecycle [из research v1]

### 8.1 4 критерия productization

1. **Real Problem Validation** — боль формулируется в 1 предложении, подтверждена ≥ 3 внешними людьми.
2. **Agent Failure Gap** — инструмент делает то, что Claude/ChatGPT не могут prompt'ом.
3. **Efficiency Gain** — экономит ≥ 30 min/week, замер 2 недели.
4. **Cognitive Load** — работает, когда Дмитрий уставший в 23:00 во вторник.

### 8.2 Триггер в PM-системе

Quarterly ritual — `portfolio graduation-review`:

```sql
SELECT p.slug, p.name
FROM projects p
JOIN project_types pt ON p.type_id = pt.id
JOIN project_statuses ps ON p.status_id = ps.id
WHERE pt.slug IN ('personal-utility', 'personal-project')
  AND ps.slug = 'maintained'
  AND p.last_touched_at > DATE('now', '-30 days');
```

По каждой — Дмитрий оценивает 4 критерия (через `portfolio graduation-review <slug>`). ≥ 3/4 → status `graduating` + автосоздание нового `project` типа `business_product` + NP-XXX модуль.

---

## 9. Слой 9 — Multi-agent readiness (v0.7+) [NEW]

### 9.1 API groundwork

Перед подключением мультиагентной платформы:

- FastAPI endpoint над SQLite/PostgreSQL.
- Auth: API-токены per-participant (generated при добавлении нового AI-агента).
- RBAC: роли определяют что агент может читать/писать:
  - **AI-CEO** — read all, write pm strategic decisions только.
  - **AI-PM** — read all, write tasks/sprints/action_log, no direct code.
  - **AI-Marketing** — read own projects, write marketing-related tasks.
  - **AI-Knowledge** (NP-004) — read all, write `research_findings`.
  - **AI-QA** — read tasks, write reviews (через Superpowers pipeline).
  - **AI-Developer** (Claude Code) — текущие полномочия.

### 9.2 Inter-agent протокол

- **MVP (v0.7)**: REST API с JSON. Каждый агент ходит через HTTP.
- **v1 (Q4 2026)**: MCP (Model Context Protocol) если платформа поддерживает.
- **v2 (будущее)**: A2A / ACP стандарты.

### 9.3 Выбор платформы

Сейчас **не выбрана**. Research Блок D в [RESEARCH_QUESTIONS_V2.md](./RESEARCH_QUESTIONS_V2.md) покрывает сравнение OpenClaw / paperclip / Agent Zero / CrewAI / AutoGen / LangGraph + другие.

### 9.4 AI-PM behavior (target, v1.0)

Ежедневно (через cron):
1. AI-PM читает `action_log` за последние 24 часа.
2. По каждому активному `project` — оценивает velocity и blockers.
3. Если проект буксует ≥ 3 дней — пингует Дмитрия с вопросами.
4. Генерирует draft sprint review за день до окончания спринта.
5. Предлагает refinement предложения для backlog.

---

## 10. Слой 10 — Anti-patterns (объединённый список из research + Superpowers)

### 10.1 Из первой волны research

1. **Мега-система (СДВГ-ловушка)** — не связывать всё сразу в монолит.
2. **Код без модели данных** — через AGENTS.md обязать data-model-first.
3. **Агентизация детерминированных задач** — 20-строчный скрипт > ИИ-агента.
4. **Раздувание AGENTS.md > 200 строк** — "Lost in the Middle".
5. **Context Collapse** — длинный тред → галлюцинации → `/clear`.
6. **Denial of Wallet** — runaway loops. Hard limits + биллинг-мониторинг каждые 3 дня.
7. **Prototype Illusion** — Human-on-the-loop approve перед деплоем.

### 10.2 Из Superpowers (дополнения)

8. **NO PROD CODE WITHOUT FAILING TEST FIRST** — TDD Iron Law.
9. **NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION** — verification-before-completion Iron Law.
10. **Старт работы на main/master без разрешения** — всегда worktree.
11. **Skip review checkpoints** — spec compliance + code quality оба обязательны.
12. **"Keep as reference"** старого кода перед TDD — delete means delete.
13. **Dispatch parallel implementation subagents** — они конфликтуют, один за раз.

---

## 11. End-to-end пример жизненного цикла задачи

**Ситуация**: клиент Cifro хочет добавить интеграцию с отчётами, время высказал в discovery-встрече.

1. **Запись встречи**: Bitrix24 CoPilot транскрибирует → автоматически появляется событие в `action_log` (через webhook в `atlas`).
2. **Triage (Monday Kickstart)**: Дмитрий вызывает `portfolio pull-inbox` — видит новый inbox-item «Cifro: интеграция с отчётами». Классифицирует: `portfolio classify <item> --project cifro-pro --type feature --priority P1`.
3. **Sprint planning**: `sprint add-task --sprint "Sprint 3" --project cifro-pro --title "..." --cpp "Отчёты за период X-Y автоматически приходят в Slack в 9:00 каждое утро" --story-points 5`.
4. **Agent pickup**: Claude Code читает `<repo>/AGENTS.md` клиента cifro-pro, находит `quality_tier: T1`, `pm_project_id`. Запускает Superpowers.
5. **Brainstorming**: Дмитрий + Claude через `superpowers:brainstorming` согласуют spec. Файл `_project/docs/SCALING_PRODUCT/cifro-pro/specs/2026-05-15-reports-slack-integration.md`. PM-система обновляет `tasks.superpowers_spec_path`.
6. **Writing plans**: `superpowers:writing-plans` → bite-sized TDD-план. Файл `...plans/2026-05-15-...md`.
7. **Worktree**: `superpowers:using-git-worktrees` → `.worktrees/reports-slack-integration/` + ветка.
8. **Subagent-driven development**: implementer пишет тесты → GREEN → spec-reviewer → code-quality-reviewer → approved.
9. **Finishing**: Option 2 (PR) → `gh pr create`. Дмитрий делает PR Review of One → compound engineering правила в AGENTS.md.
10. **Merge**: post-merge hook → `tasks.status = done`, `tasks.completed_at`, `action_log += entry(action=task_completed, actor=claude-code)`.
11. **Sprint review**: `sprint review --sprint "Sprint 3"` — показывает velocity = 21 story points (5 из них — эта задача), все done.
12. **Retrospective**: `sprint retro` — Дмитрий записывает «spec было слишком большим на одну задачу, в следующий раз декомпозировать».
13. **Expense**: если интеграция потребовала платный Slack bot → `expense add --project cifro-pro --vendor Slack --amount-monthly 8.75 --currency USD`.

---

## 12. Open questions → Research v2

См. [RESEARCH_QUESTIONS_V2.md](./RESEARCH_QUESTIONS_V2.md). Ключевые:

- ЦКП vs OKR vs KPI vs NorthStar metric — как правильно формулировать на уровне task и проекта?
- Scrum для solo + AI-команда — какие ceremonies работают, какие — театр?
- Multi-agent orchestration framework — какой выбрать (OpenClaw / paperclip / CrewAI / Agent Zero / ...)?
- Inter-agent protocol — MCP / A2A / ACP?
- Database schema паттерны PM-систем — что использует Linear / Jira / ClickUp?
- Эволюция схемы без ломки — EAV, JSONB custom fields, schema versioning.
