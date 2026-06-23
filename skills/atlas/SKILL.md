---
name: atlas
description: "CLI `atlas` — локальная PM-система портфеля проектов Дмитрия (SQLite) с двусторонним синком в ядро-хаб notion-api-b24 (оттуда фанаут в Б24/Notion). Команды в ЕД. ЧИСЛЕ, --json по умолчанию: project / task / epic / checklist / member / participant / hypothesis / type / status / tag / idea / inbox / action-log / backup / sync / profile. EN triggers — atlas CLI, add project, create task with cpp, portfolio tasks, add checklist item, sync push/pull/watch daemon, register atlas profile, provision project to core, import b24 group, make project personal, add hypothesis, archive project, onboard project folder into atlas. RU triggers — атлас, добавь проект, создай задачу с ЦКП, задачи портфеля, добавь пункт чек-листа, синк push/pull, sync watch демон, заведи профиль атласа, разложи проект в ядро и notion, втяни группу Б24, сделай проект личным, добавь гипотезу, заархивируй проект, онбординг папки в atlas, синхронизация атласа с хабом."
---

# atlas

Respond in the user's language.

CLI `atlas` — **личная PM-система портфеля проектов** Дмитрия (NP-005). Живёт в локальном SQLite
(`~/.atlas/atlas.db`) и **двусторонне синкается с ядром-хабом** `notion-api-b24`. Atlas НЕ ходит в
Notion/Б24 напрямую — он шлёт события в ядро, а ядро фанаутит в Б24/Notion (и обратно).

```
Atlas (SQLite) ⇄ ядро-хаб notion-api-b24 ⇄ Б24 / Notion
```

> **Важно (навык переписан 2026-06):** прежний Notion-задачник (`today/overdue/tasks/files/whoami/
> notion-projects/agenda/no-date`) **УДАЛЁН** — синк теперь идёт через ядро. Команды переименованы в
> **единственное число** (`project`, `task`, `tag`…), а `--json` стал **дефолтом** (для человека —
> `--text`/`--plain`).

## When activated

- Портфель: «какие у меня проекты», «создай/обнови/заархивируй проект», список по типу/статусу/тегу.
- Задачи портфеля: создать задачу с ЦКП, список/карточка, смена статуса, чек-листы, участники, эпики.
- Провижн: разложить проект в ядро+Notion (`project add`), сделать личным/командным, втянуть группу Б24.
- Синк с хабом: `sync push/pull/watch/up`, фоновый демон, профили-сторы (`profile register`, `--profile`).
- Гипотезы: фальсифицируемые гипотезы по продукту/маркетингу (`hypothesis`).
- Идеи/inbox: инкубатор идей и свалка сырья на разбор AI.
- Git/layout/backup портфеля; аудит (`action-log`).
- Онбординг существующей папки проекта в Atlas (делегируется субагенту, см. playbook).

### When NOT to activate

- Прямые REST/webhooks/CRM Bitrix24 → навык `bitrix24`.
- Внутренняя разработка самого ядра-хаба `notion-api-b24` (адаптеры, оркестратор) — это другая кодовая база.
- Notion API в UI (формулы, automations) — вне скоупа.

## Route by request type

| Запрос | Куда смотреть |
|---|---|
| Точный синтаксис любой команды/флага | [references/commands.md](references/commands.md) — полный каталог |
| Синк с хабом, профили, scope, демон, синк чек-листов | [references/sync-and-profiles.md](references/sync-and-profiles.md) |
| Провижн проекта, entity_kind, статусы, теги, архив, git/layout | [references/projects-and-layout.md](references/projects-and-layout.md) |
| Как вести себя как PM, делегировать, онбордить папку, гипотезы | [references/agent-playbook.md](references/agent-playbook.md) |

Всегда сверяй команду с живым CLI: `atlas <group> --help` — источник правды.

## Карта команд (ед. число; `atlas <group> <sub>`)

`project` (CRUD + провижн + теги + архив + `git`/`layout` подгруппы) · `task` (CRUD + lease:
`claim`/`release`/`renew`/`take`/`stale` для мультиагентности, `--cpp` обязателен) ·
`epic` · `checklist` (синкается) · `member` (роли на задаче) · `hypothesis` (ledger) · `participant`
(люди портфеля) · `type` / `status` / `tag` (справочники) · `idea` / `inbox` (инкубатор/свалка) ·
`action-log` (аудит) · `backup` · `sync` (push/pull/watch/up/daemon) · `profile` (сторы).

Глобально: `--profile/-P <slug>` (выбрать стор), `--json/-J` (дефолт), `--text/--plain`, `--version`.

Ref-резолв: project — slug|UUID|short-UUID; task — number|slug|UUID; прочее — slug|UUID.

## Instructions

1. **Определи намерение → группу команд** по карте выше и таблице Route.
2. **Сверься с живым CLI**: `atlas <group> --help` — источник правды (навык мог отстать; точные флаги — в [references/commands.md](references/commands.md)).
3. **Собери команду**: ref-резолв (slug/number/UUID), обязательные флаги (`--name` на `project add`, `--cpp` на `task add`). Slug придумай сам (kebab-case).
4. **Выполни.** `--json` — дефолт; человеку добавь `--text`. Мутации с эффектом наружу (`archive`, `sync push` спорного, массовые правки) — сначала покажи что уйдёт и подтверди.
5. **Синхронизируй**: после правок, влияющих на порталы, — `atlas sync push` (исходящее) и `atlas sync pull` (входящее), либо фоновый `atlas sync watch`/`up`.

## Examples

### Пример 1 — создать проект с раскладкой в 3 системы (типичный)

User: «Заведи проект "Лендинг Каши", это клиентский.»

```bash
atlas project add --name "Лендинг Каши" --slug kasha-landing --type client-project \
  --team --one-line "Лендинг для клиента Каша" \
  --tag owner:cifro-pro --tag stack:notion --tag domain:marketing
```
По умолчанию проект **личный + раскладывается в ядро/Notion**; `--team` делает командным (уйдёт и в Б24).
`--no-sync` — только в Atlas. Slug придумай сам (kebab-case), не полагайся на автотранслит.

### Пример 2 — задача с ЦКП и пунктами чек-листа

User: «Поставь задачу собрать структуру лендинга, ответственный — я.»

```bash
atlas task add --project kasha-landing --title "Собрать структуру лендинга" \
  --cpp "Готов согласованный wireframe из 6 секций" --assignee dmitry --priority P1
atlas checklist add --task <number|slug> --text "Прототип в Figma" --due 2026-06-25
atlas sync push   # выгрузить в ядро → Б24/Notion
```
`--cpp` обязателен (измеримый результат, не activity). Чек-лист синкается двусторонне.

### Пример 3 — подключить фоновый синк (стор-профиль)

```bash
atlas profile register --name "Атлас (моё)" --member dmitry --scope personal  # → atlas-dmitry
atlas --profile atlas-dmitry sync up         # install + start демона long-poll
atlas --profile atlas-dmitry sync pull --timeout 5   # разовый прогон
```
Профиль = отдельный стор (своя БД + ключ + scope). `scope=personal` — только мои задачи; `all` — все.

### Пример 4 — гипотеза (конкурентный анализ → стратегия)

```bash
atlas hypothesis add --project kasha-landing --title "Соцдоказательство выше оффера" \
  --statement "если поднять блок отзывов над оффером, то конверсия лида ↑ на 15%" \
  --metric "CR лендинга" --baseline "3%" --target "3.5%" --method "A/B 2 недели"
# по итогу замера:
atlas hypothesis close <ref> --verdict "подтверждено: CR 3.6%"
```

### Пример 5 — обратный провижн: втянуть группу Б24

User: «Заведи в atlas нашу группу Обучения из Битрикса.»

```bash
atlas project import-b24 38 --notion-kind компанейский   # group_id=38 → ядро+Notion+Atlas
```

## Rules

1. **Источник правды — живой CLI.** Перед сложной командой сверься с `atlas <group> --help`. Команды в
   ед. числе; Notion-legacy (`today/tasks/files/whoami/notion-projects`) удалены — не предлагай их.
2. **`--json` — дефолт.** Для разговора с Дмитрием добавляй `--text`/`--plain`. Для делегирования — json.
3. **`--cpp` обязателен на `task add`.** Не знаешь ЦКП — спроси, не выдумывай заглушку.
4. **Slug придумывай сам** (kebab-case, англ., суть). Занятый явный `--slug` → ошибка, предложи другой.
5. **Soft-delete по умолчанию** (`archived_at`). `--hard` — только когда явно надо, с подтверждением.
6. **Пиши через CLI, не руками.** git/layout/БД — канон atlas; не запускай `git init`/`glab`/правку
   `atlas.db` напрямую, если у проекта есть запись в БД. Правки entity_link — `project link/unlink`.
7. **Миграции БД atlas — только Alembic + Ask First**, с бэкапом `atlas.db`. Не пиши схему руками.
8. **action-log read-only.** Только `atlas action-log list`; таблица append-only.
9. **Подтверждай мутации с эффектом наружу.** Перед `archive`/массовой правкой/`sync push` спорного —
   покажи что уйдёт. Перенос даты по явной просьбе — без подтверждения.
10. **Respond in the user's language.** Инструкции тут на EN/RU; с Дмитрием — на русском (с диакритикой).

## Troubleshooting

**Команда «не найдена» / падает «No such command».** Навык мог отстать — сверься с `atlas <group>
--help`. Частая причина: старое имя во множественном числе (`atlas projects`/`pm-tasks`/`tags`) —
теперь ед. число (`atlas project`/`task`/`tag`).

**`sync pull` возвращает applied:N, но локально пусто.** Проверь, что у проекта/задачи есть `backend_id`
и что payload с хаба несёт контейнер (`parent_task_backend_id`/`project_slug`). Подробности маршрутов —
[references/sync-and-profiles.md](references/sync-and-profiles.md).

**Регистрация демона/Scheduled Task падает PermissionDenied.** Окружение Claude изолировано — демон
(`sync up`/`daemon install`, `backup install`) ставит Дмитрий в своей пользовательской сессии.

**«ambiguous» при резолве проекта/участника.** Покажи кандидатов из вывода CLI и спроси точное имя. Не угадывай.

## Субагенты (`agents/`)

`atlas:project-initializer` (`agents/project-initializer.md`) — автономно изучает папку проекта и
предлагает/применяет metadata + теги в Atlas-БД. Делегируй при онбординге папки. Подробнее —
[references/agent-playbook.md](references/agent-playbook.md).
