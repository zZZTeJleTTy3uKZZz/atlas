# Cycle 1 Pitch — NP-005 Personal PM Infrastructure

**Cycle**: 2 недели (2026-04-23 → 2026-05-07)
**Формат**: Shape Up pitch (вместо Scrum sprint plan) — после research v2.
**Автор**: Дмитрий (owner), Claude Code (implementer), future AI-PM (observer).

> Это не backlog из 14 задач. Это **3 bets** с жёстким appetite и гибким scope.
> Если одна bet не укладывается в appetite — **сгорает** (circuit breaker).

---

## 1. Problem

Vernandoм pivot на DB-first + Superpowers + multi-agent-ready у меня на руках **MVP PM-слоя** (SQLite + Alembic + 6 CLI команд, 28/28 тестов GREEN, E2E пройден). Но это пока только **каркас**:

- Нет реального portfolio, который живёт в БД (в demo были 2 строки — `np-005` + `cifro-pro`, потом cleanup).
- Нет Notion-синка — БД изолирована.
- Не прогнана ни одна реальная фича через полный Superpowers workflow (brainstorming → writing-plans → worktree → subagent-driven → finishing).
- Не прожит ни один Shape Up cycle с Shutdown Ritual и Day Theming.
- Research v2 показал: мой подход v0.3 (Scrum + планирование всего) — неправильный для соло. Правильнее — **Shape Up + 1-2 bets/cycle + Day Theming**.

**Что больше всего бесит** (приоритизация по Jason Fried [v2_02 §1.3]): когда открываю очередной репо — не знаю, кто я сейчас (Orchestrator, PM, Developer?) и где актуальный статус этого проекта. **Нужна одна команда, которая даёт полный контекст за 30 секунд.**

---

## 2. Appetite

**2 недели** (14 календарных дней). Если не укладываюсь — bet сгорает, часть переносится в Cycle 2 с урезанным scope.

Deep Work hours budget: 24 ч/нед × 2 = **48 ч**. Половина (24 ч) на продуктовую работу (NP-005), четверть (12 ч) на клиентов, четверть (12 ч) на утилиты и admin.

---

## 3. Solution — 3 bets (fat marker sketches)

### 🎯 Bet #1 — `atlas context <slug>` — команда «где я нахожусь»

**Scope**: одна CLI команда, которая при запуске в директории любого проекта читает `AGENTS.md` frontmatter + запрос в `portfolio.db` + последние 10 записей `action_log` и выдаёт краткую сводку (роль, tier, последние действия, pending tasks, связанные блокноты NotebookLM).

**Fat-marker sketch**:
```
$ cd ~/Documents/PROJECT/atlas
$ atlas context
  Project: atlas (shared-infrastructure, T1)
  Status: active · Owner: dmitry · Agents: claude-code, ai-pm
  Last touched: 2026-04-23 10:37
  Open tasks: 0
  Recent action_log (3):
    2026-04-23 10:37 | project created | np-005, cifro-pro (demo)
    2026-04-23 10:15 | migration applied | 0a6b3db9f107
  NotebookLM:
    - notion-api: (not assigned)
    - pm-research-v2: 9f109c5e-...
```

**Почему бесит сейчас и зачем нужно**: при каждом cd в репо надо руками проверять git log + AGENTS.md + статусы. Хочу одну команду.

### 🎯 Bet #2 — onboard 3 пилотных проектов в БД (реальные, не demo)

**Scope**:
- `cifro-pro` (client-project) — собственный портал Cifro.pro.
- `np-005` (business-product) — сам PM-проект.
- `docs-parsing` (personal-utility) — зрелая утилита.

Для каждого:
1. Запись в `portfolio.db` через `portfolio create` с полной метаинформацией (git path, local path, priority, description, one_line).
2. `AGENTS.md` в соответствующем репо скопирован из шаблона + заполнен (у `atlas` уже есть, у остальных — создать).
3. `action_log` — первая запись `project_onboarded`.

**Цель**: проверить что шаблоны AGENTS.md работают в живых проектах, а БД — источник правды.

### 🎯 Bet #3 — прогон одной реальной фичи через полный Superpowers workflow

**Scope**: фича `atlas portfolio push` (PM → Notion DS_PROJECTS зеркало). Не через обычный coding, а через полный цикл:

1. `superpowers:brainstorming` → spec в `_project/docs/SCALING_PRODUCT/specs/2026-04-28-portfolio-push.md`.
2. `superpowers:writing-plans` → plan в `...plans/2026-04-28-portfolio-push.md` с bite-sized TDD-задачами.
3. `superpowers:using-git-worktrees` → `.worktrees/portfolio-push/`.
4. `superpowers:subagent-driven-development` → implementer + spec-reviewer + code-quality-reviewer.
5. `superpowers:finishing-a-development-branch` → Option 1 (merge locally).

**Цель**: сам workflow проверяем. Не важно какой функционал, важно что цикл проходит чисто и даёт качественный результат. После Cycle 1 — знаем, работает Superpowers для нашего случая или нет.

---

## 4. Rabbit Holes (известные риски)

- **`atlas context` может превратиться в мега-команду**. Ограничение: не выводить ничего, что не помещается на одном экране терминала (< 40 строк).
- **Onboard 3 проектов может затянуться** из-за классификации/заполнения. Ограничение: 30 мин на проект, не больше.
- **Superpowers-prowned pilot** может не пройти с первого раза. Fallback: если subagent-driven не работает — делаем T2 (writing-plans + TDD инлайн). Фича получится, workflow перемоделируем.
- **Notion API** (для Bet #3) может глючить / rate-limit. Fallback: dry-run режим без реальной записи в Notion для первой итерации.

---

## 5. No Gos (scope hammering — что ТОЧНО НЕ делаем)

- ❌ Не делаем `sprint plan / review / retro` команды — Shape Up эти ceremonies не требует.
- ❌ Не делаем `expense` команды — перенесено в Cycle 2.
- ❌ Не делаем `task create/done/block` — в Cycle 1 `action_log` пишем программно, не через CLI.
- ❌ Не пишем FastAPI — multi-agent API только в v0.7.
- ❌ Не добавляем SQLAlchemy-relationships — FK-id-only, достаточно для MVP.
- ❌ Не пытаемся перезапустить зависшие 7 research блоков (Orient уже дал достаточно).
- ❌ Не onboard'им остальных клиентов (Ferrum/KSO/Bankety/... — в Cycle 2+).
- ❌ Не трогаем `_project/docs/ARCHITECTURE/decisions/` пока нет реальных ADR-решений.
- ❌ Не пишем прод-ready auth / RBAC — v0.7.

---

## 6. Day Theming schedule на Cycle 1

| День | Тема | Bets |
|---|---|---|
| **Ср 23.04** | Product (NP-005) | Ритуалы / прогон Cycle Planning / первая сессия Bet #1 |
| **Чт 24.04** | Client | Клиентские задачи (B24 webhooks, support) |
| **Пт 25.04** | Utilities / Admin | Bet #2 onboard docs-parsing + заполнение шаблона |
| **Пн 28.04** | Product (NP-005) | Bet #1 добить или Bet #3 brainstorming |
| **Вт 29.04** | Product | Bet #3 writing-plans + subagent-driven |
| **Ср 30.04** | Client | Клиенты |
| **Чт 01.05** | Праздник / буфер | — |
| **Пт 02.05** | Utilities / Admin | Буфер / ошибки agent_log |
| **Пн 05.05** | Product | Bet #3 finishing / merge |
| **Вт 06.05** | Product | Cycle 1 Retrospective |
| **Ср 07.05** | Утро — Cycle 2 shaping | Retrospective + pitch Cycle 2 |

**Буфер 30 мин** между тематическими блоками. **После каждого созвона** — Post-Interaction Block 15-30 мин.

**Post-day Shutdown Ritual** (15 мин):
1. Проверить inbox (Notion, Telegram, почта).
2. `atlas action-log tail` — одна запись макро-скоупа за день.
3. `/clear` всех Claude-вкладок.
4. Физически закрыть ноутбук — работа закончена.

---

## 7. Metrics — Circuit Breakers

Замер в конце Cycle 1 (2026-05-07):

| KPI | Target | Fail → |
|---|---|---|
| **Appetite compliance** | 3/3 bets в срок | если ≥ 2 не уложились — Shape Up не работает для меня, переоценка |
| **Deep Work Hours** | ≥ 30 ч (из 48 budget) | < 30 → нужен Motion/Amie для защиты календаря |
| **Cost per Bet (Claude Code)** | < $50 | > $100 — context rot, spec писать лучше |
| **Context Rot cycles** | ≤ 2 per task | > 2 — spec/plan недостаточно детальны |
| **Acceptance Rate** | ≥ 70% | < 50% — AGENTS.md requires tuning |
| **Клиенты-burn signal** | < 12 ч/нед реально | > 15 ч — сократить активный портфель клиентов |

---

## 8. Связь с BACKLOG.md v3

Старый `BACKLOG.md` был расписан по Scrum-логике (Sprint 1 = 14 задач NP5-001..014). После research v2 эта логика заменяется Shape Up.

**Cycle 1 покрывает**:
- SP-16 (прогон фичи через Superpowers) = **Bet #3**.
- SP-18 (onboarding pilot-проектов) = **Bet #2**.
- SP-15 (`action-log tail` CLI) = частично внутри **Bet #1** (используется `atlas context`).
- SP-11 / 21 (ритуалы) = **Day Theming + Shutdown Ritual** прожиты.

**Cycle 2 потенциально покрывает**:
- S1-01..S1-02 (sprints/expenses/prd/stacks таблицы) — только если появится реальная нужда.
- S1-03..S1-05 (Notion mirror full push/pull) — расширение Bet #3 если пройдёт.
- S1-06..S1-07 (onboarding ещё 4 клиентов + 4 утилит) — но уже с учётом capacity math (не больше 5 активных клиентов).
- S1-12 (API drift для NP-002) — если появится реальная работа с NP-002.

**Cycle 3+** — Multi-agent groundwork, API, FastAPI.

---

## 9. Что делаем ПРЯМО СЕЙЧАС (next concrete steps)

### Шаг 1 — принять pitch (0-5 мин)
Дмитрий читает этот файл и:
- ✅ **Accept as-is** — сразу Bet #1.
- ↔ **Adjust** — предложить изменения по scope / appetite / no-gos.
- ❌ **Reject** — переформулировать bet.

### Шаг 2 — Cycle Planning (30 мин)
Выбрать pilot проекты для Bet #2:
- [ ] Подтвердить: `cifro-pro` / `np-005` / `docs-parsing` — или другие 3?
- [ ] Решить: выбрать одну «тёмную» папку для классификации сейчас (`NL/`, `Tech/`, `Перетяжка/`, `Шуклин/`, `Спецификации/`), или оставить до Cycle 2?
- [ ] Обновить todos: 3 bets как высокоуровневые задачи (не разбивать на микро).

### Шаг 3 — начать Bet #1 (Wed 23.04)
- [ ] Superpowers `brainstorming` пропустить (команда простая, 1 файл).
- [ ] Сразу `writing-plans` (RED-GREEN TDD план на 4-6 задач).
- [ ] Worktree `.worktrees/context-command/`.
- [ ] TDD imp.
- [ ] Merge в main.
- [ ] Дома запустить `atlas context` в 3 разных директориях — smoke test.

### Шаг 4 — Bet #2 (Thu-Fri 24-25.04)
- [ ] `atlas portfolio create cifro-pro --type client-project ...`
- [ ] `atlas portfolio create np-005 --type business-product ...`
- [ ] `atlas portfolio create docs-parsing --type personal-utility ...`
- [ ] Для каждого — создать `<repo>/AGENTS.md` из шаблона.
- [ ] Проверить что `atlas context` в этих директориях выдаёт осмысленную сводку.

### Шаг 5 — Bet #3 (Mon-Wed 28-30.04 + Fri 02.05)
Полный Superpowers цикл на `portfolio push`. Это — флагманская проверка парадигмы.

### Шаг 6 — Cycle 1 Retrospective (Tue 06.05)
Замер всех KPI (см. §7). Решение: продолжаем Shape Up или откатываемся на Scrum-подход.

---

## 10. Open questions (не блокеры Cycle 1)

1. **Сокращение клиентского портфеля с 10 → 5 активных** — research рекомендует (см. capacity math в [research/v2_02 §2]). Это требует стратегического решения Дмитрия — кого выключить из активной работы на cycle? Решение не в Cycle 1 — просто записано.
2. **Motion / Amie** — внедрять на Cycle 2 или работать первый cycle без них чтобы замерить Deep Work Hours «как есть»?
3. **Notion DS_PROJECTS** — структура полей. У `projects.py` сейчас читается только title, b24_company_id, b24_contact_id, status. Нужно ли расширить Notion-базу (добавить Priority, One_Line, PM_Status поля) ПЕРЕД Bet #3? Или push из PM создаёт нужные свойства автоматически через API?
4. **Зависшие 7 research блоков** (A/C/D/F/G/I/J) — возвращаемся к ним в Cycle 2 или совсем списываем в пользу прагматизма?

---

**Статус pitch**: ⏳ awaits Dmitry's acceptance.

После принятия — обновляю `BACKLOG.md` (пометив старые Sprint-задачи как deprecated-in-favor-of-cycle-pattern) и `ACTION_LOG.md`, и приступаем к Bet #1.
