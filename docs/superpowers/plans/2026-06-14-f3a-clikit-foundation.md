# F3a — clikit-фундамент Atlas CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Подключить библиотеку `clikit` к `atlas` и заложить фундамент будущего sync-слоя — типизированный конфиг (`AtlasConfig`), доменный клиент к backend-хабу (`BackendClient` с `X-API-Key`) и перевод root-CLI на `clikit.build_root_app` (`--json` по умолчанию) — БЕЗ изменения доменной модели и БЕЗ миграций.

**Architecture:** `atlas` остаётся автономным локальным PM-CLI; F3a добавляет ровно инфраструктурный слой. `AtlasConfig` наследует `clikit.AppConfig` (слои global/project/local + env `ATLAS_*`). `BackendClient` — тонкая обёртка над `clikit.HttpClient`, добавляющая заголовок `X-API-Key` и доменные методы `push_events`/`poll_events` (сами события — в F3c/F3d). Root-CLI пересобирается через `build_root_app("atlas", …)`, сохраняя все существующие под-приложения; существующие команды (Rich-вывод) продолжают работать без изменений — их перевод на `emit_data` относится к F3e.

**Tech Stack:** Python 3.11, `clikit` (path-зависимость через `[tool.uv.sources]`), `pydantic-settings` (внутри clikit), `httpx` + `pytest-httpx`, `typer`, `pytest` + `pytest-asyncio` (новый, для async-клиента), `uv`, hatchling.

---

## File Structure

- **Modify** `pyproject.toml` — добавить `clikit` (path-зависимость) и `pytest-asyncio` (dev) + `asyncio_mode = "auto"`.
- **Create** `src/atlas/appconfig.py` — `AtlasConfig(clikit.AppConfig)` + `load_config()`. Одна ответственность: типизированная конфигурация CLI.
- **Create** `src/atlas/pm/sync/__init__.py` — новый пакет sync-слоя (пока пустой публичный API).
- **Create** `src/atlas/pm/sync/backend_client.py` — `BackendClient`: доменный клиент к хабу поверх `clikit.HttpClient`, auth `X-API-Key`.
- **Modify** `src/atlas/cli.py:41` — заменить конструктор `app` на `clikit.build_root_app(...)` (всё остальное — без изменений).
- **Create** `tests/test_appconfig.py`, `tests/test_sync_backend_client.py`, `tests/test_cli_root.py`.

Все работы — на ветке `feat/f3-atlas-cli-sync` (уже создана). Тесты запускаются `uv run pytest` (pythonpath=src уже в конфиге).

---

### Task 1: Подключить clikit + pytest-asyncio

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_clikit_dep.py`

- [ ] **Step 1: Написать падающий тест на доступность clikit**

Create `tests/test_clikit_dep.py`:

```python
"""F3a: clikit подключён как зависимость atlas."""


def test_clikit_importable():
    import clikit

    assert hasattr(clikit, "AppConfig")
    assert hasattr(clikit, "HttpClient")
    assert hasattr(clikit, "build_root_app")


def test_clikit_version_present():
    import clikit

    assert isinstance(clikit.__version__, str)
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `uv run pytest tests/test_clikit_dep.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'clikit'`.

- [ ] **Step 3: Добавить clikit и pytest-asyncio в pyproject.toml**

В `pyproject.toml`, в массив `[project].dependencies` добавить строку `"clikit"`:

```toml
dependencies = [
    "httpx>=0.27",
    "typer>=0.12",
    "rich>=13.7",
    "python-dotenv>=1.0",
    "python-dateutil>=2.9",
    "pytz>=2024.1",
    # PM-слой (Spike v0.4+)
    "sqlalchemy>=2.0.30",
    "alembic>=1.13",
    "python-frontmatter>=1.1",
    "python-slugify>=8.0.4",
    # F3a: clikit-фундамент (локальная path-зависимость)
    "clikit",
]
```

В `[project.optional-dependencies].dev` добавить `"pytest-asyncio>=0.24"`:

```toml
dev = [
    "pytest>=8.3",
    "pytest-httpx>=0.32",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "ruff>=0.5",
]
```

Добавить секцию источников uv (после `[build-system]` или в конце файла) — `clikit` берётся из соседней папки `_storage/clikit` как editable:

```toml
[tool.uv.sources]
clikit = { path = "../clikit", editable = true }
```

Включить авто-режим asyncio в существующей секции `[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 4: Синхронизировать окружение**

Run: `uv sync --extra dev`
Expected: clikit устанавливается editable (`../clikit`), подтягиваются его транзитивные зависимости (stamina, pydantic-settings, platformdirs, tomli-w); pytest-asyncio установлен.

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `uv run pytest tests/test_clikit_dep.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Прогнать весь набор — убедиться, что ничего не сломалось**

Run: `uv run pytest -q`
Expected: все существующие тесты по-прежнему PASS (asyncio_mode=auto не влияет на sync-тесты).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml tests/test_clikit_dep.py
git commit -m "feat(f3a): подключить clikit (path) + pytest-asyncio"
```

---

### Task 2: AtlasConfig на clikit.AppConfig

**Files:**
- Create: `src/atlas/appconfig.py`
- Test: `tests/test_appconfig.py`

- [ ] **Step 1: Написать падающие тесты**

Create `tests/test_appconfig.py`:

```python
"""F3a: AtlasConfig — типизированный конфиг CLI на clikit.AppConfig."""
from atlas.appconfig import AtlasConfig, load_config


def test_defaults():
    cfg = AtlasConfig()
    assert cfg.base_url == "http://localhost:8000"
    assert cfg.portal_id == "atlas-local"
    assert cfg.api_key == ""


def test_env_override(monkeypatch, tmp_path):
    # Изолируем global-конфиг во временный каталог (не читать реальный ~/.config).
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_BASE_URL", "https://hub.example.com")
    monkeypatch.setenv("ATLAS_API_KEY", "secret123")
    cfg = AtlasConfig.load("atlas")
    assert cfg.base_url == "https://hub.example.com"
    assert cfg.api_key == "secret123"
    assert cfg.portal_id == "atlas-local"


def test_load_config_helper(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path))
    cfg = load_config()
    assert isinstance(cfg, AtlasConfig)
    assert cfg.portal_id == "atlas-local"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_appconfig.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atlas.appconfig'`.

- [ ] **Step 3: Реализовать AtlasConfig**

Create `src/atlas/appconfig.py`:

```python
"""Конфигурация Atlas CLI на clikit.AppConfig (F3a).

Наследует слоистый конфиг clikit (global/project/local + env ``ATLAS_*`` +
init-kwargs). Добавляет поля backend-хаба. Секрет ``api_key`` берётся из env
``ATLAS_API_KEY`` или интерполяции ``${ATLAS_API_KEY}`` в TOML — в git не лежит.
"""
from __future__ import annotations

from clikit import AppConfig


class AtlasConfig(AppConfig):
    """Конфиг Atlas CLI: адрес backend-хаба, ключ, id Atlas-портала.

    - ``base_url``  — адрес backend-хаба (notion-api-b24);
    - ``api_key``   — admin-API-ключ (X-API-Key); пустой → команды синка
      потребуют его явно;
    - ``portal_id`` — id Atlas-портала на бэке (seed ``atlas-local``).
    """

    base_url: str = "http://localhost:8000"
    api_key: str = ""
    portal_id: str = "atlas-local"


def load_config() -> AtlasConfig:
    """Загрузить конфиг бренда ``atlas`` (слои + env)."""
    return AtlasConfig.load("atlas")
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_appconfig.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/atlas/appconfig.py tests/test_appconfig.py
git commit -m "feat(f3a): AtlasConfig на clikit.AppConfig (base_url/api_key/portal_id)"
```

---

### Task 3: BackendClient — доменный клиент к хабу

**Files:**
- Create: `src/atlas/pm/sync/__init__.py`
- Create: `src/atlas/pm/sync/backend_client.py`
- Test: `tests/test_sync_backend_client.py`

- [ ] **Step 1: Написать падающие тесты**

Create `tests/test_sync_backend_client.py`:

```python
"""F3a: BackendClient — клиент к backend-хабу (X-API-Key) поверх clikit.HttpClient."""
import pytest

from atlas.pm.sync.backend_client import BackendClient


async def test_push_events_sends_api_key(httpx_mock):
    httpx_mock.add_response(
        method="POST", url="http://hub/api/v1/events", json={"accepted": 1}
    )
    client = BackendClient("http://hub", "secret123")
    result = await client.push_events([{"op": "create", "entity_kind": "task"}])
    assert result == {"accepted": 1}
    req = httpx_mock.get_request()
    assert req.headers["X-API-Key"] == "secret123"
    await client.aclose()


async def test_poll_events_passes_since_and_timeout(httpx_mock):
    httpx_mock.add_response(method="GET", json={"events": [], "cursor": None})
    client = BackendClient("http://hub", "k")
    result = await client.poll_events("2026-06-14T00:00:00", timeout=5.0)
    assert result == {"events": [], "cursor": None}
    url = str(httpx_mock.get_request().url)
    assert "since=2026" in url
    assert "timeout=5" in url
    await client.aclose()


async def test_poll_events_without_since_omits_param(httpx_mock):
    httpx_mock.add_response(method="GET", json={"events": [], "cursor": None})
    client = BackendClient("http://hub", "k")
    await client.poll_events(None, timeout=1.0)
    url = str(httpx_mock.get_request().url)
    assert "since=" not in url
    await client.aclose()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_sync_backend_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atlas.pm.sync'`.

- [ ] **Step 3: Создать пакет sync**

Create `src/atlas/pm/sync/__init__.py`:

```python
"""Sync-слой Atlas CLI (F3): клиент к backend-хабу, outbox, push, pull.

F3a закладывает только backend_client; outbox/push/pull — F3c/F3d.
"""
```

- [ ] **Step 4: Реализовать BackendClient**

Create `src/atlas/pm/sync/backend_client.py`:

```python
"""Доменный клиент к backend-хабу (notion-api-b24) поверх clikit.HttpClient.

Auth — заголовком ``X-API-Key`` (бэк резолвит principal по ключу). clikit
HttpClient сам по себе шлёт ``Authorization: Bearer`` только при заданном
access_token; мы его НЕ задаём, а кладём ``X-API-Key`` в extra-заголовки
каждого запроса. Доменные методы: ``push_events`` (POST /events — пройдёт
через оркестратор → фанаут) и ``poll_events`` (GET /events/poll — long-poll).
"""
from __future__ import annotations

from typing import Any

from clikit import HttpClient

EVENTS_PATH = "/api/v1/events"
POLL_PATH = "/api/v1/events/poll"


class BackendClient:
    """Клиент к хабу. ``http`` можно внедрить (для тестов/переиспользования)."""

    def __init__(
        self, base_url: str, api_key: str, *, http: HttpClient | None = None
    ) -> None:
        self._http = http or HttpClient(base_url)
        self._api_key = api_key

    def _auth(self) -> dict[str, str]:
        return {"X-API-Key": self._api_key}

    async def push_events(self, events: list[dict[str, Any]]) -> Any:
        """Отправить события на хаб (батч). Возвращает JSON ответа ``/events``."""
        return await self._http.post(EVENTS_PATH, json=events, headers=self._auth())

    async def poll_events(
        self, since: str | None = None, *, timeout: float = 25.0
    ) -> Any:
        """Long-poll событий позже курсора ``since`` (ISO occurred_at)."""
        params: dict[str, Any] = {"timeout": timeout}
        if since is not None:
            params["since"] = since
        return await self._http.get(POLL_PATH, params=params, headers=self._auth())

    async def aclose(self) -> None:
        """Закрыть нижележащий HTTP-клиент."""
        await self._http.aclose()


__all__ = ["BackendClient"]
```

- [ ] **Step 5: Запустить — убедиться, что проходит**

Run: `uv run pytest tests/test_sync_backend_client.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add src/atlas/pm/sync/__init__.py src/atlas/pm/sync/backend_client.py tests/test_sync_backend_client.py
git commit -m "feat(f3a): BackendClient (push_events/poll_events, X-API-Key)"
```

---

### Task 4: Root-CLI на clikit.build_root_app

**Files:**
- Modify: `src/atlas/cli.py:41`
- Test: `tests/test_cli_root.py`

- [ ] **Step 1: Написать падающие smoke-тесты**

Create `tests/test_cli_root.py`:

```python
"""F3a: root-CLI собран через clikit.build_root_app — version/--json/субкоманды."""
from typer.testing import CliRunner

from atlas.cli import app

runner = CliRunner()


def test_version_command_json_default():
    # clikit-дефолт вывода — json: `atlas version` → {"version": "0.1.0"}.
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout
    assert '"version"' in result.stdout


def test_help_lists_existing_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "pm-tasks" in result.stdout
    assert "projects" in result.stdout
    assert "ideas" in result.stdout


def test_text_flag_switches_human_output():
    result = runner.invoke(app, ["--text", "version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `uv run pytest tests/test_cli_root.py -v`
Expected: FAIL — `test_version_command_json_default` падает: у текущего root-`app` нет команды `version` (exit_code != 0), либо вывод не JSON.

- [ ] **Step 3: Заменить конструктор root-app на build_root_app**

В `src/atlas/cli.py` добавить импорт рядом с прочими (после строки `import typer`):

```python
from clikit import build_root_app
```

Заменить строку 41:

```python
app = typer.Typer(no_args_is_help=True, help="Notion: задачи, проекты, файлы. + PM-слой projects/pm-tasks.")
```

на:

```python
app = build_root_app(
    "atlas",
    version="0.1.0",
    help="Notion: задачи, проекты, файлы. + PM-слой projects/pm-tasks.",
)
```

Всё остальное в `cli.py` (под-приложения `app.add_typer(...)`, top-level `@app.command(...)`) НЕ трогать — `build_root_app` возвращает `typer.Typer`, контракт сохранён.

Удалить существующую команду `whoami` (`@app.command("whoami")` и функцию `cmd_whoami`, строки ~407-414) — НЕТ: оставить как есть. `build_root_app` регистрирует `whoami` ТОЛЬКО при переданном `whoami_data` (мы его не передаём), поэтому конфликта нет; существующая `whoami` остаётся рабочей.

- [ ] **Step 4: Запустить smoke-тесты — убедиться, что проходят**

Run: `uv run pytest tests/test_cli_root.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Прогнать весь набор — нет регрессий**

Run: `uv run pytest -q`
Expected: всё PASS (существующие команды живы, добавились новые тесты F3a).

- [ ] **Step 6: Ручная проверка CLI (sanity)**

Run: `uv run atlas version` → `{"version": "0.1.0"}`
Run: `uv run atlas --text version` → `0.1.0`
Run: `uv run atlas --help` → список под-команд (pm-tasks, projects, ideas, …) + version.

- [ ] **Step 7: Commit**

```bash
git add src/atlas/cli.py tests/test_cli_root.py
git commit -m "feat(f3a): root-CLI на clikit.build_root_app (--json по умолчанию)"
```

---

## Self-Review — покрытие спеки F3a

| Требование спеки (§10 F3a) | Задача |
|---|---|
| `AtlasConfig` на `clikit.AppConfig` | Task 2 |
| `backend_client` (HttpClient + X-API-Key) | Task 3 |
| root на `build_root_app`, `--json` по умолчанию | Task 4 |
| без изменения модели / без миграций | соблюдено (нет файлов `models.py`/`migrations/`) |
| подключение `clikit` к проекту | Task 1 (path-зависимость) |

**Граница F3a:** существующие команды (Rich `console.print`) НЕ переводятся на `emit_data` — это F3e. F3a лишь даёт фундамент; `--json` по умолчанию активен для новых команд (`version` уже демонстрирует).

**Зависимость на стыке:** реальная проверка `push_events`/`poll_events` против живого бэка — в F3c/F3d (здесь только контракт + unit-тесты на фейк-HTTP).

**Placeholder-скан:** весь код приведён дословно; нет TBD/«добавить обработку».

**Type consistency:** `BackendClient(base_url, api_key, *, http=None)`, методы `push_events(events)`/`poll_events(since, *, timeout)`/`aclose()` — имена согласованы между тестами (Task 3) и реализацией. `AtlasConfig` поля `base_url`/`api_key`/`portal_id` согласованы между Task 2 и будущими потребителями.
