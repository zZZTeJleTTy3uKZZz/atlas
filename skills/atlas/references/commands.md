# atlas — полный справочник команд (источник правды: `atlas <group> --help`)

CLI в **единственном числе**, `--json` — **по умолчанию** (для AI/скриптов; человеку — `--text`/`--plain`).
Глобальные флаги: `--json/-J`, `--text/--plain`, `--version/-V`.

Ref-резолв (где принимается `<ref>`): project — slug | full-UUID | short-UUID(≥7); task — number | slug | UUID; participant/tag/epic/hypothesis — slug | UUID; type/status — slug.

---

## project — проекты портфеля (теги, архив, git, layout)

| Команда | Назначение |
|---|---|
| `project init` | apply миграции + seed справочников (типы/статусы/28 тегов). Идемпотентно. |
| `project add` | создать проект (по умолчанию ЛИЧНЫЙ; `--team` — командный). |
| `project list [--type --status --tag (AND) --archived]` | список. |
| `project get <ref>` | карточка (поля + теги + участники + git/layout статус). |
| `project update <ref> --…` | обновить любые поля кроме slug. |
| `project delete <ref> [--hard]` | soft (archived_at) по умолчанию; `--hard` — физическое. |
| `project make-personal <ref>` | перевести в личный (visibility=personal, владелец+lead=ты). |
| `project add-tags <ref> --tag …` / `remove-tags <ref> --tag …` | теги (идемпотентно / graceful). |
| `project member-add <ref> --member <slug> --role lead\|member` | участники проекта. |
| `project member-list <ref>` / `member-remove <ref> --member <slug>` | список / снять. |
| `project list --parent <ref>` / `--standalone` | модули контейнера / проекты без родителя. |
| `project archive <ref> --status completed\|paused\|frozen\|archived` | mv в `_Archive/<group>/` + статус. |
| `project unarchive <ref> [--status active]` | вернуть из архива. |
| `project renew <ref>` | renewal_count++ (только client-project). |
| `project move <ref> --to-type <type>` | сменить тип + физ. mv между группами. |
| `project reorganize [--dry-run\|--apply]` | синхронизировать БД ↔ ФС. |

`project add` флаги: `--name*`, `--type` (деф. personal-project), `--slug`, `--prefix`, `--priority P0..P3` (P2), `--status` (experiment), `--description`, `--one-line`, `--deadline YYYY-MM-DD`, `--local-path`, `--tag/-t` (многократно), `--setup-layout/--no-setup-layout` (on), `--canonical/--no-canonical` (on), `--init-git/--no-init-git` (off), `--private/--public`, `--group`, `--commit-message`. **Владелец/видимость**: `--team` (командный, владелец — организация; по умолчанию личный/твой), `--owner <slug>` (чужой владелец → командный), `--parent <ref>` (модуль контейнера). Контейнер: `update --parent/--no-parent`; `get` показывает Parent (у модуля) и Modules (у контейнера); защита от цикла. Физика модулей (вложенные репо + junction) — в работе (spec #3).

### project git — Git/GitLab (БД atlas = канон; не запускай `git init`/`glab` руками)
`init <ref> [--group --private/--public]` · `status <ref>` · `push <ref>` · `link <ref> --url <u>` · `move <ref> --to-group <path>` · `status-all [--type --status --tag]` · `sync-from-remote [--dry-run/--apply]`. Backend: `glab` (env `GITLAB_TOKEN`). Namespace: `<org-namespace>/…` (общее) / `<personal-namespace>/…` (личное, тег `owner:personal`).

### project layout — junction-раскладка (`_storage/<slug>/` + junction-ссылки)
`init <ref> [--copy-first --dry-run --confirm]` · `sync <ref>` (пересоздать junction по type+status) · `verify [<ref>] [--quick]` · `migrate-all [--type --status --tag --confirm]` · `list-storage`. Физика в `_storage/`, в логических папках (`Clients/Products/Tests/_Inbox/_Archive`) — junction (`mklink /J`). Смена статуса не двигает данные, только junction.

---

## task — задачи портфеля
| Команда | Назначение |
|---|---|
| `task add` | создать задачу (флаг `--cpp` ОБЯЗАТЕЛЕН). |
| `task list [--project --status --assignee …]` | список. |
| `task get <ref>` | карточка. |
| `task update <ref> --…` | обновить поля (кроме slug/number/project). `--status` — ТОЛЬКО `todo`; lifecycle — глаголами ниже. Идеи (до задачи) — пул `atlas backlog`. |
| `task delete <ref> [--hard]` | soft по умолчанию. |
| **Жизненный цикл (глаголы):** | |
| `task start <ref> [--ttl 2h --actor --session --from]` | взять в работу: lease + status=in_progress + assignee (синоним `claim`). Занята другим → exit 1. |
| `task review <ref> [--force --actor]` | → review (lease сохраняется). |
| `task block <ref> [--reason … --force --actor]` | → blocked (lease сохраняется; reason → action-log). |
| `task unblock <ref> [--actor]` | blocked→in_progress (нужно держать lease). |
| `task done <ref> [--force --actor]` | завершить → done (снимает lease, ставит completed_at). |
| `task cancel <ref> [--force --actor]` | отменить → cancelled (снимает lease). |
| `task release <ref> [--actor]` | отпустить lease (только держатель), статус не меняет. |
| `task renew <ref> [--ttl 2h --actor]` | продлить lease (heartbeat долгой работы). |
| `task take <ref> --force [--ttl --actor --session --from]` | принудительно отобрать lease (даже занятую/протухшую). |
| `task stale [--reap]` | протухшие lease: отчёт; `--reap` — освободить. |
| **Триаж (смотри в начале сессии):** | |
| `task triage [--days N --project --assignee]` | что в работе / застряло (blocked/review) / **забыто** (active, не тронуто > N дн). `--json` для агента. |
| `task triage --install [--time HH:MM]` · `--uninstall` | ежедневный Windows Scheduled Task автозапуска триажа → лог (дефолт 09:00; как `backup install`). Ставит Дмитрий (Claude-env не может). |
| **Review-workflow:** | |
| `task submit <ref> [-m "…"]` | исполнитель → review (снимает свой lease); опц. коммент-передача. |
| `task approve <ref> [-m]` | reviewer → done (одобрить). |
| `task reject <ref> -m "причина"` | reviewer: review→in_progress (причина обязательна). |
| `task reopen <ref> [-m]` | reviewer: done/cancelled→todo. |
| `task comment <ref> "текст"` · `task comments <ref>` | заметки (любой); видны в `task get`. |
| **Batch:** | |
| `task batch <file.toml> [--dry-run]` | массовое создание: `[defaults]` на батч + `[[task]]` override. Разрешение: задача › defaults › config › система. |
| `task add … --reviewer <slug>` / `--no-review` | reviewer задачи (деф. создатель / config `default_review`). |

`task add` флаги: `--project*`, `--title*`, `--cpp*` (ЦКП), `--slug`, `--description`, `--priority` (P2), `--status` (todo), `--story-points`, `--due-date YYYY-MM-DD`, `--assignee <participant-slug>`, `--epic <ref>` (привязка к эпику; бывший `--sprint`), `--quality-tier`. Provenance: `--source-project <ref>` + `--rationale` (→ origin=injected). `task list` фильтры: `--project --status --assignee --epic --source-project`.

**Жизненный цикл + lease — блокировка задач для мультиагентности.** Статус задачи меняется ГЛАГОЛАМИ (`start`/`review`/`block`/`unblock`/`done`/`cancel`), а не «голым» `update --status` (он обходил lease; теперь принимает лишь backlog\|todo). `start` (= `claim`) атомарно (optimistic-lock через `lock_version`) ставит lease (`lease_owner`+`lease_session_id`+`lease_origin`+`claimed_at`+`lease_expires_at`; TTL по умолч. 2ч) + status=in_progress + assignee. Второй агент на занятой задаче получает «задача занята» и exit 1 — двойной захват/затирание невозможны. `done`/`cancel` снимают lease (+`completed_at` у done); `review`/`block` его сохраняют; `unblock` требует держать lease. Завершить/перевести ЧУЖУЮ живую задачу — только с `--force` (иначе LeaseHeldError). Идентичность держателя: флаги `--actor`/`--session`/`--from` или env `ATLAS_ACTOR`/`ATLAS_SESSION`/`ATLAS_FROM` (дефолт actor — из конфига). Протухшие lease авто-освобождаются (ленивый reaper при глаголах/`list`; вручную — `task stale --reap`). **Lease — локальная координация мультиагентности** (в ядро не синкается; статус — синкается). `task get`/`list` показывают держателя. Любой переход защищён optimistic-lock.

## epic — эпики (вехи; задача привязывается флагом `task --epic`, бывший `--sprint`)
`add --project* --title* [--slug --goal --description --source-project --rationale --origin --injected-by]` · `list [--project --source-project]` (**без `--project` = ВЕСЬ портфель** + колонка Project) · `get <ref>` (показывает description + блок Provenance).

## sprint — итерации (спринты)
Планирование итераций: завести спринт (окно дат), набрать задачи, отслеживать прогресс. Задачи связываются с вехами через `epic` (`task --epic`). Точные подкоманды/флаги — `atlas sprint --help`.

## checklist — пункты чек-листа задачи
`add --task* --text* [--due YYYY-MM-DD]` · `list --task <ref>` · `check <item-id> [--uncheck]` · `delete <item-id>`.

## member — участники задачи (роли)
`add --task* --participant* [--role responsible|executor|watcher]` (деф. executor) · `list --task <ref>` · `rm --task --participant`. Роли: responsible (ответственный) / executor (исполнитель) / watcher (наблюдатель).

## hypothesis — реестр гипотез (Atlas Hypothesis Ledger)
`add --project* --title* [--statement «если X то метрика Y↑ на Z» --metric --baseline --target --method --task --confidence H|M|L --status draft|testing|measured|closed --slug]` · `list` · `get <ref>` · `update <ref> --…` (status-переходы авто-timestamp) · `close <ref> --verdict …` (status=closed, closed_at, опц. замер) · `delete <ref> [--hard]`.

## participant — люди портфеля
`add --name --kind human|ai_agent|contractor --slug --role …` · `list` · `get <ref>` · `update <ref>` · `delete <ref> [--hard --force --soft]`. `--force` каскадит FK (снимает с проектов/задач), `--soft` = is_active=False.

## profile — профиль текущего пользователя/актора
Идентичность актора по умолчанию (owner/actor из конфига), используемая в lease/аудите. Точные подкоманды/флаги — `atlas profile --help`.

## type / status / tag — справочники
- `type list` (колонка Group) / `type add --slug --name [--group clients|products|tests|inbox]` / `type edit <ref> --name/--description/--color/--group` (slug неизменен). **10 канон-типов**: client-project / business-product / personal-utility / personal-project / shared-infrastructure / test / inbox + **роли** kit / service / superskill. Единый источник `BASE_PROJECT_TYPES` + user-override `~/.atlas/types.toml` (merge by slug); `storage_group` = физ-группа на диске.
- `status list` / `status add --slug --name [--order-idx N]`. **5 канонических**: active / paused / archived / cancelled / experiment.
- `tag list [--category]` / `add --name --category owner|stack|domain|other --slug [--color --description]` / `get <ref>` / `update <ref>` (slug менять нельзя) / `delete <ref> [--force]`. Ref: `category:slug` или bare `slug`. Сид: 28 тегов (2 owner + 14 stack + 12 domain).

## issue — структурированные жалобы + передача задачи агент→агент (issuekit)
Богатая обратная связь: bug/feature/handoff с БЛОКИРУЮЩЕЙ проверкой полноты (валидатор `issuekit` —
неполную не пускает, как обязательный ЦКП). Главный кейс — `task handoff` (передача между агентами).
- `issue template --kind bug|feature|handoff` — пустой шаблон (заполнить).
- `issue add --kind --title --body-file <md> [--task <ref>]` — завести жалобу (валидируется; неполная → ошибка с missing).
- `issue list [--task --status open|resolved|wontfix|all --kind]` · `issue show <ref>` · `issue resolve <ref> [--wontfix]`.
- **`task handoff <ref> --to <agent> --body-file <md>`** — передать задачу с контекстом (шаблон handoff:
  что сделано / осталось / как проверить / ЦКП / контекст). Создаёт issue(handoff), переназначает на `--to`,
  снимает lease сдающего; **неполную передачу блокирует**. Принимающий: `issue show <ref>` → `task start <ref>`.

## backlog — ОСНОВНОЙ интейк идей (primary, DB-first) → задача/проект
Сырьё ДО задачи: лёгкая запись в БД (ЦКП НЕ нужен, проект опционален — global-пул «между проектами»).
Просматривается отдельно от задач, **конвертируется** в `todo`-задачу (ЦКП появляется тут) или зачаток проекта.
- `backlog add --title* [--note --project --priority --slug --md]` — завести идею (global если без `--project`).
- `backlog list [--project --global --status open|converted|archived|all]` — вид «идеи»; показывает И legacy
  idea/inbox-проекты (единый вид).  `backlog show <ref>` · `backlog edit <ref> --…` · `backlog archive <ref> [--hard]`.
- `backlog convert <ref> --as task --project <p> --cpp "…"` → создаёт `todo`-задачу; `--as project [--type --slug]` → зачаток проекта.
- Раздельные виды: `backlog list` (что преобразовать) vs `task list` (`todo` — что брать в работу). Новая задача создаётся в `todo`.

## idea — LEGACY: идеи ПРОЕКТНОГО уровня (entity_kind=idea, `_Ideas/<slug>.md`)
> Лёгкие «идея-в-задачу» — теперь `atlas backlog`. `idea` остаётся для идеи-зачатка ПРОЕКТА.
`add --slug --name --type --priority --tag … --one-line` · `list` · `show <slug>` (БД + MD) · `promote <slug> [--status active --priority … --init-git --canonical]` (idea→project: layout + MD→IDEA.md + extract backlog) · `demote <slug>` (обратно) · `update <slug> --…`.

## inbox — LEGACY: свалка сырья на разбор AI (entity_kind=inbox, `_Inbox/<slug>/`)
> Лёгкий интейк сырья — теперь `atlas backlog` (primary). `inbox` остаётся для проектной свалки.
`add --slug --name --tag …` · `list` · `show <slug>`. inbox ≠ idea (idea = сформулированная мысль; inbox = «разберись что это»).

## action-log — append-only аудит (read-only для агента)
`list [--project --actor --entity-type --action --since --limit]`. Каждый CRUD пишется сюда автоматически. Никогда не редактировать вручную.

## backup — snapshot git-репо портфеля → ветка `backup` (HEAD не трогается)
`run [--type --status --tag --ref --dry-run]` · `status [--days N]` · `install [--time HH:MM]` (Windows Task, деф. 03:00) · `uninstall` · `list-tasks`.

## config — онбординг + дефолты (config.toml)
`show` · `get <key>` · `set <key> <value>` · `init` (интерактивный визард). Поля: `owner` (дефолтный
actor/владелец), `timezone`, namespaces, `team_owner`, и **дефолты задач**: `default_priority` (P2),
`default_review` (bool — заводить ли reviewer), `default_reviewer` (slug; пусто → создатель). Дефолты
применяются в `task add`/`batch`, если не заданы явно. `api_key` — только env/secret-store.

## Топ-уровень (без группы): dashboard / init / stats / logs / update / connect
- `atlas dashboard [--project <ref>] [--json]` — операционный обзор: KPI, задачи по статусам/приоритетам, что в работе (in-flight + держатель), внимание (blocked/overdue/протухшие lease), по проектам, активность. По умолчанию Rich для человека; `--json` — для агента.
- `atlas init [--scope global|repo|all] [--agents …] [--create] [--dry-run] [--json]` — идемпотентно дописывает Atlas-дисциплину (managed-блок между маркерами) в агентские файлы. Аддитивно: чужой текст не трогается.
  - Без `--agents` — легаси: все существующие агентские файлы (`~/.claude/CLAUDE.md`, репо `AGENTS.md`/`CLAUDE.md`/`GEMINI.md`/`.cursorrules`).
  - `--agents claude,gemini,cursor,codex,copilot` (или `all`) — **точечный выбор** агентов; с `--create` создаёт их файлы (включая вложенный `.github/copilot-instructions.md`). Реестр агент→файл (18 агентов) и весь механизм онбординга — в ките **`agentskit`** (`agentskit.AGENT_REGISTRY`/`onboard`); Atlas приносит только контент (`atlas.discipline.DISCIPLINE_BODY` + namespace `atlas`).
- `atlas stats [--period <spec>] [--provenance] [--project <ref>]` — аналитика (counts/окно активности/provenance/git).
- `atlas connect [<url>] [--key K] [--no-verify]` — подключить backend (синк) / статус; ключ → secret-store. `atlas disconnect` — отключить. **Local-first**: всё работает без подключения; `sync push/pull` — только после connect.
- `atlas logs [...]` — журнал CLI/операций (человеку — Rich; агенту — `--json`). Точные фильтры — `atlas logs --help`. Ср. `action-log` (append-only аудит сущностей).
- `atlas update [--check]` — self-update CLI с PyPI (дистрибутив **atlas-pm**, команда/import `atlas`): детектит менеджер (uv/pipx/pip) и ставит свежую версию; `--check` — показать текущую/доступную без установки. **`atlas upgrade`** — legacy (git-install), не предлагать по умолчанию.

---

> **Синхронизация с внешним бэкендом — вне текущего публичного скоупа.** Atlas самодостаточен:
> весь портфель живёт в локальном SQLite. Команды/профили для синка с внешним хабом — опциональная
> фича, не входящая в этот релиз.
