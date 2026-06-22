# Atlas: канон типов проектов (конфиг-типы + роли) + лёгкий контейнер — дизайн

**Дата:** 2026-06-23
**Статус:** на ревью / в реализацию
**Репозиторий:** `_storage/atlas`

## Цель

Привести типы проектов к канону: (1) тип = единый источник правды (не 3 хардкод-списка),
конфиг-управляемый (базовые + кастом под пользователя); (2) добавить роли-типы `kit`/`service`/
`superskill`, которые выражают реальные классы портфеля; (3) дать лёгкий контейнер (`parent_id`),
чтобы клиент/зонтик группировал модули-проекты. Локально в Atlas (тип на хаб не синкается).

## Контекст (факты разведки)

- Тип сейчас размазан по 3 хардкод-спискам: `seeds.PROJECT_TYPES`, `seeds.DEFAULT_SYNC_POLICY_BY_TYPE`,
  `paths.TYPE_TO_GROUP`. Их держат в синхроне вручную. `type add` создаёт тип без политики и без группы
  → `type_slug_to_group` падает ValueError на неизвестном slug. `test`/`inbox` есть в маппингах, но как
  типов в `PROJECT_TYPES` НЕТ (фантомы).
- `ProjectType` (`models.py:46-65`): id/slug/name/description/color/is_archived/default_sync_policy/created_at.
- `Project` нет `parent_id` (вложенности нет); есть owner_id/customer_id/sync_policy/entity_kind.
- 5 канонических lifecycle-статусов (отдельная ось): active/paused/archived/cancelled/experiment.

## Не входит в scope (spec #3 / шаг разноса бэклога)

- Автоматизация git нового модельного контейнера (вложенные независимые репо, `.gitignore modules/`),
  junction-layout модулей (`project layout` для `<container>/modules/<m>/`), кросс-репо backup-скрипт.
- Применение канона к существующим 44 проектам (re-type gatewaykit→service и т.д.) — это **шаг разноса**
  после реализации (с provenance).
- Регистрация типа на бэкенде (тип локальный; не меняем).

---

## Часть A. Конфиг-управляемые типы (единый источник правды)

### Модель
`ProjectType` += `storage_group: String(20)` (clients|products|tests|inbox) — куда раскладывать проект
этого типа физически. Заменяет `paths.TYPE_TO_GROUP`. Nullable с дефолтом `products` (безопасно).

### Источник типов (base + user, вместо хардкода)
- **Базовые типы** — встроенный дефолт (Python-данные или упакованный `data/base_types.toml`): полная
  запись каждого `{slug, name, description, color, default_sync_policy, storage_group}`. Один список
  вместо трёх. Сюда же — текущие 5 + роли (часть B) + `test`(group=tests, policy=local) +
  `inbox`(group=inbox, policy=local) как ПОЛНОЦЕННЫЕ типы (чинит фантомы).
- **Пользовательские** — `types.toml` (путь: `~/.atlas/types.toml`, override через env `ATLAS_TYPES_FILE`).
  Опциональный. Каждая запись — те же поля; **merge by slug** (user переопределяет/дополняет base).
- `seed_project_types` (seeds.py) читает merged-список вместо литерала `PROJECT_TYPES`. Идемпотентно:
  существующий тип обновляется по slug (name/desc/color/policy/group), новый — создаётся.

### paths
`type_slug_to_group` читает `ProjectType.storage_group` из БД (а не `TYPE_TO_GROUP`). Fallback на
`products`, если тип/группа не найдены (вместо ValueError). `TYPE_TO_GROUP` удаляется (или остаётся как
аварийный дефолт для bootstrap до первого seed).

### CLI
- `type add` += `--default-sync-policy <slug>` (валидация против `sync_policies`), `--group
  clients|products|tests|inbox`. Без них — дефолты (policy `local`, group `products`).
- `type edit <ref>` (новая) — `--name --description --color --default-sync-policy --group` (slug не
  меняется). `type list` показывает колонки group + default_sync_policy.
- `type archive <ref>` (опц.) — `is_archived=True` (не удаляем, тип может быть на проектах).

### Миграция
Alembic: `ProjectType` += `storage_group` (nullable, backfill существующих по текущему TYPE_TO_GROUP в
upgrade). Безопасно для прода.

---

## Часть B. Роли-типы (выражают реальные классы)

Добавить в базовые типы (часть A):

| slug | name | назначение | storage_group | default_sync_policy |
|---|---|---|---|---|
| `kit` | Kit / SDK-тулкит | Переиспользуемый SDK (BaseX+registry+contract-tests): adapterkit, clikit, librarykit | products | epics |
| `service` | Сервис | Деплоится, состояние, composition root, потребляет киты: gateway, bublictr, workerkit | products | epics |
| `superskill` | Супернавык | skill+CLI+lib под сервис, dual (шарится + адаптер): notebooklm, yt-uploader | products | epics |

`factory` НЕ добавляем — reverse-factory выражается как контейнер (часть C) + provenance
(`source_project` произведённых супернавыков). `client-project` остаётся для единого продукта под клиента;
«клиент-контейнер» — это контейнер (часть C) + counterparty, не тип.

---

## Часть C. Лёгкий контейнер (parent_id)

### Модель
`Project` += `parent_id: String(36)`, FK→`projects.id` (ORM-уровень, как provenance-FK), nullable, index.
Проект с детьми = **контейнер**; с родителем = **модуль**; без обоих = **standalone**. Counterparty
(`customer_id`) ортогонален — «все проекты клиента» = фильтр по контрагенту ИЛИ навигация по контейнеру.

### CLI
- `project add --parent <ref>` — завести проект как модуль контейнера (резолв ref→id; self/цикл → ошибка).
- `project update <ref> --parent <ref>|--no-parent` — привязать/отвязать.
- `project get <ref>` — показывает `Parent` (если модуль) и `Modules` (список детей, если контейнер).
- `project list --parent <ref>` — модули контейнера; `--standalone` — без родителя (опц.).
- action_log фиксирует смену parent.

### Миграция
Alembic: `Project` += `parent_id` (nullable, index). Одной ревизией с частью A (storage_group).

### Вне scope (spec #3)
Физика: вложенные репо / junction `<container>/modules/<m>/` / git-workflow модулей — НЕ здесь.
Здесь только логическая иерархия в БД + CLI + отображение.

---

## Поток данных (пример)

```
atlas type add --slug worker-kit ... ИЛИ правка ~/.atlas/types.toml → seed подхватит
atlas project add --name "Med-Persona" --type client-project --customer persona26   # контейнер
atlas project add --name "Kibersoft Sync" --type service --parent med-persona        # модуль
atlas project get med-persona      # Modules: kibersoft-sync (service)
atlas project list --parent med-persona
```

## Обработка ошибок

- `--parent` self / цикл (A→B→A) → ошибка, не сохраняем.
- `--group` / `--default-sync-policy` неизвестные → ошибка валидации.
- `types.toml` с битым slug/policy → понятная ошибка при seed, не падаем молча.
- Миграция: все новые колонки nullable/с backfill — безопасно для существующих 44 проектов.

## Тестирование (TDD)

- Конфиг-типы: base-only seed заводит все типы с group+policy; `types.toml` override меняет/добавляет тип
  (merge by slug); `type add --group --default-sync-policy` сохраняет; `type edit` меняет; `type_slug_to_group`
  читает из БД; неизвестный тип → fallback products, не ValueError.
- Фантомы: `test`/`inbox` теперь полноценные типы (group tests/inbox).
- Роли: kit/service/superskill засеяны с верными group/policy.
- Контейнер: add --parent делает модуль; get показывает Parent/Modules; list --parent фильтрует;
  цикл/self → ошибка; update --parent/--no-parent.
- Миграция: апгрейд throwaway-БД, storage_group backfill, parent_id присутствует.
- Регрессия: весь suite зелёный; боевая БД мигрируется отдельно с бэкапом.

## Открытые вопросы

1. `types.toml` путь — `~/.atlas/types.toml` (глобально) или на профиль (`profiles/<p>/types.toml`)?
   (Рек.: глобальный `~/.atlas/types.toml`, типы — свойство пользователя, не стора.)
2. `shared-infrastructure` — оставить как есть или поглотить в `kit`/`service`/`infra`? (Рек.: оставить
   как зонтик «инфра вообще», роли kit/service уточняют; re-type — на шаге разноса, по решению.)
