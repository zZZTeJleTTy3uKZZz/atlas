# atlas — проекты: модель, видимость, раскладка, архив

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

## Видимость и владелец проекта

`atlas project add` по умолчанию создаёт **личный** проект (lead = ты, владелец из конфига). Управление:

- `--team` — командный проект (владелец — организация).
- `--owner <slug>` — чужой владелец → проект становится командным.
- `make-personal <ref>` — перевести существующий проект в личный (visibility=personal, владелец+lead=ты).

Без хардкода: владелец по умолчанию = ты (из конфига), не зашит в код.

## Slug + prefix (правило агента)

Сам придумай осмысленный `--slug` в kebab-case (2-3 слова, англ., отражает суть) — не полагайся на
автотранслит русского имени. `project.slug` глобально уникален (`[a-z0-9-]`, 2-50). `project.prefix`
(1-5, `[a-z0-9]`, уникален) автогенерится (`acme`→`acm`) или `--prefix`. `task.slug` — только
task-часть, система добавит `{prefix}-`. Занятый явный `--slug` → ошибка (не авто-суффикс).

Плохо: `acme-corp-portal-vnedreniye-bitrix-crm`. Хорошо: `acme` / `acme-b24`.

## ЦКП (Ценный Конечный Продукт) — обязателен на задаче

`task add --cpp` обязателен. ЦКП = измеримый результат, не activity.
❌ «Сделать рефакторинг auth» · ✅ «Пользователь входит за email+пароль за 2 сек».
Не знаешь ЦКП — спроси, не выдумывай заглушку. Поле — `cpp_description NOT NULL`.

## Теги (4 категории)

`owner` (organization / personal) · `stack` (b24 / notion / python / anthropic-api / telegram …) ·
`domain` (marketing / sales / ai-agents / pm-tools / crm …) · `other`. Slug глобально уникален,
ref `category:slug` или bare `slug`. **При создании проекта — минимум 3 тега**: `owner:<X>`
(обязательно), `stack:<Y>`, `domain:<Z>`. Фильтр `project list --tag A --tag B` = AND. Нет тега —
сначала `atlas tag add`, не лепи `other` если подходит stack/domain.

## Soft-delete / status auto-timestamps

`delete` по умолчанию soft (`archived_at`, пропадает из `list`, виден по `get`); `--hard` — физическое
с подтверждением. У задачи `task start`→started_at, `task done`→completed_at (+started_at),
реоткрытие из done в `todo`/`backlog` (через `update --status`) чистит completed_at — CLI ведёт сам, не ставь вручную.

## Archive engine (физика + логический статус)

`project archive <ref> --status completed|paused|frozen|archived` — `mv` в `_Archive/<group>/` +
статус + запоминает `archived_group`. `unarchive` возвращает по `archived_group`. `renew` (только
client-project) — `renewal_count++`. `move --to-type` — смена типа (+ физ. mv если группа меняется).
`reorganize --dry-run|--apply` — drift БД↔ФС.

Смысл статусов: `completed` (разово закрыт; клиент → возможен renew) · `paused` (вернёмся, недели) ·
`frozen` (≥3 мес.) · `archived` (мёртв, history only). Всегда выбирай осмысленный, не дефолтный.

## Git (GitLab/GitHub) + junction layout + backup

- **git** (`project git …`): БД atlas = канон, не запускай `git init`/`glab`/`gh` руками. Два провайдера:
  - **GitLab** (дефолт): `glab` backend, env `GITLAB_TOKEN`. Вложенные namespace
    `<org-namespace>/…` (общее) / `<personal-namespace>/…` (личное, `owner:personal`) — derive по type/status/tags.
  - **GitHub** (`--provider github`): `gh` backend (`gh auth login` / `GH_TOKEN`). Namespace плоский =
    **owner** (user/org): из `--group <owner>`, либо `config github_owner`, либо текущего `gh api user`.
  - `project git init <ref> [--provider gitlab|github] [--group …] [--private/--public]`;
    `move --to-group <group|owner>`, `sync-from-remote` — per-project по `git_provider`;
    `status`/`push`/`link` — provider-agnostic.
- **epic worktree** (`epic worktree …`, #300): изолированный цикл работы над эпиком в отдельной ветке/дереве.
  - `create <epic> [--base --path]` — `git worktree add` + ветка `epic/<slug>` в репо проекта эпика
    (по умолчанию рядом: `<repo>.worktrees/epic-<slug>`). У эпика должен быть slug.
  - `list <epic>` — worktree'ы репо (epic/* помечены ★). `merge <epic> [--into --push --remove]` — влить
    ветку эпика в base **после приёмки**; безопасно: основной репо обязан быть чист и на base-ветке
    (иначе ошибка-подсказка, ветки молча не переключаем; конфликт → `merge --abort` авто-откат).
    `remove <epic> [--force]` — снять worktree. Состояние держит git (без схемы в БД); autobackup
    веток эпика покрывает штатный `atlas backup`.
- **layout** (`project layout …`): физика в `_storage/<slug>/`, в логических папках — junction
  (`mklink /J`). Смена статуса не двигает данные. `verify` — проверка целостности.
- **backup** (`atlas backup …`): ежедневный snapshot всех git-репо → ветка `backup` на GitLab без
  переключения HEAD (`git commit-tree` + `update-ref`). Windows Task в 03:00.

## CWD (правило для AI)

- Portfolio-задачи (idea/inbox/кросс-проектные atlas-команды) → cwd `~/Documents/PROJECT/`.
- Project-задачи (код/тесты конкретного проекта) → cwd `…/PROJECT/<Group>/<slug>/` (junction →
  `_storage/<slug>/`). CLI работает из любой cwd (env `ATLAS_PROJECTS_ROOT`); cwd важен для AGENTS.md-контекста.

## Структура внутри проекта + что безопасно удалять

Каноническая раскладка проекта (пишет `atlas:project-initializer`): `AGENTS.md` (главный канон —
суть/принципы/правила/агенты), `README.md`, `_project/docs/{PROJECT_LOG, SCALING_PRODUCT/BACKLOG.md,
ARCHITECTURE/decisions}` (документация/состояние/ADR), исходники/skills/cases. Источник истины по
метаданным — БД atlas; при расхождении README↔AGENTS.md — побеждает AGENTS.md.

- **Артефакты/выгрузки/временное** → `_artifacts/` или `_scratch/` (gitignore, безопасно удалять).
- **Документация** → `_project/docs/…` (не удалять — это история/состояние).
- **Никогда не удаляй вслепую**: `_storage/<slug>/`, `.git/`, `_project/docs/`. Архивируй проект через
  `atlas project archive` (soft, физика не двигается), а не `rm`.
- Корневая папка портфеля — `atlas config set projects_root <path>` (или `config init`); по умолчанию
  `~/Documents/PROJECT`.
