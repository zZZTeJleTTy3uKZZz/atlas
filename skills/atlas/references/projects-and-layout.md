# atlas — проекты: модель, провижн, раскладка, архив

## Модель сущностей (`entity_kind`) + статусы

| `entity_kind` | Где живёт | Когда |
|---|---|---|
| `inbox` | `_Inbox/<slug>/` | свалка сырья на разбор AI |
| `idea` | `_Ideas/<slug>.md` | сформулированная мысль до решения «делать» |
| `project` | `_storage/<slug>/` + junction в группе | полноценный проект |

`atlas project` работает только с `entity_kind='project'`; idea/inbox — отдельные группы (`atlas idea`, `atlas inbox`).

**5 канонических статусов**: `active` / `paused` / `archived` / `cancelled` / `experiment`
(legacy idea/research/maintained/dormant/graduating сконвертированы миграцией 007).

Типы (сид): `client-project` / `business-product` / `personal-utility` / `personal-project` /
`shared-infrastructure` / `test`. Группа на диске: client-project → `Clients/`, test → `Tests/`,
остальное → `Products/`.

## Провижн (раскладка проекта по 3 системам)

`atlas project add` по умолчанию создаёт **личный** проект и **раскладывает его в ядро + Notion**
(lead = ты, Notion-страница, entity_links, targets). Управление:

- `--team` — командный проект (владелец Цифро.ПРО), синкается в командные порталы (включая Б24).
- `--owner <slug>` — чужой владелец → проект становится командным.
- `--no-sync` — создать только в Atlas, без раскладки в ядро/Notion.
- `make-personal <ref>` — перевести существующий проект в личный (ядро+Atlas).
- `import-b24 <group_id> [--notion-kind …]` — обратное направление: втянуть существующую группу Б24
  в ядро+Notion+Atlas (автономность «Б24 → всё»).
- `link <ref> --portal <p> --external-id <id>` / `unlink <ref> --portal <p>` — ручная правка
  entity_link без `docker exec` в ядро.

Без хардкода: владелец по умолчанию = ты (из конфига), не зашит в код.

## Slug + prefix (правило агента)

Сам придумай осмысленный `--slug` в kebab-case (2-3 слова, англ., отражает суть) — не полагайся на
автотранслит русского имени. `project.slug` глобально уникален (`[a-z0-9-]`, 2-50). `project.prefix`
(1-5, `[a-z0-9]`, уникален) автогенерится (`cifro`→`cif`) или `--prefix`. `task.slug` — только
task-часть, система добавит `{prefix}-`. Занятый явный `--slug` → ошибка (не авто-суффикс).

Плохо: `cifro-pro-portal-vnedreniye-bitrix-crm`. Хорошо: `cifro` / `cifro-b24`.

## ЦКП (Ценный Конечный Продукт) — обязателен на задаче

`task add --cpp` обязателен. ЦКП = измеримый результат, не activity.
❌ «Сделать рефакторинг auth» · ✅ «Пользователь входит за email+пароль за 2 сек».
Не знаешь ЦКП — спроси, не выдумывай заглушку. Поле — `cpp_description NOT NULL`.

## Теги (4 категории)

`owner` (cifro-pro / dmitry) · `stack` (b24 / notion / python / anthropic-api / telegram …) ·
`domain` (marketing / sales / ai-agents / pm-tools / crm …) · `other`. Slug глобально уникален,
ref `category:slug` или bare `slug`. **При создании проекта — минимум 3 тега**: `owner:<X>`
(обязательно), `stack:<Y>`, `domain:<Z>`. Фильтр `project list --tag A --tag B` = AND. Нет тега —
сначала `atlas tag add`, не лепи `other` если подходит stack/domain.

## Soft-delete / status auto-timestamps

`delete` по умолчанию soft (`archived_at`, пропадает из `list`, виден по `get`); `--hard` — физическое
с подтверждением. У задачи `--status in_progress`→started_at, `done`→completed_at (+started_at),
откат из done чистит completed_at — CLI ведёт сам, не ставь вручную.

## Archive engine (физика + логический статус)

`project archive <ref> --status completed|paused|frozen|archived` — `mv` в `_Archive/<group>/` +
статус + запоминает `archived_group`. `unarchive` возвращает по `archived_group`. `renew` (только
client-project) — `renewal_count++`. `move --to-type` — смена типа (+ физ. mv если группа меняется).
`reorganize --dry-run|--apply` — drift БД↔ФС.

Смысл статусов: `completed` (разово закрыт; клиент → возможен renew) · `paused` (вернёмся, недели) ·
`frozen` (≥3 мес.) · `archived` (мёртв, history only). Всегда выбирай осмысленный, не дефолтный.

## Git/GitLab + junction layout + backup

- **git** (`project git …`): БД atlas = канон, не запускай `git init`/`glab` руками. `glab` backend,
  env `GITLAB_TOKEN`. Namespace `cifropro1/…` (общее) / `zzztejletty3ukzzz/…` (личное, `owner:dmitry`).
- **layout** (`project layout …`): физика в `_storage/<slug>/`, в логических папках — junction
  (`mklink /J`). Смена статуса не двигает данные. `verify` — проверка целостности.
- **backup** (`atlas backup …`): ежедневный snapshot всех git-репо → ветка `backup` на GitLab без
  переключения HEAD (`git commit-tree` + `update-ref`). Windows Task в 03:00.

## CWD (правило для AI)

- Portfolio-задачи (idea/inbox/кросс-проектные atlas-команды) → cwd `~/Documents/PROJECT/`.
- Project-задачи (код/тесты конкретного проекта) → cwd `…/PROJECT/<Group>/<slug>/` (junction →
  `_storage/<slug>/`). CLI работает из любой cwd (env `ATLAS_PROJECTS_ROOT`); cwd важен для AGENTS.md-контекста.
