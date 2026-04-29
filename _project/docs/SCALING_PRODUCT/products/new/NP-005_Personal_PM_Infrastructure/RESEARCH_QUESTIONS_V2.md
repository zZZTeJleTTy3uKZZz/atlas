# RESEARCH_QUESTIONS_V2 — /ultraplan для второй волны deep research

**Версия**: v2 (2026-04-22)
**Тема**: IT Project Management, Scrum/Agile, Multi-Agent Orchestration, PM Database schemas, ЦКП/OKR
**Блокнот NotebookLM**: `9f109c5e-312f-4058-9c98-aee59853c58e` — «Cifro.pro — IT PM & Multi-Agent Orchestration (NP-005 v2)» (создан 2026-04-22 late).
**Статус запуска**: все 10 блоков запущены через `notebooklm source add-research ... --mode deep --no-wait`. Background subagent (ID `a6de2410...`) ждёт завершения и импорта sources. Ожидаемый wall-clock — 2-4 часа (параллельная очередь NotebookLM).
**Скрипт запуска**: `research/v2_launch_research.py` (Python + ThreadPoolExecutor 5 workers).

> **Применяется Progressive Inquiry** из обновлённого skill `notebooklm` (v5, 2026-04-22): сначала Orient вопрос по КАЖДОМУ блоку отдельно (после импорта research sources), потом focused follow-ups informed by answers. НЕ задавать все 10 блоков вопросов сразу — это нарушает skill.
>
> **Режим запуска source add-research**: `--mode deep --no-wait` (можно параллельно по 3-4).

---

## Как запускать

1. Создать блокнот: `notebooklm create "Cifro.pro — IT PM & Multi-Agent Orchestration (NP-005 v2)"`.
2. По каждому блоку A-J:
   ```bash
   notebooklm source add-research "<PROMPT из блока>" --mode deep --no-wait --notebook <id>
   ```
3. Дождаться завершения всех 10 (15-30 мин каждый): `notebooklm research wait --import-all --notebook <id>`.
4. Применить Progressive Inquiry: Orient (карта содержания) → focused follow-ups один за одним.
5. Синтез ответов в `research/v2_A..J_*.md`.
6. Обновить MODEL.md, ARCHITECTURE.md, PRD.md на основе результатов.

---

## Блок A · IT Project Management fundamentals 2026

**Зачем**: понять, в каких моделях вообще работает PM в IT, какие роли, артефакты, фреймворки применимы к нашей гибридной ситуации (solo + AI-команда + контрактники + клиенты).

**Артефакт**: раздел «Foundational PM principles» в ARCHITECTURE v3.

**Prompt**:
```
Comprehensive analysis of IT project management fundamentals for 2026. Cover:

1. Main frameworks: PRINCE2, PMBOK (7th edition), PMI Disciplined Agile, SAFe 6.0,
   Scrum, Kanban, Scrumban, XP (Extreme Programming), Shape Up (Basecamp),
   Lean Startup, ICE/RICE prioritization. For each: core concepts, when it fits,
   when it fails, 2026-relevant adaptations.

2. PM roles across models: Project Manager (classic), Product Manager, Product Owner,
   Scrum Master, Program Manager, Delivery Manager, Technical Project Manager,
   Engineering Manager. Responsibilities, decision rights, RACI boundaries,
   typical artifacts produced.

3. PM in different contexts:
   - Agency/consulting (client delivery, multi-client capacity planning)
   - Product company (product development, roadmap)
   - Solo founder / micro-team (1-5 people)
   - AI-first / agent-driven development
   For each: what works, anti-patterns.

4. Core PM artifacts: Charter, PRD, MRD, tech spec, ADR, backlog, roadmap,
   burndown, velocity report, risk register, RAID log, stakeholder matrix,
   dependencies map. When to create what, how often to update.

5. PM rituals/ceremonies: daily standup, weekly sync, sprint planning, sprint review,
   retrospective, refinement, PI planning, quarterly review. What's value,
   what's theater. Specific adaptations for AI-augmented teams.

6. Stakeholder management: communication cadence, status reports, escalation paths.

Cite specific books, research papers, industry reports (Gartner, Forrester,
McKinsey), engineering blogs (First Round, Stripe Press, Linear blog,
Basecamp/37signals). Give concrete examples with named teams/companies.
```

---

## Блок B · Scrum detailed — ceremonies, artifacts, adaptations for AI teams

**Зачем**: Дмитрий явно упомянул Scrum и спринты. Нужна глубокая конкретика (не абстракции), особенно адаптации под solo-оркестратора + AI.

**Артефакт**: Слой 6 (Ритуалы) в ARCHITECTURE v3 + Scrum-разделы в PRD.

**Prompt**:
```
Deep practical guide to Scrum in 2026 for small teams (1-5 people) and AI-augmented
teams where agents execute most of the work.

1. Scrum Guide 2020 (Sutherland/Schwaber) — core: 3 roles (Product Owner, Scrum
   Master, Developers), 5 events (Sprint, Planning, Daily, Review, Retro), 3 artifacts
   (Product Backlog, Sprint Backlog, Increment). 2020 changes vs 2017. Real
   interpretations vs theoretical.

2. Sprint mechanics:
   - Sprint length: 1 / 2 / 3 / 4 weeks — decision criteria.
   - Sprint goal — how to formulate (SMART, outcome-focused).
   - Capacity planning: story points, velocity, factor of previous-sprint velocity,
     accounting for meetings, PTO, interruptions.
   - Definition of Ready (DoR) vs Definition of Done (DoD).
   - Commitment vs forecast — Scrum Guide 2020 shift.

3. Story writing:
   - User stories: "As a <user>, I want <action>, so that <outcome>" template.
   - INVEST criteria (Independent, Negotiable, Valuable, Estimable, Small, Testable).
   - Acceptance criteria: Given/When/Then (BDD) vs checklist.
   - Story points: Fibonacci, T-shirt, ideal hours. Which works better.
   - Epic vs feature vs story vs task hierarchy.

4. Ceremonies deep-dive:
   - Daily Standup: 3 questions vs Walk-the-Wall vs silent async standup.
   - Sprint Planning: Part 1 (what) + Part 2 (how), time-boxing.
   - Sprint Review: demo, stakeholders, feedback capture.
   - Retrospective: formats (Start/Stop/Continue, 4L, Sailboat, Mad/Sad/Glad,
     5 Whys). Anti-patterns: complaining, blame, no action items.
   - Backlog Refinement: when, who, time-box, readiness targets.

5. Scrum metrics: velocity, burndown, burnup, commit-to-complete ratio,
   escaped defects, cycle time, lead time, WIP limits. Which matter, which don't.

6. Scrum for solo + AI teams:
   - Who's PO, who's SM when one person does everything?
   - How to run retro alone — self-retrospective techniques.
   - AI agents as "developers" — how to track their velocity.
   - Adapting ceremonies when "team" includes autonomous agents.

7. Scrum vs Kanban for our context:
   - Sprint commitments vs continuous flow.
   - Cadence vs flexibility tradeoff.
   - Hybrid Scrumban for services/consulting + product development.

Cite: Scrum Guide 2020, "Scrum: The Art of Doing Twice the Work in Half the Time"
(Jeff Sutherland), Linear blog posts on their cycles, Basecamp Shape Up,
Atlassian Agile Coach, Mike Cohn's writings, Henrik Kniberg's "Scrum and XP
from the Trenches", Agile Alliance experience reports.
```

---

## Блок C · Multi-agent orchestration — frameworks comparison 2026

**Зачем**: Дмитрий планирует подключить OpenClaw / paperclip / аналог для мультиагентной команды. Нужен полный сравнительный анализ + критерии выбора.

**Артефакт**: Слой 9 (Multi-agent readiness) в ARCHITECTURE + решение в Sprint 2.

**Prompt**:
```
Comprehensive comparison of multi-agent orchestration frameworks in 2025-2026.
For each framework: architecture, primary use case, supported agent patterns,
programming model, observability, cost, community, production readiness.

Frameworks to compare:
- CrewAI (joaomdmoura)
- AutoGen (Microsoft Research)
- LangGraph (LangChain team)
- LlamaIndex agents
- OpenAI Swarm / new multi-agent SDK
- Anthropic SDK subagents
- Claude Code task tool (subagents)
- Codex / OpenHands
- Agent Zero
- SuperAGI
- MetaGPT
- ChatDev
- AgentVerse
- MiniAutoGen
- Pydantic AI
- OpenClaw — need to search and evaluate
- paperclip — need to search and evaluate
- Any newer frameworks from 2026

For each:
1. Core architecture (supervisor-worker, democratic, hierarchical, graph-based).
2. Agent communication patterns (shared memory, message passing, API, file-based).
3. State management (stateless, persistent, checkpointed).
4. Deployment model (SaaS, self-hosted, hybrid).
5. Observability (tracing, logs, cost tracking).
6. Role specialization support (custom personas, tools per role).
7. Integration with external systems (APIs, databases, file systems).
8. Cost model (token costs, hosting, licenses).
9. Community size, maintenance cadence, production deployments.
10. Key strengths and weaknesses.

Include comparison matrix and decision framework:
- Use case: solo founder + 5-7 AI-agents (CEO, PM, Marketing, Knowledge, QA,
  Developer, Designer).
- Requirements: database-backed state (our PM system), git integration,
  Python ecosystem preferred, works with Claude Opus/Sonnet + GPT-5 + Gemini.
- Budget: <$500/month for compute + API.

Which framework(s) fit best? Worst? What are migration paths between them?

Cite specific production deployments, benchmarks, engineering blogs,
github stars/activity, recent releases.
```

---

## Блок D · Inter-agent communication protocols: MCP, A2A, ACP, OpenAPI+JSON

**Зачем**: выбрать протокол для общения между AI-агентами в нашей системе.

**Артефакт**: §9.2 Inter-agent protocol в ARCHITECTURE.

**Prompt**:
```
Inter-agent communication protocols comparison 2025-2026:

1. MCP (Model Context Protocol) by Anthropic:
   - Architecture, transport (stdio, SSE, HTTP), schema.
   - Supported clients (Claude Desktop, Claude Code, Cursor, Zed, Cline).
   - Server ecosystem.
   - Limitations: single-request/response, not full agent-to-agent.
   - How to use for multi-agent coordination: are servers used as shared state?

2. A2A (Agent-to-Agent protocol) — emerging standards, who's proposing:
   - Google A2A if exists, OpenAI proposals, academia papers.
   - Spec, adoption, compatibility with MCP.

3. ACP (Agent Communication Protocol):
   - History from FIPA-ACL, KQML. Modern reinterpretations 2024-2026.
   - Practical tools implementing it.

4. OpenAPI + JSON-RPC for agents:
   - Treating each agent as a REST/RPC service.
   - Schema registry, versioning.
   - When this is the right choice vs MCP.

5. Event-driven architectures for agents:
   - Kafka, NATS, Redis streams as message bus for agent coordination.
   - Event sourcing for agent state.

6. Shared database as coordination mechanism:
   - Agents read/write to PostgreSQL with row-level locking.
   - Event tables (action_log pattern) as communication.
   - When this is better than explicit message passing.

7. Decision matrix for our setup:
   - 5-7 AI agents + 1 human + future human contractors.
   - SQLite → PostgreSQL migration path.
   - Python-first with possibility of TypeScript agents.
   - Local-first (Windows 11 laptop) with optional cloud deploy.
   - Observability and debuggability requirements.

Cite: Anthropic MCP documentation (modelcontextprotocol.io), AgentOps blog,
academic papers on agent communication protocols, real deployments.
```

---

## Блок E · Project documentation hierarchy: PRD, MRD, tech spec, ADR

**Зачем**: определить четкие правила какой документ когда создавать, чтобы избежать bloat и не упустить важное.

**Артефакт**: templates/ в nation-task-cli + §5 Методология.

**Prompt**:
```
IT project documentation hierarchy best practices 2025-2026 for small teams and
AI-augmented development:

1. Types of documents and when to create each:
   - Charter / One-pager — initial business case
   - MRD (Market Requirements Document) — market-side
   - PRD (Product Requirements Document) — product-side
   - Design Doc / RFC — architectural proposal
   - Tech Spec — implementation detail
   - ADR (Architecture Decision Record) — decision log
   - API spec (OpenAPI / GraphQL schema) — interface contract
   - User Story / Ticket — granular task
   - Postmortem / Retro — after-the-fact
   - Runbook / SOP — operational

2. PRD templates from leading companies:
   - Stripe Press / Patio11
   - Shreyas Doshi
   - Lenny's Newsletter
   - Figma PRD template
   - Linear PRD philosophy
   - Shape Up "pitch" format (Basecamp)
   - Amazon 6-pager
   - Working backwards (Amazon)

3. ADR practices (Michael Nygard, Gregor Hohpe):
   - Template: Context, Decision, Consequences.
   - When to write ADR (vs skip).
   - Where to store (repo /docs/adr/).
   - Numbering and indexing.
   - Real examples from open-source projects.

4. Tech spec vs PRD — boundaries:
   - What goes in PRD (problem, users, success metrics, scope).
   - What goes in tech spec (architecture, data model, APIs, edge cases).
   - Anti-pattern: conflating them.

5. Documentation for AI-driven development:
   - AGENTS.md / CLAUDE.md standards (already covered in research v1).
   - Specs optimized for agent consumption (structured, machine-readable).
   - "Point and Call" / "Spec-driven development" (Kiro, BMAD).

6. Keeping docs alive (anti-rot):
   - Doc-as-code, lint rules for docs.
   - Auto-generation from code (OpenAPI, Pydantic, JSONSchema).
   - Review cadence, owner per doc.
   - Deprecation protocol.

7. For our context (solo + AI-team):
   - Minimum viable docs per project type (utility / product / client).
   - When PRD is overkill (personal-utility experiments).
   - Automating doc maintenance through AI agents.

Cite: ThoughtWorks TechRadar, Martin Fowler blog, Atlassian docs,
Shreyas Doshi Twitter/substack, Lenny's Newsletter, Google Engineering Practices.
```

---

## Блок F · Database schema patterns for PM systems

**Зачем**: наша PM-система в виде БД — хочется учесть что работает у Linear, Jira, Asana, ClickUp, Notion, чтобы не изобретать велосипед.

**Артефакт**: обновление MODEL.md v0.2.

**Prompt**:
```
Database schema patterns for modern project management systems 2025-2026.
Reverse engineer and analyze:

1. Linear.app schema (known through their API):
   - Core entities: Issue, Project, Team, Cycle, Roadmap, Initiative, Milestone.
   - Relationships: how tasks flatten/nest.
   - Custom fields vs core fields.
   - Workflow/state machine design.
   - Identity model (user, team, org).

2. Jira Cloud schema:
   - Epics, Stories, Tasks, Subtasks hierarchy.
   - Projects, Sprints, Boards, Filters.
   - Custom fields (massive flexibility) — pros/cons for our scale.
   - JQL query language — what it tells us about underlying schema.

3. Asana data model:
   - Tasks as first-class, nested subtasks.
   - Projects, Portfolios, Goals.
   - Dependencies.
   - Rules/automations data model.

4. ClickUp hierarchy (Everything/Spaces/Folders/Lists/Tasks):
   - Flexibility vs complexity.

5. Notion databases:
   - Schemaless at UI level, structured underneath.
   - Relation, rollup, formula fields.

6. GitHub Projects (v2):
   - Items, fields, views.
   - Connection to Issues/PRs.

7. Common patterns:
   - Hierarchy: flat vs nested vs DAG.
   - Custom fields: EAV vs JSONB vs dedicated columns vs separate tables.
   - Workflow/state machines: FSM in db vs app layer.
   - Dependencies: M:N blocker/blocked_by.
   - Comments: polymorphic vs per-entity.
   - Attachments: file storage integration.
   - Permissions: row-level vs role-based.
   - Activity/audit log: append-only vs versioned.

8. Migration patterns:
   - Schema evolution without downtime.
   - Zero-downtime migrations (expand-contract pattern).
   - Alembic vs Flyway vs dbmate vs custom.
   - Testing migrations (dump → migrate → verify).

9. For our context (SQLite MVP → PostgreSQL):
   - When to split tables vs add columns.
   - Indexing strategy for 1000-10000 projects / 10000-100000 tasks.
   - Full-text search (FTS5 in SQLite, pg_trgm in Postgres).
   - Time-series data (action_log, agent_runs) — retention, archival.

Cite: Linear engineering blog, Atlassian developer docs, open-source PM tools
(Plane, Taskcafe, OpenProject) source code, database design textbooks.
```

---

## Блок G · Metrics: ЦКП, OKR, KPI, NorthStar — formulation at task/project/portfolio level

**Зачем**: Дмитрий явно упомянул ЦКП (Ценный Конечный Продукт). Нужна практика формулирования для каждого уровня иерархии.

**Артефакт**: руководство по заполнению `tasks.cpp_description` + обновление PRD §10 success metrics.

**Prompt**:
```
Value-focused metrics in project management 2025-2026, deep comparison:

1. ЦКП (Ценный Конечный Продукт) — Russian management tradition
   (L. Ron Hubbard methodology adoption in Russian business):
   - Origin, definition: the exchangeable product of a role/task.
   - How Russian business (Visotsky Consulting, Gennady Tishin) use it.
   - Formulating ЦКП at: post (role), task, department, company level.
   - Common mistakes and anti-patterns.
   - Comparison with western concepts.

2. OKR (Objectives and Key Results) — Google/Andy Grove:
   - Formulation: qualitative Objective + 3-5 measurable Key Results.
   - Aspirational (70% is success) vs committed (100%).
   - Cadence: quarterly standard.
   - Books: "Measure What Matters" (John Doerr), "Radical Focus" (Christina Wodtke).
   - Anti-patterns: OKRs as KPIs, top-down only, too many.

3. KPI (Key Performance Indicator):
   - Leading vs lagging indicators.
   - Input vs output vs outcome metrics.
   - When KPI is the right tool (operational monitoring).

4. NorthStar Metric (Sean Ellis, Amplitude):
   - Single most important metric.
   - NSM Framework: NSM + 3-5 input metrics.
   - Examples: Airbnb (nights booked), Facebook (DAU), Spotify (minutes listened).

5. Other frameworks:
   - Jobs-to-be-Done outcomes.
   - Value stream metrics (DORA: deployment frequency, lead time, MTTR, change failure rate).
   - Pirate Metrics (AARRR: Acquisition, Activation, Retention, Referral, Revenue).

6. Metrics for solo-operator + AI team:
   - Personal KPIs (deep work hours, velocity, cognitive load).
   - Team metrics when "team" is agents (acceptance rate, cost per task, agent uptime).
   - Burn rate (API + subscriptions) as constraint.

7. Task-level ЦКП (our context):
   - How to write ЦКП for coding task ("function X does Y") vs outcome ("user can Z").
   - ЦКП vs acceptance criteria — overlap and difference.
   - Measurability: binary vs scaled.

8. Project-level metrics:
   - Project health dashboards: green/yellow/red signals.
   - Leading indicators of trouble.
   - Resource burn vs value delivered.

9. Portfolio-level metrics:
   - Portfolio velocity, throughput, health distribution.
   - Which projects drain energy without returning value.
   - Quarterly graduation/archival signals.

Cite: "Measure What Matters" (Doerr), Christina Wodtke blog, Hubbard Management
System, Visotsky Consulting materials (Russian), Linear metrics philosophy,
Amplitude NSM research, Reforge blog posts on metrics, DORA State of DevOps.
```

---

## Блок H · Capacity planning для multi-track work

**Зачем**: у Дмитрия 10 клиентов + 5 продуктов + утилиты + личные. Как не разорваться и не перегрузить агентов.

**Артефакт**: Слой 7 (Capacity) в ARCHITECTURE + `sprint plan` command enhancements.

**Prompt**:
```
Capacity planning for multi-track work by solo operators and small teams (1-5 people)
with AI agents as force multipliers:

1. Classical capacity planning:
   - Hours available vs hours needed per sprint.
   - Focus factor (% of ideal work time actually available).
   - Accounting for: meetings, interruptions, context switching, research, admin.
   - Typical focus factor: 50-70% for knowledge work.

2. Multi-track allocation:
   - Ratio models: 60/30/10 (client-core/R&D/learning), 70/20/10, 50/30/20.
   - Energy-based allocation (deep-work slots vs admin vs creative vs reactive).
   - Seasonal themes (one-big-thing per quarter).

3. AI agent capacity:
   - Parallelism: how many agents can one person supervise.
   - Queue/backlog of tasks per agent.
   - Agent-specific velocity (cheap model fast for simple, expensive slow for complex).

4. Anti-patterns:
   - Over-committing in sprint planning.
   - Ignoring WIP limits (work-in-progress).
   - Context-switching tax.
   - Hidden work (research, debugging) not tracked.

5. Tooling for capacity planning:
   - Motion.app / Reclaim AI (AI-scheduled).
   - Linear Cycles capacity view.
   - GitHub Projects milestone capacity.
   - Custom: story points × historical velocity × focus factor.

6. Prioritization under constraints:
   - Now/Next/Later + WIP limits.
   - Must/Should/Could/Won't (MoSCoW).
   - ICE/RICE scoring for cross-track comparison.
   - Cost of Delay weighted shortest job first (WSJF from SAFe).

7. Signals of overcommitment:
   - Missed sprint goals ≥ 2 consecutive.
   - Growing backlog of "in progress" tasks.
   - Quality regression (bugs escape, defect rate up).
   - Personal: sleep, mood, focus quality.

8. Solo + AI team specifics:
   - Human oversight limit: how much agent output can one person review per day.
   - Response time SLA for agent questions.
   - Batching reviews vs interleaving with new tasks.

Cite: Shape Up (Basecamp), Linear Method, DHH/37signals blog posts,
Manuel Kiessling, Cal Newport "Deep Work", Bud Caddell "On the Brink",
Reforge capacity planning content.
```

---

## Блок I · Notion internals + integration patterns for PM

**Зачем**: Дмитрий уже ведёт в Notion. Нужна глубокая интеграция без конфликтов.

**Артефакт**: `atlas portfolio push/pull-*` команды + §3.2 Интеграционные команды в ARCHITECTURE.

**Prompt**:
```
Notion as a PM mirror and inbox — integration patterns and gotchas 2025-2026:

1. Notion API capabilities:
   - Databases, pages, blocks, properties.
   - Relation and rollup properties — how they work.
   - Formula and computed properties.
   - Rate limits, pagination.
   - Webhooks (if available 2026).

2. Common patterns for syncing external DB ↔ Notion:
   - Identity mapping (external_id as property).
   - Conflict resolution (canonical field per property).
   - Append-only blocks for logs.
   - Rollup caching vs live query.

3. Notion as inbox for ideas:
   - Quick capture on mobile (Notion AI for voice, Fast Notion entry).
   - Tagging conventions.
   - Pull to external PM.

4. Notion as due-date canonical:
   - Date property with reminders.
   - iCal feed for calendar sync.
   - Filtering and views.

5. Real integrations:
   - Linear ↔ Notion.
   - Jira ↔ Notion.
   - GitHub ↔ Notion.
   - Custom Python scripts using notion-sdk-py.

6. Anti-patterns:
   - Bidirectional sync of same property → infinite loops.
   - Over-reliance on Notion as source of truth for dev work.
   - Losing data when Notion renames properties.
   - Ignoring Notion rate limits in batch sync.

7. Our specific case:
   - Notion DS_PROJECTS with b24_company_id and b24_contact_id.
   - Notion DS_TASKS as inbox + due dates.
   - SQLAlchemy ↔ notion-sdk-py mapping strategy.
   - Handling Notion API outages (queue retries).

8. Tradeoffs vs alternatives:
   - Obsidian as alternative "second brain" (already covered in research v1).
   - Capacities, Tana, Mem.ai for multi-database.

Cite: Notion API docs, notion-sdk-py github, AI-Jason/Felix Krause Notion
integration tutorials, Thomas Frank PM templates.
```

---

## Блок J · Migration cases: custom PM tools and their evolution

**Зачем**: понять траекторию от markdown к БД к полноценной SaaS. Не повторить чужих ошибок.

**Артефакт**: Roadmap refinement в PRD §9.

**Prompt**:
```
Case studies of teams that built custom PM systems (2018-2026) — from internal
tools to public products:

1. Linear (founder stories):
   - Started as Cal Henderson + Karri Saarinen internal tool.
   - What schema they chose, what they pivoted.
   - When they extracted to SaaS.

2. Height, Shortcut, Notion (PM features), Hey Calendar:
   - Migration trajectories.

3. Open-source PM tools:
   - Plane (Linear-clone open source) — schema and architecture.
   - Taskcafe, OpenProject, Vikunja.

4. Internal tools that stayed internal:
   - Basecamp's Shape Up running on their own Basecamp (meta).
   - How Stripe / Airbnb / Netflix engineering teams track work internally
     (anecdotal from engineering blogs).

5. From markdown-git to database:
   - Git-based PM tools (gitea issues, tracker, gtd.md approaches).
   - When people give up and migrate to database.
   - Migration patterns (CSV exports, API sync, reimplement from scratch).

6. Workflow evolution:
   - Year 1: plaintext + manual lists.
   - Year 2: markdown + automation scripts.
   - Year 3: database + CLI.
   - Year 4: + web UI.
   - Year 5: + multi-user + SaaS.
   Real examples of teams at each stage.

7. Our decision points:
   - When to add web UI (if ever).
   - When to open-source.
   - When to productize to clients.
   - When to merge with existing tool instead.

8. Anti-patterns:
   - Building "perfect" PM tool without using it.
   - Migrating too often (markdown → Notion → Linear → own-DB → ...).
   - Not syncing new-tool back to team.

Cite: Linear Changelog, Notion Engineering blog, Basecamp Signal v Noise,
Stripe Press, Airbnb Engineering Medium, relevant Hacker News threads
with specific URLs.
```

---

## §K · Итоговый синтезирующий запрос (после всех A-J)

После импорта всех 10 research-ответов — задать блокноту:

> «На основе всех 10 блоков research (A-J), собери финальную рекомендацию для моей ситуации: solo Orchestrator (Дмитрий) + AI-partner (Claude Code) + в ближайшие 3 месяца подключение мультиагентной команды (AI-CEO, AI-PM, AI-Marketing, AI-Knowledge, AI-QA), + 10 клиентов Bitrix24 + 5 бизнес-продуктов + 14+ утилит. Стек: SQLite → PostgreSQL, Python, Superpowers plugin + NotebookLM, Notion как user surface.
>
> Дай:
> 1. Итоговую архитектуру PM-системы: таблицы БД + ключевые связи + 3-5 самых важных CLI-команд.
> 2. Выбранную мультиагентную платформу (1 лучший вариант + 1 запасной) с обоснованием.
> 3. Выбранный протокол inter-agent communication.
> 4. Scrum-ceremonies адаптированный чеклист (что делать, что не делать).
> 5. Шаблон ЦКП для task-уровня (4-6 примеров разных типов задач).
> 6. 10 анти-паттернов именно для нашей конфигурации (не общих).
> 7. Roadmap: что в Spike (v0.4), что в Sprint 1 (v0.5), что в Sprint 2 (v0.6), что в v0.7+, с обоснованием порядка.»

Ответ → в `research/v2_K_synthesis.md`.

---

## §L · Вопросы к Дмитрию перед запуском research v2

- [ ] Блокнот для v2: создать новый `Cifro.pro — IT PM & Multi-Agent Orchestration (NP-005 v2)` или использовать общий? (Рекомендация: отдельный, тема большая.)
- [ ] Порядок запуска: все 10 параллельно через `--no-wait` (≈ 4-6 часов wall-clock, если NotebookLM не ограничит) или по 3-4 за раз (безопаснее для billing)? (Рекомендация: 3-4 параллельно, 3 волны.)
- [ ] Добавить ли Блок M про **личные финансы / налоги / учёт для ИП/ООО**? Это актуально для expenses-tracking, но выходит за scope NP-005. (Моя рекомендация: не добавлять — это отдельная тема.)
- [ ] Добавить ли Блок N про **security / RBAC / compliance для multi-agent systems** (особенно если будут внешние подрядчики)? (Моя рекомендация: добавить как Блок N, это важно до v0.7.)

---

## Статус готовности к запуску

- [x] Вопросы сформулированы.
- [ ] Блокнот назначен.
- [ ] Ответ Дмитрия по §L.
- [ ] Research запущен.
- [ ] Результаты синтезированы в `research/v2_*.md`.
- [ ] ARCHITECTURE, MODEL, PRD обновлены.
- [ ] Spike v0.4 старт после (или параллельно с) синтеза.
