# RESEARCH_QUESTIONS — Вопросы для deep research по NP-005

> **Цель**: расширить мышление Дмитрия про PM-инфраструктуру и подтвердить/опровергнуть гипотезы из [METHODOLOGY.md](./METHODOLOGY.md). Каждый блок = 1 отдельный research-запуск в NotebookLM (можно распараллелить через `--no-wait`).
>
> **Блокнот**: назначить новый (предложение — `Cifro.pro — Personal PM Infrastructure (NP-005)`) или временно импортировать в `d01b964f-1c9e-4c87-a999-4dddd69eef3b` (общий «Новые продукты»).
>
> **Режим**: `--mode deep` для всех — нужна глубина и максимум источников.

---

## Как использовать этот файл

1. Скопировать готовый **prompt** из любого блока ниже.
2. Запустить:
   ```bash
   notebooklm source add-research "<PROMPT>" --mode deep --no-wait --notebook <ID>
   ```
3. После завершения всех research — задать блокноту сводные вопросы из §9 «Итоговые запросы».
4. Ответы вернуть в `research/` подпапку модуля с цитатами, потом синтезировать в `METHODOLOGY.md v2` и `ARCHITECTURE.md`.

---

## Блок A · Multi-track portfolio architecture for solo operators and small teams

**Зачем**: понять как устроена PM-инфраструктура у людей, которые одновременно ведут клиентские проекты, собственные продукты и R&D-эксперименты. Дмитрий ведёт 4 трека — нужно знать проверенные паттерны.

**Артефакт**: раздел «Portfolio structure patterns» в `ARCHITECTURE.md`.

**Prompt**:
```
Portfolio management architectures for solo developers, indie founders, agency operators,
and staff-level engineers who simultaneously run (1) client delivery work,
(2) their own product bets, (3) R&D utilities and experiments, and (4) personal projects.
How do successful operators (Paul Graham, Simon Willison, Thorsten Ball, Pieter Levels,
Steph Ango, George Hotz, Arvid Kahl, Rob Walling, Jason Cohen, tptacek / Thomas Ptacek,
Patrick McKenzie, Fabrizio Rinaldi, Amjad Masad) organize their multi-track portfolios?

Compare taxonomies: bet-size (big bets vs. small bets), lifecycle (experiment → product → sunset),
revenue vs. strategic, customer-facing vs. internal. What frameworks are used for
cross-track prioritization (ICE, RICE, WSJF, Wardley Maps, Now/Next/Later, T-Shirt Sizing,
Cost of Delay, MoSCoW, Kano model, Opportunity Solution Tree).

What practices prevent track-thrashing and loss of focus: WIP limits, dedicated deep-work
blocks, seasonal themes, "one thing at a time" rules, concurrent-track caps.

What signals tell an operator to kill a utility, promote an experiment to a product,
or graduate from personal use to public release? Extract real anecdotes with year and source.
Cite case studies, interviews, Twitter/X threads, blog posts with URLs where available.
```

---

## Блок B · AI-ready context and agent-first documentation standards

**Зачем**: выяснить state-of-the-art 2025-2026 по документации, которую читает AI-агент. Есть ли emerging conventions для `AGENTS.md`, `CLAUDE.md`, project-context файлов?

**Артефакт**: раздел «Project standard» в `METHODOLOGY.md` + образец AGENTS.md v2 в `ARCHITECTURE.md`.

**Prompt**:
```
AI-first project documentation standards 2025-2026 for agent-executed development:
how do teams structure AGENTS.md, CLAUDE.md, GEMINI.md, .cursorrules, .windsurfrules,
PROJECT.md, PRD.md, ARCHITECTURE.md, CONTEXT.md, STACK.md, DATA_TABLE.md so that
Claude Code, Codex, Cursor, Aider, Devin, Windsurf, Anthropic SDK agents can load
project context in under 2 minutes and act correctly.

What information MUST live in the project-level agent briefing file (AGENTS.md/CLAUDE.md):
architecture overview, non-goals, coding conventions, test commands, deployment steps,
domain glossary, edge cases, forbidden patterns, model-specific guidance, decision records.

How are emerging conventions evolving? Compare proposals from Anthropic (skills, AGENTS.md),
OpenAI (codex), Cursor (.cursorrules), OpenHands, Aider, Zed, Continue.dev. Any de-facto
standard forming? Any schemas / validators / linters?

How do teams keep agent-briefing files from rotting? Doc-as-code, auto-sync from code,
agent-maintained docs, pre-commit hooks. Real examples from open-source projects
(names, repos, URLs).

What anti-patterns are documented: too-long context, contradictory instructions,
outdated examples, leaking secrets, "dead" documentation that agents follow blindly?
Case studies of failures.
```

---

## Блок C · Agentic development project management practices

**Зачем**: чем PM для «агент-исполнитель, человек-надзиратель» отличается от классического PM. Что нужно менять в планировании, estimation, verification.

**Артефакт**: раздел «Agentic PM practices» в `METHODOLOGY.md`.

**Prompt**:
```
Project management practices specific to agentic software development (where the primary
executor is an AI coding agent like Claude Code, Cursor Agent, Aider, Devin, OpenHands,
Codex) 2024-2026. How do teams at Anthropic, Google, OpenAI, Cognition, Magic,
Replit, Vercel, Supabase, Linear, Factory, Cline structure their work when agents do
the bulk of code-writing?

Compare: task decomposition granularity (how small should a task be for an agent?),
planning artifacts (PRD, spec, plan-markdown, TDD first), verification rituals
(human review, test-first, CI gates, shadow-mode), success metrics (tasks-completed-correctly,
rollback rate, agent-human edit ratio, time-to-PR, cost-per-task).

What changes vs. classical agile / sprint / scrum / kanban? Does agile still apply?
Velocity, story points, burndown charts — still useful or obsolete?

How do teams handle agent failures: runaway loops, hallucinated APIs, missed edge cases,
spec drift? What's the recovery protocol?

What tooling has emerged: agent orchestration (LangGraph, CrewAI, Swarm), observability
(Langfuse, Braintrust, Helicone, Phoenix), spec management (Spec-driven development,
taskmaster, superpowers skills), verification (eval harnesses, test generators).

Cite specific teams, post-mortems, engineering blog posts, conference talks with URLs.
Quantify where possible: "team X reduced cycle time from Y to Z days after adopting W".
```

---

## Блок D · Integration-heavy project management (external APIs, reverse engineering, SDK drift)

**Зачем**: половина работы Дмитрия — интеграция с Bitrix24, Notion, Google, NotebookLM. Внешние API меняются, документация дрейфует, часть эндпоинтов недокументирована. Нужна PM-практика именно под этот класс.

**Артефакт**: раздел «Integration lifecycle» в `METHODOLOGY.md`.

**Prompt**:
```
Best practices for managing integration-heavy projects where external APIs / SDKs /
third-party platforms drive the work: CRM integrations (Bitrix24, Salesforce, HubSpot,
Pipedrive), productivity tools (Notion, Asana, Monday, Linear), cloud platforms
(Google Workspace, Microsoft 365, Slack), search (Algolia, Elastic), AI platforms
(OpenAI, Anthropic, Google Gemini).

How do teams at Zapier, Make, n8n, Merge.dev, Paragon, Tray.io, Workato, Segment,
RudderStack track (a) which APIs they depend on, (b) which endpoints they use,
(c) the documentation status of each endpoint (official, beta, undocumented),
(d) last-tested date, (e) known gotchas and rate limits?

What artifacts exist: integration inventory, API capability matrix, endpoint ownership
registry, compatibility matrix across plan tiers, version pinning strategies?

How is reverse-engineered / undocumented endpoint work managed vs. officially documented
work? Risk tiering, monitoring, canary usage.

How do teams detect and respond to upstream API changes: webhook monitoring, schema diff,
automated regression, RSS/changelog feeds, community signals (Reddit, Stack Overflow,
GitHub issues).

Specific patterns for Russian-speaking Bitrix24 ecosystem developers if available —
community practices, common pitfalls.

Cite real teams, engineering blogs, open-source integration libraries with URLs.
```

---

## Блок E · Tooling for personal project management — what do individual senior engineers actually use in 2025-2026

**Зачем**: выбрать стек инструментов. Linear для одного человека? Obsidian + Tasks? Notion? TickTick? Motion? Reclaim? Roam? GitHub Projects? Locally-run Markdown?

**Артефакт**: раздел «Tooling decision matrix» в `ARCHITECTURE.md`.

**Prompt**:
```
Personal project management tool stacks of senior software engineers, staff engineers,
tech leads, indie founders, and agency owners in 2025-2026. What combinations of tools
are actually used day-to-day?

Compare categories:
- Task / backlog tracking: Linear (solo or team), Height, Shortcut, Notion Tasks,
  GitHub Projects, Obsidian Tasks, Logseq TODOs, TickTick, Todoist, Things 3,
  OmniFocus, Motion, Reclaim, Sunsama, Superhuman Tasks, Apple Reminders.
- Knowledge / notes: Obsidian, Logseq, Roam, Notion, Bear, Craft, Capacities,
  Anytype, Tana, Reflect, Supernotes, plain Markdown in git.
- Calendar & time blocking: Google Calendar, Cron, Amie, Notion Calendar,
  Akiflow, Morgen, Fantastical.
- Kanban / boards: Trello, GitHub Projects, Linear Cycles, Height, Airtable,
  Coda, Basecamp.
- Writing / specs: Google Docs, Notion, Bear, Markdown + git, Spec-driven (taskmaster,
  Anthropic skills), Overleaf for technical, Confluence, Almanac.
- Time tracking / reflection: Toggl, Harvest, RescueTime, Screen Time, Timing,
  Clockify, self-built scripts.

For each category: when is it the right choice, when is it overkill, when underkill?
Decision criteria: solo vs. team, local-first vs. cloud, speed of capture, retrieval
quality, AI integration, offline, data ownership, cost at scale.

What real workflows chain multiple tools? "Capture in X → plan in Y → execute in Z → review
in W". Examples with named people / teams where possible.

What's emerging in 2025-2026? AI-native tools (Limitless, Granola, Cove, Wispr Flow),
calendar-task fusion (Motion, Akiflow), memory-first tools (Mem.ai, Reflect), local-first
(Anytype, Tana, Capacities).

Anti-patterns: tool sprawl, constant migration, over-configuration, productivity theatre.
Cite specific workflows with URLs (Twitter threads, Substack posts, blog posts, YouTube).
```

---

## Блок F · Lifecycle «utility → product» — when and how does a personal tool become a public product

**Зачем**: Дмитрий строит утилиты, некоторые могут стать продуктами. Нужны критерии «созрело ли», протокол миграции, risk-флаги.

**Артефакт**: раздел «Promotion criteria» в `METHODOLOGY.md` + чеклист в `ARCHITECTURE.md`.

**Prompt**:
```
The lifecycle path "personal utility → public product" in software: when is a tool
ready to graduate from private use to a product, and what's the protocol? Ship-it
stories and failures from 2015-2026.

Case studies: Obsidian (Erica and Shida — from personal use to product), Beeper
(Eric Migicovsky), TablePlus, Linear (from Cal's workflows), Vercel / Next.js
(Guillermo Rauch), Supabase (Paul Copplestone), Fathom Analytics, Plausible, Zed
(Conrad Irwin / Nathan Sobo), Ghostty (Mitchell Hashimoto), Logseq, Obsidian Publish,
Tailscale (initially internal), Excalidraw, TinyMCE, Fabrizio Rinaldi / Linear design,
Steph Ango / Obsidian, Arvid Kahl / Zero to Sold.

Criteria to productize: demonstrated repeat use, clear persona beyond self,
documented pain, willingness to pay signal, total addressable market, differentiation,
competitive moat, founder-market fit, technical readiness.

Criteria NOT to productize (parking as "personal only"): niche audience,
unsustainable maintenance burden, regulatory exposure, security-sensitive, requires
team you don't have, conflicts with day job / client work.

Migration protocol: naming, licensing, separation of personal data, documentation,
onboarding flow, support infrastructure, legal (TOS, privacy policy, billing entity).

Warning signs of premature productization: burnout, feature creep, loss of original
user (self), ballooning support, revenue < value of time.

Cite specific migration stories with dates and outcomes. URLs preferred.
```

---

## Блок G · Measurement and retrospective rituals for solo operators and small teams

**Зачем**: как не попасть в activity theater. Какие ритуалы/метрики работают у одиночек. Weekly / monthly / quarterly review — что реально двигает дело.

**Артефакт**: раздел «Review rituals» в `METHODOLOGY.md`.

**Prompt**:
```
Retrospective and measurement rituals for solo operators and teams of 1-5 people
doing mixed work (client delivery, own products, R&D). What works and what's theater?

Weekly review practices: GTD weekly review (David Allen), Ultralearning / Ali Abdaal,
Tiago Forte / BASB, Cortex podcast "yearly themes", Shape Up "cool-down weeks",
Linear's cycle model, Basecamp's 6-week cycles + 2-week cooldown, First Round Review
frameworks, Asana "Reflect" templates.

Metrics that work for solo: cycle time, throughput, WIP count, lead time, % of plan
completed, weekly retro scores, energy budget, deep-work hours, shipped vs. parked.

Metrics that DON'T work: velocity without team context, hours worked, number of commits,
number of PRs closed, number of tasks moved.

What signals a track needs to be killed: 2-3 missed weekly goals in a row, lead time
blowout, interest collapse, external dependency freeze, customer / user feedback drought.

What signals a track should be accelerated: compounding demand, repeat mentions in
conversations, competitive threat, market window, high-engagement user feedback.

How do people run reviews without becoming bureaucrats? Minimum viable rituals.

Cite specific systems (with founders / authors / URLs): e.g. Steph Ango's
"Evaluate your system", Derek Sivers "HELL YEAH or no", Cal Newport's Deep Work
review, David Allen's GTD, Andy Hunt / Pragmatic Thinking, James Clear / habit review,
HBR / McKinsey reflection frameworks.

Anti-patterns: too many rituals, ritual theatre, metric-driven burnout, review-as-procrastination.
```

---

## Блок H · Hybrid Bitrix24 + Notion + local markdown + NotebookLM workflows

**Зачем**: конкретный стек Дмитрия. Клиенты и CRM в Bitrix24, проекты и задачи в Notion, dev в local markdown, research в NotebookLM. Как это сшить без ручного дублирования.

**Артефакт**: раздел «Integration map» в `ARCHITECTURE.md`.

**Prompt**:
```
Workflows that combine CRM (Bitrix24, Salesforce, HubSpot), productivity hubs
(Notion, ClickUp, Coda, Airtable), local-first docs (Obsidian, Logseq, plain git markdown),
and AI research tools (NotebookLM, Perplexity Spaces, Exa, ChatGPT Projects) into
a single personal / small-team operating system.

For each integration direction:
- CRM ↔ Notion (client company → project page, deal → task, contact → person)
- Notion ↔ local markdown (task → spec file, project → repo, doc ↔ markdown)
- Local markdown ↔ NotebookLM (doc folder → notebook sources, research answers →
  markdown files with citations)
- All of the above ↔ AI agents (Claude Code reads everything, writes back where allowed)

What integration patterns exist: single source of truth (SSOT) per data type,
bidirectional sync, unidirectional projection, event-sourced logs, webhook fan-out,
polling reconciliation.

Tools for sync: Zapier, Make, n8n, Paragon, Merge, self-built Python / Node scripts,
Notion API + CRM API + LLM in the loop.

Conflict resolution: what wins when Notion and local disagree? Last-write-wins,
canonical field per concept, manual merge, timestamp-authoritative, operational-transform.

Specific Bitrix24 + Notion patterns (Russian-speaking Bitrix24 community practices
if available): rest.cifrosoft, bitrix-utils, b24pysdk, notion-py, self-built bridges.

Anti-patterns: double-entry, sync drift, stale references, circular updates.

Cite real workflows with URLs (blog posts, GitHub repos, community forum threads).
```

---

## §9 · Итоговые запросы к блокноту (после импорта всех ответов A-H)

После того, как все 8 блоков ответов импортированы в NotebookLM — задать блокноту синтезирующие вопросы:

1. «Собери единую архитектуру Personal PM Infrastructure для одиночки, который ведёт 10 клиентских проектов, 4+ новых продуктов бизнеса, 14+ личных утилит и 1+ личных проектов одновременно. Опиши слои: портфель, стандарт проекта, routing, ритуалы, tooling, memory. Оптимизируй под AI-агентов как основных исполнителей. Укажи, какие решения подтверждены источниками, какие — экстраполяция.»

2. «Дай мне решающий чеклист на 10-15 пунктов: что конкретно внедрить в следующие 2 недели, чтобы система начала работать. Приоритизируй по ROI (эффект / усилие).»

3. «Какие 3-5 ловушек самые частые у людей, которые строят такие системы "для себя"? По каким сигналам их распознать и как отловить до того, как они убьют систему.»

4. «Сравни (a) подход "всё в Notion" vs. (b) подход "Notion для CRM-части + local markdown для dev-части" vs. (c) чистый local-first. Для моего профиля (разработчик-одиночка, Bitrix24-интеграции, AI-агенты, Russian-speaking SMB клиенты) — какой выигрывает по каким критериям?»

5. «Какие практики PM НЕ масштабируются с 1 человека на 3 человек? Что надо заложить с самого начала, чтобы не переделывать?»

Ответы сводного этапа → в `METHODOLOGY.md v2` и `ARCHITECTURE.md v1`.

---

## §10 · Что Дмитрий должен проверить перед запуском research

- [ ] Блоки A-H вопросов — корректны по формулировке, ничего критичного не упущено?
- [ ] Назначен ли блокнот NotebookLM (новый или временный `d01b964f-...`)?
- [ ] Порядок запуска: запускаем все 8 параллельно через `--no-wait`, или по 2-3 за раз?
- [ ] Нужно ли добавить Блок I про приватность и data-ownership (GDPR, ФЗ-152), если система когда-то пойдёт на B2B?
- [ ] Есть ли локальные источники (свои документы, транскрипты обсуждений), которые стоит загрузить в блокнот как контекст ДО research?
