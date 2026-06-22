# Atlas: Provenance задач/эпиков + портфельные обзоры — дизайн

**Дата:** 2026-06-20
**Статус:** на ревью
**Репозиторий:** `_storage/atlas` (atlas CLI, PM-слой NP-005)

## Цель

Задача/эпик в бэклоге проекта должны нести **происхождение**: из какого проекта пришли и
**почему** (по какому принципу заведены). Плюс — видеть весь портфель разом (все эпики/задачи с их
проектом). Это закрывает реальный сценарий Дмитрия: агент, работая в проекте A, заносит найденную
работу в бэклог проекта B — и в B видно, что задача пришла из A и зачем.

Изменения **чисто локальные** в Atlas (SQLite). Синк с хабом и бэкенд **не трогаются** — provenance
это PM-метаданные портфеля, наружу не уходят.

## Не входит в scope (отдельные спеки)

- Контейнеры/модули (`parent_id`), типы-роли (kit/service/superskill), конфиг-типы, OS-symlink в
  `create_junction` — это **спека #2 (структура/типы)**.
- Изменения протокола синка, бэкенда, провижна.

## Архитектура

Один слой — локальная PM-БД Atlas. Точки изменения:
`models.py` (Task, Epic) → Alembic-миграция → `commands/pm_tasks.py`, `commands/epic.py` → `action_log`.
Никаких внешних вызовов; provenance не сериализуется в синк (`sync/mapper.py` не меняется).

## Модель данных

### Task — новые колонки (`models.py`, таблица `tasks`)

| Поле | Тип | Назначение |
|---|---|---|
| `source_project_id` | `String(36)`, FK→`projects.id`, nullable, index | проект-источник инжекта; NULL = заведена нативно в своём проекте |
| `origin` | `String(20)`, NOT NULL, default `'native'`, CHECK `('native','injected','imported','split')` | категория происхождения |
| `rationale` | `Text`, nullable | человеко/агенто-читаемое «почему/по какому принципу заведена» |
| `injected_by` | `String(36)`, FK→`participants.id`, nullable | кто/какой агент инжектнул |
| `injected_at` | `DateTime`, nullable | когда произошёл инжект |

### Epic — симметрично (`models.py`, таблица `epics`)

- `source_project_id`, `origin`, `rationale`, `injected_by`, `injected_at` — те же поля и семантика.
- `description` (`Text`, nullable) — **добавляется** (сейчас у эпика только `goal`); для симметрии с задачей.

### Миграция

Одна Alembic-ревизия (down_revision = текущий head после `f3f1a2b3c4d5`), `batch_alter_table` для SQLite
(как в существующих миграциях): добавляет 5 колонок в `tasks`, 6 в `epics` (+`description`), индексы
`idx_tasks_source_project`, `idx_epics_source_project`. Все колонки nullable / с дефолтом — безопасно для
существующих строк. **Боевая `~/.atlas/atlas.db` мигрируется отдельно с бэкапом (Ask First).**

### Инвариант origin↔source

- `source_project_id` задан → `origin` авто-становится `injected` (если явно не передан иной), `injected_at=now()`.
- `source_project_id` пуст → `origin` остаётся `native`.
- `origin=imported|split` допускают `source_project_id` (импорт/расщепление), но не требуют авто-инжекта.

## CLI

### `task add` (`commands/pm_tasks.py`)
Новые опции: `--source-project <ref>`, `--rationale <text>`, `--origin native|injected|imported|split`,
`--injected-by <participant-slug>`. Поведение: при `--source-project` — резолв ref→id, авто
`origin=injected` + `injected_at=now()`; `--injected-by` резолвится в participant.id (нерезолвимый → ошибка).
Запись в `action_log` (`task_created`) дополняется `source_project` + `rationale`.

### `task get`
Добавить блок **Provenance**: Source project (slug + name) / Origin / Rationale / Injected by / Injected at.
Печатается только если `origin != native` или есть `source_project_id`.

### `task list`
- Новый фильтр `--source-project <ref>` (задачи, пришедшие из проекта X).
- В выводе — пометка происхождения (например колонка `Origin` или маркер `←<src-slug>` у injected).

### `epic add` / `get` / `list` — симметрично
- `epic add`: `--source-project`, `--rationale`, `--origin`, `--injected-by`, **`--description`**.
- `epic get`: блок Provenance + `description`.
- `epic list`: **снять `required` с `--project`** → портфельный режим (все эпики) + **колонка `Project`**
  (JOIN `Project.slug`, по образцу `task list`). С `--project` — как раньше. + фильтр `--source-project`.

### Консистентность `--json` (баг)
`task list` и `task get` сейчас написаны на голом `typer`+`rich` и **игнорируют** глобальный `--json`
(всегда печатают таблицу). Перевести на `clikit.emit_data` (как `epic`/`hypothesis`), чтобы
`atlas --json task list/get` реально отдавал JSON для агентов. Provenance-поля включить в JSON-вывод.

## Поток данных (сценарий инжекта)

1. Агент в проекте A находит работу для B:
   `atlas task add --project B --source-project A --rationale "при разборе типов нашёл, что gatewaykit мисномер — переименовать в service"`.
2. Atlas: резолв B и A → id; `origin=injected`, `injected_at=now()`, `injected_by` (из `--injected-by`
   или дефолтного участника, если задан); запись задачи + `action_log`.
3. `atlas task list --project B` → задача помечена injected ←A.
4. `atlas task get <ref>` → блок Provenance: «из A, потому что …».
5. `atlas epic list` (без `--project`) → все эпики портфеля с колонкой Project.

## Обработка ошибок

- Нерезолвимый `--source-project` / `--injected-by` → явная ошибка (как у прочих ref-резолвов), задача не создаётся.
- Неизвестный `--origin` → ошибка валидации (CHECK + проверка в CLI).
- `source_project_id == project_id` (инжект «сам в себя») → предупреждение, `origin` остаётся `native`
  (источник = тот же проект бессмысленен).
- Миграция идемпотентна по факту наличия колонок; повторный прогон безопасен.

## Тестирование (TDD)

- Модель/миграция: апгрейд на throwaway-БД до head, проверка наличия колонок и дефолтов.
- `task add --source-project` → `origin=injected`, `injected_at` заполнен, source резолвнут; без него — `native`.
- `task add --source-project == project` → warning, `origin=native`.
- `task get` печатает Provenance только при наличии источника.
- `task list --source-project X` фильтрует; маркер injected в выводе.
- `epic list` без `--project` → все эпики + колонка Project; с `--project` — как раньше.
- `--json` для `task list/get` реально отдаёт JSON с provenance-полями (регресс-фикс бага).
- `action_log` для `task_created` содержит `source_project` + `rationale`.
- Регрессия: существующие тесты task/epic зелёные.

## Открытые вопросы (к ревью)

1. `--source-project` при кросс-проектном заносе — делать **обязательным** (нельзя завести задачу в чужой
   проект без указания источника) или оставить опциональным? (Рекомендация: опционально, но если агент
   указывает `--project` ≠ «текущего», подсказывать заполнить источник.)
2. `injected_by` по умолчанию — брать из дефолтного участника-агента (если настроен) или оставлять NULL
   без явного флага?
