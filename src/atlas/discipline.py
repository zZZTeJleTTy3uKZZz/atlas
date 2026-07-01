"""Контент Atlas-дисциплины (плагин поверх ``agentskit``).

Механизм инъекции/реестр агентов/резолв путей переехали в кит ``agentskit``;
здесь остаётся только то, что специфично Atlas: тело managed-блока
(``DISCIPLINE_BODY``) и namespace (``"atlas"``, из него agentskit выводит
маркеры ``<!-- ATLAS:BEGIN/END -->`` — обратная совместимость с уже
прописанными блоками сохраняется).
"""
from __future__ import annotations

#: namespace плагина Atlas — agentskit выводит из него маркеры ATLAS:*.
ATLAS_NAMESPACE = "atlas"

#: Тело блока «Atlas-дисциплина» (без маркеров). Ссылается на глаголы жизненного
#: цикла (start/done/...) и dashboard — единый канон ведения задач.
DISCIPLINE_BODY = """\
## Atlas — ведение задач

> Managed-блок `atlas init` — не редактируй вручную. Детали и остальные команды — в навыке `atlas`.

Работу веди в Atlas — локальном PM портфеля (`~/.atlas/atlas.db`); это источник истины по задачам.

- **В начале сессии — `atlas task triage`**: что в работе / застряло (blocked/review) / ЗАБЫТО
  (active-задачи, давно не тронуты). Не плоди забытые — доводи или закрывай.
- **Смотри задачи:** `atlas task list` (фильтры `--project <slug>` / `--assignee <slug>` / `--status`);
  карточка — `atlas task get <ref>`.
- **Заводи / правь (CRUD):** `atlas task add --project <p> --title "…" --cpp "…"` (ЦКП обязателен);
  правка полей — `atlas task update <ref> --…`.
- **Идеи → задачи:** сырьё/идею (ЦКП не нужен, проект опционален) кидай в пул
  `atlas backlog add --title "…" [--project <p>]`; смотри `atlas backlog list`; преобразуй в задачу
  `atlas backlog convert <ref> --as task --project <p> --cpp "…"` (→ `todo`) или в проект `--as project`.
- **Статус — ГЛАГОЛАМИ:** `atlas task start <ref>` (взять в работу) → `done` / `block --reason "…"` /
  `unblock` / `review` / `cancel`. Новая задача создаётся в `todo` (запланирована); `update --status`
  принимает лишь `todo`. Сырые идеи (до задачи) — в пуле `atlas backlog` (см. ниже).
- **Приёмка (мультиагент):** исполнитель сдаёт `task submit -m "что сделал/дальше"`; закрыть в done
  может ТОЛЬКО reviewer (`task approve` / вернуть `task reject -m "…"`). Контекст — `task comment` / `task get`.
- **Передача задачи агенту (богатый контекст):** `atlas task handoff <ref> --to <agent> --body-file <md>` —
  тело по шаблону `atlas issue template --kind handoff` (что сделано / осталось / как проверить / ЦКП / контекст);
  **неполную передачу блокирует** (валидатор issuekit). Принимающий: `atlas issue show <ref>` → `task start <ref>`.
- **Как составлять задачи и эпики:** задача = самодостаточный вертикальный срез с измеримым ЦКП
  (что её закрывает — результат, не активность), независимо проверяемый. Эпик = крупная цель/тема из
  нескольких задач; большую цель дроби на независимые задачи. Методология — навыки
  `superpowers:brainstorming` (дизайн до кода) и `superpowers:writing-plans` (декомпозиция).
- **Деструктив (archive, `--hard`) → покажи дельту и подтверди** (soft-delete по умолчанию).

Больше (спринты, дашборд `atlas dashboard`, журнал `atlas logs`, гипотезы, синк) — **используй навык
`atlas`** (`atlas <group> --help` — источник правды).\
"""
