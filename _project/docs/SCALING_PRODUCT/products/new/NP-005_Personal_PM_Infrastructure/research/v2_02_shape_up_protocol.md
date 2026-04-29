# v2_02 — Shape Up + Day Theming Practical Protocol (Step 2)

**Source**: блокнот `9f109c5e-...` focused follow-up.
**Saved as note**: `Shape Up + Day Theming Protocol (Step 2) (cbff4cb6...)`
**Date**: 2026-04-23

---

## 1. Shape Up адаптация для соло

### Длительность cycle
**2-3 недели** (не 6 как у Basecamp). Shape Up книга прямо говорит: *"a tiny team can throw out most of the structure... your bets can be different sizes each time: maybe two weeks here, three weeks there"* [1]. Работая соло — бесшовное переключение shaping ↔ building без формального кулдауна.

### Pitch template (30 минут) — 5 пунктов [2]

1. **Problem** — какую реальную боль решаем.
2. **Appetite** — жёсткий таймбокс ("готов потратить максимум 1 неделю").
3. **Solution** — fat-marker sketch (базовая схема данных, не UI-вайрфрейм).
4. **Rabbit Holes** — известные технические риски.
5. **No Gos** — scope hammering [3]: что мы ТОЧНО НЕ делаем.

### Приоритизация Bets

- **Для клиентов** — WSJF (cost of delay / job size) [4, 5]: чья задача принесёт больше денег за меньше усилий.
- **Для своих продуктов** — интуиция Jason Fried [6]: *"Just fucking pick one. Which one is pissing you off the most?"*. Личная мотивация = rocket fuel [7], быстрее любой математики.

### Параллельность bets

**НЕТ** — Shape Up требует uninterrupted time для одной ставки [8]. Нельзя смешивать клиента и продукт одномоментно. Но можно в течение недели — **через Day Theming**.

---

## 2. Day Theming для 29 треков

### Недельное расписание (шаблон)

| День | Тема | Что делаем |
|---|---|---|
| **Пн / Чт** | Client Days | Клиентские внедрения (10 клиентов) |
| **Вт / Ср** | Product Days (Deep Work) | NP-005 + другие продукты. Никаких звонков. |
| **Пт** | Utilities / Admin (Studio Day) | Task batching для 14 утилит + admin [13] |

Цель — избежать context switching penalty [9-12].

### Capacity math — брутальная правда

**Формула**: 5 дней × 8 ч × 0.6 focus factor = **24 реальных часа в неделю** [14-16].

- 10 клиентов × 2 ч/нед = 15 ч.
- Остаётся на продукты: **9 ч/нед**.

**Вердикт**: 10 активных клиентов соло — путь к выгоранию. **Сократить активный портфель до 5 клиентов/неделю** либо автоматизировать через AI.

### Срочные B24-запросы (webhook-срывы)

- Буфер **30 мин** между блоками [17].
- После каждого созвона — "Post-Interaction Block" 15-30 мин на закрытие циклов и запись в БД [18].
- Если срочность сломала день — **не нагонять**. Новый time-block план на оставшиеся часы [19].

---

## 3. Совместимость с Superpowers + PM-БД

### Pitch → Superpowers spec

Shape Up Pitch является **входом** для `superpowers:writing-plans` [20].

```
Pitch (Problem/Appetite/No-Gos)
  ↓
Claude Opus ('slow') генерит design.md + tasks.md [21, 22]
  ↓
Claude Code ('fast') пишет код в worktree
```

### Фиксация прогресса в SQLite при Day Theming

**НЕ** логировать каждый микро-таск. **Shutdown Ritual** 15 мин в конце тематического дня [23]:

- `notion-task-cli` записывает 1-2 коммита в `action_log` с **макро-скоупами** (не микро-тасками) [3, 24].

---

## 4. Metrics (первые 4 недели)

### KPI для меня (solo PM)

1. **Appetite compliance** — уложился в отведённые 2 недели? Если нет — bet сгорает (circuit breaker), не переносится [8].
2. **Deep Work Hours** — часы непрерывной работы без мессенджеров [10].
3. **Sprint Commitment Reliability** — % завершённых scopes от запланированных [25].

### KPI для Claude Code

1. **Cost per Bet** — $10-$50 норма для сложных задач [26].
2. **Context Rot** — количество циклов «ИИ ошибается → я исправляю → снова ошибается». Если > 2 на задачу — ИИ не справляется со spec [27].
3. **Acceptance Rate** — % сгенерированных `tasks.md` закрытых без переписывания архитектуры.

### Сигналы «не работает»

- >25% времени на bugs / unplanned work [25, 28].
- ИИ пишет 800 строк, которые падают 5 раз подряд из-за плохой data model [29].

---

## 5. Future-proofing для multi-agent (Q3 2026)

### Роль Дмитрия = Facilitator, не Builder [30, 31]

Ты перестанешь писать код. Смотришь на план агентов → решаешь: *Explore* (идеи) или *Focus* (код) [35, 36].

### Новые таблицы БД для multi-agent

1. **`agent_context`** — ссылки на утверждённые ADR (Architectural Decision Records). Память о прошлых решениях.
2. **`mcp_servers`** — связь `project_id` → разрешённые `mcp_server_id` (изоляция прав: агент утилиты не должен видеть биллинг клиента) [34].
3. **`task_handoffs`** — state machine переходов: `PM_Agent_Done` → `Architect_Agent_Drafting` → `Dev_Agent_Coding`. БД управляет переходами между агентами, не сами агенты [26, 32, 33].

---

## Итог research v2 (B+H+E + синтез)

Блокнот дал **практический протокол**, который заменяет абстрактную архитектуру v0.3 на конкретный рабочий день. Ключевые ходы:

1. ❌ Scrum → ✅ Shape Up (2-3 нед cycle, fixed time, variable scope).
2. ❌ Backlog бесконечный → ✅ 1-2 bets per cycle + pitch 30 мин.
3. ❌ 10 клиентов параллельно → ✅ 5 активных + Day Theming.
4. ❌ Continuous context → ✅ Fresh Chat Per Task + Shutdown Ritual 15 мин.
5. ❌ Micro-task logging → ✅ Macro-scope в action_log в конце дня.
6. ❌ Классический Sprint Planning → ✅ Pitch → writing-plans (Superpowers) → worktree.
