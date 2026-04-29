# MODEL — NP-005 Personal PM Infrastructure

**Версия**: v0.3 (2026-04-24)
**Stack**: SQLite → PostgreSQL (при необходимости) · SQLAlchemy 2.x · Alembic · Python 3.11+
**Цель файла**: структура БД для PM-системы, миграции, отношения, seed data. Более детальная техническая реализация — в коде `atlas` после Sprint 1.

**v0.2 изменения**: добавлены `projects.prefix`, `tasks.number`, `tasks.slug` (миграция 002) и `tasks.archived_at` для soft-delete (миграция 003). Реализован полный CRUD MVP в CLI `atlas` (см. CHANGELOG v0.4.1).

**v0.3 изменения (PLANNED, миграция 004)**: добавлены таблицы `tags` (§2.8) + `project_tags` (§2.9) для универсальных тегов (owner/stack/domain/other); поля `projects.renewal_count INT DEFAULT 0` и `projects.archived_group TEXT NULL` для archive engine (см. ARCHITECTURE §2.7); seed нового `project_type` со slug=`test`; seed 5 новых `project_statuses` (idea/research/planned/paused/completed/frozen). Запросы: AND-фильтр по тегам (§6.5) и archive report (§6.6).

---

## 1. ER-схема (textual overview)

```
project_types (1) ─< (N) projects
project_statuses (1) ─< (N) projects
projects (1) ─< (N) prd_snapshots
projects (1) ─< (N) expenses
projects (1) ─< (N) tasks
projects (M) >─── (M) stacks  via project_stacks
projects (M) >─── (M) participants  via project_participants

sprints (1) ─< (N) tasks   (nullable FK)
participants (1) ─< (N) tasks  (assignee)
participants (1) ─< (N) action_log  (actor)

action_log — append-only, все entities логируются туда
```

---

## 2. Таблицы MVP (v0.4 Spike)

Минимум, нужный чтобы начать работать.

### 2.1 `project_types`

```sql
CREATE TABLE project_types (
    id          UUID PRIMARY KEY,
    slug        TEXT UNIQUE NOT NULL,   -- client-project, business-product, personal-utility, personal-project, shared-infrastructure
    name        TEXT NOT NULL,
    description TEXT,
    color       TEXT,                    -- для UI
    is_archived BOOLEAN NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

**Seed data** (при первой миграции):
```sql
INSERT INTO project_types (id, slug, name, description) VALUES
    ('...', 'client-project', 'Клиентские проекты', 'Внедрения Bitrix24 + AI-агенты для клиентов Cifro.pro'),
    ('...', 'business-product', 'Новые бизнес-продукты', 'SaaS-продукты Cifro.pro (NP-001..005+)'),
    ('...', 'personal-utility', 'Личные утилиты', 'Dev-утилиты Дмитрия (Tests/* и пр.)'),
    ('...', 'personal-project', 'Личные проекты', 'Собственные инициативы (Дима/*)'),
    ('...', 'shared-infrastructure', 'Общая инфраструктура', 'Инструменты, используемые многими проектами');
```

### 2.2 `project_statuses`

```sql
CREATE TABLE project_statuses (
    id         UUID PRIMARY KEY,
    slug       TEXT UNIQUE NOT NULL,   -- experiment / active / maintained / dormant / archived / graduating
    name       TEXT NOT NULL,
    description TEXT,
    order_idx  INT NOT NULL,            -- для сортировки в UI
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 2.3 `projects`

```sql
CREATE TABLE projects (
    id                  UUID PRIMARY KEY,
    slug                TEXT UNIQUE NOT NULL,            -- 2-50 chars, [a-z0-9-], глобально уникальный
    prefix              VARCHAR(5) UNIQUE NOT NULL,      -- 1-5 chars, [a-z0-9], авто из slug (миграция 002)
    name                TEXT NOT NULL,
    type_id             UUID NOT NULL REFERENCES project_types(id),
    status_id           UUID NOT NULL REFERENCES project_statuses(id),
    priority            TEXT NOT NULL CHECK (priority IN ('P0','P1','P2','P3')),
    description         TEXT,
    one_line_summary    TEXT NOT NULL,
    estimated_deadline  DATE,
    git_repo_url        TEXT,
    local_path          TEXT,
    notion_project_id   TEXT,           -- для sync с Notion DS_PROJECTS
    notebooklm_id       TEXT,           -- если есть dedicated блокнот
    b24_company_id      TEXT,           -- опциональная ссылка на B24 (для client-project)
    renewal_count       INTEGER NOT NULL DEFAULT 0,  -- инкрементится через `atlas projects renew` (миграция 004)
    archived_group      TEXT,             -- 'clients' | 'products' | 'tests'; NOT NULL при archived_at IS NOT NULL (миграция 004)
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_touched_at     TIMESTAMP,       -- обновляется при каждой активности
    archived_at         TIMESTAMP
);

CREATE INDEX idx_projects_type ON projects(type_id);
CREATE INDEX idx_projects_status ON projects(status_id);
CREATE INDEX idx_projects_priority ON projects(priority);
CREATE INDEX idx_projects_last_touched ON projects(last_touched_at DESC);
CREATE INDEX idx_projects_archived_group ON projects(archived_group);
```

### 2.4 `participants`

```sql
CREATE TABLE participants (
    id            UUID PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('human','ai_agent','contractor')),
    slug          TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    role_default  TEXT,                   -- CEO / PM / Developer / Marketing / Knowledge / QA / Designer / Owner
    email         TEXT,
    metadata_json TEXT,                   -- JSON: модель LLM, платформа, ставка контрактника, etc.
    is_active     BOOLEAN NOT NULL DEFAULT 1,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

**Seed data для MVP**:
```sql
INSERT INTO participants (id, kind, slug, name, role_default) VALUES
    ('...', 'human', 'dmitry', 'Дмитрий Семёнов', 'Orchestrator'),
    ('...', 'ai_agent', 'claude-code', 'Claude Code', 'Developer/PM');
```

### 2.5 `project_participants` (M:N)

```sql
CREATE TABLE project_participants (
    project_id                UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    participant_id            UUID NOT NULL REFERENCES participants(id) ON DELETE RESTRICT,
    role_in_project           TEXT NOT NULL,   -- override role_default если надо
    allocated_weekly_hours    DECIMAL(4,1),
    joined_at                 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    left_at                   TIMESTAMP,
    PRIMARY KEY (project_id, participant_id)
);
```

### 2.6 `tasks`

```sql
CREATE TABLE tasks (
    id                       UUID PRIMARY KEY,
    number                   INTEGER UNIQUE NOT NULL,                 -- глобальный auto-increment (миграция 002)
    slug                     VARCHAR(100) UNIQUE NOT NULL,            -- '{project.prefix}-{task-part}', глобально уникальный (миграция 002)
    project_id               UUID NOT NULL REFERENCES projects(id),
    sprint_id                UUID REFERENCES sprints(id),            -- nullable — задача может быть в backlog
    assignee_id              UUID REFERENCES participants(id),
    title                    TEXT NOT NULL,
    description              TEXT,
    cpp_description          TEXT NOT NULL,                           -- ЦКП — Ценный Конечный Продукт
    status                   TEXT NOT NULL CHECK (status IN ('backlog','todo','in_progress','review','done','blocked','cancelled')),
    priority                 TEXT NOT NULL CHECK (priority IN ('P0','P1','P2','P3')),
    story_points             INT,
    due_date                 DATE,
    -- интеграции
    notion_page_id           TEXT,
    git_branch               TEXT,
    git_pr_url               TEXT,
    superpowers_spec_path    TEXT,
    superpowers_plan_path    TEXT,
    quality_tier             TEXT CHECK (quality_tier IN ('T1','T2','T3')),
    -- timestamps
    created_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at               TIMESTAMP,
    completed_at             TIMESTAMP,
    archived_at              TIMESTAMP                                  -- soft-delete (миграция 003)
);

CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_tasks_sprint ON tasks(sprint_id);
CREATE INDEX idx_tasks_assignee ON tasks(assignee_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_due ON tasks(due_date);
CREATE INDEX idx_tasks_number ON tasks(number);
CREATE INDEX idx_tasks_archived ON tasks(archived_at);
```

### 2.7 `action_log` (append-only audit)

```sql
CREATE TABLE action_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor_id     UUID REFERENCES participants(id),
    entity_type  TEXT NOT NULL,    -- project / task / sprint / expense / participant / ...
    entity_id    UUID,              -- nullable для глобальных событий
    action       TEXT NOT NULL,    -- created / updated / status_changed / assigned / commented / deleted / archived
    details_json TEXT                -- JSON со всеми old/new values
);

CREATE INDEX idx_action_log_entity ON action_log(entity_type, entity_id);
CREATE INDEX idx_action_log_actor ON action_log(actor_id);
CREATE INDEX idx_action_log_timestamp ON action_log(timestamp DESC);
```

**Правило**: никогда не делать `UPDATE` / `DELETE` на `action_log` — только `INSERT`. Мутации аудита запрещены.

### 2.8 `tags` [NEW v0.3, миграция 004]

Универсальные теги для проектов: owner (кто владелец), stack (технологии), domain (бизнес-домен), other (остальное).

```sql
-- §2.8 tags
CREATE TABLE tags (
    id          UUID PRIMARY KEY,
    slug        TEXT UNIQUE NOT NULL,      -- cifro-pro, b24, marketing, ai-agents
    name        TEXT NOT NULL,              -- "Cifro.pro", "Bitrix24", "Маркетинг"
    category    TEXT NOT NULL CHECK (category IN ('owner','stack','domain','other')),
    color       TEXT,                       -- optional hex для UI
    description TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tags_category ON tags(category);
```

**Примеры seed (см. W4-18 в BACKLOG)**:
- `owner`: `dmitry`, `artem`, `shared`.
- `stack`: `python`, `b24`, `notion-api`, `sqlite`, `postgresql`, `fastapi`, `telegram-api`, `wb-api`, `claude-code`.
- `domain`: `pm`, `crm`, `marketing`, `ai-agents`, `docs-parsing`, `finance`, `research`.
- `other`: `experimental`, `deprecated`, `critical-path`.

### 2.9 `project_tags` (M:N) [NEW v0.3, миграция 004]

Связь проектов и тегов, many-to-many.

```sql
-- §2.9 project_tags
CREATE TABLE project_tags (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tag_id     UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, tag_id)
);

CREATE INDEX idx_project_tags_tag ON project_tags(tag_id);
```

Запросы — см. §6.5 (AND-фильтр `atlas projects list --tag <slug>`).

---

## 3. Таблицы v0.5 (Sprint 1)

### 3.1 `sprints`

```sql
CREATE TABLE sprints (
    id                     UUID PRIMARY KEY,
    name                   TEXT NOT NULL,            -- Sprint 1, Spike, Sprint 2025-W17
    goal                   TEXT,
    start_date             DATE NOT NULL,
    end_date               DATE NOT NULL,
    status                 TEXT NOT NULL CHECK (status IN ('planning','active','review','done','cancelled')),
    velocity_story_points  INT,                     -- вычисляется при закрытии: сумма story_points от тасков в 'done'
    retro_notes            TEXT,
    created_at             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (end_date > start_date)
);
```

### 3.2 `expenses`

```sql
CREATE TABLE expenses (
    id                  UUID PRIMARY KEY,
    project_id          UUID REFERENCES projects(id),       -- nullable — расход может быть общим (не привязан к проекту)
    description         TEXT NOT NULL,
    vendor              TEXT,                                 -- Anthropic / Motion / Vercel / ...
    amount_monthly      DECIMAL(10,2),
    amount_one_time     DECIMAL(10,2),
    currency            TEXT NOT NULL CHECK (currency IN ('RUB','USD','EUR')),
    category            TEXT NOT NULL CHECK (category IN ('subscription','api-usage','hosting','hardware','contractor-fee','other')),
    started_at          DATE,
    ended_at            DATE,
    auto_renewing       BOOLEAN,
    notes               TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_expenses_project ON expenses(project_id);
CREATE INDEX idx_expenses_category ON expenses(category);
```

### 3.3 `prd_snapshots`

```sql
CREATE TABLE prd_snapshots (
    id              UUID PRIMARY KEY,
    project_id      UUID NOT NULL REFERENCES projects(id),
    version         TEXT NOT NULL,            -- v1, v2, v0.3, ...
    pain            TEXT,                     -- боль, которую решает
    features_json   TEXT,                     -- JSON array ключевых фич
    primary_user    TEXT,                     -- ICP
    metrics_json    TEXT,                     -- JSON array метрик успеха
    non_goals       TEXT,
    markdown_path   TEXT,                     -- опц. ссылка на полный PRD.md
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, version)
);
```

### 3.4 `stacks` и `project_stacks`

```sql
CREATE TABLE stacks (
    id                UUID PRIMARY KEY,
    slug              TEXT UNIQUE NOT NULL,
    name              TEXT NOT NULL,            -- Python 3.11 / Bitrix24 REST / Notion API / ...
    category          TEXT NOT NULL CHECK (category IN ('language','framework','service','database','integration','ai-model','other')),
    official_docs_url TEXT,
    notebooklm_id     TEXT,                      -- если есть блокнот по этому стеку
    is_active         BOOLEAN NOT NULL DEFAULT 1,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE project_stacks (
    project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stack_id    UUID NOT NULL REFERENCES stacks(id) ON DELETE RESTRICT,
    role        TEXT NOT NULL CHECK (role IN ('core','dependency','integration','optional')),
    notes       TEXT,
    added_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, stack_id)
);
```

---

## 4. Таблицы v0.7+ (future, multi-agent)

### 4.1 `agent_runs` (опционально)

Трекать сессии выполнения задач AI-агентами.

```sql
CREATE TABLE agent_runs (
    id                  UUID PRIMARY KEY,
    task_id             UUID NOT NULL REFERENCES tasks(id),
    agent_participant_id UUID NOT NULL REFERENCES participants(id),
    started_at          TIMESTAMP NOT NULL,
    ended_at            TIMESTAMP,
    model               TEXT,                       -- claude-opus-4-7, gpt-5, ...
    input_tokens        INT,
    output_tokens       INT,
    cost_usd            DECIMAL(10,4),
    status              TEXT CHECK (status IN ('running','completed','failed','cancelled')),
    result_summary      TEXT,
    artifacts_json      TEXT                         -- paths к plan/spec/code artefacts
);
```

### 4.2 `research_findings`

```sql
CREATE TABLE research_findings (
    id                UUID PRIMARY KEY,
    project_id        UUID REFERENCES projects(id),
    task_id           UUID REFERENCES tasks(id),
    notebook_id       TEXT NOT NULL,              -- NotebookLM notebook id
    question          TEXT NOT NULL,
    answer_summary    TEXT,
    full_answer_path  TEXT,                        -- путь к локальному markdown
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

---

## 5. Миграционный план

### 5.0 История применённых миграций

| # | Hash | Дата | Статус | Что меняет |
|---|---|---|---|---|
| 001 | `0a6b3db9f107` | 2026-04-23 | APPLIED | Initial MVP schema — все таблицы §2 + seed (project_types, project_statuses, participants). |
| 002 | `0d172deaa09b` | 2026-04-24 | APPLIED | `projects.prefix` String(5) UNIQUE NOT NULL + `tasks.number` Integer UNIQUE NOT NULL + `tasks.slug` String(100) UNIQUE NOT NULL. Backfill через slug→prefix авто-генератор + последовательный number. |
| 003 | `c55f75e76e5b` | 2026-04-24 | APPLIED | `tasks.archived_at` DateTime nullable — soft-delete для задач (по аналогии с `projects.archived_at`). |
| 004 | (pending) | (TBD на запуске) | **PLANNED** | Tags + archive engine. Создаёт `tags` (§2.8) + `project_tags` (§2.9). Добавляет `projects.renewal_count INT DEFAULT 0` + `projects.archived_group TEXT NULL`. Сидит новый `project_type` со slug=`test`. Сидит 5 новых `project_statuses`: `idea`, `research`, `planned`, `paused`, `completed`, `frozen` (существующие сохраняются: `experiment` / `active` / `maintained` / `dormant` / `archived` / `graduating`). |

### 5.1 Первая миграция (v0.4 Spike) — DONE

`001_initial.py` (`0a6b3db9f107`) — создаёт все MVP таблицы (§2) + seed data project_types, project_statuses, participants.

### 5.2 Миграция 002 (v0.4.1 CRUD) — DONE

`002_add_project_prefix_and_task_number_slug.py` (`0d172deaa09b`):
- `projects.prefix` (VARCHAR(5), UNIQUE, NOT NULL) — человекочитаемый короткий идентификатор для построения slug-ов задач.
- `tasks.number` (INTEGER, UNIQUE, NOT NULL) — глобальный auto-increment номер.
- `tasks.slug` (VARCHAR(100), UNIQUE, NOT NULL) — формат `{project.prefix}-{task-part}`.

### 5.3 Миграция 003 (v0.4.1 soft-delete) — DONE

`003_add_tasks_archived_at_for_soft_delete.py` (`c55f75e76e5b`):
- `tasks.archived_at` (DATETIME, NULLABLE) — поддержка soft-delete по аналогии с `projects.archived_at`.

### 5.4 Миграция 004 (v0.5 Tags + Archive Engine) — PLANNED

`004_tags_and_archive_engine.py` (hash TBD на запуске W4):

**Schema changes**:
- Создаёт таблицу `tags` (см. §2.8).
- Создаёт таблицу `project_tags` (см. §2.9).
- `ALTER TABLE projects ADD COLUMN renewal_count INTEGER NOT NULL DEFAULT 0`.
- `ALTER TABLE projects ADD COLUMN archived_group TEXT` (nullable).
- `CREATE INDEX idx_projects_archived_group ON projects(archived_group)`.

**Seed data**:
- Новый `project_type`: slug=`test`, name=`"Test-проекты"`, description=`"Эксперименты, утилиты, изолированные песочницы"`.
- 5 новых `project_statuses`:
  - `idea` — просто идея, ещё не решили делать.
  - `research` — research-фаза, собираем информацию.
  - `planned` — решено делать, ждёт старта.
  - `paused` — стартовал, приостановлен (архивный статус).
  - `completed` — работа закончена успешно (архивный статус).
  - `frozen` — заморожен надолго (архивный статус).
- Существующие статусы (`experiment / active / maintained / dormant / archived / graduating`) — сохраняются.

**Backfill**: `archived_group` заполняется для уже архивных проектов (у которых `archived_at IS NOT NULL`) — на основе их текущего `project_type` (mapping `client-project → clients`, `business-product → products`, etc.) или через ручной скрипт `atlas projects reorganize --dry-run` (см. BACKLOG W4-17).

### 5.5 Будущие миграции (v0.6+ Sprint 1+)

- `005_sprints_expenses_prd_stacks.py` — создаёт §3 таблицы.
- `006_multi_agent_extensions.py` (v0.7) — `agent_runs`, `research_findings`, RBAC-поля на `participants`.

---

## 6. Query examples (для команд CLI)

### 6.1 `portfolio list --type personal-utility --status active`

```sql
SELECT p.slug, p.name, p.priority, p.one_line_summary, p.last_touched_at
FROM projects p
JOIN project_types pt ON p.type_id = pt.id
JOIN project_statuses ps ON p.status_id = ps.id
WHERE pt.slug = 'personal-utility'
  AND ps.slug = 'active'
  AND p.archived_at IS NULL
ORDER BY p.priority, p.last_touched_at DESC;
```

### 6.2 `sprint show --name "Sprint 1"`

```sql
SELECT
    t.number AS "#",
    t.slug AS task_slug,
    t.title,
    t.cpp_description AS cpp,
    t.status,
    t.story_points,
    part.name AS assignee,
    pr.slug AS project
FROM tasks t
JOIN sprints s ON t.sprint_id = s.id
LEFT JOIN participants part ON t.assignee_id = part.id
LEFT JOIN projects pr ON t.project_id = pr.id
WHERE s.name = 'Sprint 1'
  AND t.archived_at IS NULL
ORDER BY t.priority, t.status, t.number;
```

### 6.2a `pm-tasks get <ref>` (resolve по number / slug / UUID)

CLI принимает `ref` в любой из 4 форм. Под капотом — ровно одна из этих SQL-выборок (см. `resolve_task_ref` в `src/atlas/pm/slugs.py`):

```sql
-- ref = '42'           → SELECT * FROM tasks WHERE number = 42;
-- ref = 'atl-fix-bug'  → SELECT * FROM tasks WHERE slug = 'atl-fix-bug';
-- ref = '<full-uuid>'  → SELECT * FROM tasks WHERE id = '<full-uuid>';
-- ref = 'a1b2c3d'      → SELECT * FROM tasks WHERE id LIKE 'a1b2c3d%';
```

### 6.3 `expense report --month 2026-04`

```sql
SELECT
    pr.slug AS project,
    e.category,
    SUM(COALESCE(e.amount_monthly, 0)) AS monthly_total,
    SUM(COALESCE(e.amount_one_time, 0)) AS onetime_total,
    e.currency
FROM expenses e
LEFT JOIN projects pr ON e.project_id = pr.id
WHERE (e.started_at IS NULL OR e.started_at <= DATE('2026-04-30'))
  AND (e.ended_at IS NULL OR e.ended_at >= DATE('2026-04-01'))
GROUP BY pr.slug, e.category, e.currency
ORDER BY monthly_total DESC;
```

### 6.4 `action-log tail --project cifro-pro --limit 20`

```sql
SELECT al.timestamp, p.name AS actor, al.entity_type, al.action, al.details_json
FROM action_log al
LEFT JOIN participants p ON al.actor_id = p.id
WHERE al.entity_type = 'task'
  AND al.entity_id IN (SELECT id FROM tasks WHERE project_id = (SELECT id FROM projects WHERE slug = 'cifro-pro'))
ORDER BY al.timestamp DESC
LIMIT 20;
```

### 6.5 `atlas projects list --tag owner:dmitry --tag stack:b24` (AND-фильтр) [NEW v0.3]

AND-логика: проект должен иметь **все** указанные теги. Два распространённых варианта — через `INTERSECT` или через `HAVING COUNT`.

**Вариант A — через `INTERSECT` (самый прозрачный)**:

```sql
-- каждый --tag раскрывается в SELECT, который даёт множество project_id с данным тегом;
-- INTERSECT возвращает пересечение. Если тегов N → N блоков, N-1 INTERSECT.
SELECT p.slug, p.name, p.priority, p.one_line_summary
FROM projects p
WHERE p.id IN (
    SELECT pt.project_id
    FROM project_tags pt
    JOIN tags t ON pt.tag_id = t.id
    WHERE t.slug = 'dmitry'

    INTERSECT

    SELECT pt.project_id
    FROM project_tags pt
    JOIN tags t ON pt.tag_id = t.id
    WHERE t.slug = 'b24'
)
  AND p.archived_at IS NULL
ORDER BY p.priority, p.last_touched_at DESC;
```

**Вариант B — через `HAVING COUNT(DISTINCT)`** (если тегов много или нужен dynamic N):

```sql
SELECT p.slug, p.name, p.priority
FROM projects p
JOIN project_tags pt ON p.id = pt.project_id
JOIN tags t ON pt.tag_id = t.id
WHERE t.slug IN ('dmitry', 'b24')
  AND p.archived_at IS NULL
GROUP BY p.id, p.slug, p.name, p.priority
HAVING COUNT(DISTINCT t.slug) = 2    -- = количество переданных --tag флагов
ORDER BY p.priority;
```

Для CLI `atlas projects list --tag <slug>...` оба варианта эквивалентны; реализация на стороне `src/atlas/pm/` — по выбору разработчика (Вариант B предпочтителен для N переменного).

### 6.6 `atlas projects archive-report` — отчёт по архивным статусам [NEW v0.3]

Ответ на вопросы: сколько проектов в каком архивном статусе, кто давно не renew, время в архиве.

```sql
-- 1. Сводка по статусам архива
SELECT
    ps.slug AS status,
    p.archived_group,
    COUNT(*) AS projects_count,
    AVG(CAST(julianday('now') - julianday(p.archived_at) AS INTEGER)) AS avg_days_in_archive,
    MIN(p.archived_at) AS oldest_archived,
    MAX(p.archived_at) AS newest_archived
FROM projects p
JOIN project_statuses ps ON p.status_id = ps.id
WHERE p.archived_at IS NOT NULL
  AND ps.slug IN ('completed', 'paused', 'frozen', 'archived')
GROUP BY ps.slug, p.archived_group
ORDER BY p.archived_group, ps.slug;
```

```sql
-- 2. Клиенты, которые давно не были renew (потенциал для re-engagement)
SELECT
    p.slug,
    p.name,
    p.renewal_count,
    p.archived_at,
    CAST(julianday('now') - julianday(p.archived_at) AS INTEGER) AS days_in_archive,
    ps.slug AS status
FROM projects p
JOIN project_types pt ON p.type_id = pt.id
JOIN project_statuses ps ON p.status_id = ps.id
WHERE pt.slug = 'client-project'
  AND p.archived_at IS NOT NULL
  AND ps.slug IN ('completed', 'paused')
  AND CAST(julianday('now') - julianday(p.archived_at) AS INTEGER) > 180
ORDER BY days_in_archive DESC;
```

```sql
-- 3. Client renewal health — кто возвращался, сколько раз
SELECT
    p.slug,
    p.name,
    p.renewal_count,
    ps.slug AS current_status,
    p.last_touched_at
FROM projects p
JOIN project_types pt ON p.type_id = pt.id
JOIN project_statuses ps ON p.status_id = ps.id
WHERE pt.slug = 'client-project'
ORDER BY p.renewal_count DESC, p.last_touched_at DESC;
```

---

## 7. Notion mirror mapping

Для MVP `atlas portfolio push`:

| PM-поле | Notion DS_PROJECTS property | Direction |
|---|---|---|
| `projects.name` | Title | PM → Notion |
| `projects.slug` | "Slug" (rich_text) | PM → Notion |
| `project_types.slug` | "Тип" (select) | PM → Notion |
| `project_statuses.slug` | "PM_Status" (select) | PM → Notion (read-only для пользователя Notion) |
| `projects.priority` | "Priority" (select) | PM → Notion |
| `projects.one_line_summary` | "Summary" (rich_text) | PM → Notion |
| `projects.git_repo_url` | "Git" (URL) | PM → Notion |
| `projects.local_path` | "Local Path" (rich_text) | PM → Notion |
| `projects.estimated_deadline` | "Deadline" (date) | **Notion → PM** (Дмитрий правит глазами) |
| `notion_status` (не в PM) | "Notion_Status" (status) | Only in Notion (Дмитрий маркирует лично) |
| `dev_notes` (не в PM) | "Dev_Notes" (rich_text) | Only in Notion |

---

## 8. Open questions по data model

Ждут ответа в research v2 (RESEARCH_QUESTIONS_V2.md, Блок G):

- Нужны ли отдельные таблицы `epics` (над `tasks`) или достаточно поля `parent_task_id` (self-reference)?
- Как моделировать **dependencies между задачами** (blocker / blocked_by)? Отдельная M:N таблица `task_dependencies`?
- Нужен ли `comments` на tasks (для общения между агентами) или достаточно action_log?
- Custom fields как у Notion/Linear — нужны или избыточны? Если нужны — EAV или JSONB поле?
- Архивация vs soft-delete — везде использовать `archived_at` или отдельное поле `deleted_at`?
