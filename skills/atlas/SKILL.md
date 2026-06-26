---
name: atlas
description: "CLI `atlas` — local-first PM-система портфеля проектов и задач (локальный SQLite, без сети). Команды в ЕД. ЧИСЛЕ, --json по умолчанию: project / task / epic / checklist / member / participant / hypothesis / type / status / tag / idea / inbox / action-log / backup. EN triggers — atlas CLI, add project, create task with cpp, portfolio tasks, add checklist item, make project personal, add hypothesis, archive project, onboard project folder into atlas, local pm cli, sqlite task manager. RU triggers — атлас, добавь проект, создай задачу с ЦКП, задачи портфеля, добавь пункт чек-листа, сделай проект личным, добавь гипотезу, заархивируй проект, онбординг папки в atlas, локальная pm-система, портфель проектов."
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
- Задачи портфеля: создать задачу с ЦКП, список/карточка, смена статуса, чек-листы, участники, эпики.
- Управление проектом: сделать личным/командным (`project make-personal`, `--team`), теги, git/layout.
- Гипотезы: фальсифицируемые гипотезы по продукту/маркетингу (`hypothesis`).
- Идеи/inbox: инкубатор идей и свалка сырья на разбор AI.
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

`project` (CRUD + теги + архив + `git`/`layout` подгруппы) · `task` (CRUD + lease:
`claim`/`release`/`renew`/`take`/`stale` для мультиагентности, `--cpp` обязателен) ·
`epic` · `checklist` (пункты задачи) · `member` (роли на задаче) · `hypothesis` (ledger) · `participant`
(люди портфеля) · `type` / `status` / `tag` (справочники) · `idea` / `inbox` (инкубатор/свалка) ·
`action-log` (аудит) · `backup`.

Глобально: `--json/-J` (дефолт), `--text/--plain`, `--version`.

Ref-резолв: project — slug|UUID|short-UUID; task — number|slug|UUID; прочее — slug|UUID.

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

### Пример 2 — задача с ЦКП и пунктами чек-листа

User: «Поставь задачу собрать структуру лендинга, ответственный — я.»

```bash
atlas task add --project kasha-landing --title "Собрать структуру лендинга" \
  --cpp "Готов согласованный wireframe из 6 секций" --assignee alice --priority P1
atlas checklist add --task <number|slug> --text "Прототип в Figma" --due 2026-06-25
```
`--cpp` обязателен (измеримый результат, не activity).

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

## Troubleshooting

**Команда «не найдена» / падает «No such command».** Навык мог отстать — сверься с `atlas <group>
--help`. Частая причина: старое имя во множественном числе (`atlas projects`/`pm-tasks`/`tags`) —
теперь ед. число (`atlas project`/`task`/`tag`).

**Регистрация Scheduled Task падает PermissionDenied.** Окружение Claude изолировано — Windows-задачу
(`backup install`) ставит пользователь в своей пользовательской сессии.

**«ambiguous» при резолве проекта/участника.** Покажи кандидатов из вывода CLI и спроси точное имя. Не угадывай.

## Субагенты (`agents/`)

`atlas:project-initializer` (`agents/project-initializer.md`) — автономно изучает папку проекта и
предлагает/применяет metadata + теги в Atlas-БД. Делегируй при онбординге папки. Подробнее —
[references/agent-playbook.md](references/agent-playbook.md).
