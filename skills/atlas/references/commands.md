# atlas — полный справочник команд (источник правды: `atlas <group> --help`)

CLI в **единственном числе**, `--json` — **по умолчанию** (для AI/скриптов; человеку — `--text`/`--plain`).
Глобальные флаги: `--profile/-P <slug>` (выбрать стор-профиль), `--json/-J`, `--text/--plain`, `--version/-V`.

Ref-резолв (где принимается `<ref>`): project — slug | full-UUID | short-UUID(≥7); task — number | slug | UUID; participant/tag/epic/hypothesis — slug | UUID; type/status — slug.

---

## project — проекты портфеля (+ провижн в ядро/Notion/Б24, теги, архив, git, layout)

| Команда | Назначение |
|---|---|
| `project init` | apply миграции + seed справочников (типы/статусы/28 тегов). Идемпотентно. |
| `project add` | создать проект (по умолчанию ЛИЧНЫЙ + раскладка в ядро/Notion). |
| `project list [--type --status --tag (AND) --archived]` | список. |
| `project get <ref>` | карточка (поля + теги + участники + git/layout статус). |
| `project update <ref> --…` | обновить любые поля кроме slug. |
| `project delete <ref> [--hard]` | soft (archived_at) по умолчанию; `--hard` — физическое. |
| `project make-personal <ref>` | перевести в личный (visibility=personal, владелец+lead=ты) — ядро+Atlas. |
| `project import-b24 <group_id> [--notion-kind личный\|клиентский\|компанейский]` | втянуть группу Б24 в ядро+Notion+Atlas. |
| `project link <ref> --portal <p> --external-id <id> [--kind project]` | привязать к сущности портала в ядре (entity_link, без docker exec). |
| `project unlink <ref> --portal <p>` | снять связь с порталом. |
| `project add-tags <ref> --tag …` / `remove-tags <ref> --tag …` | теги (идемпотентно / graceful). |
| `project member-add <ref> --member <slug> --role lead\|member` | участники проекта. |
| `project member-list <ref>` / `member-remove <ref> --member <slug>` | список / снять. |
| `project list --parent <ref>` / `--standalone` | модули контейнера / проекты без родителя. |
| `project archive <ref> --status completed\|paused\|frozen\|archived` | mv в `_Archive/<group>/` + статус. |
| `project unarchive <ref> [--status active]` | вернуть из архива. |
| `project renew <ref>` | renewal_count++ (только client-project). |
| `project move <ref> --to-type <type>` | сменить тип + физ. mv между группами. |
| `project reorganize [--dry-run\|--apply]` | синхронизировать БД ↔ ФС. |

`project add` флаги: `--name*`, `--type` (деф. personal-project), `--slug`, `--prefix`, `--priority P0..P3` (P2), `--status` (experiment), `--description`, `--one-line`, `--deadline YYYY-MM-DD`, `--local-path`, `--tag/-t` (многократно), `--setup-layout/--no-setup-layout` (on), `--canonical/--no-canonical` (on), `--init-git/--no-init-git` (off), `--private/--public`, `--group`, `--commit-message`. **Провижн-флаги**: `--team` (командный, владелец Цифро.ПРО; по умолчанию личный/твой), `--owner <slug>` (чужой владелец → командный), `--no-sync` (только в Atlas, без ядра/Notion), `--parent <ref>` (модуль контейнера). Контейнер: `update --parent/--no-parent`; `get` показывает Parent (у модуля) и Modules (у контейнера); защита от цикла. Физика модулей (вложенные репо + junction) — в работе (spec #3).

### project git — Git/GitLab (БД atlas = канон; не запускай `git init`/`glab` руками)
`init <ref> [--group --private/--public]` · `status <ref>` · `push <ref>` · `link <ref> --url <u>` · `move <ref> --to-group <path>` · `status-all [--type --status --tag]` · `sync-from-remote [--dry-run/--apply]`. Backend: `glab` (env `GITLAB_TOKEN`). Namespace: `cifropro1/…` (общее) / `zzztejletty3ukzzz/…` (личное, тег `owner:dmitry`).

### project layout — junction-раскладка (`_storage/<slug>/` + junction-ссылки)
`init <ref> [--copy-first --dry-run --confirm]` · `sync <ref>` (пересоздать junction по type+status) · `verify [<ref>] [--quick]` · `migrate-all [--type --status --tag --confirm]` · `list-storage`. Физика в `_storage/`, в логических папках (`Clients/Products/Tests/_Inbox/_Archive`) — junction (`mklink /J`). Смена статуса не двигает данные, только junction.

---

## task — задачи портфеля
| Команда | Назначение |
|---|---|
| `task add` | создать задачу (флаг `--cpp` ОБЯЗАТЕЛЕН). |
| `task list [--project --status --assignee …]` | список. |
| `task get <ref>` | карточка. |
| `task update <ref> --…` | обновить (кроме slug/number/project; status авто-ведёт started_at/completed_at). |
| `task delete <ref> [--hard]` | soft по умолчанию. |
| `task claim <ref> [--ttl 2h --actor --session --from]` | взять в работу: lease + status=in_progress + assignee. Занята другим → exit 1. |
| `task release <ref> [--actor]` | отпустить lease (только держатель). |
| `task renew <ref> [--ttl 2h --actor]` | продлить lease (heartbeat долгой работы). |
| `task take <ref> --force [--ttl --actor --session --from]` | принудительно отобрать (даже занятую/протухшую). |
| `task stale [--reap]` | протухшие lease: отчёт; `--reap` — освободить. |

`task add` флаги: `--project*`, `--title*`, `--cpp*` (ЦКП), `--slug`, `--description`, `--priority` (P2), `--status` (backlog), `--story-points`, `--due-date YYYY-MM-DD`, `--assignee <participant-slug>`, `--epic <ref>` (привязка к эпику; бывший `--sprint`), `--quality-tier`. Provenance: `--source-project <ref>` + `--rationale` (→ origin=injected). `task list` фильтры: `--project --status --assignee --epic --source-project`.

**Lease/claim (Волна 8) — блокировка задач для мультиагентности.** `claim` атомарно (optimistic-lock через `lock_version`) ставит lease (`lease_owner`+`lease_session_id`+`lease_origin`+`claimed_at`+`lease_expires_at`; TTL по умолч. 2ч) + status=in_progress + assignee. Второй агент на занятой задаче получает «задача занята» и exit 1 — двойной захват/затирание невозможны. Идентичность держателя: флаги `--actor`/`--session`/`--from` или env `ATLAS_ACTOR`/`ATLAS_SESSION`/`ATLAS_FROM` (дефолт actor — `dmitry`). Протухшие lease авто-освобождаются (ленивый reaper при `claim`/`list`; вручную — `task stale --reap`). `task update --status done|cancelled` авто-снимает lease. **Lease — ЛОКАЛЬНАЯ координация, в ядро НЕ синкается.** `task get`/`list` показывают держателя. Любой `task update` защищён optimistic-lock (параллельная правка → ошибка version conflict).

## epic — эпики (вехи/спринты; задача привязывается флагом `task --epic`)
`add --project* --title* [--slug --goal --description --source-project --rationale --origin --injected-by]` · `list [--project --source-project]` (**без `--project` = ВЕСЬ портфель** + колонка Project) · `get <ref>` (показывает description + блок Provenance).

## checklist — пункты чек-листа задачи (синкаются Atlas↔ядро↔Б24/Notion)
`add --task* --text* [--due YYYY-MM-DD]` · `list --task <ref>` · `check <item-id> [--uncheck]` · `delete <item-id>` (локально + enqueue delete наружу).

## member — участники задачи (роли)
`add --task* --participant* [--role responsible|executor|watcher]` (деф. executor) · `list --task <ref>` · `rm --task --participant`. Состав синкается в поле «Ответственный»/исполнители порталов.

## hypothesis — реестр гипотез (Atlas Hypothesis Ledger)
`add --project* --title* [--statement «если X то метрика Y↑ на Z» --metric --baseline --target --method --task --confidence H|M|L --status draft|testing|measured|closed --slug]` · `list` · `get <ref>` · `update <ref> --…` (status-переходы авто-timestamp) · `close <ref> --verdict …` (status=closed, closed_at, опц. замер) · `delete <ref> [--hard]`.

## participant — люди портфеля
`add --name --kind human|ai_agent|contractor --slug --role …` · `list` · `get <ref>` · `update <ref>` · `delete <ref> [--hard --force --soft]`. `--force` каскадит FK (снимает с проектов/задач), `--soft` = is_active=False.

## type / status / tag — справочники
- `type list` (колонки Group + Sync policy) / `type add --slug --name [--group clients|products|tests|inbox --default-sync-policy <slug>]` / `type edit <ref> --name/--description/--color/--group/--default-sync-policy` (slug неизменен). **10 канон-типов**: client-project / business-product / personal-utility / personal-project / shared-infrastructure / test / inbox + **роли** kit / service / superskill. Единый источник `BASE_PROJECT_TYPES` + user-override `~/.atlas/types.toml` (merge by slug); `storage_group` = физ-группа на диске.
- `status list` / `status add --slug --name [--order-idx N]`. **5 канонических**: active / paused / archived / cancelled / experiment.
- `tag list [--category]` / `add --name --category owner|stack|domain|other --slug [--color --description]` / `get <ref>` / `update <ref>` (slug менять нельзя) / `delete <ref> [--force]`. Ref: `category:slug` или bare `slug`. Сид: 28 тегов (2 owner + 14 stack + 12 domain).

## idea — инкубатор идей (entity_kind=idea, `_Ideas/<slug>.md`)
`add --slug --name --type --priority --tag … --one-line` · `list` · `show <slug>` (БД + MD) · `promote <slug> [--status active --priority … --init-git --canonical]` (idea→project: layout + MD→IDEA.md + extract backlog) · `demote <slug>` (обратно) · `update <slug> --…`.

## inbox — свалка сырья на разбор AI (entity_kind=inbox, `_Inbox/<slug>/`)
`add --slug --name --tag …` · `list` · `show <slug>`. inbox ≠ idea (idea = сформулированная мысль; inbox = «разберись что это»).

## action-log — append-only аудит (read-only для агента)
`list [--project --actor --entity-type --action --since --limit]`. Каждый CRUD пишется сюда автоматически. Никогда не редактировать вручную.

## backup — snapshot git-репо портфеля → ветка `backup` (HEAD не трогается)
`run [--type --status --tag --ref --dry-run]` · `status [--days N]` · `install [--time HH:MM]` (Windows Task, деф. 03:00) · `uninstall` · `list-tasks`.

## sync — синхронизация Atlas ↔ backend-хаб → см. [sync-and-profiles.md](sync-and-profiles.md)
`push` (выгрузить outbox → хаб) · `pull [--timeout 25]` (один long-poll цикл) · `watch [--timeout 25]` (устойчивый фон) · `up` (install+start демон) · `daemon install|uninstall|status` (Windows Task `atlas-sync-watch`).

## profile — онбординг Atlas-сторов (профиль = отдельный стор: своя БД + ключ) → см. [sync-and-profiles.md](sync-and-profiles.md)
`register --name* [--scope all|personal --member <slug> --global-role admin]`. Дёргает ядро `/admin/profiles`, сохраняет `profiles/<slug>/config.toml` + `atlas.db`. Использование: `atlas --profile <slug> …`.
