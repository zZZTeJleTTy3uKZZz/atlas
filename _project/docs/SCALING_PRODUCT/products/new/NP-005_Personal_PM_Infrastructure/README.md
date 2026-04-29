# NP-005 · Personal PM Infrastructure — операционная система для портфеля разработки, AI-агентов и интеграций

**Статус**: 📝 Spec v0.3 MEGA PIVOT (DB-first + Superpowers + multi-agent ready, research v2 готов к запуску)
**Приоритет**: P0 личный (не на продажу сейчас; платформа, на которой потом ведётся вся работа Дмитрия и любая следующая волна NP-*; станет операционкой координации всех участников — людей, AI-агентов, внешних подрядчиков)
**Владелец**: Дмитрий
**Тип**: 🔧 личная утилита (future — продукт; критерии в ARCHITECTURE §8)
**NotebookLM research v1**: `0c2805ab-42f8-4e98-86c7-e7a618f0f850` · «Эволюция ИИ-инструментов» (47 sources, применён Progressive Inquiry)
**NotebookLM research v2**: `9f109c5e-312f-4058-9c98-aee59853c58e` · «Cifro.pro — IT PM & Multi-Agent Orchestration (NP-005 v2)» — 10 deep-research блоков запущены 2026-04-22; background subagent ждёт завершения
**Stack**: SQLite + SQLAlchemy 2.x + Alembic + Python 3.11 · `atlas` расширяемый · Superpowers plugin v5.0.7 · AGENTS.md канонический

## Карта модуля

| Документ | Что внутри | Версия |
|---|---|---|
| [PRD.md](./PRD.md) | **Главный документ видения**: пользователи (+ multi-agent future), принципы, функциональные требования, мультиагентная модель (AI-CEO/PM/Marketing/Knowledge/QA/Developer), Scrum-слой, roadmap v0.4 → v1.0 | v0.3 |
| [MODEL.md](./MODEL.md) | Схема БД: 6 MVP-таблиц + 4 расширения + 2 multi-agent, seed data, SQL-queries, Notion mirror mapping | v0.1 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Техническая реализация — 11 слоёв (Portfolio DB / AGENTS.md cascade / SSOT / Superpowers / API Drift / Scrum + rituals / Tooling / Utility→Product / Multi-agent / Anti-patterns / end-to-end flow) | v2 |
| [BACKLOG.md](./BACKLOG.md) | Волны W0 (сделано) → W1 (research v2) → W2 (Spike v0.4) → W3 (Sprint 1) → W4 (Sprint 2) → W5 (v0.7) → W6 (v1.0) | v3 |
| [RESEARCH_QUESTIONS_V2.md](./RESEARCH_QUESTIONS_V2.md) | **/ultraplan** — 10 блоков для второй волны deep research: IT PM / Scrum / Multi-agent / Protocols / Docs / DB schemas / Metrics (ЦКП) / Capacity / Notion / Migration | v1 |
| [OVERVIEW.md](./OVERVIEW.md) | Исходный контекст, проблема, границы (для истории) | v0.1 |
| [METHODOLOGY.md](./METHODOLOGY.md) | Статус гипотез v0.1 → v0.2 → v0.3 + 7 новых гипотез H1-H7 | v0.3 |
| [RESEARCH_QUESTIONS.md](./RESEARCH_QUESTIONS.md) | Вопросы первой волны research (history) | v0.1 |
| [research/](./research/) | Ответы research v1: `00_operational_architectures_soloist.md` (fulltext источника №29), `01_content_map.md`, `02_implementation_protocol_14d.md`, `03_integration_sync_protocol.md`. Research v2 результаты попадут сюда как `v2_A..J_*.md` | — |
| [CHANGELOG.md](./CHANGELOG.md) | v0.1 → v0.2 → v0.2.1 PIVOT → v0.3 MEGA PIVOT | v0.3 |

## Правило работы

- Модуль — **атомарный**. Всё про NP-005 живёт здесь.
- Верхнеуровневая связь — одна строка в `products/new/README.md`.
- Любая идея/инсайт про PM-инфраструктуру → сначала open question в `RESEARCH_QUESTIONS.md` или гипотеза в `METHODOLOGY.md`, потом уже задача в `BACKLOG.md`.
- Обновления доков модуля → запись в `CHANGELOG.md` + одна строка в `PROJECT_LOG/ACTION_LOG.md`.

## Три «дальних» правила

- **Личная утилита, но с продуктовым мышлением.** Пишется как будто это SaaS, но не выпускается наружу — пока не созреет PMF и не накопится подтверждение что другим разработчикам-одиночкам или agency-founder'ам это нужно в той же форме.
- **Agent-first operation.** Задача системы — чтобы AI-агент (Claude Code / Codex / любой следующий) за 2 минуты входил в любой проект портфеля и был полезным, а Дмитрий тратил время на стратегию и валидацию, а не на перелистывание папок.
- **Бритва Оккама по количеству сущностей.** Если отчёт можно не заводить — не заводим. Если skill можно не писать — не пишем. Инфраструктура должна экономить внимание, а не добавлять ритуалы.
