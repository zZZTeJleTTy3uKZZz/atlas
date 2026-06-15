# Atlas CLI — полный справочник команд

Запуск: `uv run atlas <команда>` (из каталога проекта) либо `atlas <команда>`.
Вывод по умолчанию — **JSON** (для ИИ-агентов/скриптов). `--text` — человекочитаемо (Rich).

## Глобальные флаги (перед командой)

| Флаг | Назначение |
|---|---|
| `--json` / `-J` | форсить JSON-вывод (дефолт) |
| `--text` / `--plain` | человекочитаемый вывод |
| `--profile <p>` / `-P` | активный профиль конфигурации |
| `--version` / `-V` | версия CLI |

Пример: `atlas --text epic list --project acme`.

---

## Проекты — `atlas projects`

- `atlas projects list` — список проектов портфеля.
- `atlas projects show <slug>` — карточка проекта.
- `atlas projects create --slug <s> --name "<name>" --type <type-slug> [...]` — создать.

Типы (`type`): `client-project`, `business-product`, `personal-utility`, `personal-project`, `shared-infrastructure`, `test`.

> Принадлежность и пространство синка: `owner`/`customer` (контрагенты) задают «чей проект», а **порталы синка определяются командой проекта** (участниками). `sync_policy` задаёт глубину выгрузки наружу.

## Задачи — `atlas pm-tasks`

- `atlas pm-tasks add --project <ref> --title "<t>" --cpp "<ЦКП>" [--priority P2] [--status backlog] [--slug <s>] [--due-date YYYY-MM-DD] [--assignee <participant-slug>] [--quality-tier T1|T2|T3]`
  - **`--cpp` (Ценный Конечный Продукт) обязателен.** При policy=full операция кладётся в outbox.
- `atlas pm-tasks list [--project <ref>] [--status <s>] [--assignee <slug>] [--archived]`
- `atlas pm-tasks get <ref>` — `ref` = number | slug | UUID | префикс UUID.
- `atlas pm-tasks update <ref> [--title|--cpp|--status|--priority|--due-date|--assignee|--quality-tier|...]` — slug/number/project неизменяемы.
  - Статусы: `backlog|todo|in_progress|review|done|blocked|cancelled`. `in_progress`→ставит `started_at`; `done`→`completed_at`.
- `atlas pm-tasks delete <ref> [--hard]` — soft-archive (по умолчанию) или физическое удаление. Кладёт `delete` в outbox.

## Эпики — `atlas epic`

- `atlas epic add --project <ref> --title "<t>" [--slug <s>] [--goal "<g>"]` — создать веху/спринт. Enqueue (entity_kind=epic).
- `atlas epic list --project <ref>` — эпики проекта.
- `atlas epic get <ref>` — карточка по slug или UUID.

JSON add: `{"id": "...", "slug": "...", "title": "...", "status": "active"}`.

## Чек-листы — `atlas checklist`

- `atlas checklist add --task <ref> --text "<шаг>"` — добавить пункт (позиция авто). Enqueue (entity_kind=checklist).
- `atlas checklist list --task <ref>` — пункты задачи.
- `atlas checklist check <item-uuid> [--uncheck]` — отметить выполненным / снять. Enqueue update.

## Участники задачи — `atlas member`

- `atlas member add --task <ref> --participant <slug> [--role executor]` — назначить (роли: `responsible|executor|watcher`). Идемпотентно.
- `atlas member list --task <ref>` — участники задачи.
- `atlas member rm --task <ref> --participant <slug> --role <role>` — снять.

> Участники — это и есть «куда синкать»: член, присутствующий в Б24/Notion, делает задачу видимой в его портале. ИИ-агент (`kind=ai_agent`) в порталах не присутствует → его задача остаётся локальной.

## Синхронизация — `atlas sync`

- `atlas sync push` — выгрузить pending-операции из локального outbox на backend-хаб (`POST /api/v1/events`). Хаб фанаутит в Б24/Notion. JSON: `{"sent": N}`.
- `atlas sync pull [--timeout 25]` — один цикл входящего синка (long-poll `/events/poll`), применить события локально по `backend_id`. JSON: `{"applied": N, "cursor": "..."}`.
- `atlas sync watch [--timeout 25]` — устойчивый входящий синк (long-poll; ошибки сети не валят цикл — retry с backoff). Ctrl+C для остановки.
- `atlas sync up` — **подключиться к хабу**: установить и запустить фоновый демон (long-poll в фоне, автостарт при входе пользователя + авто-рестарт при падении). Рекомендуемый способ постоянного синка.
- `atlas sync daemon install|uninstall|status` — управление фоновым демоном (Windows Scheduled Task `atlas-sync-watch`; лог в `cache_dir/sync-watch.log`).

Конфиг синка (`AtlasConfig`, слои global/project/local + env `ATLAS_*`): `base_url` (адрес хаба), `api_key` (`X-API-Key`, из `${ATLAS_API_KEY}` / config.toml / keyring), `portal_id` (например `atlas-dmitry`). **Без `api_key`/`base_url` команды `sync*` не работают, но весь локальный PM (projects/pm-tasks/epic/checklist/...) полностью функционирует автономно.**

## Справочники и прочее

- `atlas participants` — участники (люди/ИИ-агенты/контрактники).
- `atlas types` / `atlas statuses` / `atlas tags` — справочники проектов.
- `atlas ideas` / `atlas inbox` — идеи (стадия 0) и входящие на разбор.
- `atlas action-log tail [--project <ref>]` — аудит (append-only).
- `atlas backup` — бэкап-движок.

## Сводки (быстрый обзор)

- `atlas today` — активные задачи на сегодня.
- `atlas overdue` — просроченные.
- `atlas agenda` — overdue + today + no-date.
- `atlas no-date` — активные без дедлайна.

(Команды `atlas tasks`, `atlas notion-projects`, `atlas files` — прямой доступ к Notion-базам, legacy-слой до полного перехода на хаб.)

---

## Типичные сценарии (для ИИ-агента)

**Завести веху и наполнить её работой:**
```
atlas epic add --project acme --title "Спринт 5: онбординг"
atlas pm-tasks add --project acme --title "API регистрации" --cpp "Рабочий эндпоинт /register с тестами"
atlas checklist add --task ACM-12 --text "Схема запроса/ответа"
atlas checklist add --task ACM-12 --text "Тесты + реализация"
atlas member add --task ACM-12 --participant claude-code --role executor
```

**Синхронизация цикл:**
```
atlas sync push            # выгрузить свои изменения в команду (Б24/Notion)
atlas sync pull            # подтянуть изменения извне
atlas sync watch           # держать постоянный приём (long-poll)
```

**Прочитать состояние программно (JSON):**
```
atlas pm-tasks list --project acme           # → JSON-массив задач
atlas epic list --project acme               # → JSON-массив эпиков
atlas pm-tasks get ACM-12                     # → JSON карточки
```
