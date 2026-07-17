---
name: atlas
description: "CLI `atlas` — local-first PM-система портфеля проектов и задач (локальный SQLite, без сети). Команды в ЕД. ЧИСЛЕ, --json по умолчанию: project / task / epic / sprint / checklist / member / participant / hypothesis / type / status / tag / backlog / issue / action-log / backup. EN triggers — atlas CLI, add project, create task with cpp, portfolio tasks, add checklist item, make project personal, add hypothesis, archive project, onboard project folder into atlas, local pm cli, sqlite task manager. RU triggers — атлас, добавь проект, создай задачу с ЦКП, задачи портфеля, добавь пункт чек-листа, сделай проект личным, добавь гипотезу, заархивируй проект, онбординг папки в atlas, локальная pm-система, портфель проектов."
---

# atlas

Respond in the user's language.

CLI `atlas` — **local-first PM-система портфеля проектов** (SQLite). Всё живёт в локальном SQLite
(`~/.atlas/atlas.db`) и работает без сети — самодостаточно, без внешних сервисов.

```
Atlas (SQLite, local-first) — проекты · задачи · эпики · идеи · гипотезы
```

> **Соглашения CLI:** команды в **единственном числе** (`project`, `task`, `tag`…), а `--json` —
> **дефолт** (для читаемого вывода — `--text`/`--plain`).

## When activated

- Портфель: «какие у меня проекты», «создай/обнови/заархивируй проект», список по типу/статусу/тегу.
- Задачи портфеля: создать задачу с ЦКП, список/карточка, жизненный цикл глаголами (start/done/block…), чек-листы, эпики.
- Операционный обзор портфеля (`dashboard`); прописать дисциплину в агентов (`init`).
- Управление проектом: сделать личным/командным (`project make-personal`, `--team`), теги, git/layout.
- Гипотезы: фальсифицируемые гипотезы по продукту/маркетингу (`hypothesis`).
- Интейк идей: backlog (основной пул, convert → task/project); idea/inbox — legacy.
- Git/layout/backup портфеля; аудит (`action-log`).
- Онбординг существующей папки проекта в Atlas (делегируется субагенту, см. playbook).

### When NOT to activate

- Прямые REST/webhooks/CRM Bitrix24 → навык `bitrix24`.
- Notion API в UI (формулы, automations) — вне скоупа.

## Route by request type

| Запрос | Куда смотреть |
|---|---|
| Точный синтаксис любой команды/флага | [references/commands.md](references/commands.md) — полный каталог |
| Проект, entity_kind, статусы, теги, архив, git/layout | [references/projects-and-layout.md](references/projects-and-layout.md) |
| Как вести себя как PM, делегировать, онбордить папку, гипотезы | [references/agent-playbook.md](references/agent-playbook.md) |

Всегда сверяй команду с живым CLI: `atlas <group> --help` — источник правды.

## Карта команд (ед. число; `atlas <group> <sub>`)

`project` (CRUD + теги + архив + `git`/`layout` подгруппы) · `task` (CRUD + **жизненный цикл
глаголами** + lease, `--cpp` обязателен) · `epic` · `sprint` (итерации) · `checklist` (пункты
задачи) · `member` (роли на задаче) · `hypothesis` (ledger) · `participant` (люди портфеля) ·
`type` / `status` / `tag` (справочники) · `backlog` (**основной интейк** идей, DB-first;
convert --as task|project) · `issue` (bug/feature/**handoff**) · `idea` / `inbox` (**legacy** —
см. backlog) · `action-log` (аудит) · `backup` · `profile`.

Топ-уровень (без группы): **`dashboard`** (операционный обзор портфеля) · **`init`**
(прописать Atlas-дисциплину в агентские файлы) · `stats` (аналитика) · `config` (онбординг) ·
`update` (self-update с PyPI, пакет atlas-pm) · `connect`/`disconnect` (внешний backend) · `logs`.

Глобально: `--json/-J` (дефолт), `--text/--plain`, `--version`.

Ref-резолв: project — slug|UUID|short-UUID; task — number|slug|UUID; прочее — slug|UUID.

## Жизненный цикл задачи — ГЛАГОЛАМИ, не «голым» статусом

Статус задачи (`backlog → todo → in_progress → review → done`, плюс `blocked`/`cancelled`)
меняется намеренными ГЛАГОЛАМИ — они уважают lease (мультиагентную блокировку), а «голый»
`update --status` его обходил:

| Действие | Команда | Что делает |
|---|---|---|
| Взять в работу | `atlas task start <ref>` | lease + status=in_progress + assignee (синоним `claim`) |
| На ревью | `atlas task review <ref>` | → review, lease сохраняется |
| Заблокировать | `atlas task block <ref> --reason "…"` | → blocked, lease сохраняется |
| Разблокировать | `atlas task unblock <ref>` | blocked→in_progress (нужно держать lease) |
| Завершить | `atlas task done <ref>` | → done, снимает lease, ставит completed_at |
| Отменить | `atlas task cancel <ref>` | → cancelled, снимает lease |

### Review-workflow (мультиагентная приёмка)

**По умолчанию review ВЫКЛЮЧЕН** (`default_review=False`) — соло-режим: `start → done` напрямую, reviewer не
нужен. Включить приёмку на задаче: `--reviewer <slug>` (или глобально `atlas config set default_review true`).
Когда включена — **закрыть в `done` может ТОЛЬКО назначенный reviewer**, исполнитель сдаёт на проверку:

| Действие | Команда | Кто |
|---|---|---|
| Сдать на проверку | `atlas task submit <ref> -m "что сделал/дальше"` | исполнитель (→review, снимает свой lease) |
| Одобрить и закрыть | `atlas task approve <ref> [-m "…"]` | **только reviewer** (→done) |
| Вернуть в работу | `atlas task reject <ref> -m "причина"` | только reviewer (→in_progress) |
| Переоткрыть закрытую | `atlas task reopen <ref> [-m "…"]` | только reviewer (done/cancelled→todo) |
| Комментарий | `atlas task comment <ref> "текст"` · `atlas task comments <ref>` | любой |

Поток: agent1 `task add … --reviewer agent1` → agent2 `start` → работа → `submit -m "…"` → agent1 `reject -m "…"`
(или `approve`). Комментарии и reviewer видны в `task get` — следующий агент получает весь контекст.

- **`task add` / `update --status` принимают ТОЛЬКО `backlog|todo`** (планирование). Lifecycle-статусы
  (in_progress/review/done/blocked/cancelled) — лишь глаголами. Иначе ошибка с подсказкой.
- **lease — локальная координация** (мультиагентность): занятую другим задачу `start` не возьмёт
  (exit 1). Прервался → `task release`; долгая работа → `task renew` (heartbeat); чужую протухшую
  отобрать → `task take --force` или закрыть/перевести с `--force`. `task stale --reap` — реап протухших.
- Завершать/переводить ЧУЖУЮ живую задачу — только с `--force` (иначе LeaseHeldError).

## Обзор и онбординг

- **`atlas dashboard`** (коротко `atlas dash` / `atlas -D`) — операционный board (для человека — Rich:
  KPI, что в работе, что заблокировано/просрочено, по проектам; для агента — `--json`). Глянь ПЕРЕД
  работой. Аналитика — `atlas stats`.
- **`atlas init`** — идемпотентно прописывает Atlas-дисциплину (managed-блок) в агентские файлы
  (`~/.claude/CLAUDE.md`, репо `AGENTS.md`/`CLAUDE.md`/…), чтобы каждая сессия вела задачи в Atlas.
- **`atlas config init`** — интерактивный визард конфига (owner, timezone, дефолты задач). Дефолты
  (`default_priority`, `default_review`, `default_reviewer`) применяются в `task add`/`batch`, если не
  заданы явно. Правка точечно — `atlas config set <key> <value>`.
- **`atlas connect <url> [--key]`** — опционально подключить внешний backend (синк); ключ — в защищённый
  secret-store. `atlas connect` (без url) — статус; `atlas disconnect` — отключить. **Local-first**: всё
  работает и без подключения; `sync push/pull` — только после `connect`.
- **`atlas update`** — self-update CLI с PyPI (дистрибутив atlas-pm, команда atlas): детектит менеджер
  (uv/pipx/pip) и ставит свежую версию; `--check` — показать текущую/доступную. `atlas upgrade` — legacy
  (git-install), не предлагать по умолчанию.

## Массовое создание — `task batch <file.toml>`

`[defaults]` — общие настройки батча (project, priority, reviewer/no_review…), `[[task]]` — задачи
(любое поле override'ит default). Разрешение значения: **задача › defaults › config › система**.
`--dry-run` — превью без записи.

```toml
[defaults]
project = "kasha"
priority = "P3"
no_review = true            # или reviewer = "dmitry"
[[task]]
title = "Собрать структуру"
cpp = "wireframe из 6 секций"
[[task]]
title = "Срочная правка"
cpp = "готово на проде"
priority = "P1"             # override дефолта батча
```

## Instructions

1. **Определи намерение → группу команд** по карте выше и таблице Route.
2. **Сверься с живым CLI**: `atlas <group> --help` — источник правды (навык мог отстать; точные флаги — в [references/commands.md](references/commands.md)).
3. **Собери команду**: ref-резолв (slug/number/UUID), обязательные флаги (`--name` на `project add`, `--cpp` на `task add`). Slug придумай сам (kebab-case).
4. **Выполни.** `--json` — дефолт; человеку добавь `--text`. Деструктивные мутации (`archive`, массовые правки, `--hard`) — сначала покажи что изменится и подтверди.

## Examples

### Пример 1 — создать проект (типичный)

User: «Заведи проект "Лендинг Каши", это клиентский.»

```bash
atlas project add --name "Лендинг Каши" --slug kasha-landing --type client-project \
  --team --one-line "Лендинг для клиента Каша" \
  --tag owner:my-org --tag stack:notion --tag domain:marketing
```
По умолчанию проект **личный** (владелец+lead = ты); `--team` делает командным (владелец — организация).
Slug придумай сам (kebab-case), не полагайся на автотранслит.

### Пример 2 — задача с ЦКП + жизненный цикл глаголами

User: «Поставь задачу собрать структуру лендинга, ответственный — я.»

```bash
atlas task add --project kasha-landing --title "Собрать структуру лендинга" \
  --cpp "Готов согласованный wireframe из 6 секций" --assignee alice --priority P1
atlas checklist add --task <number|slug> --text "Прототип в Figma" --due 2026-06-25
atlas task start <ref>     # взять в работу (lease + in_progress); НЕ update --status in_progress
# …работа…
atlas task done <ref>      # завершить (снимет lease, проставит сроки)
```
`--cpp` обязателен (измеримый результат, не activity). Статус — глаголами (start/done/...), не --status.

### Пример 3 — гипотеза (конкурентный анализ → стратегия)

```bash
atlas hypothesis add --project kasha-landing --title "Соцдоказательство выше оффера" \
  --statement "если поднять блок отзывов над оффером, то конверсия лида ↑ на 15%" \
  --metric "CR лендинга" --baseline "3%" --target "3.5%" --method "A/B 2 недели"
# по итогу замера:
atlas hypothesis close <ref> --verdict "подтверждено: CR 3.6%"
```

## Rules

1. **Источник правды — живой CLI.** Перед сложной командой сверься с `atlas <group> --help`. Команды в
   ед. числе; Notion-legacy (`today/tasks/files/whoami/notion-projects`) удалены — не предлагай их.
   Интейк идей веди через backlog (primary); idea/inbox — legacy.
2. **`--json` — дефолт.** Для читаемого вывода используй `--text`/`--plain`. Для делегирования — json.
3. **`--cpp` обязателен на `task add`.** Не знаешь ЦКП — спроси, не выдумывай заглушку.
4. **Slug придумывай сам** (kebab-case, англ., суть). Занятый явный `--slug` → ошибка, предложи другой.
5. **Soft-delete по умолчанию** (`archived_at`). `--hard` — только когда явно надо, с подтверждением.
6. **Пиши через CLI, не руками.** git/layout/БД — канон atlas; не запускай `git init`/`glab`/правку
   `atlas.db` напрямую, если у проекта есть запись в БД.
7. **Миграции БД atlas — только Alembic + Ask First**, с бэкапом `atlas.db`. Не пиши схему руками.
8. **action-log read-only.** Только `atlas action-log list`; таблица append-only.
9. **Подтверждай деструктивные мутации.** Перед `archive`/массовой правкой/`--hard` —
   покажи что изменится. Перенос даты по явной просьбе — без подтверждения.
10. **Respond in the user's language.** Инструкции тут на EN/RU; отвечай на языке пользователя.
11. **Статус — ГЛАГОЛАМИ, не `update --status`.** Взять в работу — `task start` (не `update --status
    in_progress`); завершить — `task done`; блок/ревью/отмена — `task block/review/cancel`.
    `update --status` принимает лишь `backlog|todo`. Перед работой над задачей бери её через `start`
    (lease защищает от двойного захвата в мультиагентности).

## Troubleshooting

**Команда «не найдена» / падает «No such command».** Навык мог отстать — сверься с `atlas <group>
--help`. Частая причина: старое имя во множественном числе (`atlas projects`/`pm-tasks`/`tags`) —
теперь ед. число (`atlas project`/`task`/`tag`).

**Регистрация Scheduled Task падает PermissionDenied.** Окружение Claude изолировано — Windows-задачу
(`backup install`) ставит пользователь в своей пользовательской сессии.

**«ambiguous» при резолве проекта/участника.** Покажи кандидатов из вывода CLI и спроси точное имя. Не угадывай.

**`update --status in_progress/done/...` падает («ставится командой task start/done»).** Так и задумано:
lifecycle-статусы меняются глаголами (`task start/done/review/block/unblock/cancel`), а `update --status`
принимает лишь `backlog|todo`. Это защищает lease от обхода.

## Субагенты (`agents/`)

`atlas:project-initializer` (`agents/project-initializer.md`) — автономно изучает папку проекта и
предлагает/применяет metadata + теги в Atlas-БД. Делегируй при онбординге папки. Подробнее —
[references/agent-playbook.md](references/agent-playbook.md).
