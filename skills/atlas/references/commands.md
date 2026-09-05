# atlas — полный справочник команд (источник правды: `atlas <group> --help`)

CLI в **единственном числе**, `--json` — **по умолчанию** (для AI/скриптов; человеку — `--text`/`--plain`).
Глобальные флаги: `--json/-J`, `--text/--plain`, `--version/-V`.

Ref-резолв (где принимается `<ref>`): project — slug | full-UUID | short-UUID(≥7); task — number | slug | UUID; person/tag/epic/hypothesis — slug | UUID; type/status — slug.

> **RESTful-канон (v0.3.0):** подчинённые ресурсы вложены в родителя —
> `task member`/`task checklist`, `project tag`/`project member`, `epic worktree`,
> `backup schedule`, `backend connect|disconnect|status`, `log list|raw`. Реестр людей —
> `person` (бывш. `participant`). Старых плоских имён (`member`, `checklist`,
> `action-log`, `logs`, `connect`, `upgrade`, `project member-add`/`add-tags`) больше нет.

---

## project — проекты портфеля (теги, архив, git, layout)

| Команда | Назначение |
|---|---|
| `project init` | apply миграции + seed справочников (типы/статусы/28 тегов). Идемпотентно. |
| `project add` | создать проект (по умолчанию ЛИЧНЫЙ; `--team` — командный). |
| `project list [--type --status --tag (AND) --archived]` | список **портфеля**: модули (проекты с родителем) СКРЫТЫ по умолчанию — это части контейнера, а не единицы портфеля. Сколько их — в колонке `modules` у контейнера. |
| `project get <ref>` | карточка (поля + теги + участники + git/layout статус). |
| `project update <ref> --…` | обновить любые поля кроме slug. |
| `project delete <ref> [--hard]` | soft (archived_at) по умолчанию; `--hard` — физическое. |
| `project make-personal <ref>` | перевести в личный (visibility=personal, владелец+lead=ты). |
| `project tag add <ref> --tag …` / `project tag rm <ref> --tag …` | теги (идемпотентно / graceful). |
| `project member add <ref> --member <slug> --role lead\|member` | участники проекта. |
| `project member list <ref>` / `project member rm <ref> --member <slug>` | список / снять. |
| `project list --parent <ref>` / `--standalone` / **`--modules`** / **`--all`** | модули конкретного контейнера / только самостоятельные (= умолчание) / **только модули** / **проекты и модули вместе**. Флаги области взаимоисключающи: две области сразу → отказ. |
| `project archive <ref> --status completed\|paused\|frozen\|archived` | mv в `_Archive/<group>/` + статус. |
| `project unarchive <ref> [--status active]` | вернуть из архива. |
| `project renew <ref>` | renewal_count++ (только client-project). |
| `project move <ref> --to-type <type>` | сменить тип + физ. mv между группами. |
| `project reorganize [--dry-run\|--apply]` | синхронизировать БД ↔ ФС. |

`project add` флаги: `--name*`, `--type` (деф. personal-project), `--slug`, `--prefix`, `--priority P0..P3` (P2), `--status` (experiment), `--description`, `--one-line`, `--deadline YYYY-MM-DD`, `--local-path`, `--tag/-t` (многократно), `--setup-layout/--no-setup-layout` (on), `--canonical/--no-canonical` (on), `--init-git/--no-init-git` (off), `--private/--public`, `--group`, `--commit-message`. **Владелец/видимость**: `--team` (командный, владелец — организация; по умолчанию личный/твой), `--owner <slug>` (чужой владелец → командный), `--parent <ref>` (модуль контейнера). Контейнер: `update --parent/--no-parent`; `get` показывает Parent (у модуля) и Modules (у контейнера); защита от цикла. Физика модулей (вложенные репо + junction) — в работе (spec #3). `project update` дополнительно умеет `--no-local-path` (снять путь: папки физически нет — запись честнее сломанного пути).

### project git — Git/GitLab (БД atlas = канон; не запускай `git init`/`glab` руками)
`init <ref> [--group --private/--public]` · `status <ref>` · `push <ref>` · `link <ref> --url <u>` · `move <ref> --to-group <path>` · `status-all [--type --status --tag]` · `sync-from-remote [--dry-run/--apply]`. Backend: `glab` (env `GITLAB_TOKEN`). Namespace: `<org-namespace>/…` (общее) / `<personal-namespace>/…` (личное, тег `owner:personal`).

### project adr — журнал архитектурных решений
`adr init <ref>` · `adr init --all [--dry-run]` — разложить `docs/adr/` (README + шаблон `0000-template.md`), идемпотентно. `--all` берёт активные КОДОВЫЕ проекты (папка есть и под git); идеи и каталоги с выгрузками пропускает. Правило: ADR читают ПЕРЕД архитектурной правкой; заводят, когда решение дорого откатить + без контекста выглядит странно + были альтернативы; отменённому ставят `superseded`, задним числом не переписывают.

### project layout — junction-раскладка (`_storage/<slug>/` + junction-ссылки)
`init <ref> [--copy-first --dry-run --confirm]` · `sync <ref>` (пересоздать junction по type+status) · `verify [<ref>] [--quick]` · `migrate-all [--type --status --tag --confirm]` · `list-storage` · `orphans [--kind unregistered|slug_mismatch|path_broken]` (аудит расхождений диск↔БД: каталог `_storage/` без проекта, проект с несуществующим `local_path`, каталог с именем не как slug; смотрит и модули, и архивные; служебные каталоги `_*`/`.* ` пропускает). Физика в `_storage/`, в логических папках (`Clients/Products/Tests/_Inbox/_Archive`) — junction (`mklink /J`). Смена статуса не двигает данные, только junction.

---

## task — задачи портфеля
| Команда | Назначение |
|---|---|
| `task add` | создать задачу (флаг `--cpp` ОБЯЗАТЕЛЕН). |
| `task list [--project --status --assignee …]` | список. |
| `task get <ref>` | карточка. |
| `task update <ref> --…` | обновить поля (кроме slug/number/project). `--status` — ТОЛЬКО `todo`; lifecycle — глаголами ниже. Идеи (до задачи) — пул `atlas backlog`. |
| `task delete <ref> [--hard]` | soft по умолчанию. |
| **`task move <ref> --to-project <slug>`** | **перенести задачу в другой проект.** Номер, история и комментарии сохраняются; slug пересобирается под prefix нового проекта; связь с эпиком прежнего проекта снимается (эпик↔задача — внутрипроектная связь). |
| **`task unconvert <ref>`** | **вернуть задачу в пул `backlog`** — это НЕ `cancel`: задача не провалилась, а вернулась в раздумья. Карточка (ЦКП/описание/приоритет/проект) уезжает в запись пула, сама задача архивируется, slug освобождается под возврат. Обратно — `backlog convert <ref> --as task`. |
| **Жизненный цикл (глаголы):** | |
| `task start <ref> [--ttl 2h --actor --session --from]` | взять в работу: lease + status=in_progress + assignee (синоним `claim`). Занята другим → exit 1. |
| `task review <ref> [--force --actor]` | → review (lease сохраняется). |
| `task block <ref> [--reason … --force --actor]` | → blocked (lease сохраняется; reason → audit log). |
| `task unblock <ref> [--actor]` | blocked→in_progress (нужно держать lease). |
| `task done <ref> [--force --actor]` | завершить → done (снимает lease, ставит completed_at). |
| `task cancel <ref> [--force --actor]` | отменить → cancelled (снимает lease). |
| `task release <ref> [--actor]` | отпустить lease (только держатель), статус не меняет. |
| `task renew <ref> [--ttl 2h --actor]` | продлить lease (heartbeat долгой работы). |
| `task take <ref> --force [--ttl --actor --session --from]` | принудительно отобрать lease (даже занятую/протухшую). |
| `task stale [--reap]` | протухшие lease: отчёт; `--reap` — освободить. |
| **Триаж (смотри в начале сессии):** | |
| `task triage [--days N --project --assignee]` | что в работе / застряло (blocked/review) / **забыто** (active, не тронуто > N дн). `--json` для агента. |
| `task triage --install [--time HH:MM]` · `--uninstall` | ежедневный Windows Scheduled Task автозапуска триажа → лог (дефолт 09:00; как `backup schedule install`). Ставит Дмитрий (Claude-env не может). |
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

**Жизненный цикл + lease — блокировка задач для мультиагентности.** Статус задачи меняется ГЛАГОЛАМИ (`start`/`review`/`block`/`unblock`/`done`/`cancel`), а не «голым» `update --status` (он обходил lease; теперь принимает лишь `todo`). `start` (= `claim`) атомарно (optimistic-lock через `lock_version`) ставит lease (`lease_owner`+`lease_session_id`+`lease_origin`+`claimed_at`+`lease_expires_at`; TTL по умолч. 2ч) + status=in_progress + assignee. Второй агент на занятой задаче получает «задача занята» и exit 1 — двойной захват/затирание невозможны. `done`/`cancel` снимают lease (+`completed_at` у done); `review`/`block` его сохраняют; `unblock` требует держать lease. Завершить/перевести ЧУЖУЮ живую задачу — только с `--force` (иначе LeaseHeldError). Идентичность держателя: флаги `--actor`/`--session`/`--from` или env `ATLAS_ACTOR`/`ATLAS_SESSION`/`ATLAS_FROM` (дефолт actor — из конфига). Протухшие lease авто-освобождаются (ленивый reaper при глаголах/`list`; вручную — `task stale --reap`). **Lease — локальная координация мультиагентности** (в ядро не синкается; статус — синкается). `task get`/`list` показывают держателя. Любой переход защищён optimistic-lock.

## epic — эпики (вехи; задача привязывается флагом `task --epic`, бывший `--sprint`)
`add --project* --title* [--slug --goal --description --source-project --rationale --origin --injected-by]` · `list [--project --source-project]` (**без `--project` = ВЕСЬ портфель** + колонка Project) · `get <ref>` (показывает description + блок Provenance).
Групповой lease: `epic claim <ref>` / `epic release <ref>` (захват/освобождение эпика с каскадом на его задачи).
**Перенос между проектами:** `epic move <ref> --to-project <slug>` — эпик едет ВМЕСТЕ со своими задачами
(оставить их значило бы порвать связь; переносить эпик по частям нельзя).
**Возврат в пул:** `epic unconvert <ref>` — эпик и ВСЕ его задачи становятся записями `backlog` каскадом,
задачи ссылаются на запись эпика; поднять обратно целиком — `backlog convert <ref> --as epic`.
Суб-группа изоляции веток — `epic worktree create|list|merge|remove <ref>` (git worktree на ветке `epic/<slug>`; детали — [projects-and-layout.md](projects-and-layout.md)).

## milestone — контрольные точки (обязательства перед человеком)

**Не путать с эпиком.** Эпик отвечает на «что мы строим», контрольная точка — на «что и когда мы
предъявляем человеку»: у неё обязательны дата, артефакт и приёмщик, и закрыть её вправе только
приёмщик. «Показали клинике авторассылку и согласовали тексты» — контрольная точка; «написали
коннектор к МИС» — эпик.

`add --project* --title* [--criterion --due YYYY-MM-DD --acceptor --track --slug]` ·
`list [--project --state --all]` · `get <ref>` ·
`update <ref> [--title --criterion --due --acceptor --track --artifact]`.

**Состав** (членство многие-ко-многим, НЕ иерархия — одна и та же работа может входить и в
промежуточное демо, и в финальную сдачу):
`include <ref> --epic <ref> | --task <ref>` · `exclude <ref> --epic|--task <ref>`.
Состав не выходит за границы проекта. КТ готова к сдаче, когда закрыт весь состав.

**Конвейер** (человек ставит цель → агент работает → человек принимает):
`ready <ref>` (planning → ready_for_ai; **закрыт без критерия, даты и состава**) ·
`start <ref>` (агент взял в цикл) ·
`submit <ref> --artifact <url> [--force]` (→ ready_for_review; **без артефакта нельзя**, `--force` —
осознанная сдача при незакрытом составе) ·
`verify <ref>` (я посмотрел) · `accept <ref> [--outcome confirmed|adjusted|dropped]` ·
`reject <ref> --reason*` (вернуть на доработку) · `block <ref> --reason*` / `unblock <ref>` ·
`cancel <ref> --reason*` (обязательство снято: не сдана и не ждёт — её больше нет в плане).

## review — сверка портфеля одной командой

`atlas review [--project <slug>] [--all]` — ответ на «как дела» и на «где мы себя обманываем»:
что ждёт МОЕЙ приёмки, что крутится у агентов, что просрочено и заблокировано, дыры планирования
(КТ без критерия / даты / приёмщика / состава, проекты без КТ и без паспорта), **забытые проекты**
(числятся активными, но никто не трогал 30+ дней — довести или приостановить осознанно), размер
зависшей очереди синхронизации. По умолчанию смотрит только проекты, которые ведутся как проекты
(клиентские, с живыми КТ или с начатым паспортом); `--all` — весь активный портфель.

Паспорт проекта заполняется через `project update <ref> --point-a --point-b --done-criteria
--appetite-days --hard-deadline` (+ мосты `--b24-item-id --b24-entity-type-id`).

## sprint — итерации (спринты, Scrum-тайм-боксы)
`add --project* …` (окно дат) · `list` · `get <ref>` · **жизненный цикл**: `start <ref>` / `close <ref>` / `cancel <ref>` · `assign <sprint-ref> <task-ref…>` (набрать задачи) · `board <ref>` (доска задач спринта) · `velocity` (метрика скорости). Задачи связываются с вехами и через `epic` (`task --epic`). Точные флаги — `atlas sprint --help`.

## task depends — зависимости задач: что кого ждёт (суб-ресурс task)

`task depends add <ждущая> --on <держатель> [--reason «зачем ждём»]` · `task depends remove <ждущая> --on <держатель>` · `task depends list [<задача>]`.

**`task start` ОТКАЗЫВАЕТ**, пока держатель не закрыт (done/cancelled) — это отказ, а не предупреждение: предупреждение, которое можно проигнорировать, проигнорируют, и порядок работ вернётся в голову. Отказ называет держателя и причину.

Главный случай — межпроектный: задача клиентского проекта ждёт задачу в ките, и знает об этом только тот, кто заводил обе. `list` без аргумента показывает все связи; с задачей — обе стороны: `waits_for` («почему стою») и `blocks` («кого я задерживаю» — видно только отсюда).

Круг ожидания не создаётся: связь, замыкающая круг, отбивается с показом пути. Две задачи, ждущие друг друга, обе перестали бы браться в работу, а по отдельности причина не видна. В сверке (`atlas review`) ждущие задачи идут отдельной группой: их не надо распределять, надо разблокировать.

## task checklist — пункты чек-листа задачи (суб-ресурс task)
`task checklist add <task-ref> --text* [--due YYYY-MM-DD]` · `task checklist list <task-ref>` · `task checklist check <item-id> [--uncheck]` · `task checklist delete <item-id>`.

## task member — участники задачи, роли (суб-ресурс task)
`task member add <task-ref> --participant* [--role responsible|executor|watcher]` (деф. executor) · `task member list <task-ref>` · `task member rm <task-ref> --participant --role`. Роли: responsible / executor / watcher. (Не путать с `project member` — люди проекта.)

## hypothesis — реестр гипотез (Atlas Hypothesis Ledger)
`add --project* --title* [--statement «если X то метрика Y↑ на Z» --metric --baseline --target --method --task --confidence H|M|L --status draft|testing|measured|closed --slug]` · `list` · `get <ref>` · `update <ref> --…` (status-переходы авто-timestamp) · `close <ref> --verdict …` (status=closed, closed_at, опц. замер) · `delete <ref> [--hard]`.

## counterparty — контрагенты: с кем у нас отношения (уровень НАД проектом)

`counterparty add --name* [--kind person|company --slug --note]` · `counterparty list [--kind]` · `counterparty get <ref>` · `counterparty update <ref> --…` · `counterparty attach <проект> --customer <ref> | --owner <ref>` · `counterparty orphans`.

Зачем отдельная сущность, а не тег: проект отвечает на «что делаем», контрагент — на «для кого» и «чьё это». Один контрагент держит несколько проектов, и вопрос «что у нас по клиенту» без него не отвечается — приходится помнить, какие слаги чьи.

**Две роли, и путать их дорого.** `--customer` — заказчик, тот, кто платит и принимает; `--owner` — владелец, чьё это дело (у своих продуктов заказчика нет вовсе, а владелец есть). Проект без контрагента виден в `orphans`: это дыра, а не норма — пока принадлежность не проставлена, работа по проекту невидима на уровне отношений.

## person — люди портфеля (реестр; бывш. `participant`)
`person add --name --kind human|ai_agent|contractor --slug --role …` · `person list` · `person get <ref>` · `person update <ref>` · `person delete <ref> [--hard --force --soft]`. `--force` каскадит FK (снимает с проектов/задач), `--soft` = is_active=False. (Доменная модель в БД/аудите — по-прежнему `Participant`; переименован только CLI-фасад.)

## profile — онбординг отдельного Atlas-стора (профиль = своя БД + ключ)
`profile register <slug> --name … [--scope --member --global-role]` — завести НОВЫЙ профиль-стор (изолированная `atlas.db` + свой api_key), напр. `admin`/`test`. Переключение между сторами — глобальный флаг `atlas --profile <slug> …`. Это НЕ «дефолтный actor/владелец» (тот — `config owner`), а отдельное хранилище портфеля. Точные флаги — `atlas profile --help`.

## type / status / tag — справочники
- `type list` (колонка Group) / `type add --slug --name [--group clients|products|tests|inbox]` / `type update <ref> --name/--description/--color/--group` (slug неизменен). **10 канон-типов**: client-project / business-product / personal-utility / personal-project / shared-infrastructure / test / inbox + **роли** kit / service / superskill. Единый источник `BASE_PROJECT_TYPES` + user-override `~/.atlas/types.toml` (merge by slug); `storage_group` = физ-группа на диске.
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
- `backlog add --title* [--note --project --priority --slug --source --md]` — завести идею (global если
  без `--project`; `--source inbox` — сырьё-свалка на разбор AI).
- `backlog list [--project --global --status open|converted|archived|all]` — вид «идеи»; показывает И
  legacy idea/inbox-записи БД (единый вид). `backlog show <ref>` · `backlog edit <ref> --…` · `backlog archive <ref> [--hard]`.
- `backlog convert <ref> --as task --project <p> --cpp "…"` → создаёт `todo`-задачу;
  `--as epic --project <p>` → поднимает эпик; если запись пришла из `epic unconvert`, вместе с ним
  восстанавливаются и его задачи с прежними связями;
  `--as project [--type --slug --setup-layout --canonical --init-git --private --group]` → зачаток проекта
  **С МАТЕРИАЛИЗАЦИЕЙ** (layout/junction + canonical README/AGENTS + опц. git) — эквивалент прежнего `idea promote`.
- Раздельные виды: `backlog list` (что преобразовать) vs `task list` (`todo` — что брать в работу). Новая задача создаётся в `todo`.

> **idea / inbox как top-level команды УБРАНЫ (#867).** Единый интейк — `backlog`: лёгкая идея →
> `backlog add`; сырьё-свалка → `backlog add --source inbox`; материализация в проект →
> `backlog convert --as project` (layout/canonical/git). Legacy-записи `entity_kind=idea/inbox`
> в БД по-прежнему видны в `backlog list`.

## log — журнал событий портфеля (поверх action_log)
- `log list [--limit --project --entity-type --action --actor --since]` — обогащённо: кто / что / проект / приоритет (человеку Rich, агенту `--json`; бывш. `atlas logs`).
- `log raw [--project --actor --entity-type --action --since --limit]` — сырой append-only аудит (бывш. `atlas action-log list`; read-only, каждый CRUD пишется автоматически, вручную не редактировать).

## backup — snapshot git-репо портфеля → ветка `backup` (HEAD не трогается)
`run [--type --status --tag --ref --dry-run]` · `status [--days N]` · `schedule install [--time HH:MM]` (Windows Task, деф. 03:00) · `schedule uninstall` · `schedule list`.

## config — онбординг + дефолты (config.toml)
> **Владелец из коробки:** `owner` по умолчанию = **`admin`** (#899) — atlas работает сразу после
> `project init`, участник `admin` заводится сидом, настройка не обязательна. Сменить владельца:
> `atlas config set owner <slug>` (+ `atlas person add --slug <slug> --kind human --name "…"`, если
> такого участника ещё нет). Добавить других людей/агентов — `atlas person add`.

`show` · `get <key>` · `set <key> <value>` · `setup` (интерактивный визард; бывш. `config init`). Поля: `owner` (дефолтный
actor/владелец), `timezone`, namespaces, `team_owner`, и **дефолты задач**: `default_priority` (P2),
`default_review` (bool — заводить ли reviewer), `default_reviewer` (slug; пусто → создатель). Дефолты
применяются в `task add`/`batch`, если не заданы явно. `api_key` — только env/secret-store.

## Топ-уровень (без группы): dashboard / init / setup / stats / update  (+ ресурс-группы `backend`, `log`)
- `atlas dashboard [--project <ref>] [--json]` — операционный обзор: KPI, задачи по статусам/приоритетам, что в работе (in-flight + держатель), внимание (blocked/overdue/протухшие lease), по проектам, активность. По умолчанию Rich для человека; `--json` — для агента.
- `atlas init [--scope global|repo|all] [--agents …] [--create] [--dry-run] [--json]` — идемпотентно дописывает Atlas-дисциплину (managed-блок между маркерами) в агентские файлы. Аддитивно: чужой текст не трогается.
  - Без `--agents` — легаси: все существующие агентские файлы (`~/.claude/CLAUDE.md`, репо `AGENTS.md`/`CLAUDE.md`/`GEMINI.md`/`.cursorrules`).
  - `--agents claude,gemini,cursor,codex,copilot` (или `all`) — **точечный выбор** агентов; с `--create` создаёт их файлы (включая вложенный `.github/copilot-instructions.md`). Реестр агент→файл (18 агентов) и весь механизм онбординга — в ките **`agentskit`** (`agentskit.AGENT_REGISTRY`/`onboard`); Atlas приносит только контент (`atlas.discipline.DISCIPLINE_BODY` + namespace `atlas`).
- `atlas setup [--scope global|repo|all] [--agents …] [--no-rules] [--no-hooks] [--uninstall] [--dry-run]` — **turnkey-онбординг** в агента: правила (как `init`) **+** SessionStart-хук для Claude Code. Хук пишет `~/.claude/hooks/session_atlas.py` и идемпотентно мержит `~/.claude/settings.json` (гоняет `atlas task triage`, впрыскивает сводку портфеля в старт сессии), НЕ трогая чужие хуки. `--uninstall` снимает хук; `--dry-run` — превью. Зовётся установщиками `install.ps1`/`install.sh` после установки CLI.
- `atlas stats [--period <spec>] [--provenance] [--project <ref>]` — аналитика (counts/окно активности/provenance/git).
- `atlas backend connect [<url>] [--key K] [--no-verify]` — подключить backend (синк); ключ → secret-store. `atlas backend status` — статус; `atlas backend disconnect` — отключить. **Local-first**: всё работает без подключения; `sync push/pull` — только после connect. (Журнал событий — ресурс `atlas log`, см. выше.)
- `atlas update [--check] [--from-git]` — self-update CLI с PyPI (дистрибутив **atlas-pm**, команда/import `atlas`): детектит менеджер (uv/pipx/pip) и ставит свежую версию; `--check` — показать текущую/доступную; **`--from-git`** — legacy pipx-reinstall из git (заменяет убранную команду `atlas upgrade`).

---

> **Синхронизация с внешним бэкендом — вне текущего публичного скоупа.** Atlas самодостаточен:
> весь портфель живёт в локальном SQLite. Команды/профили для синка с внешним хабом — опциональная
> фича, не входящая в этот релиз.
