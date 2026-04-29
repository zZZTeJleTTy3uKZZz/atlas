# v2_01 — Content Map (Orient Step 1)

**Source**: блокнот `9f109c5e-312f-4058-9c98-aee59853c58e` «Cifro.pro — IT PM & Multi-Agent Orchestration (NP-005 v2)» (121 sources, 104 ready).
**Saved as note**: `Content Map v2 (Orient Step 1) (9976eca6...)`
**Date**: 2026-04-23

Блокнот построен под темы Scrum / Capacity / Docs (блоки B, H, E — они единственные завершились из 10). Но 104 ready sources дают богатый контекст сверх этих тем.

---

## 🗺 5 тематических кластеров

### Кластер 1 — AI-First Development и Spec-Driven подход
- **«Спецификация — это продукт»** [1-3]: без жёстких specs ИИ генерирует technical debt на машинной скорости.
- **Метод «Point and Call»** [4, 5]: сначала data model + логика проговаривается/записывается, потом делегируется ИИ.
- **Разделение моделей** [6, 7]: "медленная" модель (Opus/o1) планирует, "быстрая" пишет код.
- **BMAD фреймворк** [8, 9]: AI-роли PM / Architect / Scrum Master / Developer — текстовая передача контекста.

**Цитата**: *"Data model first because it's the core part of the logic of any system... Pure logic second because these are the interactions between modules."* [12]

### Кластер 2 — AGENTS.md / CLAUDE.md / PRD standards
- **Instruction Budget** [14-16]: каждый токен в CLAUDE.md загружается каждый запрос → "context rot" при раздутии.
- **Progressive Disclosure** [17, 18]: иерархия — корневой AGENTS.md ссылается на `docs/TYPESCRIPT.md`, `docs/TESTING.md`. ИИ сам подтягивает нужное.
- **PRD нового поколения** [19-21]: шаблоны Lenny Rachitsky, Amazon PR/FAQ, обязательный раздел "Non-Goals".

**Цитата**: *"The ideal AGENTS.md file should be as small as possible... describe capabilities. Give hints about where things might be."* [15, 26]

### Кластер 3 — Capacity Planning, Focus Factor, Metrics
- **Capacity ≠ Velocity** [27-29]: velocity — прошлое, capacity — будущее (часы с учётом отпусков).
- **Focus Factor 0.6-0.8** [30, 31]: реальные 4.8-6 ч продуктивного времени из 8.
- **WSJF** [32, 33]: (business value + time criticality + risk reduction) / job size.
- **70/20/10** [15, 34]: 70% learning from doing, 20% from people, 10% formal.

**Цитата**: *"executing a project generally takes 6-6.5 hours per day."* [30, 31]

### Кластер 4 — Shape Up vs Scrum для малых команд
- **Микро-команды работают иначе** [36, 37]: для 1-3 человек + AI классический Scrum с митингами = overhead.
- **Shape Up** [38, 39]: 6-недельные cycles (fixed time, variable scope) + 2 недели cool-down.
- **Throw less at the problem** [40, 41]: 37signals ведут 4 продукта микро-командами.

**Цитата**: *"For these reasons, a tiny team can throw out most of the structure... You don't need to work six weeks at a time... but you're doing it more fluidly."* [37]

### Кластер 5 — Deep Work, Ритуалы, защита от выгорания
- **Time Blocking + Day Theming** [43, 44]: целые дни под одну роль = избегаем context switching penalty.
- **Task Batching** [45, 46]: 30-минутный блок для админки. Reclaim AI автоматически защищает Focus Time.
- **Shutdown Ritual** [47]: проверка инбоксов + закрытие циклов + физическая фиксация конца работы.

---

## 🚀 3 мощных тезиса для solo + AI-команда

1. **Спецификация — главный продукт.** Compound Engineering: компактные strict markdown specs (PRD/ADR) как API для AI-агентов.
2. **Progressive Disclosure важнее промптинга.** AGENTS.md как маршрутизатор (`правила БД → db_rules.md`) — спасает контекст, уменьшает галлюцинации.
3. **Shape Up + Day Theming вместо Scrum.** Как solo с портфелем 29 направлений — сгоришь от бэклогов. Fixed time + variable scope + day batching.

---

## ⚠️ Пробелы блокнота для NP-005

1. **Схема реляционной БД для multi-portfolio** — не описана. Как связать Clients(10) ↔ Products(5) ↔ Utilities(14) ↔ Tasks ↔ Agents в SQLite — нет ни примеров, ни ER-диаграмм.
2. **CLI ↔ локальные LLM/агенты** — не описано. Как `notion-task-cli` триггерит локальную агентскую команду (OpenClaw) — отсутствуют паттерны.
3. **State Management для autonomous агентов через реляционную БД** — BMAD использует текстовые файлы; как агенты должны логировать действия в SQLite чтобы не мешать друг другу — нет.
4. **Математика Capacity для 1 человека × 29 потоков** — формулы только для команд (6 человек × 8 ч). Когнитивная нагрузка нелинейна для соло.
