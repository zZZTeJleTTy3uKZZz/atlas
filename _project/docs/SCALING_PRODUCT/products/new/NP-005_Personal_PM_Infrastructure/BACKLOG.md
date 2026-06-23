# BACKLOG — NP-005 Personal PM Infrastructure v4

**Версия**: v4 (2026-04-24, + ВОЛНА 4 Tags & Archive Engine; существующие Sprint 2 / v0.7 / v1.0 смещены в W5/W6/W7)
**Основано на**: [PRD.md v0.3](./PRD.md), [ARCHITECTURE.md v2.2](./ARCHITECTURE.md), [MODEL.md v0.3](./MODEL.md).

> Цель backlog — запуск первой работающей версии PM-системы (v0.4 Spike) и расширение в Sprint 1 / 2 до рабочего инструмента по всему портфелю.

---

## ВОЛНА 0 — Pre-work (уже сделано в этой сессии 2026-04-22)

- [x] **W0-01** Global Orient-вопрос к блокноту `0c2805ab-...` → content map.
- [x] **W0-02** Focused follow-up #1 → 14-day implementation protocol.
- [x] **W0-03** Focused follow-up #2 → integration sync protocol.
- [x] **W0-04** Download fulltext источника №29 (33k символов) в `research/00_*.md`.
- [x] **W0-05** ARCHITECTURE.md v1 (10 слоёв).
- [x] **W0-06** BACKLOG.md v1 (14 задач).
- [x] **W0-07** Pivot 1 (B24 leaf, not core).
- [x] **W0-08** Pivot 2 (DB-first + Superpowers + multi-agent).
- [x] **W0-09** PRD.md v0.3 с полным видением.
- [x] **W0-10** MODEL.md v0.1 — схема БД.
- [x] **W0-11** ARCHITECTURE.md v2 — переписана под новые решения.

---

## ВОЛНА 1 — Deep Research v2 (2026-04-23 → 2026-04-27) [NEW]

**Цель**: через NotebookLM собрать знания по IT project management, Scrum, multi-agent orchestration. Запустить ДО Spike v0.4 чтобы не проектировать вслепую.

- [ ] **W1-01** Блокнот NotebookLM — создать или решить использовать существующий (например `Cifro.pro — Personal PM Infrastructure v2 (NP-005)`). Решение Дмитрия.
- [ ] **W1-02** Запустить 10 блоков research из [RESEARCH_QUESTIONS_V2.md](./RESEARCH_QUESTIONS_V2.md) через `notebooklm source add-research ... --mode deep --no-wait`. По 3-4 параллельно.
- [ ] **W1-03** После завершения — применить Progressive Inquiry (из обновлённого skill `notebooklm`): Orient вопрос + 3-5 focused follow-ups.
- [ ] **W1-04** Синтез ответов в `research/v2_*.md` файлы.
- [ ] **W1-05** Обновить MODEL.md, ARCHITECTURE.md, PRD.md на основе research v2. Version bumps → v0.5 / v3 / v0.4 соответственно.

**Блокеры для Spike**: W1-01 нужен для старта W1-02. После W1-02 — Spike может идти параллельно с окончанием research, если нужны ранние результаты.

---

## ВОЛНА 2 — Spike v0.4 (2026-04-27 → 2026-05-03, 5-7 дней)

**Цель**: получить минимальную работающую PM-БД + одну фичу через Superpowers на одном pilot-проекте. Доказать, что концепция работает.

**Pilot-проект**: `atlas` (он же носитель будущей PM-функциональности).

### Файлы и окружение

- [ ] **SP-01** Global `~/.claude/AGENTS.md` (≤ 100 строк): роль Orchestrator, стек по умолчанию, data-model-first, compound engineering, Fresh Chat Per Task, ссылка на Superpowers workflow, ссылка на Tiers.
- [ ] **SP-02** 3 шаблона `AGENTS.md` в `templates/agents/`:
  - `AGENTS_client_project.md`
  - `AGENTS_business_product.md`
  - `AGENTS_personal_utility.md`
  - Все с YAML frontmatter согласно ARCHITECTURE §2.2.

### Git + Superpowers setup

- [ ] **SP-03** В `atlas/` (если не git-репо — `git init`): `.worktrees/` в `.gitignore`, создать директории `_project/docs/SCALING_PRODUCT/specs/` и `plans/`. Проверить, что `superpowers:using-git-worktrees` работает.
- [ ] **SP-04** Pytest setup в `atlas`: `pyproject.toml` с `[tool.pytest.ini_options]`, `tests/conftest.py`, 1 smoke test `test_placeholder.py`. `pytest` зелёный.
- [ ] **SP-05** Добавить `atlas/AGENTS.md` с frontmatter (`type=shared_infrastructure, quality_tier=T1`), ссылкой на будущий `pm_project_id`.

### База данных

- [x] **SP-06** Добавить зависимости в `atlas/pyproject.toml`: `sqlalchemy>=2.0`, `alembic>=1.13`. ✅ done.
- [x] **SP-07** Создать структуру `atlas/src/atlas/pm/`:
  - `models.py` — SQLAlchemy declarative_base, классы `ProjectType`, `ProjectStatus`, `Project`, `Participant`, `ProjectParticipant`, `Task`, `ActionLog` (только MVP таблицы из MODEL.md §2). ✅ done.
  - `db.py` — engine, session factory, path к `~/.cifro-pm/portfolio.db`. ✅ done.
  - `seeds.py` — функции `seed_project_types()`, `seed_project_statuses()`, `seed_participants()` (Дмитрий + Claude Code). ✅ done.
- [x] **SP-08** Alembic setup:
  - `alembic init atlas/migrations`.
  - `env.py` настроен на читать модели из `pm.models`.
  - Первая миграция `001_initial.py` (`0a6b3db9f107`) создаёт все таблицы + seed.
  - Команда `atlas projects init` вызывает `alembic upgrade head` + seed. ✅ done.

### CLI-команды (минимум)

- [x] **SP-09** `atlas projects init` — создаёт БД, применяет миграции, сидит. ✅ done (был `portfolio init`).
- [x] **SP-10** `atlas projects add <slug> --name "..." --type <type-slug> --one-line "..."` — добавляет проект. ✅ done (был `create`).
- [x] **SP-11** `atlas projects list [--type <t>] [--status <s>]` — табличный вывод. ✅ done.
- [x] **SP-12** `atlas projects get <ref>` — карточка проекта + список связанных tasks. ✅ done (был `show`).
- [ ] **SP-13** `atlas task create --project <slug> --title "..." --cpp "..."` — добавляет task. → реализовано как `pm-tasks add` (см. CHANGELOG v0.4.1).
- [ ] **SP-14** `atlas task done <id>` — update status + action_log. → реализовано как часть `pm-tasks update --status done`.
- [ ] **SP-15** `atlas action-log tail [--project <slug>]` — последние N записей. → реализовано как `action-log list`.

### Дополнительно сделано сверх плана (CHANGELOG v0.4.1) [NEW]

- [x] **SP-EXTRA-01** `projects update`, `projects delete --soft/--force` (soft через `archived_at`).
- [x] **SP-EXTRA-02** `pm-tasks add / list / get / update / delete` — полный CRUD с обязательным `--cpp`.
- [x] **SP-EXTRA-03** `participants add / list / get / update / delete` (с cascade `--force` или soft `--soft`).
- [x] **SP-EXTRA-04** `types add / list` и `statuses add / list` — вынесены top-level.
- [x] **SP-EXTRA-05** `action-log list` — read-only вьювер append-only лога.
- [x] **SP-EXTRA-06** Миграция 002 (`0d172deaa09b`): `projects.prefix`, `tasks.number`, `tasks.slug`.
- [x] **SP-EXTRA-07** Миграция 003 (`c55f75e76e5b`): `tasks.archived_at`.
- [x] **SP-EXTRA-08** Модуль `src/atlas/pm/slugs.py` — `slugify_text` (translit RU), `generate_unique_slug` (-2/-3), `generate_prefix_from_slug`, `build_task_slug`, `next_task_number`, `resolve_project_ref`, `resolve_task_ref`.
- [x] **SP-EXTRA-09** TDD: 28 baseline → **180 passed** (+152 тестов).
- [x] **SP-EXTRA-10** Notion-side `projects` → `notion-projects` (для очистки имени для PM-projects).

### Superpowers pilot

- [ ] **SP-16** Через полный Superpowers workflow реализовать ОДНУ реальную фичу: команду `atlas portfolio push` (PM → Notion DS_PROJECTS). Шаги:
  - `superpowers:brainstorming` → spec → `_project/docs/SCALING_PRODUCT/specs/2026-04-28-portfolio-push.md`.
  - `superpowers:writing-plans` → plan → `_project/docs/SCALING_PRODUCT/plans/2026-04-28-portfolio-push.md`.
  - `superpowers:using-git-worktrees` → `.worktrees/portfolio-push/`.
  - `superpowers:subagent-driven-development` (T1) → TDD + 2-stage review.
  - `superpowers:finishing-a-development-branch` → merge.
- [ ] **SP-17** Документировать что работает / что неудобно / что нужно тюнить в файле `research/spike_retro.md`.

### Pilot onboarding

- [ ] **SP-18** Занести 3 pilot-проекта в БД через `portfolio create`:
  - `cifro-pro` (client-project, priority P0, git=..., local_path=...).
  - `np-005` (business-product, priority P0, self-reference).
  - `docs-parsing` (personal-utility, priority P1, git=...).
- [ ] **SP-19** Создать `<repo>/AGENTS.md` в каждом из 3 pilot'ов (скопировать соответствующий шаблон, заполнить frontmatter).
- [ ] **SP-20** Проверить `atlas sync-agents` (опц., если успеем) или ручной sync.

### Ритуалы + retro

- [ ] **SP-21** Прожить одну неделю: Monday Kickstart (через `portfolio pull-inbox` — если готова; иначе вручную) + Friday Wind-down.
- [ ] **SP-22** В конце Spike — **Spike Retro**: что сработало, что театр, куда копать в Sprint 1. Записать в `sprints.retro_notes` через `sprint retro` или в markdown `research/spike_retro.md`.

---

## ВОЛНА 3 — Sprint 1 (2026-05-03 → 2026-05-17, 14 дней)

**Цель**: расширить PM-БД до полного функционала MVP, onboarding ещё 5 проектов, внедрить Scrum-ceremonies.

> **Note (2026-04-24, v4 BACKLOG)**: задачи onboarding pilot'ов (S1-06, S1-07, S1-08) **переносятся в W5** (после W4 — Tags & Archive Engine). Причина: полноценный onboarding всех проектов портфеля требует готовых тегов (owner/stack/domain) и работающего archive engine. Onboarding без тегов = придётся второй раз проходить по всем 28+ проектам. В W3 остаётся только расширение БД (S1-01..05), Scrum-ceremonies setup (S1-09..11), API drift setup (S1-12) и multi-agent groundwork (S1-13..14).

### Расширение БД

- [ ] **S1-01** Миграция 002: добавить таблицы `sprints`, `expenses`, `prd_snapshots`, `stacks`, `project_stacks` (MODEL.md §3).
- [ ] **S1-02** CLI команды: `sprint plan / show / review / retro / standup`, `expense add / list / report`, `prd snapshot --project <slug> --version v1 ...`, `stack add / link <project> <stack>`.

### Notion mirror полноценный

- [ ] **S1-03** `atlas portfolio pull-inbox` (Notion → PM, с тегом `inbox`).
- [ ] **S1-04** `atlas portfolio pull-dates` (Notion due-dates → PM tasks).
- [ ] **S1-05** `atlas portfolio push --full` (все пока поля: projects + tasks + sprints).

### Расширение onboarding

- [ ] **S1-06** Onboard ещё 4 клиента (Ferrum, KSO, Bankety, Kasha) с полным AGENTS.md.
- [ ] **S1-07** Onboard ещё 4 утилиты (fin_analitik, notion-api-b24, lightrag, AI Prodazhnik).
- [ ] **S1-08** Onboard оставшиеся бизнес-продукты (NP-001, NP-002, NP-003, NP-004).

### Scrum ceremonies в живую

- [ ] **S1-09** Провести Sprint Planning через CLI — начать Sprint 2 (после Sprint 1 с реальным запуском).
- [ ] **S1-10** Ежедневный standup через `sprint standup`.
- [ ] **S1-11** Sprint Review + Retro в конце.

### API Drift

- [ ] **S1-12** API Drift setup для NP-002 Bitrix24 API Wrapper: `openapi.yaml`, `spectral` pre-commit, `oasdiff` перед релизом.

### Multi-agent groundwork (начало)

- [ ] **S1-13** Добавить RBAC-поля в `participants`: `role_permissions` (JSON). Миграция 003.
- [ ] **S1-14** Спроектировать (design doc) FastAPI endpoint над `portfolio.db`. Не реализовывать — задокументировать endpoints.

---

## ВОЛНА 4 — Tags & Archive Engine (2026-04-24 → ~2 дня) [в работе]

**Цель**: расширить atlas до поддержки универсальных тегов (owner/stack/domain) и полного archive engine (physical move + logical statuses + renewal tracking). После этого — onboarding всех проектов портфеля.

**Основано на**: [ARCHITECTURE.md §2.7 v2.2](./ARCHITECTURE.md), [MODEL.md §2.8/2.9 v0.3](./MODEL.md), [ADR-001 в atlas repo](../../../../../../atlas/_project/docs/ARCHITECTURE/decisions/ADR-001-archive-layout.md).

### Schema + Models

- [ ] **W4-01** Миграция 004: таблицы `tags`, `project_tags` + поля `projects.renewal_count`, `projects.archived_group` + seed нового `project_type` `test` + 5 новых `project_statuses` (idea/research/planned/paused/completed/frozen).
- [ ] **W4-02** Обновить `src/atlas/pm/models.py`: classes `Tag`, `ProjectTag` + new fields в `Project`.

### Utilities

- [ ] **W4-03** `src/atlas/pm/tags.py`: `resolve_tag_ref`, `generate_tag_slug`, helpers.

### CLI — Tags

- [ ] **W4-04** `atlas tags add --slug ... --name ... --category <owner|stack|domain|other> [--color] [--description]`
- [ ] **W4-05** `atlas tags list [--category <c>]`
- [ ] **W4-06** `atlas tags get <ref>`
- [ ] **W4-07** `atlas tags update <ref> --field ...`
- [ ] **W4-08** `atlas tags delete <ref> [--force]` (force если тег привязан к проектам)

### CLI — Projects: tags integration

- [ ] **W4-09** `atlas projects add --tag owner:dmitry --tag stack:b24 ...` (множественные теги)
- [ ] **W4-10** `atlas projects list --tag <slug>` AND-фильтр
- [ ] **W4-11** `atlas projects add-tags <slug> --tag X --tag Y`
- [ ] **W4-12** `atlas projects remove-tags <slug> --tag X`

### CLI — Projects: archive engine

- [ ] **W4-13** `atlas projects archive <slug> --status completed|paused|frozen` → physical mv в `_Archive/<group>/` + update status + archived_at + archived_group.
- [ ] **W4-14** `atlas projects unarchive <slug> [--status active]` → physical mv обратно (по archived_group) + clear archived_at.
- [ ] **W4-15** `atlas projects renew <slug>` → renewal_count++, status=active, unarchive если в архиве, action_log.
- [ ] **W4-16** `atlas projects move <slug> --to-type <new-type>` → смена типа, physical mv в новую группу.
- [ ] **W4-17** `atlas projects reorganize [--dry-run] [--apply]` → diff физики и БД, по желанию синхронизация.

### Seed данных

- [ ] **W4-18** `atlas/scripts/seed_tags.py` или batch через CLI — ~30 тегов (owner/stack/domain).

### Tests (TDD)

- [ ] **W4-19** Тесты для каждой фичи (RED-GREEN). Ожидаемо +60-80 тестов.

### Verification

- [ ] **W4-20** Все тесты GREEN, smoke test полного цикла (add project with tags → archive → unarchive → renew).

**Выход W4**: atlas готов к onboarding всех 28+ проектов. Далее — W5 onboarding.

---

## ВОЛНА 4.5 — Git Provider + Storage Architecture (2026-04-25 → ~3 дня) [в работе]

**Цель**: интегрировать в atlas Git и провайдер GitLab; перейти на физическую модель «всё в `_storage/`, логика через junction'ы». Обеспечить резервное копирование всех проектов в GitLab и обратимое перемещение между статусами без физического перемещения файлов.

**Триггер**: 2026-04-24 — инцидент с `Med Persona` (моя ошибка `Remove-Item -Recurse -Force` после robocopy /MOVE; данные восстановлены из VSS shadow от 22.04). Вывод: каждый проект должен быть под git + GitLab бэкап, и физика не должна двигаться при логических операциях.

**Архитектурные решения**:
- Top-level GitLab namespace = `cifropro1` (уже существует, private).
- Subgroups: `clients`, `products`, `tests`, `inbox`, `archive` (+ `archive/{clients,products,tests,inbox}`).
- Имена project repos = slug проекта (без префиксов).
- Физика: `PROJECT/_storage/<slug>/` — единое неподвижное место.
- Логика: junction `PROJECT/<group_folder>/<DisplayName>` → `_storage/<slug>/`. При смене статуса пересоздаются junction'ы; репо в GitLab переносится через `glab repo transfer`.
- Backend: subprocess + `glab` CLI (без библиотек типа `python-gitlab`).

### Окружение

- [x] **W45-01** Установка `glab` CLI v1.93.0 (winget GLab.GLab) + добавление в User PATH (через hardlink в `.local/bin`).
- [x] **W45-02** Авторизация в GitLab (PAT `glpat-...` в env `GITLAB_TOKEN`, User scope). Username: `cemon345rus`.

### Atlas — Git integration (Sub-agent A)

- [ ] **W45-03** Миграция 006: колонки `git_remote_url`, `git_default_branch` (default 'main'), `git_provider` ('gitlab'|'github'|null), `git_initialized_at`, `git_last_pushed_at` + roundtrip-тест.
- [ ] **W45-04** `src/atlas/pm/git_backend.py`: `GitBackend` Protocol, `GitLabBackend` (subprocess + glab), `LocalGitOps` (init/commit/remote/push/status). Все subprocess мокаются в тестах.
- [ ] **W45-05** `src/atlas/pm/git_paths.py`: `derive_group_path(type, status, archived_group)` → `cifropro1/{clients|products|tests|inbox|archive/...}`.
- [x] **W45-06** Команды `atlas projects git {init,status,push,link,move,status-all,sync-from-remote}` с TDD (выполнено 2026-04-27, +27 тестов). `git_paths.py` расширен `owner_tags` параметром — namespace routing по тегу `owner:dmitry` → `zzztejletty3ukzzz/...`, иначе → `cifropro1/...`. Все subprocess (glab/git) мокаются в тестах.
- [x] **W45-07** SKILL.md: §3.13 Git/GitLab integration (выполнено 2026-04-27).

### Atlas — Junction architecture (Sub-agent B)

- [ ] **W45-08** `src/atlas/pm/junctions.py`: `create_junction`/`remove_junction`/`is_junction` обёртки над `mklink /J` и `cmd /c rmdir`. Safety: `remove_junction` отказывается удалять реальные папки.
- [ ] **W45-09** `src/atlas/pm/layout.py`: `get_storage_path`, `get_logical_path`, `plan_migrate_to_storage`, `migrate_to_storage`, `sync_logical`, `verify`. Формулы пути на основе type+status, без новых БД-колонок.
- [x] **W45-10** Команды `atlas projects layout {init,sync,verify,migrate-all,list-storage}` с `--dry-run`/`--copy-first`/`--confirm` (выполнено 2026-04-27, +23 теста). Safety: destructive ops требуют `--confirm`, `migrate-all` без `--confirm` → forced dry-run. `remove_junction` всегда сначала `is_junction` check.
- [x] **W45-11** SKILL.md: §3.14 Layout & junction architecture (выполнено 2026-04-27).

### GitLab — Subgroups (Sub-agent C)

- [ ] **W45-12** Создать в `cifropro1` subgroups: `clients`, `products`, `tests`, `inbox`, `archive`, `archive/{clients,products,tests,inbox}`. Идемпотентно (если уже есть — переиспользовать). Результат — `gitlab_groups.json` в папке NP-005.

### Bulk

- [ ] **W45-13** Bulk init: `atlas projects git init` для всех 12 проектов в `Clients/` + atlas в `Products/atlas`. Создаст репо в правильных GitLab subgroups.
- [ ] **W45-14** Bulk physical move: `atlas projects layout migrate-all --copy-first` — перенос всего в `_storage/<slug>/` + создание junction'ов в текущих логических папках. Обязательно с verify, без удалений до подтверждения.

### Migration of existing GitLab artifacts

- [ ] **W45-15** Перенос `cifropro1/banket-elista/kalkulator` → `cifropro1/clients/bankety/banket-elista/kalkulator` (после согласования модульной системы).
- [ ] **W45-16** Перенос top-level groups `newpeop` и `shuklin2` → `cifropro1/clients/newpeople`, `cifropro1/clients/shuklin` (после согласования; их членство передаётся через `glab group transfer`).

### Claude Code sessions migration

- [ ] **W45-17** Перенос истории сессий Claude Code (`~/.claude/projects/C--...PROJECT-Metela-Med-Persona/`) → новый путь `~/.claude/projects/C--...PROJECT-_storage-med-persona/` (или симлинк). То же для остальных проектов после bulk move. Скрипт `scripts/migrate_cc_sessions.py`.

### Будущая модульность (запись для следующих волн)

- [ ] **W45-18** [SPEC только, реализация позже] Модульная система: разбиение монолитного клиентского репо на модули → подпроекты в subgroup `cifropro1/clients/<client>/<module>`. Atlas хранит модули как отдельные `Project` с `parent_project_id` (потребует миграцию). Триггер: когда у клиента появится 2+ модулей (Bankety уже имеет: kalkulator + основной). Сейчас — каждый клиент = один монолитный репо как **бэкап**.
- [ ] **W45-19** [связано с W45-18] Подключить уже найденные nested `.git/` как отдельные репо в нужных subgroup'ах. На 2026-04-27 при пилоте Med-Persona найден nested `.git` в `Тест АПИ/` (был empty init, 0 commits — перенесён в `PROJECT/_old_git_backups/`); ожидаем что у Bankety / NL / Shuklin тоже есть subprojects с собственными `.git`. Сейчас все nested `.git/` отключаются (move в `PROJECT/_old_git_backups/`), их история сохраняется. После реализации модульной системы (W45-18) — поднять обратно как отдельные репо.
- [ ] **W45-20** Разобрать `Tests/*` (19 проектов, push'нуты в `zzztejletty3ukzzz/tests/`): что архивировать (мусор `null/`, `output/`, `vendor/`), что переносить в личные продукты Дмитрия (например `notebooklm_bundle` → `personal-utility`, `lightrag` → `personal-utility`, `notion-api-b24` → может стать продуктом NP-002). Решение делать через `atlas projects move/archive` после bulk-onboarding.
- [ ] **W45-21** Очистить commit history Bankety от `modules/shared/config/google-credentials.json`. На 2026-04-27 при push'е `cifropro1/clients/bankety` сохранена history 21+ commits, в одном из них (`329d5e2 update submodules` и предшествующих) есть credentials.json. Локальный файл сохранён, в новом HEAD untracked + в gitignore. Нужно `git filter-repo` (или BFG-cleaner) для удаления blob'а из всех commits. Репо приватный — не критично, но security debt.
- [ ] **W45-22** Переименовать GitLab top-level group `zzztejletty3ukzzz` → `dmitry-projects` (или другое читаемое имя). Через web UI Settings → General → Path. После — обновить локальные remote URLs (`git remote set-url origin ...`) для всех transferred репо (NL, Shuklin + 19 tests).
- [ ] **W45-23** Решить судьбу старых top-level groups `newpeop` и `shuklin2` в GitLab. Они созданы как отдельные namespace'ы для конкретных клиентов (предположительно: Newpeop под NL, Shuklin2 под Shuklin), вероятно для возможности дать доступ клиенту к их репо. Содержат `newpeop/media`, `shuklin2/finance/unit`. Решение: оставить как есть (доступ для клиентов), либо transfer всё в `dmitry-projects/clients/<slug>/<module>`. Записан как отложенная задача.
- [x] **W45-24** Bulk onboard всех push'нутых проектов в atlas БД (выполнено 2026-04-27). Применили миграцию 006 к prod-БД (`~/.atlas/atlas.db`), затем 32 × `atlas projects add` с `--git-repo-url`, `--local-path`, `--tag owner:* -t stack:* -t domain:*`. Итог: **БД atlas = 32 проекта = соответствует GitLab**. По типу: 11 client-project (9 cifropro + 2 dmitry: nl, shuklin), 1 inbox (cifro), 1 personal-utility (atlas), 19 test. По статусу: 5 active, 3 maintained, 11 dormant, 4 idea, 18 experiment, 3 research (с учётом что некоторые попадают в несколько категорий через grep). Теги owner:cifro-pro=10, owner:dmitry=22 (atlas + nl + shuklin + 19 tests).

### Daily backup branch (W45-25 — реализовано 2026-04-27)

- [x] **W45-25a** Создан `Products/atlas/scripts/backup/daily_backup.sh` — snapshot working tree → branch `backup` через git low-level (`write-tree` в TEMP index → `commit-tree` → `update-ref`). НЕ переключает HEAD, НЕ затрагивает working tree пользователя. Push только если tree отличается от предыдущего backup.
- [x] **W45-25b** `daily_backup_all.sh` — обходит все репо в `PROJECT/{Clients,Products,Tests,_Inbox,_storage}/*`, для каждого вызывает daily_backup.sh. Логи в `scripts/backup/logs/backup-YYYY-MM-DD.log`.
- [x] **W45-25c** `register_task.ps1` — регистрация Windows Scheduled Task `atlas-daily-backup` через `New-ScheduledTask*`. По умолчанию 03:00 ежедневно. Идемпотентно.
- [x] **W45-25d** Smoke test на atlas: ✅ branch `backup` создан на remote (commit `31c7f793`), working tree не тронут.
- [x] **W45-25e** Интегрировать в atlas как команды: `atlas backup {run,status,install,uninstall,list-tasks}` (выполнено 2026-04-27, +33 теста). `pm/backup.py` — pure-logic detached от typer, unit-testable. `_select_projects()` берёт из БД atlas с фильтрами. Каждый backup пишет в `action_log` (action=`backup`). Shell-скрипты сохранены — продолжают работать как альтернатива. SKILL.md §3.15 добавлено.

### atlas products group в личном namespace

- [x] **W45-26** Создана subgroup `zzztejletty3ukzzz/products/` (id=131111320). Atlas project transferred туда (был `cifropro1/products/atlas` → теперь `zzztejletty3ukzzz/products/atlas`). Local remote обновлён. Также подсвечивает что `git_paths.py.derive_group_path` нужно расширить логикой owner — для type=personal-utility при owner:dmitry → `zzztejletty3ukzzz/products` вместо `cifropro1/products` (записать в W45-06 расширение).

**Выход W4.5**: все 12 проектов под git + GitLab; единый layout `_storage/` + junction'ы; смена статуса не двигает физику; миграция модульности и orphan-групп — отдельной волной.

### Дополнения W4.5 (2026-04-27 wave-end)

- [x] **W45-27a** ClaudeCode settings.json (~/.claude/settings.json) — добавлены 23 `ask` permission rules для destructive Windows commands: `PowerShell(Remove-Item*)`, `Bash(rm -rf*)` и варианты, `Bash(cmd /c rmdir*)` etc. Триггер — инцидент 2026-04-24 с `Remove-Item -Recurse -Force` который стер Med-Persona. Теперь любое Remove-Item требует одобрения от Дмитрия.
- [x] **W45-27b** Memory `terminology_projects.md` — зафиксировано что «проекты» = собирательный термин (клиентские, продукты, личные, тесты, inbox). Внутри клиента — «проекты клиента» (модули).
- [x] **W45-28** Bulk migrate-all → `_storage/` + junction'ы для **31 проекта** выполнено 2026-04-27 (32 минус Med-Persona). Pilot Bankety (1383 МБ, 10632 файла) — md5-hashes идентичны через junction и storage. Final state: 31/32 OK, Med-Persona оставлена в `Clients/Med-Persona`.

### Дополнения W4.5 (продукты + bugs из реального использования)

- [x] **W45-29** NP-001 ИИ РОПчик создан как `business-product` (cifro-pro, idea, P0) → `cifropro1/products/np-001-ai-ropchik`. Spec мигрирован из `_project/.../products/new/NP-001_AI_ROPCHIK.md` в `_storage/np-001-ai-ropchik/SPEC.md` + README.md.
- [x] **W45-30** NP-004 ИИ Конвейер Знаний создан как `personal-utility` (dmitry, research, P0) → `zzztejletty3ukzzz/products/np-004-ai-knowledge-conveyor`. Целая папка spec'и (10 файлов: ARCHITECTURE/MODEL/PRD/BACKLOG v0.3 + research) перенесена из `_project/.../products/new/NP-004_AI_Knowledge_Conveyor/` в `_storage/np-004-.../`. В старом месте — stub `MOVED.md`.
- [ ] **W45-31** NP-002 Bitrix24 API Wrapper и NP-003 Marketplace Integrations — пока оставлены как spec в `_project/.../products/new/`. Инициализировать как отдельные продукты когда начнём их разрабатывать.

### W45-32: Bugs в atlas CLI (обнаружены 2026-04-27 в boevoм режиме)

- [x] **W45-32a** ИСПРАВЛЕНО 2026-04-28. `GitLabBackend.create_remote()` теперь использует `glab api groups/<encoded>` (получить namespace_id) + `glab api -X POST projects -F namespace_id=<id> -F name=<repo> -F visibility=<...> -F default_branch=main`. Старый `glab repo create <full_path>` не работал с subgroup'ами.
- [x] **W45-32b** ИСПРАВЛЕНО 2026-04-28. `LocalGitOps.add_remote()` теперь idempotent: при ошибке "remote already exists" автоматически делает `git remote set-url`. И `atlas projects git link`, и `add --init-git` теперь работают повторно без ручной чистки.
- [x] **W45-32c** ИСПРАВЛЕНО 2026-04-28. `atlas projects update` теперь принимает `--git-remote-url <URL>` (новый канон). Любой из двух флагов (`--git-remote-url` или legacy `--git-repo-url`) синхронизирует ОБА поля БД (`git_remote_url` + `git_repo_url`) — устраняет рассинхрон.
- [x] **W45-32d** ИСПРАВЛЕНО 2026-04-28. `atlas projects layout sync --force` принимает реальные директории: переносит их в `_old_git_backups/<name>-real-YYYY-MM-DD/` и создаёт junction поверх. Без `--force` сохраняется старое safety-поведение (отказ).
- [x] **W45-32e** ИСПРАВЛЕНО 2026-04-28. `atlas projects archive <slug>` теперь junction-aware: если `local_path` это junction (на `_storage/<slug>/`), физика остаётся на месте, junction пересоздаётся в `_Archive/<group>/<slug>/` (не shutil.move).
- [x] **W45-32f** ИСПРАВЛЕНО 2026-04-28. `atlas projects layout migrate-all --exclude <slug>` (multi) добавлен. По умолчанию `atlas` всегда исключён (см. W45-32h). Опция `--allow-self` отключает safeguard явно.
- [x] **W45-32g** ИСПРАВЛЕНО 2026-04-28. `_perform_storage_move()`: rmdir после copy не fatal если `src.exists() == False` (значит реально удалилось); robocopy /MOVE rc=1..7 как success (no files copied / warnings), >=8 как ошибка. Большие проекты (NL/Shuklin) больше не валятся.
- [x] **W45-32h** ИСПРАВЛЕНО 2026-04-28. `migrate-all` по умолчанию исключает `atlas` (self-migration safeguard). Чтобы разрешить — `--allow-self`. Защищает от lockup'а `.venv/Scripts/atlas.exe` во время миграции.
- [x] **W45-32i** Verified 2026-04-28. `_resolve_tags_or_die()` уже даёт понятную ошибку: `Tag 'X' не найден. Создайте: atlas tags add --slug ... --category ...`. Не silent failure (видимо был исправлен ранее, в backlog осталось как заметка).
- [x] **W45-32j** ИСПРАВЛЕНО 2026-04-28. `GitLabBackend.transfer_to_group()` теперь использует `glab api -X PUT projects/<id>/transfer -F namespace=<group_id>` вместо несуществующего `glab repo transfer --group`. Получает project_id и namespace_id через `glab api`.
- [x] **W45-32k** ИСПРАВЛЕНО 2026-04-28 — см. W45-32c (унифицирован).
- [x] **W45-32m** (новый, 2026-04-28) ИСПРАВЛЕНО. `atlas projects add` и `update` теперь принимают relative `--local-path` — резолвится через `ATLAS_PROJECTS_ROOT`. Если `--local-path` опущен в `add` — auto-derive по type+slug через `expected_project_path()`.
- [x] **W45-32n** (новый, 2026-04-28) ИСПРАВЛЕНО. `perform_git_init()`: `set_default_branch("main")` ДО первого commit'а, иначе HEAD остаётся на `master`, и `git push origin main` падает с "src refspec does not match any". Регрессия от старого порядка вызовов.

### W45-37: «Один проект — одна команда» (`atlas projects add --init-git`) — реализовано 2026-04-28

**Цель**: создать новый проект полностью одной CLI командой — БД-запись + `_storage/<slug>/` + junction в logical (Products/Tests/...) + canonical `README.md` / `AGENTS.md` / `.gitignore` + git init local + GitLab repo + push.

- [x] **W45-37a** Расширены flags `atlas projects add`:
  - `--setup-layout/--no-setup-layout` (default: setup-layout) — создать `_storage/<slug>/` + junction.
  - `--canonical/--no-canonical` (default: canonical) — записать canonical README/AGENTS/.gitignore (если их ещё нет).
  - `--init-git/--no-init-git` (default: no-init-git) — git init + GitLab create + push после layout/canonical.
  - `--private/--public` (default: private) — visibility GitLab репо.
  - `--group <path>` — явный GitLab namespace path (если опущен — derive по type+owner).
  - `--commit-message` — текст initial коммита.
- [x] **W45-37b** Helper `perform_git_init()` (extract из `init_cmd`) для повторного использования из add-flow и git-init flow одинаковая логика.
- [x] **W45-37c** Helper `_create_canonical_files()` + 3 шаблона (README/AGENTS/.gitignore) с подстановкой metadata проекта.
- [x] **W45-37d** Helper `_setup_storage_and_junction()` — создаёт `_storage/<slug>/` если нет, junction в logical если logical свободен (или junction уже на правильный target — оставляет).
- [x] **W45-37e** Smoke test end-to-end: `atlas projects add --name "Smoke Test V2" --slug smoke-test-v2 --type test --status experiment --priority P3 --tag owner:dmitry --init-git` → за одну команду создаётся: БД-запись + storage + junction + 3 canonical файла + git init + GitLab repo `zzztejletty3ukzzz/tests/smoke-test-v2` + push initial commit. Verified.
- [x] **W45-37f** 507/507 тестов PASS после всех фиксов (включая 26 тестов которые пришлось адаптировать под новые поведения GitLabBackend и default behavior `add`).
- [x] **W45-32l** `atlas projects delete --hard` теперь удаляет полностью (исправлено 2026-04-28):
  - `--hard` (default) → удалить из БД + снять junction + перенести `_storage/<slug>/` в `_old_git_backups/<slug>-deleted-YYYY-MM-DD/`.
  - `--hard --keep-files` → legacy-поведение (только БД, файлы и junction остаются — нужно для редких случаев).
  - `--hard --with-gitlab` → дополнительно удалить GitLab-репозиторий через `glab repo delete <full-path> --yes` (требует второго подтверждения).
  Реализация: `_storage/atlas/src/atlas/pm/commands/projects.py::delete_cmd` + хелперы `_hard_delete_physical` / `_hard_delete_gitlab` / `_gitlab_full_path_from_remote_url`. Junction снимается через safe `remove_junction()` (с проверкой что это действительно junction). Storage переносится через `_perform_storage_move` (robocopy /MOVE). Verified end-to-end на тестовых проектах.

### W45-33: AI-onboarding субагент (atlas:project-initializer) — реализовано 2026-04-27

- [x] **W45-33a** Создан `~/.claude/skills/atlas/agents/project-initializer.md` — субагент изучает папку проекта (AGENTS.md, README, pyproject, _project/docs, factory_registry, src/, skills/, cases/, modules/), извлекает metadata, обновляет Atlas-БД (one-line, description, теги, optionally type/status/priority через `atlas projects update / move / add-tags / remove-tags`) и переписывает/создаёт README.md в каноническом формате. НЕ удаляет файлы, НЕ двигает физику, НЕ делает git push.
- [x] **W45-33b** SKILL atlas обновлён — новая секция §4 «Субагенты» (project-initializer + roadmap для bulk-onboarder/doc-actualizer/module-promoter/test-classifier) и §5 «Канон AI-agents-driven проектов» (AGENTS.md обязателен, skills/, agents/, связь через Canonical AI-extensions, отдельная отметка в description).

### W45-34: Будущие субагенты atlas (TODO)

- [ ] **W45-34a** `atlas:bulk-onboarder` — обходит подкаталоги (например `Tests/*` или указанной папки), для каждого делает `atlas projects add` (минимально, только slug+type+status), затем делегирует `atlas:project-initializer` для актуализации метаданных. Сценарий: «онбордни всё в `Tests/`».
- [ ] **W45-34b** `atlas:doc-actualizer` — periodic (через scheduled task раз в N дней) пересматривает `description` всех active проектов на основе изменений в их `AGENTS.md` / `_project/docs/`. Чтобы метаданные не протухали. Триггер для запуска — заметная разница в файле AGENTS.md vs время `last_touched_at` в БД.
- [ ] **W45-34c** `atlas:module-promoter` — анализирует `nested .git/` в `_old_git_backups/` (W45-19), предлагает план поднятия модулей как отдельных проектов в `cifropro1/clients/<client>/<module>` etc. Использует git log + структуру папки + spec'и (если есть).
- [ ] **W45-34d** `atlas:test-classifier` — для Волны 5 (W5-01..04): проходит по всем `type=test` проектам, предлагает что архивировать / переводить в personal-utility / переименовывать. Делегирует `project-initializer` для каждого решения.

### W45-35: Atlas CLI — хук для запуска субагента

- [ ] **W45-35** Добавить в atlas CLI команду `atlas projects init-meta <slug>` которая:
   1. Проверяет что проект есть в БД и `local_path` существует.
   2. Запускает `atlas:project-initializer` через какой-то механизм (если Claude Code SDK доступен) или генерирует prompt-файл который пользователь скопирует в свою сессию для запуска через Agent tool.
   3. После завершения субагента — проверяет что БД обновилась.
   Требует исследования: как atlas (Python CLI) может запускать Claude Code субагент. Вероятно через `claude-code-sdk` (Python) или просто генерация prompt-файла.

### W45-36: Превратить atlas skill в local plugin (правильный путь по доке Claude Code)

- [ ] **W45-36** На 2026-04-28: личные skills в `~/.claude/skills/<name>/agents/` НЕ загружаются Claude Code как subagent_types. Documented поддерживаемые scopes: `~/.claude/agents/`, `.claude/agents/`, plugin's `agents/`, managed settings, CLI flag. Для namespace `atlas:project-initializer` нужен plugin. Пока (до миграции) субагент живёт как personal в `~/.claude/agents/atlas-project-initializer.md` (с префиксом `atlas-`, subagent_type = `atlas-project-initializer`), и в frontmatter указан `skills: [atlas]` для preload SKILL.md контента.

   План миграции в plugin (~30-45 мин когда дойдут руки):
   1. Создать структуру `~/.claude/plugins/atlas-local/atlas/0.1.0/` с `.claude-plugin/plugin.json` + `marketplace.json`.
   2. Перенести `~/.claude/skills/atlas/SKILL.md` → `<plugin>/skills/atlas/SKILL.md`.
   3. Перенести `~/.claude/agents/atlas-project-initializer.md` → `<plugin>/agents/project-initializer.md` (без префикса atlas, т.к. namespace добавится автоматически).
   4. Зарегистрировать в `~/.claude/settings.json` через `extraKnownMarketplaces.atlas-local` (source=directory) + `enabledPlugins."atlas@atlas-local": true`.
   5. После рестарта `subagent_type: atlas:project-initializer` будет работать нативно. Skill доступен как `atlas:atlas`.
   6. Удалить старые места (`~/.claude/skills/atlas/`, `~/.claude/agents/atlas-project-initializer.md`) только после verify что plugin загружается.

   **Почему стоит сделать**: на момент 2026-04-28 в backlog запланировано 4+ новых субагента (`bulk-onboarder`, `doc-actualizer`, `module-promoter`, `test-classifier`) + slash commands (`/atlas:init`, etc.) — все будут жить в одной plugin-папке. Plugin даёт version, namespace, единое управление, slash commands, hooks (через settings.json для plugin agents). Reference: [superpowers plugin structure](https://github.com/obra/superpowers).

   **Caveat (из доки Claude Code)**: plugin subagents НЕ поддерживают `hooks`/`mcpServers`/`permissionMode` в frontmatter (security). Если понадобится — копия в `~/.claude/agents/`.

### W45-39: Entity model refactor — `entity_kind` + урезание статусов [SPEC, 2026-04-29]

**Контекст** (от Дмитрия 2026-04-29): текущая модель Atlas смешивает «сущность» и «тип проекта» в одной колонке `project_types`. У Дмитрия в голове чистая декомпозиция:

| Измерение | Значения |
|---|---|
| **Сущность** (entity_kind) | inbox / idea / project (client пока через type=client-project; Волна 6 решит про parent_project_id) |
| **Статус** | active / paused / archived / cancelled / experiment |

Текущие 11 статусов (`idea`, `experiment`, `active`, `research`, `maintained`, `planned`, `dormant`, `graduating`, `archived`, `paused`, `frozen`) — половина не используется. И `idea` сидит как **status**, хотя по смыслу это **другая сущность** (живёт в `_Ideas/<slug>.md`, не в `_storage/<slug>/`).

Семантика:
- **inbox** = свалка сырья на разбор. AI читает и предлагает куда (idea / project / client / архив).
- **idea** = сформулированная мысль о продукте, ещё до решения «делать». 1 idea = 1 .md файл.
- **project** = полноценный проект (любой тип: business-product / personal-utility / personal-project / shared-infrastructure / test / client-project).

**Миграция 007** (план):

1. Добавить колонку `projects.entity_kind: VARCHAR(20) NOT NULL DEFAULT 'project'`.
   - CHECK CONSTRAINT: `entity_kind IN ('inbox','idea','project')`.
2. Backfill существующих: `UPDATE projects SET entity_kind='inbox' WHERE type_id=<inbox-type-id>`. Все остальные → `project` (default).
3. Удалить `inbox` из `project_types` seeds (теперь это entity_kind, не type). Проекты с прошлым `type=inbox` остаются как-есть в БД (тэг для миграции — переехать на новые `entity_kind`-проекты-инбокс), но новый `add` для инбокса делается через `atlas inbox add` без type.
4. Урезать `project_statuses` до 5: `active`, `paused`, `archived`, `cancelled` (новый), `experiment`.
   - Конверсия: `idea` (status) — этот статус выкидывается, проекты с ним получают `entity_kind=idea` + `status=active`.
   - `research`/`maintained`/`planned`/`graduating` → `active`.
   - `dormant`/`frozen` → `paused`.
5. Обновить `TYPE_TO_GROUP` в `paths.py`: убрать `inbox` (теперь entity_kind).

**Routing физики** (на основе entity_kind + type):
- `entity_kind=inbox` → `_Inbox/<slug>/` (file ИЛИ папка).
- `entity_kind=idea` → `_Ideas/<slug>.md` (один MD-файл, без папки).
- `entity_kind=project, type=client-project` → `Clients/<slug>/` (junction → `_storage/<slug>/`).
- `entity_kind=project, type=business-product/personal-*/shared-infra` → `Products/<slug>/`.
- `entity_kind=project, type=test` → `Tests/<slug>/`.

**CLI extension**:

- `atlas inbox add/list/show/triage` — управление inbox-материалами.
- `atlas ideas add/list/show/promote/demote` — см. W45-38.
- `atlas projects add/list/get/update/delete/...` — как сейчас, но фильтрует только `entity_kind=project` по умолчанию. Флаг `--all-kinds` для отображения всех.
- `atlas projects add --kind <inbox|idea|project>` (default `project`) — backward-compat способ создать любой kind.

**Tasks**:

- [ ] **W45-39a** Migration 007: `ALTER TABLE projects ADD COLUMN entity_kind`. CHECK constraint. Backfill.
- [ ] **W45-39b** Migration 008: урезание `project_statuses` (drop + reseed canonical 5 + конверсия существующих записей).
- [ ] **W45-39c** Обновить `models.py.Project.entity_kind` + seeds.
- [ ] **W45-39d** `paths.py`: `entity_kind_to_root()` хелпер (inbox → `_Inbox`, idea → `_Ideas`, project → TYPE_TO_GROUP routing).
- [ ] **W45-39e** Tests: миграция up/down, конверсия данных, routing.
- [ ] **W45-39f** `SKILL.md` atlas: новая секция «Entity model + Status palette».

**Открытые до реализации W45-39**:
- Add `entity_kind=client` сейчас или ждать Волну 6 (parent_project_id)? **Решение**: ждать Волну 6 — слишком много завязок (модули клиента, sub-repo, billing). Сейчас клиенты остаются как `entity_kind=project, type=client-project`.

---

### W45-38: Idea management — incubator для entity_kind=idea (стадия 0 проекта) [SPEC, 2026-04-29]

**Контекст** (от Дмитрия 2026-04-29): для проектов на стадии «идея» НЕ заводить полноценный `_storage/<slug>/` + junction. Вместо этого — единая папка `_Ideas/`, где каждая идея = один MD-файл, а общий `_Ideas/BACKLOG.md` собирает «idea-стадные» задачи всех идей вместе. Когда идея переходит в полноценный проект (promote) — Atlas автоматически (а) меняет `entity_kind: idea → project`, (б) создаёт `_storage/<slug>/` + junction, (в) перемещает MD-файл идеи внутрь как `IDEA.md`, (г) вытаскивает строки `_Ideas/BACKLOG.md` помеченные `#<slug>` и переносит в `_storage/<slug>/BACKLOG.md`. То есть **idea management = первый этап lifecycle**, до того как проект становится «полноценной» папкой.

**Зависит от**: W45-39 (entity_kind колонка) — должно быть реализовано до W45-38.

**Зачем**: сейчас в `Metela/New Projects/_project/docs/SCALING_PRODUCT/products/new/` лежат 5 спецификаций NP-001..NP-005 как полные документы. NP-001 уже promoted (есть `_storage/np-001-ai-ropchik/`), но в workspace всё ещё полная спека (должна быть STUB как у NP-004). NP-002/NP-003 — чистые идеи, ждут промоут. NP-005 — особый случай (это сам Atlas). Без явного incubator'а каждая идея либо превращается в полнокровный проект преждевременно (создаём `_storage/` под то, что ещё не решили делать), либо живёт где-то «вне Atlas» (как сейчас — в workspace-папке, не зарегистрирована в БД).

**Модель**:

```
~/Documents/PROJECT/
├── _Ideas/                                  # NEW логическая группа
│   ├── README.md                            # как заполнять (правила)
│   ├── BACKLOG.md                           # общий backlog: #<slug> tagging
│   ├── np-001-ai-ropchik.md                 # каждая идея = 1 MD
│   ├── np-002-b24-api-wrapper.md
│   ├── np-006-ai-sales-dashboard.md
│   └── ...
```

**Atlas БД**: идея = `Project` с `entity_kind='idea'` (новая колонка от W45-39). Отличия от `entity_kind='project'`:
- `local_path` = `<root>/_Ideas/<slug>.md` (один файл, не директория).
- НЕТ `_storage/<slug>/`, НЕТ junction'ов.
- `git_remote_url=NULL` — git-репо появляется только при promote.
- `status` — обычный (active/paused/archived/cancelled), не путать с прежним `status=idea`.

**Routing физики** (от W45-39): при `entity_kind=idea` маппинг через `entity_kind_to_root()` всегда возвращает `_Ideas/`, независимо от того какого type идея (`business-product` / `personal-utility` / `client-project`). Type фиксируется заранее в idea, чтобы при promote сразу было известно куда перенесёт.

**Backlog-format в `_Ideas/BACKLOG.md`** (правило заполнения):

```markdown
# Backlog по идеям (incubator)

## По идеям

### #np-002-b24-api-wrapper
- [ ] **P0** Решить: SDK + Wrapper или сразу Wrapper / решить нужен ли OAuth-flow на старте
- [ ] **P1** Ресёрч: какие методы Bitrix REST реально используются клиентами Cifro

### #np-003-marketplace
- [ ] **P0** Сформулировать «что продаётся» (sklady / готовые модули / advisory)
- [ ] **P1** Анализ конкурентов на marketplace.bitrix24.ru

## Общее (не привязано к конкретной идее)
- [ ] (idea-cross-cutting задачи)
```

Таги формата `#<slug-проекта>` обязательны для всех task'ов привязанных к идее. При promote — все строки между `### #<slug>` и следующим `### `/`## ` экстрактятся в новый `_storage/<slug>/BACKLOG.md`.

**CLI (предлагаемые)**:

```sh
# Создать идею (короткий путь)
atlas ideas add --slug <s> --name <n> --type business-product \
                --priority P2 --tag owner:cifro-pro --tag domain:...
# → запись в БД (entity_kind=idea, status=active), создаёт _Ideas/<slug>.md
#   из template (metadata: slug, type, priority, owner, created_at, one-line)

# Список / показать
atlas ideas list                 # фильтры --type / --tag / --status
atlas ideas show <slug>          # карточка БД + содержимое .md

# Промоут идеи в проект
atlas ideas promote <slug> [--status active] [--priority P0] \
    [--init-git] [--canonical/--no-canonical]
# 1. update БД: entity_kind=idea → entity_kind=project, last_touched_at=now
# 2. resolve type → group (TYPE_TO_GROUP)
# 3. mkdir _storage/<slug>/, junction <group>/<slug> → _storage/<slug>/
# 4. mv _Ideas/<slug>.md → _storage/<slug>/IDEA.md
# 5. extract_idea_backlog: строки между `### #<slug>` и след-секция →
#    _storage/<slug>/BACKLOG.md (с пометкой "Перенесено из _Ideas/BACKLOG.md DD")
#    из исходного _Ideas/BACKLOG.md эта секция удаляется
# 6. (опц --canonical) README.md / AGENTS.md / .gitignore (как в add)
# 7. (опц --init-git) git init + GitLab create + push
# 8. log_action: action=idea_promoted_to_project

# Откат
atlas ideas demote <slug>
# Обратное: entity_kind=project → idea, mv IDEA.md → _Ideas/<slug>.md,
# snять junction, перенести _storage/<slug>/ → _old_git_backups/.
# Требует confirm.

# Архивирование идеи (отказ)
atlas ideas update <slug> --status cancelled
# Просто статус → cancelled. Остаётся в _Ideas/, но не показывается в `list`
# без --all-statuses.
```

**Алгоритм `extract_idea_backlog(slug, backlog_path)`**:
- regex для секции: `^### #<slug>\s*$` до `^### #` или `^## ` (следующая ## или ###).
- Извлечь блок строк, обернуть заголовком и сноской на источник:
  ```markdown
  # Backlog (перенесено из _Ideas/BACKLOG.md 2026-MM-DD)

  <содержимое секции #<slug>>
  ```
- Из `_Ideas/BACKLOG.md` строки секции удалить, оставить пустую строку.

**Шаблон `_Ideas/<slug>.md`** (генерируется `atlas ideas add`):

```markdown
# {name}

> {one_line}

## Метаданные

- **Slug**: `{slug}`
- **Type-hint** (на промоут): `{type_slug}`
- **Priority**: {priority}
- **Status**: active
- **Owner**: {owner_tags}
- **Tags**: {tags_str}
- Создано: {date}

## Проблема / гипотеза

(заполнить — что за проблема, у кого, как сейчас решают, наша гипотеза)

## Целевой ICP

(кто пользователь, какой сегмент, размер рынка)

## MVP scope

(минимальный продукт-обещание для проверки)

## Decision criteria (что должно произойти, чтобы промоутнуть в project)

- [ ] (например, 3 кастомера сказали «дам предзаказ»)
- [ ] (или хотя бы 1 разговор с pilot-клиентом подтвердил problem-fit)

## Ресурсы

- ссылки, NotebookLM blocks, конкуренты, etc.
```

**Tasks (зависят от W45-39)**:

- [x] **W45-38a** ✅ 2026-04-29. `_Ideas/` (root) + `README.md` (canon заполнения) + `BACKLOG.md` (template) — auto-create через `ensure_ideas_root()` при первом `atlas ideas add`.
- [x] **W45-38b** ✅ 2026-04-29. `atlas ideas {add,list,show,promote,demote,update}` в `commands/ideas.py`. Аналогично `atlas inbox {add,list,show}` в `commands/inbox.py`.
- [x] **W45-38c** ✅ 2026-04-29. `pm/ideas.py::extract_idea_backlog()` — regex `### #<slug>`, возвращает `(extracted_block, remaining_text)`.
- [x] **W45-38d** ✅ 2026-04-29. `pm/ideas.py::render_idea_md()` — IDEA.md из шаблона (Метаданные, Проблема, ICP, MVP, Decision criteria, Ресурсы).
- [x] **W45-38e** ✅ 2026-04-29. `commands/ideas.py::promote_cmd` оркестрирует update БД + setup_layout + canonical + extract_backlog + opt git init. Через существующие helpers `_setup_storage_and_junction` + `_create_canonical_files` + `perform_git_init`.
- [x] **W45-38f** ✅ 2026-04-29. 20 кейсов в `test_pm_ideas.py` + `test_pm_ideas_cli.py`: extract_idea_backlog (simple/EOF/missing/structure), render_idea_md, ensure_ideas_root idempotent, write_idea_md overwrite-protection, ideas add/list/show/promote/update CLI.
- [x] **W45-38g** ✅ 2026-04-30. Миграция `Metela/New Projects/` выполнена:
   - **NP-002** → `_Ideas/np-002-b24-api-wrapper.md` (полное содержимое, 5.4 КБ).
     В workspace — `NP-002_BITRIX_API_WRAPPER.MOVED.md` stub.
   - **NP-003** → `_Ideas/np-003-marketplace-integrations.md` (3.9 КБ).
     В workspace — `NP-003_MARKETPLACE_INTEGRATIONS.MOVED.md` stub.
   - **NP-001** (already promoted в `_storage/np-001-ai-ropchik/`) → workspace заменён на `NP-001_AI_ROPCHIK.MOVED.md` stub. Полная спека уже в `_storage/np-001-ai-ropchik/SPEC.md`.
   - **NP-005** (Personal PM Infrastructure) → весь каталог переехал в `_storage/atlas/_project/docs/SCALING_PRODUCT/products/new/NP-005_Personal_PM_Infrastructure/` (где он логически и должен быть — это спека самого atlas).
   - **PROJECT_LOG/, marketing/, competitive/, sales/, templates/, inbox/, library/, DATA_DICTIONARY.md, AGENTS.md** → новый проект `cifro-pro-workspace` (type=`shared-infrastructure`, owner:`cifro-pro`, status=`active`, P0). Зарегистрирован в Atlas-БД. Junction `Products/cifro-pro-workspace/` → `_storage/cifro-pro-workspace/`. README + AGENTS canonical написаны.
   - **Корневой `~/Documents/PROJECT/AGENTS.md`** создан — portfolio entry-point для AI с картой 8 групп + 4 entity_kind + 5 статусов + workflow inbox→idea→project + правило cwd.
   - Папка `Metela/` остаётся пустой (cwd-lock текущей сессии Claude Code) — удалится в следующей сессии или вручную.
- [x] **W45-38h** ✅ 2026-04-30. SKILL.md atlas обновлён: добавлены §3.13 (Entity model), §3.14 (Idea management — `atlas ideas`), §3.15 (Inbox management — `atlas inbox`), §3.16 (CWD: portfolio vs project — правило для AI). Файл: `~/.claude/skills/atlas/SKILL.md`.

---

## ВОЛНА 5 — Tests актуализация (2026-04-28, отдельный sprint) [DONE]

**Цель**: разобрать 19 проектов в `Tests/`, перевести нужные в продакшн-проекты, остальные архивировать.

- [x] **W5-01** Анализ каждого test'а — выполнено, классификация распределена.
- [x] **W5-02** Решения по каждому: 7 объединены в продукты (ai-prodazhnik, b24-export-calls, baza-znaniy, docs-sandbox → np-001 / ai-knowledge-conveyor; antibot-defense-report → playwright-network-dump); 3 переведены в personal-utility (fin-analitik, docs-parsing, notion-api-b24); 7 удалены или заархивированы (specification, bitrix-booking-hidden-api, bitrix-sdk-probe, notebooklm-bundle, playwright-cli, skills-test, lightrag).
- [x] **W5-03** Renaming для readability — выполнено через subagent atlas:project-initializer.
- [x] **W5-04** Обновление GitLab repos: 10 архивных клиентов перенесены в `cifropro1/archive/clients/` и `zzztejletty3ukzzz/archive/clients/`. Tests/ subgroup — синхронизирован.
- [x] **W5-05** Cleanup `_old_git_backups/`: удалены 9 «-deleted-» бэкапов (1.4 ГБ); оставлены 3 «-merged-» (159 МБ) как safety.
- [x] **W5-06** Cleanup открытых вопросов от subagents (2026-04-28):
  - `np-001` calls/*.mp3 (3.9 МБ, 7 файлов) → exception `!calls/*.mp3` в `sources/b24-export-calls/.gitignore` для fixtures.
  - `ai-knowledge-conveyor`: удалены `legacy_baza_znaniy/.git` (130 МБ) + `legacy_baza_znaniy/src/.venv` (148 МБ) + `docs_sandbox/.git` (123 КБ) + `notebooklm-mcp/{node_modules,deploy-package,dist}` (308 МБ). Итог: **547 МБ → 0.7 МБ**.
  - NotebookLM Gemini reference (`88db97aa-…`) в `sources/ai-prodazhnik/AGENTS.md` — оставлен как исторический snapshot входного материала.

---

## ВОЛНА 6 — Канон проекта + Модульность + Подрепо (2026-05+, большая работа)

**Цель**: разработать единый канон структуры любого проекта в портфеле, ввести модульность (parent_project_id), реализовать схему подрепо — каждый модуль клиента = отдельный репо в subgroup.

- [ ] **W6-01** Спецификация «Канон проекта» — что обязательно в каждом проекте:
  - `AGENTS.md` (кто работает над этим проектом + правила)
  - `_project/docs/{PROJECT_LOG, SCALING_PRODUCT}` структура (как в Metela/New Projects)
  - `pyproject.toml` (для Python) / `package.json` (для JS) — зависимости
  - `README.md` — обзор + quickstart
  - `.gitignore` — расширение atlas universal template
  - Тесты (если код)
- [ ] **W6-02** Атласа миграция 007 — `parent_project_id` в `projects`: модули клиента ссылаются на parent client-project.
- [ ] **W6-03** Atlas миграция 008 — `module_kind` enum (main / submodule / shared / docs).
- [ ] **W6-04** Команды `atlas modules add/list/move/promote-to-product` — управление модулями в pareнте.
- [ ] **W6-05** Реальная разборка nested .git из `_old_git_backups/` (35+ репо) — каждый поднять как отдельный repo в правильной subgroup.
- [ ] **W6-06** Bulk скрипт миграции существующих монолитных клиентов (Bankety/NL/Shuklin/Med-Persona) на модульную схему.
- [ ] **W6-07** SKILL.md: новая секция «Модульная архитектура».
- [ ] **W6-08** Шаблоны `templates/project_canon/` — генерация скелета проекта одной командой `atlas projects scaffold`.

---

## ВОЛНА 7 — Работа с задачами по проектам (2026-06+)

**Цель**: атлас как инструмент управления **задачами** по всем проектам (клиентским, продуктам, личным). Сейчас в БД есть таблица `pm_tasks` (миграция 002+), но не используется на практике.

**Терминология**: «задачи по проектам» — это task'и в любом из 32+ проектов БД (включая модули из Волны 6). См. memory/terminology_projects.md.

- [ ] **W7-01** Onboard pm-tasks (atlas pm-tasks add) — для активных проектов (Med Persona, NL, Atlas) проанализировать существующие BACKLOG/TODO и создать задачи в БД atlas.
- [ ] **W7-02** Notion-mirror — синхронизация atlas pm-tasks ↔ Notion-задачник «Прагмат» (двусторонняя или односторонняя?).
- [ ] **W7-03** Команды `atlas pm-tasks daily-plan` / `weekly-review` — генерация daily/weekly plan по приоритетам и deadlines.
- [ ] **W7-04** Интеграция с Bitrix24 для клиентских проектов: `atlas pm-tasks sync --to b24` (пуш задачи в B24).
- [ ] **W7-05** AI-PM эксперимент — prompt который читает БД atlas + action_log и предлагает sprint plan (см. W6-04 в исходном backlog).

---

## ВОЛНА 5 — Sprint 2 + Onboarding (2026-05-17 → 2026-05-31)

**Note (v4 BACKLOG)**: сюда же переехали onboarding-пункты из W3 (S1-06, S1-07, S1-08) — onboarding всего портфеля теперь делается **после** Tags & Archive Engine (W4), чтобы не проходить по проектам дважды.

- [ ] **S2-01** Полный onboarding всех оставшихся проектов портфеля (включая присвоение тегов owner/stack/domain по W4).
- [ ] **S2-01a** Onboard 4 клиента (Ferrum, KSO, Bankety, Kasha) с полным AGENTS.md + теги + правильный тип. [перенесено из S1-06]
- [ ] **S2-01b** Onboard 4 утилиты (fin_analitik, notion-api-b24, lightrag, AI Prodazhnik) с тегами. [перенесено из S1-07]
- [ ] **S2-01c** Onboard оставшиеся бизнес-продукты (NP-001, NP-002, NP-003, NP-004) с тегами. [перенесено из S1-08]
- [ ] **S2-02** FastAPI реализация (не production-ready, для локального multi-agent тестирования).
- [ ] **S2-03** API-токены per-participant.
- [ ] **S2-04** Первый эксперимент с AI-PM агентом: пишем prompt, который читает БД и генерирует draft sprint plan на следующий спринт. Сравниваем с тем, что сделал бы Дмитрий.
- [ ] **S2-05** Migration testing: backup `portfolio.db` → test migration 004 → verify data integrity.
- [ ] **S2-06** Решение о платформе мультиагентности (OpenClaw vs paperclip vs другая) на основе research v2 Блок D.

### Новые инструменты онбординга / distribution

- [x] **S2-07** Физический move 9 клиентов в `PROJECT/Clients/` (2026-04-24). Med Persona не тронут — в работе. Перетяжка ↔ Shuklin транслитерированы в `Peretyazhka` / `Shuklin`.
- [x] **S2-08** Onboarding subagent pattern — добавлен в skill `atlas` как §3.12. Агент сам читает AGENTS.md/README/docs/pyproject → предлагает slug/prefix/name/description/tags → запускает `atlas projects add ...`.
- [ ] **S2-09** Новый skill `atlas-task-distribution` — отдельный навык который после онбординга проекта проходит по его `_project/docs/BACKLOG.md` / `README.md` / `TODO.md` / issues и создаёт `pm-tasks add` batch'ем с ЦКП. Триггеры: «раскинь задачи по проекту X», «собери backlog из docs». Отдельный skill, не часть atlas.md — slug пока `atlas-task-distribution`, если появится более точное название — переименуем.
- [ ] **S2-10** Команда `atlas projects onboard <path>` — shortcut CLI, который внутри: spawns onboarding subagent (см. S2-08), получает предложение, показывает пользователю, после confirm выполняет `atlas projects add ...`. Reduces trenне от manual в большинстве случаев.
- [ ] **S2-11** `atlas projects bulk-onboard <dir>` — пройти по всем папкам внутри `dir` (`Clients/` или `Products/`), онбордить те что не в БД, пропускать уже существующие. С `--dry-run`.

---

## ВОЛНА 6 — v0.7 Multi-agent groundwork (Q3 2026)

- [ ] **V07-01** Миграция на PostgreSQL (если concurrent writes становятся узким местом).
- [ ] **V07-02** Подключение AI-PM агента в production: ежедневный cron-джоб читает action_log → генерит sprint-progress report.
- [ ] **V07-03** Интеграция с выбранной мультиагентной платформой.
- [ ] **V07-04** Добавление ролей: AI-CEO, AI-Marketing, AI-Knowledge (= NP-004), AI-QA.
- [ ] **V07-05** Inter-agent коммуникация через API.

---

## ВОЛНА 7 — v1.0 Full multi-agent (Q4 2026)

- [ ] **V10-01** Все 7 AI-ролей активны и пишут в PM.
- [ ] **V10-02** Burn rate под контролем (expense-report автоматический, алерты).
- [ ] **V10-03** Дмитрий больше не пишет код вручную — только approve.
- [ ] **V10-04** AGENTS.md шаблоны и compound-engineered rules покрывают 95% кейсов.

---

## ВОЛНА 8 — Multi-Agent Concurrency: Lease/Claim блокировка задач (2026-06-23 → ~5-7 дней) [SPEC]

> **Провенанс**: намайнено из репозитория [gastownhall/beads](https://github.com/gastownhall/beads) (issue-tracker, заточенный под рои AI-агентов) 2026-06-23. Запрос Дмитрия: «фича блокировки задач — когда агент берёт задачу в работу, остальные видят, что она занята, и не дублируют/не затирают друг друга». Полный gap-анализ — многоагентный workflow `beads-atlas-mining` (8 отчётов + синтез).
>
> **Важное уточнение терминов beads** (чтобы не скопировать не то): в beads `docs/EXCLUSIVE_LOCK.md` (`.beads/.exclusive-lock`, PID+hostname) — это блокировка **файла БД** от Dolt-сервера, к координации агентов отношения почти не имеет → **skip** (у Atlas другой бэкенд). Реальная «блокировка задачи» в beads — это **claim**: `ClaimIssue(id, actor)` ставит `assignee` + `status=in_progress` атомарным compare-and-swap одним SQL-UPDATE с WHERE-guard (`internal/storage/issueops/claim.go`). Проигравший гонку получает `ErrAlreadyClaimed`. **Но у beads НЕТ TTL/lease/heartbeat** — зависшие claim'ы ищутся вручную (`bd stale` по `updated_at`). Atlas берёт идею CAS, но проектирует **настоящий lease с протуханием** — то есть делает лучше первоисточника.

**Цель**: дать Atlas механизм, которого сейчас нет совсем (подтверждено по коду — нет полей lease/claim/lock/version; `pm/commands/pm_tasks.py` при переходе в `in_progress` проверяет только `started_at IS NULL` → два агента переведут одну задачу в работу без конфликта; `assignee_id` перезаписывается вслепую). Это фундамент под будущую мультиагентность (пул AI-ролей — см. V07-04 / V10-01) и под Волну 9 (ready-очередь).

**Ключевое решение** (отличие от beads, НЕ копия): Atlas вводит TTL-lease (`lease_owner` + `lease_expires_at` + `claimed_at`) + optimistic locking через `lock_version`-колонку, потому что Atlas — единственный слой, закрывающий дыру двойного захвата.

**Инвариант синка** (критично): lease-поля и `lock_version` — это **локальная** координация Atlas-портала, в ядро (outbox/`sync/mapper.py`) **НЕ уходят**. Иначе протухание lease на одной машине затрёт состояние на другой через LWW. Lease живёт и умирает локально.

### Раздел 1 — Lease/Claim (ядро волны)

- [ ] **W8-01** Новая Alembic-миграция (`tasks_lease_optimistic_lock`): `ALTER TABLE tasks ADD COLUMN lease_owner VARCHAR(36) NULL` (логический FK на `participants.id`), `lease_expires_at DATETIME NULL`, `claimed_at DATETIME NULL`, `lock_version INTEGER NOT NULL DEFAULT 0`. Partial-индекс `idx_tasks_lease (lease_owner, lease_expires_at)`. Down-миграция дропает 4 колонки. Backfill не нужен (NULL/0).
- [ ] **W8-02** `src/atlas/pm/models.py::Task`: добавить поля `lease_owner`, `lease_expires_at`, `claimed_at`, `lock_version`. НЕ добавлять в CheckConstraint статусов (lease ортогонален статусу). Добавить индекс в `__table_args__`.
- [ ] **W8-03** `src/atlas/pm/lease.py` (pure-logic, detached от typer, unit-testable). `claim_task(session, task, actor_id, ttl)` — атомарный compare-and-swap одним UPDATE:
  ```sql
  UPDATE tasks SET lease_owner=:actor, lease_expires_at=:now+ttl,
                   claimed_at=COALESCE(claimed_at,:now), lock_version=lock_version+1
  WHERE id=:id AND lock_version=:expected_version
    AND (lease_owner IS NULL OR lease_expires_at < :now OR lease_owner=:actor)
  ```
  Проверить `rowcount==1`; иначе перечитать и поднять `LeaseHeldError(holder, expires_at)`. Идемпотентность: повторный claim тем же `actor_id` → success (для retry агента — паттерн beads `ClaimIssueIfOpen`).
- [ ] **W8-04** `lease.py`: `release_task(session, task, actor_id)` — снять lease, только если держатель == actor (иначе `LeaseNotOwnedError`). `renew_lease(session, task, actor_id, ttl)` — продлить `lease_expires_at` (heartbeat долгой работы). `take_task(session, task, actor_id, force=True)` — принудительный отбор протухшего/чужого lease с обязательной записью в ActionLog.
- [ ] **W8-05** Optimistic locking на ЛЮБОЙ апдейт: в `pm/commands/pm_tasks.py::update_cmd` инкрементировать `lock_version` при каждой мутации Task; апдейт с `WHERE lock_version=:expected` — при `rowcount==0` поднять `OptimisticLockError` (кто-то изменил параллельно). Защищает не только claim.
- [ ] **W8-06** CLI (группа `atlas task`, ед. число, `--json` по умолчанию — канон Atlas):
  - `atlas task claim <ref> [--ttl 2h] [--actor <slug>]` — взять задачу; по умолчанию actor = текущий профиль / `ai_agent`.
  - `atlas task release <ref> [--actor <slug>]` — отпустить.
  - `atlas task renew <ref> [--ttl 2h]` — продлить lease (heartbeat).
  - `atlas task take <ref> --force` — отобрать протухший/чужой lease (с confirm + ActionLog).
- [ ] **W8-07** Все lease-операции пишут в `ActionLog` (`action=task_claimed|task_released|lease_renewed|task_taken|lease_expired`, `actor_id`, `details_json` с прежним/новым держателем). Переиспользуем существующий append-only слой аудита Atlas (аналог event-лога `claimed` в beads).
- [ ] **W8-08** Инвариант синка: убедиться, что `sync/mapper.py::_task_payload` НЕ сериализует `lease_owner/lease_expires_at/claimed_at/lock_version`. Тест: `atlas task claim` НЕ создаёт запись в `outbox`.
- [ ] **W8-09** TDD (RED-GREEN, ожидаемо +25-35 тестов):
  - гонка двух `claim` на одну задачу → ровно один выигрывает, второй получает `LeaseHeldError`;
  - claim задачи с протухшим lease (`lease_expires_at < now`) → успех, прежний держатель вытеснен;
  - идемпотентный повторный claim тем же actor → success без ошибки;
  - `take --force` отбирает lease + пишет ActionLog;
  - `release` чужого lease → `LeaseNotOwnedError`;
  - параллельный `update` при устаревшем `lock_version` → `OptimisticLockError`;
  - claim НЕ попадает в outbox.

### Раздел 2 — Авто-релиз протухших lease (то, чего нет у beads)

- [ ] **W8-10** `lease.py::expire_stale_leases(session)` — `UPDATE tasks SET lease_owner=NULL, lease_expires_at=NULL WHERE lease_expires_at < now AND lease_owner IS NOT NULL`; вернуть список освобождённых, залогировать `lease_expired` в ActionLog. Детерминированно по `lease_expires_at` (а не эвристикой по `updated_at`, как `bd stale`).
- [ ] **W8-11** CLI: `atlas task stale [--reap]` — без `--reap` показывает протухшие lease (отчёт); с `--reap` освобождает.
- [ ] **W8-12** Ленивый reaper: вызывать `expire_stale_leases()` в начале `atlas task claim` и `atlas task list` (без фонового демона — single-file SQLite, дёшево чистить «по дороге»).
- [ ] **W8-13** TDD: протухший lease освобождается; свежий не трогается; reap логируется.

### Раздел 3 — Conflict-resolution синка (закрыть обещанный F3 §12.1 LWW)

- [ ] **W8-14** `src/atlas/pm/sync/apply.py`: перед `setattr` сравнивать `occurred_at` входящего события с локальным `task.updated_at` — НЕ перезаписывать более свежую локальную правку (реализовать обещанный, но не сделанный LWW-по-`occurred_at`; сейчас apply.py перезаписывает вслепую). Урок из `SyncEngine`/Linear beads (last_sync watermark + idempotency-маркер).
- [ ] **W8-15** Прокинуть `occurred_at`/`updated_at` в payload pull-канала (если ядро его шлёт; иначе использовать `updated_at` из payload).
- [ ] **W8-16** TDD: входящее устаревшее событие не затирает свежую локальную правку задачи.

**Выход Волны 8**: задачу нельзя взять дважды; протухший lease авто-освобождается; любой апдейт защищён optimistic-lock; lease не протекает в ядро; устаревший pull не затирает локальное. Atlas готов к Волне 9 (пул AI-ролей).

---

## ВОЛНА 9 — Граф зависимостей задач + ready-очередь для пула AI-ролей (2026-06-30 → ~5-7 дней) [SPEC, зависит от W8]

> **Провенанс**: майнинг beads 2026-06-23 (`docs/DEPENDENCIES.md`, `docs/MOLECULES.md`, `bd ready`). Закрывает открытый вопрос `MODEL.md:637` (таблица зависимостей задач в Atlas не реализована — есть лишь статус `blocked` как ярлык без связи на блокирующую задачу).

**Цель**: дать оркестратору ролей детерминированную очередь «что готово делать прямо сейчас», чтобы AI-роли тянули разные незаблокированные задачи и не простаивали. Это вторая половина anti-collision системы beads: агенты тянут из общей `ready`-очереди и атомарно клеймят (W8) — каждому достаётся своя задача.

**Решение** (adapt, не копия beads): берём подмножество — только рёбра `blocks` + `parent-child` (`epic_id` уже есть), без `conditional-blocks/waits-for/wisp/gate`. Готовность материализуем в колонку `is_blocked` (дешёвый `ready` без рекурсии на каждый запрос — как `issues.is_blocked` в beads).

- [ ] **W9-01** Новая Alembic-миграция (`task_dependencies_is_blocked`): таблица `task_dependencies (task_id, depends_on_id, type CHECK IN('blocks','parent-child'), PK(task_id,depends_on_id,type))` + колонка `tasks.is_blocked INTEGER NOT NULL DEFAULT 0`.
- [ ] **W9-02** `models.py`: класс `TaskDependency` + `Task.is_blocked`.
- [ ] **W9-03** `src/atlas/pm/deps.py`: `add_dep` с проверкой цикла (рекурсивный CTE по `blocks`), `remove_dep`, `recompute_is_blocked(session, affected_ids)` — пересчёт только по затронутому подграфу (BFS до fixpoint, как в beads), без бампа `updated_at` (derived state).
- [ ] **W9-04** Триггер пересчёта: при `close/reopen` задачи и при add/remove зависимости — `recompute_is_blocked` для зависимых.
- [ ] **W9-05** CLI: `atlas task dep add <ref> <depends-on>`, `atlas task dep rm`, `atlas task dep list`, `atlas task blocked`, `atlas task ready [--unassigned] [--priority P0]`.
- [ ] **W9-06** `atlas task ready --claim` — атомарно взять первую готовую незанятую задачу (паттерн `ClaimReadyIssue` beads: при проигрыше гонки перейти к следующей). Соединяет Волну 8 (claim) и Волну 9 (ready) в автономный цикл агента.
- [ ] **W9-07** Решить: синкать ли `task_dependencies` в ядро. По умолчанию НЕТ на v1 (локальный граф координации, как lease).
- [ ] **W9-08** TDD: цикл отклоняется на запись; закрытие блокера разблокирует зависимую (`is_blocked→0`); `ready` возвращает только незаблокированные; `ready --claim` в гонке не плодит дублей.

**Выход Волны 9**: автономный цикл AI-роли — `atlas task ready --claim → работа → atlas task update --status done → ядро разблокирует зависимые`. Несколько ролей работают параллельно без затирания.

---

## Прочее из beads — отложено / отклонено (решения зафиксированы 2026-06-23)

Чтобы не возвращаться к разбору повторно — вердикты по остальным подсистемам beads:

| Фича beads | Вердикт | Почему |
|---|---|---|
| Append-only audit с random-ID (crypto/rand) для параллельных писателей | **later (P3)** | `ActionLog` уже закрывает аудит; autoincrement PK для single-file SQLite на портфеле одного человека — не узкое место. Random-ID рассмотреть, если реально упрёмся в contention. |
| Execution-hints в JSON-`metadata` задачи (модель/effort/agent_type для субагента) | **later (P3)** | Полезно для роутинга задач по ролям (Волна V10), но не нужно для блокировки. Дёшево добавить позже одной JSON-колонкой. |
| Event-хуки `on_create/on_update/on_close` (скрипты в `.beads/hooks/`) | **later (P3)** | Удобная точка расширения (напр. уведомление в Telegram при claim, триггер синка), но не относится к блокировке. |
| Формулы / молекулы (шаблоны процессов, авто-разворачивание под-задач) | **later (P3)** | Интересно для повторяемых процессов, но преждевременно. |
| Dolt-бэкенд, `refs/dolt/data` sync, cell-merge, federation, merge-slot, advisory-флок на файл БД, `.exclusive-lock` | **skip** | Решения под распределённый VCS-бэкенд и multi-writer dolt-сервер. У Atlas другой бэкенд (single-file SQLite + hub-and-spoke outbox в ядро). SQLite сам даёт атомарность транзакций; координацию агентов на машине решает lease (W8), кросс-машинное — ядро-хаб. |
| Content-hash ID (sha256), адаптивная длина, nonce-коллизии | **skip** | `Task.id` уже UUID (нет гонки за счётчик); `Task.number` — косметика, его коллизия решается unique-constraint+retry. Контентный хеш нужен в распределённом VCS без центра; у Atlas центр есть (ядро присваивает `backend_id`). |
| Compaction (AI-суммаризация старых задач), gc, prune, wisps/ephemeral | **skip** | Мотивация (экономия context-window агента) и wisps специфичны для роёв beads. У Atlas есть `archived_at` + backup-движок; объёмы малы. |

---

## Determination of Done для каждой Волны

### Spike v0.4 (Волна 2)
- [ ] 3 проекта в `portfolio.db`, seed данные работают.
- [ ] Одна реальная фича (`portfolio push`) прошла полный Superpowers workflow.
- [ ] Все MVP CLI-команды работают на test-data.
- [ ] Retro записан, выводы применены к Sprint 1 плану.

### Sprint 1 (Волна 3)
- [ ] Scrum ceremonies прожиты (Sprint 2 в середине).
- [ ] `expense report` показывает реальный burn rate.
- [ ] Notion mirror полностью работает.
- [ ] Onboarding — **перенесён в W5** (см. Note в W3).

### Tags & Archive Engine (Волна 4)
- [ ] Миграция 004 применена, тесты GREEN (+60-80 тестов).
- [ ] Все CLI-команды tags работают: add/list/get/update/delete.
- [ ] Все CLI-команды archive engine работают: archive/unarchive/renew/move/reorganize.
- [ ] Seed ~30 тегов (owner/stack/domain).
- [ ] Smoke test полного цикла: add project with tags → archive → unarchive → renew.

### Sprint 2 + Onboarding (Волна 5)
- [ ] Весь портфель в БД (с тегами owner/stack/domain).
- [ ] 8+ проектов onboarded (все приоритетные клиенты + все NP + 5 утилит).
- [ ] FastAPI работает локально.
- [ ] AI-PM prompt draft сравним с планом Дмитрия.
- [ ] Решена платформа мультиагентности.

---

## Риски + митигации

| Риск | Митигация |
|---|---|
| Research v2 занимает 3+ дня и блокирует Spike | Spike не ждёт research полностью — запускаем параллельно SP-01..05 без ответов research |
| Миграция БД сломается в Sprint 1 | Alembic test-migrations на dump'е, downgrade скрипты |
| AGENTS.md + Superpowers конфликтуют (плагин ожидает CLAUDE.md) | Делаем тонкий `CLAUDE.md` → `# See AGENTS.md`, плагин доволен, канон — AGENTS.md |
| Мультиагентная платформа не нравится после research | Задерживаем v0.7 на 1 месяц, не ломает Sprint 1/2 |
| 14 задач Spike за 7 дней — overload | SP-16 (Superpowers pilot) может переехать в Sprint 1 если не успеваем |
| ЦКП формулировки theater, а не реальная ценность | Проверка на sprint review — "если эта задача не сделалась, клиент/я заметил?" |

---

## NOT IN SCOPE

### Не делаем в Spike
- Web-dashboard, UI.
- Полная миграция всех проектов (только 3 pilot'а).
- Мультиагентная платформа (только спроектировано).
- Production PostgreSQL.
- SSO, enterprise auth.

### Не делаем даже в Sprint 2
- Публичный SaaS-релиз NP-005.
- Интеграция с Jira / Asana / Linear (они заменены нашей PM).
- Собственный Bitrix24 wrapper внутри NP-005 (это — NP-002 отдельный продукт).
- Финансовый учёт за пределами expenses (налоги, счёта клиентам — не наша задача).
