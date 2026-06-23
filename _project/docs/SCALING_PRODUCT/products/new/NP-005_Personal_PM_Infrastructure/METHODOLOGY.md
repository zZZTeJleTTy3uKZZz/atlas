# METHODOLOGY — NP-005 Personal PM Infrastructure

**Версия**: v0.3 (2026-04-22 late, ПОСЛЕ pivot 2 на DB-first + Superpowers + multi-agent readiness)
**Цель файла**: гипотезы эволюционируют от v0.1 (рабочие предположения) через v0.2 (после research v1) до v0.3 (после pivot от Дмитрия).

## Статус гипотез v0.2 после pivot (2026-04-22 late)

| № | Гипотеза v0.2 | Статус v0.3 | Комментарий |
|---|---|---|---|
| 1 | Таксономия портфеля (5 категорий × 6 lifecycle) | ✅ сохранена | Теперь как `project_types` и `project_statuses` таблицы с FK связями вместо markdown-тегов |
| 2 | `PORTFOLIO.md` как SSOT в markdown + memory-index | ❌ ЗАМЕНЕНО | SSOT = реляционная БД (SQLite → PostgreSQL). `PORTFOLIO.md` — автогенерируемое зеркало. |
| 3 | 4 обязательных артефакта в проекте (AGENTS/README/CHANGELOG/ACTION_LOG) | ✅+ | AGENTS.md теперь каноничен (не CLAUDE.md!). ACTION_LOG становится полем в БД + markdown-экспорт. |
| 4 | SSOT-карта PM-first с canonical-field-per-concept | ✅ сохранена | Детализирована в ARCHITECTURE §3 (9 сущностей + правила sync) |
| 5 | Ритуалы weekly/monthly/quarterly | ✅+ | Добавлены Scrum-ceremonies (Sprint Planning / Review / Retro / Refinement / Daily Standup) поверх personal rituals |
| 6 | 4 критерия utility→product | ✅ сохранены | Реализованы как `portfolio graduation-review <slug>` команда |
| 7 | 4 skill: portfolio-register / status / onboard / review | ❌ ОТМЕНЕНЫ | Вместо skill — CLI-команды `atlas portfolio/sprint/expense`. Skills излишни — CLI лучше |
| 8 | Интеграция Claude Code + Notion + NotebookLM | ✅+ | Добавлен Superpowers plugin как отдельный слой оркестровки агентной работы |
| 9 | 7 анти-паттернов из research v1 | ✅+ | Добавлены 6 анти-паттернов из Superpowers (TDD Iron Law, verification-before-completion, etc.) |
| 10 | Правила обновления после research | ✅ | v0.3 — после pivot; v0.4 будет после research v2 |

## Новые гипотезы v0.3

### H1 — БД-first вместо markdown-first

**Why**: Дмитрий прямо сказал «хочу чтобы это была БД, чтобы можно было создавать поля, делать миграции». Markdown не масштабируется на сотни проектов и 5+ AI-агентов параллельно.

**How to apply**:
- SQLite в MVP (файл-based, single-writer, offline).
- SQLAlchemy 2.x + Alembic.
- Миграция на PostgreSQL — когда 2+ агента concurrent writes (ожидаемо в v0.7).
- Schema в MODEL.md.

### H2 — Superpowers как dev-слой поверх PM-слоя

**Why**: не изобретать собственных Agent Personas и Fresh Chat Per Task — Superpowers уже даёт отлаженный workflow.

**How to apply**:
- Каждый `task` в `tier T1` → полный Superpowers (brainstorming → writing-plans → using-git-worktrees → subagent-driven-development → finishing-a-development-branch).
- `tier T2` — облегчённый (writing-plans + TDD + verification).
- `tier T3` — только verification-before-completion.
- Tier в YAML frontmatter `AGENTS.md`.

### H3 — AGENTS.md канонично, не CLAUDE.md

**Why**: мультиагентное будущее. Не привязываемся к одному агенту.

**How to apply**: во всей нашей документации, шаблонах, CLI-командах — имя файла `AGENTS.md`. Если плагин (Superpowers) ожидает `CLAUDE.md` — создаём тонкий CLAUDE.md → `# See AGENTS.md`.

### H4 — Multi-agent readiness с самого начала

**Why**: Дмитрий планирует через 1-3 месяца подключить OpenClaw / paperclip или аналог. Если закладывать это сейчас, не будет переделок.

**How to apply**:
- `participants` таблица уже поддерживает `kind=ai_agent` с ролями.
- API-first подход (FastAPI в v0.7).
- RBAC per-participant.
- MCP / A2A протокол — решим после research v2 Блок D.

### H5 — ЦКП на уровне каждой задачи

**Why**: Дмитрий явно попросил. В русской бизнес-традиции ЦКП = exchangeable product роли или задачи. Без этого задачи — это activity, не value.

**How to apply**:
- Поле `tasks.cpp_description` обязательно (NOT NULL).
- На sprint review — каждая done-задача получает reality check «ЦКП реально доставлен?».
- Формулировка ЦКП — в research v2 Блок G.

### H6 — Scrum на уровне спринтов + personal rituals на уровне дня/недели

**Why**: Scrum даёт понятную структуру для multi-agent координации (ceremonies). Personal rituals (Monday/Friday) остаются для оркестратора.

**How to apply**:
- 2-недельные sprints.
- Ceremonies в CLI: `sprint plan / standup / refinement / review / retro`.
- Burndown derived из tasks.
- Personal rituals остаются.

### H7 — Две волны research + Progressive Inquiry

**Why**: research v1 (0c2805ab) покрыл общие Operational Architectures. Теперь нужна детализация по IT PM, Scrum, multi-agent. RESEARCH_QUESTIONS_V2.md — уже готов.

**How to apply**:
- Запустить 10 блоков research v2 через `notebooklm source add-research`.
- Применять Progressive Inquiry skill (от общего к частному).
- Синтез в `research/v2_*.md`.
- Обновить MODEL.md, ARCHITECTURE.md, PRD.md на v0.5 / v3 / v0.4.

## Пробелы, которые останутся даже после research v2

- Personal finance / tax / ИП/ООО бухгалтерия — выходит за scope NP-005 (не добавляем).
- Security & compliance для multi-agent (особенно когда появятся подрядчики) — возможный Блок N в research v2 (см. §L).
- Long-term code maintenance of AI-generated code (2-3 года) — из первой волны не закрыто.
- Конкретный технический выбор РДБМ vs NoSQL vs graph db — research v2 Блок F может не дать окончательного ответа; решим эмпирически после Spike.

---

## Ниже — статус гипотез v0.2 (после первой волны research, для истории)

---

## Статус гипотез v0.1 после research v1



> Правило чтения: каждая гипотеза отмечена ✅ (подтверждено источниками), ❌ (опровергнуто), ↔ (скорректировано). Конкретика нового дизайна — в [ARCHITECTURE.md](./ARCHITECTURE.md). Конкретные задачи первых 14 дней — в [BACKLOG.md](./BACKLOG.md).

## Статус гипотез v0.1 после research

| № | Гипотеза v0.1 | Статус | Комментарий |
|---|---|---|---|
| 1 | Таксономия портфеля: 5 категорий × 6 lifecycle | ✅ | Паттерн "Standalone Capability Group" из №29 + Dual-Track/Lean MVP/Grind Mode подтверждают matrix. |
| 2 | Единый `PORTFOLIO.md` + memory-индекс | ✅ | Markdown-артефакты как API между агентами (Fresh Chat Per Task) — подтверждено [res/02 §4]. |
| 3 | 4 обязательных артефакта в проекте (AGENTS/README/CHANGELOG/ACTION_LOG) | ↔ | **Скорректировано:** AGENTS.md обязателен, остальные — по lifecycle. Добавлено требование: ≤ 200 строк, human-curated, YAML frontmatter с back-links. Новое → `CLAUDE.local.md` (secrets) в `.gitignore`. |
| 4 | SSOT-карта: B24 клиенты, Notion задачи, markdown dev, NotebookLM research | ↔ | **Скорректировано**: добавлено правило `canonical-field-per-concept` (не last-write-wins), запрет полного bidirectional sync. Детали — `ARCHITECTURE.md §3`. |
| 5 | Ритуалы: weekly / monthly / quarterly | ↔ | **Скорректировано**: добавлен `PR Review of One` (каждая ИИ-итерация) и `Ritual Reset` (квартально). Weekly разбит на Monday Kickstart + Friday Wind-down. |
| 6 | 6 критериев «утилита → продукт» | ↔ | **Заменено на 4 более точных из №29**: Real Problem (one sentence), Agent Failure Gap (что Claude не умеет сам), Efficiency Gain ≥ 30 min/week, Cognitive Load @ 23:00. Порог: ≥ 3/4 → `graduating`. |
| 7 | 4 skill: portfolio-register / status / onboard / review | ↔ | **Отложено на Sprint 2**: в Sprint 1 достаточно ручного ведения `PORTFOLIO.md` + idempotent `atlas sync-agents`. Skills — когда паттерн уже устоится. |
| 8 | Интеграция с существующей инфраструктурой | ✅ | Claude Code = ADE (не нужны Warp/Intent). Notion + atlas = Storage Surface. Добавлено: Motion/Amie для календаря, Latenode для B24 ↔ Notion glue. |
| 9 | Риски (tool sprawl, ритуал-bloat, drift каноничности, premature productization) | ✅+ | **Подтверждено + расширено 7 анти-паттернами**: мега-система, код без модели данных, агентизация детерминированных задач, раздувание CLAUDE.md, Context Collapse, Denial of Wallet, Prototype Illusion. |
| 10 | Правила обновления после research | ✅ | — |

### Новые гипотезы, появившиеся из research (не было в v0.1)

- **Compound Engineering**: каждая ошибка ИИ в PR → новое правило в `CLAUDE.md`. Файл становится живым onboarding.
- **5-scope CLAUDE.md cascade** (Global → Project → Local Secret → Folder → Imports): глубже scope побеждает.
- **Fresh Chat Per Task + markdown-API между агентами**: 100% защита от Context Rot, экономит токены в 10×.
- **4 Agent Personas через ролевое кэширование**: Scribe/PM, Coder, QA-Critic (Adversarial Review), Researcher.
- **API Drift Governance**: Contract-First + Spectral (pre-commit) + oasdiff (pre-release). Optic избыточен.
- **KV-cache-stable memory pattern**: строго append-only для `ACTION_LOG.md`, Notion pages, transcripts. Mutation — через новые блоки с ссылкой на старые. Metadata — редактируется.

### Пробелы, которые research НЕ закрыл (→ NP5-RF01..04)

1. **Token Economics** — точная математика hard limits против Denial of Wallet.
2. **Локальные модели** (Ollama / LM Studio) — практически не покрыто.
3. **Юридические риски** (AI LEAD Act, NDA, GDPR, 152-ФЗ РФ) — нет конкретики.
4. **Long-term technical debt AI-кода** — как поддерживать миллионы строк AI-кода спустя 2-3 года.

---

## Ниже — исходный текст гипотез v0.1 (для истории). Актуальные решения — в `ARCHITECTURE.md v1`.



---

## 1. Таксономия портфеля — 4 категории + один тег жизни

Предлагаю фиксировать каждую единицу портфеля по двум измерениям: **категория** (где она живёт и кому принадлежит) и **lifecycle-стадия** (в каком она состоянии).

### 1.1 Категории

| Категория | Где хранится | Принадлежность | Пример |
|---|---|---|---|
| `client-project` | `PROJECT/Metela/<Клиент>/` | Cifro.pro → клиент | Cifro, Ferrum, KSO |
| `business-product` | `PROJECT/Metela/New Projects/_project/docs/SCALING_PRODUCT/products/new/` | Cifro.pro | NP-001 РОПчик, NP-002 Wrapper, NP-003 Marketplace |
| `personal-utility` | `PROJECT/Tests/<utility>/` | Дмитрий | docs_parsing, fin_analitik, notion-api-b24 |
| `personal-project` | `PROJECT/Дима/<project>/` | Дмитрий | AI кодер |
| `shared-infrastructure` | `PROJECT/<tool>/` (не вложенный) | общее | atlas |

### 1.2 Lifecycle-стадии (перпендикулярно категориям)

- `experiment` — прощупываем гипотезу, живёт 1-30 дней, может быть убито без сожаления.
- `active` — в работе, есть цель и дедлайн/критерий завершения.
- `maintained` — готово, поддерживаем, не развиваем активно.
- `dormant` — стоит на паузе, есть осознанная причина (ждём внешнего события, ресурса, решения).
- `archived` — закрыто, код/доки оставлены как history.
- `graduating` — утилита готовится переехать в `business-product`. Временный тег для период миграции.

### 1.3 Почему не одного тега

Категория говорит «что это по природе», стадия — «где это сейчас». Одно измерение даёт ложное чувство чистоты (`production / experiment`), но не отвечает на вопрос «кому принадлежит эта работа». Два — дают матрицу 5 × 6 = 30 осмысленных слотов, но в реальности будет использоваться ~10-15.

### 1.4 Открыто для research

- Подтвердить через Блок A — какие таксономии используют opearators уровня Willison / Levels / tptacek.
- Возможно добавить измерение **стоимости** (revenue-tier vs. strategic vs. learning) — будет ли полезно?

---

## 2. Единый реестр портфеля — `PORTFOLIO.md` + memory index

### 2.1 Где живёт канонический реестр

Предлагаю **один** markdown-файл `PROJECT/Metela/New Projects/_project/docs/PROJECT_LOG/PORTFOLIO.md` (в проекте-матке, где уже живёт ACTION_LOG, BACKLOG и т.д.) — со сводной таблицей всех единиц всех категорий.

Почему там:
- Уже работает operating-model `saas-project-operating-model`.
- Файл читается и агентом, и человеком.
- Одна версионированная точка истины.

### 2.2 Формат строки

| ID | Категория | Lifecycle | Путь | Title | Priority | Owner | Notion | NotebookLM | Last touched | One-line |
|---|---|---|---|---|---|---|---|---|---|---|
| `cl-001` | client-project | active | `Metela/Cifro/` | Cifro.pro (сам портал) | P0 | Дмитрий | `<page-id>` | — | 2026-04-21 | Самообслуживание + AI-РОПчик |
| `np-001` | business-product | active | `Metela/New Projects/.../NP-001_.../` | ИИ РОПчик | P0 | Дмитрий | — | — | 2026-04-22 | AI-ассистент для собственника |
| `ut-003` | personal-utility | maintained | `Tests/docs_parsing/` | Docs Parsing | P1 | Дмитрий | — | — | 2026-04-09 | Парсинг внешних доков в MD |
| `sh-001` | shared-infrastructure | active | `atlas/` | atlas | P0 | Дмитрий | — | — | 2026-04-21 | CLI для задач/проектов Notion |

Id-префиксы: `cl-` / `np-` / `ut-` / `pp-` / `sh-`.

### 2.3 Memory Claude — индекс реестра

В моей memory (`~/.claude/projects/.../memory/`) создаётся **один** memory-файл `project_portfolio_index.md` (type: `project`) — короткий индекс с ссылкой на канонический `PORTFOLIO.md`. Никаких дублей деталей: memory даёт «где смотреть», канонический файл даёт правду.

### 2.4 Открыто для research

- Подтвердить через Блок A и E — реально ли работает single-file-реестр у людей с 30+ единицами, или надо сразу в БД.
- Блок H — синхронизировать ли `PORTFOLIO.md` с Notion-базой `DS_PROJECTS` автоматически.

---

## 3. Стандарт проекта — минимальный набор файлов

Гипотеза: любой проект портфеля должен иметь **4 обязательных артефакта**, остальные — по необходимости.

### 3.1 Обязательные

- `AGENTS.md` (или `CLAUDE.md`) — как AI-агенту входить в проект за 2 минуты: цель, стек, команды, non-goals, ссылка в `PORTFOLIO.md`.
- `README.md` — как **человеку** войти: что это, как запустить, где документация.
- `CHANGELOG.md` — журнал заметных изменений.
- `_project/docs/PROJECT_LOG/ACTION_LOG.md` — журнал итераций (если проект модульный по saas-operating-model).

### 3.2 По ситуации

- `PRD.md` — если есть продуктовый спек.
- `ARCHITECTURE.md` — если архитектура нетривиальна.
- `BACKLOG.md` — если есть текущие задачи.
- `RESEARCH_QUESTIONS.md` — если ведётся deep research.
- `PROJECT.md` — верхнеуровневая сводка (встречается в ряде утилит).

### 3.3 Правило: «простота взрослеет»

Утилита стартует с `README.md` + `AGENTS.md`. Если выживает и растёт — добавляет `CHANGELOG.md`, `_project/docs/`, и т.д. Это означает, что сейчас ~9 проектов в `Tests/` не нарушают стандарт — они просто на ранней стадии. Миграция нужна для тех, что уже выросли (fin_analitik, docs_parsing, notion-api-b24 — уже в стандарте; AI Prodazhnik — тоже; остальные — по мере надобности).

### 3.4 Открыто для research

- Блок B — какой шаблон `AGENTS.md` становится де-факто стандартом 2025-2026.
- Нужен ли machine-readable фронтматтер (YAML) в `AGENTS.md` для ускорения агентом.

---

## 4. SSOT-карта — что где живёт канонически

| Данные | Канон | Зеркало | Почему там |
|---|---|---|---|
| Клиентские компании и контакты | Bitrix24 | Notion (проекты), memory | CRM-природа, там же деньги и коммуникация |
| Задачи по клиентам | Notion (DS_TASKS) | Bitrix24 (через B24 Tasks, опционально) | Notion удобнее для fluid-задач, B24 для календарных/бухгалтерских |
| Проекты с B24-линком | Notion (DS_PROJECTS) | markdown AGENTS.md | Связь client ↔ код |
| Dev-артефакты (код, спеки, архитектура) | local git (markdown + code) | — | single-player, быстрота, offline |
| Research (ответы, источники, цитаты) | NotebookLM | markdown в `research/` подпапках | Research — это дорогой выход LM, локально дублируем для цитирования в агентах |
| Личный календарь, ритуалы | Google Calendar (личный) | — | не нужна синхронизация с проектами сейчас |
| Контекст Claude между сессиями | memory-файлы | — | single-player Claude Code |
| Портфель в целом | `PORTFOLIO.md` (markdown) | memory index | local-first, единый файл |

### 4.1 Правила

1. **Одна строка истины на один факт.** Если факт живёт в N местах, одно помечено каноном, остальные — явные зеркала.
2. **Зеркало обновляется из канона, не наоборот.** Исключение — ручной ввод, тогда пометить дату и автора.
3. **Любой AI-агент знает SSOT-карту.** Она лежит в `AGENTS.md` каждого активного проекта (краткая выжимка) + полная в `NP-005/ARCHITECTURE.md`.

### 4.2 Открыто для research

- Блок H — реалистичность bidirectional sync Notion↔markdown, паттерны конфликт-резолюции.
- Блок D — как вести inventory внешних API (это тоже SSOT-слой — кто владеет знанием про каждый endpoint).

---

## 5. Ритуалы — минимум, который реально двигает работу

### 5.1 Weekly review (пятница, 30 минут)

Читаю `PORTFOLIO.md`. Для каждой `active` единицы: что сдвинулось за неделю, есть ли блокер, остаётся ли приоритет. Обновляю `last-touched` и `one-line`. Переношу `dormant` в `archived` если нет шансов реанимировать.

### 5.2 Monthly review (первая пятница месяца, 60 минут)

Перепроверка приоритетов. Может `experiment` пора в `active`? Может `active` пора заморозить? Считаем «энергию, ушедшую в каждую категорию» — нет ли перекоса.

### 5.3 Quarterly review (раз в 3 месяца)

Стратегический вопрос: какие `personal-utility` заслуживают `graduating`? Какие треки вообще нужно открыть/закрыть? Ответ фиксирую в `ACTION_LOG.md` корневого проекта.

### 5.4 Правило «убей рутину»

Любой ритуал, который не дал инсайта 2 раза подряд — сокращается или убивается. Ритуал — это инструмент, не обязанность.

### 5.5 Открыто для research

- Блок G — реально ли work weekly review у solo или это миф GTD.
- Есть ли AI-нативные аналоги (агент сам готовит review-draft, человек только валидирует).

---

## 6. Критерии «утилита → продукт»

Гипотеза: утилита готова стать `business-product` если выполнено **≥ 4 из 6**:

1. **Repeat use**: Дмитрий использует её каждую неделю минимум 3 месяца.
2. **Pain validated externally**: минимум 3 человека вне Cifro.pro сказали «мне бы это нужно».
3. **Docs-ready**: есть полный `README.md` + `PRD.md` + `CHANGELOG.md`, покрывающий типичные use case.
4. **Technical readiness**: нет «железных» блокеров (работает на изолированной среде, безопасность проверена, rate-limits посчитаны).
5. **Market fit gesture**: кто-то готов заплатить (или согласился на платный пилот).
6. **Maintenance bandwidth**: Дмитрий готов тратить ≥ 5 часов в неделю следующие 6 месяцев.

Если выполняется 4-5 — `graduating` lifecycle тег + новая запись `NP-XXX` в `business-product` + план миграции. Если 2-3 — `maintained`. Если 0-1 — `experiment`/`dormant`/`archived`.

### 6.1 Критерии **НЕ** продуктизировать (даже если 6 из 6)

- Конфликт с клиентским бизнесом Cifro.pro (конкуренция с собственным рынком).
- Требует команды, которой нет.
- Регуляторные риски (ФЗ-152, GDPR) без юрподдержки.
- Дмитрий не готов нести support-нагрузку.

### 6.2 Открыто для research

- Блок F — реальные истории migration (Obsidian, Linear, Tailscale) — что было сигналом «пора», что было сигналом «пока нет».

---

## 7. Skills — минимальный набор утилит для работы с портфелем

Гипотеза: нужно 4 skill-а (Claude Code).

- `portfolio-register` — добавить/обновить строку в `PORTFOLIO.md` и создать minimum-viable `AGENTS.md` в новом проекте.
- `portfolio-status` — распечатать текущее состояние портфеля, подсветить «заглохшие» и «перегруженные» категории.
- `portfolio-onboard` — запустить агента в конкретный проект: прочитать `AGENTS.md`, собрать контекст, задать 2-3 проактивных вопроса (feedback-rule Дмитрия).
- `portfolio-review` — автоматический черновик weekly / monthly review (читает `ACTION_LOG`, считает last-touched, сравнивает с прошлой неделей).

### 7.1 Открыто для research

- Блок B — есть ли готовые skill-шаблоны или надо писать с нуля.
- Блок C — как teams измеряют эффективность таких skills.

---

## 8. Интеграция с существующей инфраструктурой

### 8.1 Что уже есть и должно быть использовано

- `atlas` (Python CLI) — работа с Notion-задачами и проектами. Становится executor для всех операций Notion-side в NP-005.
- `notebooklm` skill + CLI — работа с NotebookLM (deep research, sources, notebooks). Становится executor для research-stage.
- `saas-project-operating-model` skill — стандарт структуры проекта. NP-005 строится поверх него, не заменяет.
- `brand-voice` и `discovery` skills — для бизнес-продуктов (когда утилита `graduating` пойдёт).

### 8.2 Что нужно дописать / доработать

- `atlas`: добавить команду `portfolio sync` — двунаправленная синхронизация `PORTFOLIO.md` ↔ Notion `DS_PROJECTS` (после research, если подтвердится что это полезно).
- 4 новых skill (см. §7).
- Шаблон `AGENTS.md` v2 для унификации всех проектов (после research Блок B).

### 8.3 Открыто для research

- Блок H — точные паттерны bidirectional sync для Notion + markdown + Bitrix24.

---

## 9. Риски и «чего я не знаю»

- **Tool sprawl**: Notion + markdown + B24 + NotebookLM + memory — 5 источников, легко нарастить путаницу. Research Блок E должен дать критерии «сколько инструментов — слишком много».
- **Ритуал-bloat**: weekly / monthly / quarterly может превратиться в театр. Research Блок G.
- **Masштабирование на команду**: если Cifro.pro наймёт человека, часть практик «одиночки» рассыпется. Research Блок A + G.
- **Drift каноничности**: PORTFOLIO.md и Notion будут расходиться, если нет автосинка. Research Блок H.
- **Premature productization**: соблазн сделать NP-005 продуктом до того, как он сработал на Дмитрии. Правило — сначала 6 месяцев на себе.

---

## 10. Что меняем после research

Когда ответы A-H вернутся и попадут в `research/` подпапку:

1. Каждая гипотеза в этом файле получает пометку **✅ подтверждено** / **❌ опровергнуто** / **↔ скорректировано** с цитатой из ответа и ссылкой на источник.
2. Новые инсайты, не предусмотренные в v0.1 — добавляются как §11, §12, ... с пометкой «от research».
3. Финализированная методология переезжает в `METHODOLOGY.md v2` + рождается `ARCHITECTURE.md v1` (конкретные схемы, файлы, команды).
4. Из `ARCHITECTURE.md` рождается `BACKLOG.md` — набор задач первой волны (предположительно 10-15 штук, 2-3 недели).
