# Atlas → S-kits: миграция синка / сессий / CLI на переиспользуемые киты — дизайн

**Дата:** 2026-06-23
**Статус:** утверждён, в реализацию
**Репозиторий:** `_storage/atlas`
**Задачи:** #196 (этот дизайн) · #200 (синк → librarykit-транспорт + adapterkit-структура) · #201 (сессии/ключи → librarykit) · #202 (CLI → clikit)
**Эпик:** `atl-skits-migration`

## Цель

Перевести Atlas с прямого httpx-синка и открытого хранения ключей на канон S-kits:
транспорт и сессии — из **librarykit**, декларативная структура сетевого адаптера — из
**adapterkit**, CLI-каркас — из **clikit**. Миграция кусками, каждый кусок — с зелёными
тестами, без регрессий (база `863 passed`).

## Проверенная карта китов (ground truth, по коду 2026-06-23)

| Кит | Версия | Владеет | Берём для |
|---|---|---|---|
| **librarykit** | 0.1.0 (локально) / 0.1.3 (PyPI) | Сетевой движок: `HttpClient`, `HttpxTransport`, `RetryPolicy` (header-driven), `ErrorMap`/`build_error_map`, `CursorPaginator`, `SessionStore` (envelope), `SecretStore`, `resolve_kek`, `Auth` | транспорт + ключи |
| **adapterkit** | 0.1.2 | Тонкий коннектор поверх librarykit: `NetworkAdapter`, `BaseAdapter`+`Endpoint`, `AdapterRegistry`+entry-points. Транспорт **реэкспортит из librarykit** (shim), не владеет | только структура (Endpoint-таблица) |
| **clikit** | 0.1.1 (локально) / 0.1.3 (PyPI) | CLI-каркас: `build_root_app`, `emit_data`, `AppConfig`, `command`/`async_command`, `CliError` | CLI |

Вывод: транспорт/retry/errmap/пагинация/сессии — это **librarykit** (владелец). adapterkit
добавляет только `BaseAdapter`/`Endpoint`/registry. `adapterkit-sdk`-навык описывал
домонолитный adapterkit и удалён как устаревший — единый источник правды теперь `kit-integration`.

## Решения

- **Источник зависимостей:** PyPI через `uv add` (`s-librarykit`, `s-adapterkit`; `s-clikit`
  перевести с локального `path=../clikit` на PyPI). Доступность подтверждена на PyPI.
- **#201 ключи:** librarykit `SessionStore` (envelope-шифрование) + `SecretStore`
  (keyring ОС + file-fallback) для KEK. `api_key` уходит из открытого `config.toml`.
- **#202 CLI:** clikit — чистка shim-импортов на владельцев + аудит консистентности `--json`.
- **#200 синк:** Hybrid — librarykit `HttpClient(HttpxTransport, RetryPolicy, ErrorMap)`
  под adapterkit `BaseAdapter` + `Endpoint`-таблицей (7 эндпоинтов ядра). Без registry/entry-point
  (бэкенд один). Закрываем дыру «нет error-mapping».

## Архитектура (target)

### Синк (#200)
`BackendAdapter(BaseAdapter)` с Endpoint-таблицей:
`push_events` · `poll_events` (long-poll) · `register_profile` · `provision_project` ·
`patch_project` · `link_project` · `health`.

- Транспорт: `HttpClient(HttpxTransport(base_url, retry=RetryPolicy(...)), TokenAuth(api_key, scheme="", header="X-API-Key"), error_map)`.
- `ErrorMap` через `build_error_map` по контракту ядра: коды/статусы → `RateLimited` /
  `AuthRequired` / `NotFound` / `ServerError` / `Blocked`. Сейчас error-mapping нет — это закрывается.
- `poll_events` — long-poll по курсору; таблица `SyncCursor` сохраняется; HTTP-вызов идёт
  через адаптер. Backoff остаётся в `watch_loop` (или `RetryPolicy` для транзиентных). Длинный
  poll-timeout не должен зарезаться transport-timeout.
- `push.py`/`pull.py`/`profile`/`provision` переводятся на методы `BackendAdapter`.
- Тесты: ~60 sync переписать на новый транспорт (инъекция `HttpClient`/`StubTransport` вместо
  текущего `clikit.HttpClient`), + новые на `ErrorMap`.

### Сессии / ключи (#201)
- `api_key` больше **не** в `config.toml` открытым. Хранится через librarykit `SessionStore`
  (envelope: Fernet(DEK), DEK wrapped KEK).
- KEK через `SecretStore`: keyring `atlas/kek` → file-fallback; порядок `resolve_kek` librarykit.
  Env-override KEK для headless (по аналогии с librarykit-каноном). Молча plaintext не писать
  (`KekUnavailableError`).
- `AtlasConfig`: `base_url`/`portal_id`/`scope`/`timezone` остаются в TOML; поле `api_key`
  опционально (фактический ключ — в `SessionStore` по `SessionRef(profile=<slug|default>)`).
- `profile register`: получив `api_key` от ядра → `SessionStore.save_state(ref, {...})` зашифрованно.
- Чтение ключа при старте `BackendClient`: `load_state(ref)` → расшифровать.
- **Back-compat:** открытый `api_key` в TOML → при первом доступе перенести в `SessionStore`
  и затереть поле в TOML (atomic_write). `env ATLAS_API_KEY` по-прежнему работает (override).

### CLI (#202)
- Shim-импорты → владельцы: `clikit.errors.CliError` → `librarykit.errors.CliError`;
  `clikit.transport.HttpClient` → внутри `BackendAdapter` (librarykit).
- Аудит всех команд: единый `emit_data`, `--json` дефолт. Зафиксировать `project list`
  (сейчас всегда таблица) — добавить `--json` либо явно задокументировать исключение.

## План миграции по кускам (каждый — зелёные тесты)

| Chunk | Задача | Содержание | Гейт |
|---|---|---|---|
| 0 | deps | pyproject → PyPI-киты (`s-librarykit`/`s-adapterkit`/`s-clikit`), убрать `[tool.uv.sources]`, `uv lock && uv sync` | suite green (863) |
| 1 | #202 | shim-import cleanup на владельцев + аудит `--json` | green |
| 2 | #201 | `SessionStore`+`SecretStore` для ключей, back-compat миграция plaintext→encrypted | profile-тесты green + новые |
| 3 | #200 | `BackendAdapter`+`Endpoint`+`ErrorMap` на librarykit-транспорте; перевод push/pull/profile/provision | sync-тесты green + новые |

Ветка `feat/atlas-skits-migration` от `dev` → merge в `dev`, без `sync push`.

## Тестирование
- Safety net: `863 passed` база. Каждый chunk не уменьшает число зелёных.
- Новые: `ErrorMap`-кейсы (коды ядра→исключения), `SessionStore` round-trip + миграция
  plaintext→encrypted, резолюция `Endpoint` адаптера.
- Где возможно — без сети (`StubTransport`/`httpx_mock`).

## Риски и митигации
- clikit 0.1.1→0.1.3 / librarykit 0.1.0→0.1.3 могут принести мелкие API-изменения →
  Chunk 0 ловит сразу прогоном всего suite.
- `SessionStore` требует KEK; headless/CI без keyring → file-fallback (`resolve_kek`),
  не падать молча; задать env-KEK по умолчанию для CI.
- Длинный long-poll через `BaseAdapter`: timeout poll не зарезать transport/RetryPolicy.

## Вне scope
- registry/entry-point adapterkit (бэкенд один).
- Изменение контракта ядра.
- W8-14/15/16 sync-conflict LWW (отдельная задача совместно с ядром).
