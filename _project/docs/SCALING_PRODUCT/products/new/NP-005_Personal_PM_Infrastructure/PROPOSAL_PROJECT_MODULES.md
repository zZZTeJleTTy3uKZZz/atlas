# PROPOSAL: проекты с несколькими git-репозиториями (modules)

> **Статус:** draft / awaiting decision
> **Автор:** Claude (по запросу Дмитрия) — 2026-05-02
> **Триггер:** реальный кейс при prod-деплое `med-persona` → нужен отдельный репозиторий для модуля `b24-kibersoft-sync`, при этом семантически это часть проекта `med-persona`. Текущая модель Atlas «1 project = 1 git_repo_url» этого не выражает.

---

## 1. Контекст и постановка проблемы

### Сегодня

Atlas-таблица `projects` содержит поле `git_repo_url` (1 URL на проект). Один atlas-проект жёстко 1-к-1 связан с одним git-репо в GitLab.

### Реальный кейс (med-persona)

При подготовке prod-deploy `b24-kibersoft-sync` понадобился отдельный git-репозиторий, чтобы:
- на VPS клонировать **только модуль**, а не весь monorepo (10+ модулей, 100+ MB лишнего кода)
- `git pull` на проде поднимал только нужный код, без шанса случайно подтянуть незавершённый код других модулей
- логи git-pull/CI/CD читались легко (один репо = один поток коммитов под этот контур)
- права доступа управлялись отдельно (deploy token только на этот модуль, без доступа к monorepo)

В результате в GitLab была сделана структура:

```
cifropro1/clients/
└── med-persona/                          ← group (новая)
    ├── med-persona/                      ← общий monorepo (full backup)
    └── b24-kibersoft-sync/               ← модуль для prod-deploy
```

Atlas же остался с одной записью `med-persona` и одним `git_repo_url` (теперь указывающим на monorepo). Информация о существовании `b24-kibersoft-sync` в Atlas отсутствует.

### В чём боль

1. **Невидимость модулей в Atlas.** `atlas projects list` не покажет что у `med-persona` есть отдельный prod-репозиторий. Если кто-то другой (или сам Дмитрий через 3 месяца) спросит «где код синхронизатора и куда он деплоится» — atlas не ответит.
2. **Нет place для prod-метаданных модуля.** `med-persona` сейчас имеет один `Path:` (локальный путь), один `Git:`. Где хранить `prod_url`, `vps_ip`, `deploy_token_id`, `deploy_strategy=git-pull`, `last_deploy_at` для отдельного модуля? Нигде.
3. **Action-log привязан к проекту.** События (commit, deploy, alert, incident) сейчас пишутся в action-log одного проекта. Если модулей несколько — все события смешиваются, не видно «что именно с b24-ks-sync».
4. **Появятся другие модули.** `web-quiz-integration`, `analytics-dashboard`, `bitrix-app-installer` — каждый кандидат на свой репо/прод. Без модели в Atlas через год это станет хаосом.

---

## 2. Что предлагается

Ввести в Atlas первоклассную сущность **`module`** — суб-объект проекта с собственным репо и прод-метаданными.

### 2.1 Модель данных

Новая таблица `modules`:

| Поле | Тип | Назначение |
|---|---|---|
| `id` | UUID PK | |
| `slug` | TEXT UNIQUE NOT NULL | например `med-persona-b24-sync`, формат `<project_slug>-<module_short>` |
| `project_id` | UUID FK → projects | parent |
| `name` | TEXT | человекочитаемое |
| `one_line` | TEXT | краткое описание |
| `git_repo_url` | TEXT | свой git URL (отдельный репо) |
| `local_path` | TEXT | путь на dev-машине (опционально, может совпадать с подкаталогом проекта) |
| `prod_url` | TEXT NULL | публичный URL прод-инсталляции, если есть |
| `prod_host` | TEXT NULL | IP/hostname VPS |
| `deploy_strategy` | TEXT NULL | enum: `git-pull` / `ci-cd` / `manual` / `docker-image` |
| `status` | TEXT FK → statuses | active / experiment / deprecated / archived |
| `priority` | TEXT | P0..P3 |
| `created_at`, `updated_at`, `archived_at` | TIMESTAMP | |

Действия:
- `atlas modules add --project med-persona --slug b24-sync --name "B24-KS Sync" --git-repo-url https://gitlab.com/cifropro1/clients/med-persona/b24-kibersoft-sync.git --prod-url https://sync.persona26med.ru --prod-host 185.93.111.51 --deploy-strategy git-pull`
- `atlas modules list [--project med-persona]`
- `atlas modules get med-persona/b24-sync` (или просто `b24-sync` если slug уникален глобально)
- `atlas modules update`, `atlas modules archive`, `atlas modules delete`
- `atlas projects get med-persona` теперь дополнительно показывает блок «Modules» со списком

### 2.2 Atlas-команды для git-операций

```bash
atlas modules clone b24-sync --to /opt/b24-sync/code
# ↑ читает git_repo_url из БД, делает git clone в указанную папку

atlas modules deploy b24-sync
# ↑ ssh-ится на prod_host под user deploy, делает git pull, опционально перезапускает docker-compose

atlas modules log b24-sync
# ↑ git log + список последних деплоев из action_log
```

### 2.3 Action-log

Расширить `action_log` колонкой `module_id` NULL FK. События привязываются либо к проекту целиком, либо к конкретному модулю. `atlas modules log` фильтрует по `module_id`.

### 2.4 GitLab convention (рекомендуемая, не enforced)

Когда у проекта появляется первый модуль — в GitLab:

```
cifropro1/<group>/
└── <project_slug>/                       ← group
    ├── <project_slug>/                   ← главный monorepo (или просто backup)
    └── <module_slug>/                    ← каждый отдельный модуль
```

То есть «один проект → одна group в GitLab, в группе минимум один проект (`monorepo`), плюс модули рядом».

Atlas-команда `atlas projects init-gitlab-layout <project_slug>` может автоматизировать это:
- если в GitLab проект-без-группы — переименовать его в `<slug>-tmp`, создать group, transfer + rename обратно
- запомнить новый URL в БД

---

## 3. Альтернативы (рассмотрены и отвергнуты)

### A. Хранить несколько URL в одном projects.git_repo_url (JSON/comma-separated)

❌ ломает UI (`atlas projects get` показывает один Git), ломает интеграции (которые читают как single string), нет места для per-repo метаданных (prod_url, deploy_strategy и т.д.).

### B. Заводить отдельный atlas-project на каждый модуль (med-persona и med-persona-b24-sync рядом, без иерархии)

❌ это и было моё первое предложение. Минусы:
- иерархия теряется — нужно вручную через теги/префиксы догадываться что это subset того же клиента
- статусы дублируются (если архивируем клиента — надо вручную архивировать N модулей)
- action-log размывается между projects
- `atlas projects list` загружается мусором (5 «проектов» вместо 1 с модулями)

### C. Использовать GitLab subgroups как единственный источник правды, без отражения в Atlas

❌ Atlas теряет смысл единого реестра. Запросы вида «куда задеплоен этот код» / «когда был последний commit в prod» придётся делать через `glab`, не через `atlas`. Это разрушает паттерн «atlas — единая точка входа».

### D. Submodules (git submodule в monorepo)

❌ ортогонально вопросу — submodules решают как код шарится между репо, а наш вопрос про **метаданные в Atlas**. Можно их использовать поверх предложения, но без модели modules в Atlas всё равно непонятно где prod-info жить.

---

## 4. Migration path

### 4.1 Для med-persona (текущий кейс)

После релиза фичи:

```bash
atlas modules add \
    --project med-persona \
    --slug b24-sync \
    --name "B24 ↔ Kibersoft Sync" \
    --one-line "Двусторонняя синхронизация Bitrix24 ↔ Kibersoft (МИС) для Med Persona" \
    --git-repo-url https://gitlab.com/cifropro1/clients/med-persona/b24-kibersoft-sync.git \
    --local-path ~/Documents/PROJECT/_storage/Med-Persona/modules/B24_Kibersoft_Sync \
    --prod-url https://sync.persona26med.ru \
    --prod-host 185.93.111.51 \
    --deploy-strategy git-pull \
    --status active --priority P0
```

`atlas projects get med-persona` после этого покажет:

```
med-persona  — Med Persona
  Git:       https://gitlab.com/cifropro1/clients/med-persona/med-persona.git
  ...

Modules (1):
  • b24-sync  — B24 ↔ Kibersoft Sync  [P0 active git-pull → 185.93.111.51]
```

### 4.2 Для остальных проектов

Опциональная команда `atlas modules detect <project>`:
- сканирует папку проекта
- ищет вложенные `.git/` (nested-репо)
- для каждого читает `remote.origin.url` и предлагает создать module с этим URL

### 4.3 Backwards compat

Старые `atlas projects` команды не ломаются. Поле `projects.git_repo_url` остаётся (это URL **главного / monorepo**). Модули — дополнение.

---

## 5. Open questions

1. **Уникальность slug модуля.** Глобально по всей БД (как у проектов) или scoped to project? Я бы за **глобально** — упрощает CLI (`atlas modules get b24-sync` без префикса), и вряд ли два модуля у разных клиентов получат одинаковое имя. Но требует `<project>-<module>` нейминг-конвенции.

2. **Один tags table или дублировать на modules?** Скорее переиспользовать `tags` через `module_tags` link-table — иначе дубль логики.

3. **Stage / status модулей независим от проекта?** Скорее да: модуль может быть `experiment` пока проект `active`. Иначе теряется гранулярность.

4. **Auto-detect модулей при `projects init`.** Стоит ли в `atlas projects init` (когда добавляем существующую папку) автоматически предлагать создать модули из nested .git? Я за: уменьшает ручную работу.

5. **Sprint planner и модули.** Сейчас `atlas sprint plan` работает с tasks привязанными к project. Нужно ли позволять привязку `pm_tasks.module_id`? Скорее да — но это уже фолоу-ап.

6. **`atlas modules deploy` — насколько умной должна быть команда.** Минимальная версия: `ssh prod_host "cd <path> && git pull"`. Расширенная: знает про docker-compose, делает `up -d`, ждёт healthcheck. Я бы стартовал с минимальной + хук post-deploy через `~/.atlas/hooks/<module_slug>.sh`.

---

## 6. Размер работ (грубо)

| Что | Усилия |
|---|---|
| Migration: новая таблица `modules`, link-table `module_tags`, FK `pm_tasks.module_id` (NULL) | ~2-3 часа |
| CLI команды `atlas modules {add,list,get,update,archive,delete,clone}` | ~4-6 часов |
| Расширение `atlas projects get` — рендер блока Modules | ~1 час |
| `atlas modules deploy` MVP (ssh+git pull) | ~2-3 часа |
| Опционально: `atlas projects init-gitlab-layout` (rename + group + transfer) | ~3-4 часа (можно отложить) |
| Tests + docs | ~3-4 часа |

**Итого MVP без `init-gitlab-layout`:** ~12-17 часов.

---

## 7. Рекомендация

**Реализовать.** Без этого через 3-6 месяцев Atlas станет неточным (часть кода/деплоев не видна), и придётся либо:
- разбивать клиентов на N atlas-проектов (теряя иерархию), либо
- хранить prod-метаданные в `description` как свободный текст (теряя машиночитаемость)

Оба пути хуже, чем ввести `modules` сейчас, пока случай свежий и реальный (med-persona/b24-ks-sync).

Минимальный путь: пп. 2.1 + 2.2 (без 2.4 GitLab-автоматизации) — за один спринт.

---

## 8. Связанные документы

- `BACKLOG.md` (рядом) — добавить эпик «modules support» с этой ссылкой
- `MODEL.md` — обновить ER-схему после реализации
- `ARCHITECTURE.md` — добавить раздел про модули и их прод-метаданные
- `med-persona` runbook: `_storage/Med-Persona/modules/B24_Kibersoft_Sync/docs/RUNBOOK_DEPLOY_PROD.md` — реальный пример что хочется выразить в atlas
