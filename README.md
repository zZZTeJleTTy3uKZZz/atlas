# Atlas

**Local-first PM-система для портфеля проектов и задач.** Всё живёт в локальном
SQLite (`~/.atlas/atlas.db`) и работает без сети — самодостаточно, без внешних
сервисов.

```
Atlas (SQLite, local-first) — проекты · задачи · идеи · гипотезы
```

CLI `atlas` единообразен (единственное число команд), `--json` по умолчанию
(удобно для скриптов/AI-агентов), `--text`/`--plain` — человекочитаемый вывод.

## Возможности

- **Портфель проектов**: типы, статусы, теги, владельцы, архив, git-раскладка.
- **Задачи** с ЦКП (ценный конечный продукт), чек-листами, участниками, эпиками,
  lease/claim для мультиагентной координации.
- **Идеи и inbox** — инкубатор и свалка сырья на разбор.
- **Гипотезы** — фальсифицируемый ledger по продукту/маркетингу.
- **Бэкап**, аудит (append-only action-log), статистика портфеля.

## Установка

Требуется Python ≥ 3.11.

**Рекомендуемый путь — через [skillery](https://skillery.ru)** (тогда заодно
ставится навык для Claude-агента — см. ниже):

```bash
skillery install atlas      # ставит CLI + навык
```

Без skillery — напрямую из git ([uv](https://docs.astral.sh/uv/) или pipx):

```bash
pipx install "git+https://github.com/zZZTeJleTTy3uKZZz/atlas.git"
# или для разработки:  uv sync --extra dev
```

Проверка: `atlas --version` и `atlas --help`.

## Обновление

```bash
atlas upgrade            # pipx из git: pipx upgrade atlas
atlas upgrade --reinstall  # принудительная переустановка из git (force)
atlas upgrade --check    # только показать версию + метод установки
```

- **skillery** — обновляй через skillery (re-install подтянет новые deps).
- **pipx из git** — `atlas upgrade` (= `pipx upgrade atlas`, тянет свежий коммит).
- **editable (dev)** — код живой, обнови `git pull` в репозитории.

## Онбординг (первый запуск)

1. **Задайте владельца стора** (ваш member-slug) — он станет дефолтным
   участником-актором и владельцем новых проектов:

   ```bash
   atlas config set owner alice        # ваш slug (kebab-case) → config.toml
   atlas config show                   # посмотреть весь конфиг
   ```

   Опционально там же — `org_namespace` / `personal_namespace` / `personal_owner`
   (git-раскладка), `base_url` (адрес backend для синка). Конфиг читается слоями
   `config.toml < .atlas.toml в проекте < env ATLAS_*` (на сессию можно env
   `ATLAS_OWNER=alice`). Секрет `api_key` — только через env / secret-store.

2. **Инициализируйте БД** (миграции + базовые справочники):

   ```bash
   atlas project init
   ```

   Создаёт `~/.atlas/atlas.db`, применяет миграции и заселяет типы/статусы/теги +
   участников (claude-code + ваш `owner`). Идемпотентно — повторный вызов безопасен.

3. **Готово** — создавайте проекты и задачи (см. ниже). Если `owner` не задан,
   Atlas всё равно работает, но участника-владельца придётся указывать явно
   (`--owner` / `--assignee`).

## Быстрый старт

```bash
# проект (личный по умолчанию; --team — командный)
atlas project add --name "Мой лендинг" --slug my-landing --type personal-project

# задача с ЦКП
atlas task add --project my-landing --title "Собрать структуру" \
  --cpp "Готов wireframe из 6 секций" --priority P1

# взять задачу в работу (lease-лок, атомарно)
atlas task claim <number|slug> --ttl 2h

# список (человекочитаемо)
atlas --text task list --project my-landing
```

## Конфигурация

Atlas читает слоистый конфиг (global `config.toml` < project `.atlas.toml` <
local < env `ATLAS_*`). Глобальный файл — в OS-config-каталоге (создаётся
командой `atlas config set …`). Ключевые поля (все опциональны, дефолты — generic):

| Поле / env | Назначение |
|---|---|
| `owner` / `ATLAS_OWNER` | member-slug владельца стора (дефолтный actor аудита, владелец новых проектов) |
| `org_namespace` / `ATLAS_ORG_NAMESPACE` | организационный git-namespace для раскладки проектов |
| `personal_namespace` / `ATLAS_PERSONAL_NAMESPACE` | личный git-namespace (для проектов с owner-тегом) |
| `personal_owner` / `ATLAS_PERSONAL_OWNER` | значение owner-тега, переключающее на личный namespace |
| `team_owner` / `ATLAS_TEAM_OWNER` | counterparty-владелец по умолчанию для `--team`-проектов |
| `timezone` / `ATLAS_TIMEZONE` | часовой пояс PM-БД (фиксированный offset, напр. `+03:00`) |

Без конфига Atlas полностью работает локально; владельца/namespaces задаёте под
себя.

## Навык для Claude / skillery

Репозиторий несёт навык в `skills/atlas/` (`SKILL.md` + `agents/` + `references/`) — Atlas можно
поставить как **tooling-навык** через [skillery](https://skillery.ru): install
материализует навык и ставит сам CLI (`_skill_meta.toml`). Тогда AI-агент знает,
как и когда пользоваться `atlas`.

## Разработка

```bash
uv sync --extra dev
uv run pytest -q          # тесты
uv run ruff check .       # линт
```

Миграции БД — через Alembic (`uv run alembic upgrade head`). Дисциплина:
TDD, миграции в git до деплоя, осмысленные коммиты.

## Лицензия

[MIT](LICENSE).
