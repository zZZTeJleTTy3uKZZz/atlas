# Changelog

## 0.3.4 — changelog в релизах, чистая установка навыка, онбординг с проверкой CLI

**Доставка changelog (#926)** — способ ведения `CHANGELOG.md` не меняется,
механизируется только доставка. Раньше описание релиза не доходило никуда:
теги на github были lightweight, GitHub Releases не создавались (0 на 12 тегов),
на PyPI не было даже ссылки.
- `scripts/release_notes.py` — извлекает секцию версии; один и тот же текст идёт
  в аннотацию тега, в GitHub Release и в проверку CI.
- Тег на github теперь **аннотированный**, с телом секции (`publish_public_github.sh`),
  причём с `--cleanup=verbatim`: по умолчанию `git -F` вырезает строки на `#`,
  то есть заголовок версии молча пропадал бы из каждого тега.
- **GitHub Release** создаётся тем же скриптом через Releases API (первый релиз
  на 13 тегов). Шагом в `.github/workflows/publish.yml` это сделать нельзя:
  публикация в github идёт PAT-ом без scope `workflow`, и GitHub отвергает пуш
  целиком, если в нём меняется хоть что-то в `.github/workflows/`. Повторный
  прогон тега обновляет существующий релиз, а не плодит второй.
- `[project.urls]`: `Changelog`, `Release Notes`, `Issues` — PyPI показывает их
  отдельными ссылками с иконками (PEP 753).
- Джоба **`release-guard`** в GitLab CI падает ДО публикации, если версия
  разъехалась (`pyproject` / `__init__` / манифест навыка / pin `atlas-pm` / тег)
  или в `CHANGELOG.md` нет секции для тега. Версии на PyPI неизменяемы —
  ловить рассинхрон нужно до, а не после.
- `atlas update --check` отдаёт `release_notes_url` и печатает «что нового»:
  `CHANGELOG.md` в wheel не попадает, а страница PyPI показывает README.

**Установка навыка ставит только навык (#936)** — `skills/atlas/.skillignore`.
Репозиторий монорепный (CLI в `src/`, навык в `skills/atlas/`), хаб знает про
подпапку, но его опись файлов перечисляет пути от корня — поэтому на update в
папку навыка налипали `src/`, `pyproject.toml`, `install/` и даже копия
`skills/atlas/` внутри самого навыка.
- Выбран IGNORE-режим, а не allowlist `files:` во frontmatter: allowlist удаляет
  всё неперечисленное, а per-skill `.venv/` (65 МБ, из него работает CLI-шим
  `atlas`) не защищён — `preserved_paths` skillkit хардкодит и из манифеста не
  читает. Первый же update снёс бы venv вместе с рабочей командой.
- `tests/test_skill_payload.py` роняет сьют, если в репозитории появился новый
  верхнеуровневый каталог, не учтённый фильтром.

**Онбординг навыка (#941)** — секция `[onboarding]` дополнена тем, чего не
хватало агенту, чтобы довести установку до конца:
- **шаг 0** — проверка `atlas --version` и что делать, если CLI не встал: у
  tooling-навыка пакет ставит skillkit, и он этого не может, когда в системе нет
  ни uv, ни pipx, ни pip. Даны команды установки менеджера под Windows / macOS /
  Linux и предупреждение про PATH в уже открытой оболочке;
- **шаг 5a** — бэкап портфеля (`backup run` / `status` / `schedule install`)
  сразу после подключения git-хостинга: без remote бэкапить некуда;
- контракт секции закреплён тестами (`tests/test_skill_onboarding.py`), включая
  запрет хардкодить версию в тексте.

## 0.3.3 — чистка автогена бэкапа, синхронизация AGENTS.md (#921)

**Бэкап (fix)** — `scripts/backup/backup_headless.vbs` больше НЕ хранится в git:
- Файл автогенерируется `register_task.ps1` и содержит абсолютные пути конкретной
  машины (`C:\Users\<…>\…`), поэтому после переезда репозитория
  `Products/atlas` → `_storage/atlas` закоммиченная копия указывала на
  несуществующий путь и давала вечный «грязный» diff. Теперь он в `.gitignore` —
  рядом с таким же артефактом `scripts/sync_watch_headless*.vbs`.
- `register_task.ps1` пишет тело VBS ASCII-safe (комментарии латиницей): генератор
  сохраняет файл в ASCII, из-за чего кириллица вырождалась в `?????`.

**Документация** — раздел «Atlas — ведение задач» в `AGENTS.md` приведён к
актуальному CLI: `task triage` в начале сессии, пул `backlog` (add/list/convert)
вместо сырых idea/inbox, опциональная приёмка (`submit`/`approve`/`reject`),
передача задачи агенту (`task handoff` + шаблон issuekit).

Плюс убрана жёстко прописанная версия из `README.md` («сейчас `0.3.0`» при
фактических 0.3.2) — вместо неё ссылка на этот файл. README — тело страницы
PyPI, так что рассинхрон был публичным.

Изменений в коде CLI нет — API и поведение команд идентичны 0.3.2.

## 0.3.2 — работа из коробки, onboarding навыка, hotfix guard entity_kind

**Из коробки (#899)** — atlas больше не требует настраивать владельца:
- `AtlasConfig.owner` по умолчанию **`admin`**: на чистой установке `project init`
  сам заводит участника-владельца, команды не требуют `--owner/--actor`.
- Сменить владельца: `atlas config set owner <slug>` (+ `atlas person add --slug
  <slug> --kind human --name "…"`); явно заданный owner по-прежнему побеждает дефолт.

**Onboarding навыка (#900)** — секция `[onboarding]` в `_skill_meta.toml`
(контракт skillkit: `summary`/`next_steps`/`docs`, печатается инсталлятором):
обязательные шаги — папка-хранилище портфеля (`config set projects_root`) и
`atlas project init`; опционально — свой GitLab/GitHub namespace (`git init`/
`git link` для репо с историей), кастомные типы проектов, вложенность-модули
(`--parent`), онбординг ИИ-агента (`atlas setup`).

**Hotfix регрессии 0.3.1 (guard entity_kind из #894)**
- Гейт «не проект портфеля» отвергает ТОЛЬКО явные `idea`/`inbox`. Было строгое
  `!= "project"`, которое отвергло бы и запись с `entity_kind = NULL`.
- **`project get` теперь отдаёт `entity_kind`** — раньше поля не было в карточке,
  оно читалось как `None`, и отказ `task add` было невозможно продиагностировать
  (легко принять legacy-idea за «сломанную миграцию»).
- **`project update --entity-kind project|idea|inbox`** — штатная починка legacy-
  классификации без ручных правок БД (кейс: запись помечена `idea`, хотя несёт
  сотни задач).

## 0.3.1 — закрытие тех-долга аудита 2026-06-30 (#894)

Починены все 16 оставшихся дефектов аудита (`docs/design/2026-06-30-atlas-audit-findings.md`).

**Исправления с влиянием на данные/надёжность**
- **backup больше не пропускает проекты** [5]: гейт смотрел на legacy `git_repo_url`,
  который `git link`/`move`/`sync-from-remote` не заполняли — привязанные проекты
  молча не бэкапились. Источник правды теперь `git_remote_url` (+ зеркалирование legacy).
- **sync-демон стартовал из чужой папки** [6]: `parents[4]` (артефакт rename pm→sync)
  → поиск `pyproject.toml` вверх по дереву.
- **push учитывает неудачные попытки** [13]: `mark_failed` был мёртвым кодом; теперь
  attempts/last_error пишутся, а в `failed` запись уходит только по порогу (5) —
  одиночная сетевая ошибка не выбрасывает событие из очереди.
- **pull различает applied/skipped** [12]: пропущенные события (гонка порядка доставки)
  больше не считаются применёнными и видны в логе `watch`.
- **archive атомарнее** [17]: новый junction создаётся ДО снятия старого.

**Поведенческие изменения**
- `task reject` возвращает задачу в **todo** (было `in_progress`) [10] — `submit` снимает
  lease, а `in_progress` без lease нарушал инвариант. Брать заново — `task start`.
- `task reject` **только из `review`** [4] (принимал `blocked` в обход lease-гейта `unblock`).
- `task approve` проверяет reviewer-гейт ДО идемпотентного закрытия и не пишет
  approve-комментарий, если перехода не было [9].
- `task reopen` сбрасывает `started_at` [16] (иначе lead-time считался от первого старта).
- `project git init` **не падает на кастомном типе** [11] — fallback `products`, как в layout.
- `backlog archive` запрещён для уже `converted` идеи [19] (`--hard` по-прежнему удаляет).
- `backlog`-команды по legacy idea/inbox дают предметный маршрут в `atlas project` [8];
  `backlog add` не занимает slug legacy-записи [14]; `--project` не принимает idea/inbox [15].
- Глобальные `--json/--text` уважают POSIX-сентинел `--` [20].

## 0.3.0 — RESTful-канон срезов CLI (BREAKING)

Приведение поверхности CLI к канону `<ресурс> <глагол>` (методология kit-integration):
подчинённые ресурсы вложены в родителя (как `epic worktree` / `project git`),
плоские дефисы и дубли убраны. **Чистый разрыв — старых имён больше нет.**

### Migration (старое → новое)

| Было | Стало |
|---|---|
| `atlas member add --task <t> …` | `atlas task member add <t> …` |
| `atlas checklist add --task <t> …` | `atlas task checklist add <t> …` |
| `atlas project member-add <p> …` | `atlas project member add <p> …` |
| `atlas project member-list/-remove` | `atlas project member list / rm` |
| `atlas project add-tags / remove-tags` | `atlas project tag add / rm` |
| `atlas participant …` | `atlas person …` (домен `Participant` не меняется) |
| `atlas type edit <t> …` | `atlas type update <t> …` |
| `atlas logs …` | `atlas log list …` |
| `atlas action-log list …` | `atlas log raw …` |
| `atlas connect <url>` / `disconnect` | `atlas backend connect <url>` / `disconnect` / `status` |
| `atlas backup install / uninstall / list-tasks` | `atlas backup schedule install / uninstall / list` |
| `atlas config init` | `atlas config setup` |
| `atlas upgrade [--reinstall]` | `atlas update --from-git` |

### Не тронуто

- `idea` / `inbox` — остаются top-level: это НЕ дубли `backlog`, а отдельные
  сущности со своей материализацией (`idea promote` → layout/junction/IDEA.md/
  extract-backlog; `inbox` → физическая свалка `_Inbox/<slug>/`). Сведение в
  `backlog` = либо потеря функциональности, либо отдельная миграция — вынесено
  из этого релиза.
- `dash` — короткий алиас `dashboard` (осознанный UX).
- `sync up` — оставлен как есть (обёртка над `sync daemon install`).

### Notes

- Реестр людей переименован только на уровне CLI-фасада (`person`); доменная
  модель `Participant`/`ProjectParticipant` и `action_log entity_type="participant"`
  сохранены — рассинхрон CLI↔домен намеренный.
