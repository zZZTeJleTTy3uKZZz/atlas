---
name: atlas
description: Use when ведёшь проекты/эпики/задачи/чек-листы в локальном Atlas PM CLI, синхронизируешь их с Битрикс24/Notion через backend-хаб, назначаешь участников на задачи, или программно (--json) читаешь/меняешь портфель проектов. Триггеры — "atlas", "мои проекты", "мои задачи", "эпик", "спринт", "чек-лист задачи", "засинкать с Б24/Notion", "что у меня по проекту".
---

# Atlas — локальный PM CLI (портал Atlas Framework)

`atlas` — командный интерфейс к локальному PM-стору (SQLite `~/.atlas/atlas.db`) портфеля проектов Дмитрия. Это **портал** Atlas Framework: ведёт проекты/эпики/задачи локально и синхронизирует их с backend-хабом, который раскидывает по Битрикс24/Notion.

## Главное для ИИ-агента

- **Вывод по умолчанию — JSON** (одна структура на строку). Парси stdout как JSON. Для человека добавь `--text`.
- Глобальные флаги: `--json` / `--text` / `--profile <p>` / `--version`.
- Запуск: `uv run atlas <команда>` (из каталога проекта) или `atlas <команда>` (если установлен).

## Доменная модель (иерархия)

```
Project → Epic (веха/спринт) → Task (для ИИ-агента) → ChecklistItem (шаги)
                                  └─ TaskMember (responsible | executor | watcher)
```

- **Project** — принадлежность через `owner`/`customer` (контрагенты), характер через `type`, глубина синка через `sync_policy`.
- **Epic** = крупная человеческая веха (синкается наружу). **Task** = единица для ИИ-агента. **ChecklistItem** = шаги.

## Ключевые команды

| Действие | Команда |
|---|---|
| Проекты | `atlas projects list|show|create` |
| Задачи | `atlas pm-tasks add|list|get|update|delete` (ЦКП `--cpp` обязателен при add) |
| Эпики | `atlas epic add|list|get --project <ref>` |
| Чек-листы | `atlas checklist add|list|check --task <ref>` |
| Участники | `atlas member add|list|rm --task <ref> --participant <slug> --role <responsible\|executor\|watcher>` |
| Сводки | `atlas today | overdue | agenda` |

## Синхронизация с хабом

- `atlas sync push` — выгрузить локальные изменения (из outbox) на хаб → фанаут в Б24/Notion.
- `atlas sync pull` — один цикл входящего синка (применить изменения с хаба локально).
- `atlas sync watch` — постоянный входящий синк (long-poll, мгновенная доставка; Ctrl+C для остановки).

**Что синкается наружу** определяется политикой проекта (`sync_policy`: `local`/`epics`/`media`/`full`) × присутствием исполнителя в портале. По умолчанию ИИ-задачи и чек-листы остаются локальными, наружу уходят эпики — команда не захламляется. Менять глубину: задать `Project.sync_policy`.

## Когда что использовать

- Завести веху → `atlas epic add`. Разбить на задачи → `atlas pm-tasks add --project ... --cpp ...`. Шаги задачи → `atlas checklist add`. Посадить агента/человека → `atlas member add`.
- После локальных правок выгрузить в команду → `atlas sync push`. Получить изменения извне → `atlas sync pull`.

Полный справочник всех команд, опций и примеров JSON-вывода — `references/commands.md`.
