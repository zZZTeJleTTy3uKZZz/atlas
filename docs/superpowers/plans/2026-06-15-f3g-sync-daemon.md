# F3g — Фоновый sync-демон (устойчивый watch + автостарт/рестарт) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Steps use `- [ ]`.

**Goal:** Сделать long-poll синк фоновым по умолчанию: устойчивый `watch_loop` (сетевые/HTTP-ошибки не валят цикл — retry с backoff), Windows-демон через Task Scheduler (автостарт при входе + авто-рестарт при падении, headless), и команда подключения `atlas sync up` (поставить демон + запустить).

**Architecture:** `pull.watch_loop` — бесконечный устойчивый цикл поверх `pull_once` (ловит любое исключение → лог + backoff-sleep → продолжает; KeyboardInterrupt/Cancelled пробрасывает). `daemon.py` — управление Scheduled Task `atlas-sync-watch` (генерирует headless VBS-обёртку, запускающую `atlas sync watch` через git-bash без окна; `Register-ScheduledTask -AtLogOn` + `RestartCount`) через `powershell`. CLI: `atlas sync daemon install|uninstall|status` + `atlas sync up` (install+start). `watch` пишет лог в `AppPaths('atlas').cache_dir/sync-watch.log`.

**Tech Stack:** Python 3.14, clikit (`AppPaths`/`async_command`/`emit_data`), Windows Task Scheduler через `powershell` subprocess, git-bash как runner. pytest + pytest-asyncio.

**Соглашения:** образец Scheduled Task — `scripts/backup/register_task.ps1` (headless VBS `SW_HIDE`, идемпотентная регистрация). Демон Windows-специфичен; на не-Windows команды возвращают `{"ok": False, "error": "только Windows"}`.

---

## File Structure

- **Modify** `src/atlas/pm/sync/pull.py` — добавить `watch_loop`.
- **Create** `src/atlas/pm/sync/daemon.py` — install/uninstall/status Scheduled Task.
- **Modify** `src/atlas/pm/commands/sync.py` — `watch` через watch_loop + лог; `daemon`-подгруппа; `up`.
- **Create** tests: `tests/test_sync_watch_loop.py`, `tests/test_sync_daemon.py`, `tests/test_sync_daemon_cli.py`.

Ветка `feat/f3g-sync-daemon`. `cd <ATLAS> && uv run pytest <path> -v`.

---

### Task 1: устойчивый `watch_loop`

**Files:** Modify `src/atlas/pm/sync/pull.py`; Test `tests/test_sync_watch_loop.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_sync_watch_loop.py`:

```python
"""F3g: watch_loop — устойчивый цикл pull (ошибки не валят, retry с backoff)."""
import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import Base
from atlas.pm.sync import pull


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'w.db'}")
    Base.metadata.create_all(eng)
    return eng


class _Flaky:
    """poll_events: 1-й раз бросает (сеть), потом пусто; на 4-м — Cancelled (стоп)."""
    def __init__(self):
        self.n = 0

    async def poll_events(self, since=None, *, timeout=25.0):
        self.n += 1
        if self.n == 1:
            raise RuntimeError("network down")
        if self.n >= 4:
            raise asyncio.CancelledError
        return {"events": [], "cursor": None}

    async def aclose(self):
        pass


async def test_watch_loop_retries_on_error(engine):
    results = []
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    with pytest.raises(asyncio.CancelledError):
        await pull.watch_loop(
            engine, _Flaky(), timeout=0.1,
            on_result=results.append, _sleep=fake_sleep,
        )
    # была хотя бы одна ошибка-результат и хотя бы один backoff-sleep
    assert any("error" in r for r in results)
    assert sleeps and sleeps[0] >= 1.0
    # после ошибки цикл продолжился (были и успешные pull-результаты)
    assert any("applied" in r for r in results)
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_sync_watch_loop.py -v`
Expected: FAIL — `AttributeError: module 'atlas.pm.sync.pull' has no attribute 'watch_loop'`.

- [ ] **Step 3: Реализация — добавить `watch_loop` в `pull.py`**

В конец `src/atlas/pm/sync/pull.py` (до `__all__`) добавить:

```python
async def watch_loop(
    engine, client, *, channel: str = "atlas", timeout: float = 25.0,
    on_result=None, max_backoff: float = 60.0, _sleep=None,
) -> None:
    """Бесконечный устойчивый цикл pull: сетевые/HTTP-ошибки НЕ валят цикл —
    логируются через on_result и ретраятся с экспоненциальным backoff
    (сброс при успехе). KeyboardInterrupt/CancelledError пробрасываются (стоп).
    """
    import asyncio

    from atlas.pm.db import make_session

    sleep = _sleep or asyncio.sleep
    backoff = 1.0
    while True:
        try:
            with make_session(engine) as session:
                result = await pull_once(session, client, channel=channel, timeout=timeout)
            backoff = 1.0
            if on_result is not None:
                on_result(result)
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001 — устойчивость важнее точечной обработки
            if on_result is not None:
                on_result({"error": str(exc), "retry_in": backoff})
            await sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
```

Обновить `__all__`:

```python
__all__ = ["pull_once", "watch_loop"]
```

- [ ] **Step 4: GREEN**

Run: `uv run pytest tests/test_sync_watch_loop.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add src/atlas/pm/sync/pull.py tests/test_sync_watch_loop.py
git commit -m "feat(f3g): watch_loop — устойчивый long-poll цикл (retry+backoff)"
```

---

### Task 2: `daemon.py` — Windows Scheduled Task

**Files:** Create `src/atlas/pm/sync/daemon.py`; Test `tests/test_sync_daemon.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_sync_daemon.py`:

```python
"""F3g: daemon — установка/снятие Scheduled Task (powershell мокается)."""
import subprocess
import sys
import types

import pytest

from atlas.pm.sync import daemon


def _fake_run(rc=0, out="installed", err=""):
    def run(script):
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)
    return run


def test_install_registers_atlogon_task(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "_bash_exe", lambda: r"C:\Git\bin\bash.exe")
    monkeypatch.setattr(daemon, "_atlas_root", lambda: tmp_path)
    captured = {}

    def run(script):
        captured["script"] = script
        return types.SimpleNamespace(returncode=0, stdout="installed", stderr="")

    res = daemon.install(run=run)
    assert res["ok"] is True
    assert res["task"] == daemon.TASK_NAME
    # ключевое: триггер при входе + рестарт + регистрация
    assert "-AtLogOn" in captured["script"]
    assert "RestartCount" in captured["script"]
    assert "Register-ScheduledTask" in captured["script"]
    # headless VBS создан
    assert (tmp_path / "scripts" / "sync_watch_headless.vbs").exists()


def test_install_no_bash(monkeypatch):
    monkeypatch.setattr(daemon, "_bash_exe", lambda: None)
    res = daemon.install(run=_fake_run())
    assert res["ok"] is False


def test_uninstall_calls_unregister(monkeypatch):
    captured = {}
    def run(script):
        captured["script"] = script
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    res = daemon.uninstall(run=run)
    assert res["ok"] is True
    assert "Unregister-ScheduledTask" in captured["script"]
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_sync_daemon.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atlas.pm.sync.daemon'`.

- [ ] **Step 3: Реализация `daemon.py`**

Create `src/atlas/pm/sync/daemon.py`:

```python
"""Фоновый sync-демон через Windows Task Scheduler.

Регистрирует задачу `atlas-sync-watch`, запускающую `atlas sync watch` без окна
(headless VBS, как scripts/backup/register_task.ps1) при входе пользователя,
с авто-рестартом при падении. Не-Windows → {"ok": False}.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

TASK_NAME = "atlas-sync-watch"

_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


def _bash_exe() -> str | None:
    for p in _BASH_CANDIDATES:
        if Path(p).exists():
            return p
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cand = Path(local) / "Programs" / "Git" / "bin" / "bash.exe"
        if cand.exists():
            return str(cand)
    return None


def _atlas_root() -> Path:
    # .../src/atlas/pm/sync/daemon.py → корень проекта (4 уровня вверх от файла)
    return Path(__file__).resolve().parents[4]


def _run_ps(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True,
    )


def _to_unix(bash: str, win_path: Path) -> str:
    try:
        r = subprocess.run([bash, "-c", f"cygpath '{win_path}'"],
                           capture_output=True, text=True)
        out = r.stdout.strip()
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass
    return str(win_path).replace("\\", "/")


def install(*, run=None) -> dict:
    """Зарегистрировать и запустить демон (идемпотентно)."""
    if os.name != "nt":
        return {"ok": False, "error": "демон поддерживается только на Windows"}
    run = run or _run_ps
    bash = _bash_exe()
    if bash is None:
        return {"ok": False, "error": "git-bash не найден (установи Git for Windows)"}
    root = _atlas_root()
    root_unix = _to_unix(bash, root)

    vbs = root / "scripts" / "sync_watch_headless.vbs"
    vbs.parent.mkdir(parents=True, exist_ok=True)
    vbs.write_text(
        "' sync_watch_headless.vbs — АВТОГЕН daemon.install, не редактировать.\n"
        "' Запускает atlas sync watch через git-bash без окна (Run стиль 0 = SW_HIDE).\n"
        "Option Explicit\n"
        "Dim sh\n"
        'Set sh = CreateObject("WScript.Shell")\n'
        f'sh.Run "{bash} -l -c ""cd \'{root_unix}\' && uv run atlas --text sync watch""", 0, False\n',
        encoding="ascii",
    )

    ps = f"""
$ErrorActionPreference='Stop'
$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument '"{vbs}"'
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650)
Get-ScheduledTask -TaskName "{TASK_NAME}" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Register-ScheduledTask -TaskName "{TASK_NAME}" -Description "atlas: фоновый long-poll синк с хабом" -Action $Action -Trigger $Trigger -Settings $Settings | Out-Null
Start-ScheduledTask -TaskName "{TASK_NAME}"
Write-Output "installed"
""".strip()
    res = run(ps)
    return {
        "ok": res.returncode == 0,
        "task": TASK_NAME,
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
    }


def uninstall(*, run=None) -> dict:
    if os.name != "nt":
        return {"ok": False, "error": "только Windows"}
    run = run or _run_ps
    ps = (
        f'Get-ScheduledTask -TaskName "{TASK_NAME}" -ErrorAction SilentlyContinue '
        f'| Unregister-ScheduledTask -Confirm:$false; Write-Output "removed"'
    )
    res = run(ps)
    return {"ok": res.returncode == 0, "task": TASK_NAME, "stdout": res.stdout.strip()}


def status(*, run=None) -> dict:
    if os.name != "nt":
        return {"ok": False, "error": "только Windows"}
    run = run or _run_ps
    ps = (
        f'$t = Get-ScheduledTask -TaskName "{TASK_NAME}" -ErrorAction SilentlyContinue; '
        f'if ($t) {{ Write-Output $t.State }} else {{ Write-Output "NOT_INSTALLED" }}'
    )
    res = run(ps)
    state = (res.stdout or "").strip()
    return {"ok": res.returncode == 0, "task": TASK_NAME,
            "installed": state != "NOT_INSTALLED", "state": state}


__all__ = ["install", "uninstall", "status", "TASK_NAME"]
```

- [ ] **Step 4: GREEN**

Run: `uv run pytest tests/test_sync_daemon.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/atlas/pm/sync/daemon.py tests/test_sync_daemon.py
git commit -m "feat(f3g): daemon — Scheduled Task atlas-sync-watch (AtLogOn + рестарт)"
```

---

### Task 3: CLI `atlas sync daemon` + `atlas sync up` + устойчивый watch с логом

**Files:** Modify `src/atlas/pm/commands/sync.py`; Test `tests/test_sync_daemon_cli.py`

- [ ] **Step 1: Падающий тест (RED)**

Create `tests/test_sync_daemon_cli.py`:

```python
"""F3g: CLI atlas sync daemon/up подключены."""
from typer.testing import CliRunner

from atlas.cli import app

runner = CliRunner()


def test_sync_help_has_daemon_and_up():
    res = runner.invoke(app, ["sync", "--help"])
    assert res.exit_code == 0
    assert "daemon" in res.stdout
    assert "up" in res.stdout


def test_daemon_help_has_subcommands():
    res = runner.invoke(app, ["sync", "daemon", "--help"])
    assert res.exit_code == 0
    assert "install" in res.stdout
    assert "uninstall" in res.stdout
    assert "status" in res.stdout
```

- [ ] **Step 2: RED**

Run: `uv run pytest tests/test_sync_daemon_cli.py -v`
Expected: FAIL — нет `daemon`/`up` в выводе.

- [ ] **Step 3: Обновить `sync.py`**

В `src/atlas/pm/commands/sync.py` добавить импорты:

```python
from atlas.pm.sync import daemon as daemon_mod
from atlas.pm.sync import pull as pull_mod  # уже есть
```

Заменить `watch_cmd` на устойчивый (через `watch_loop` + лог в файл):

```python
@sync_app.command("watch")
@async_command
async def watch_cmd(
    timeout: float = typer.Option(25.0, "--timeout", help="Таймаут long-poll, сек."),
) -> None:
    """Устойчивый фоновый входящий синк (long-poll, не падает на ошибках сети)."""
    import asyncio
    import datetime as _dt

    from clikit import AppPaths

    cfg = load_config()
    client = BackendClient(cfg.base_url, cfg.api_key)
    engine = make_engine(_db_url())
    logfile = AppPaths("atlas").cache_dir / "sync-watch.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)

    def _log(result: dict) -> None:
        line = f"{_dt.datetime.now().isoformat(timespec='seconds')} {result}\n"
        try:
            with logfile.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass

    try:
        await pull_mod.watch_loop(engine, client, timeout=timeout, on_result=_log)
    except (KeyboardInterrupt, asyncio.CancelledError):
        emit_data({"stopped": True}, text_renderer=lambda r: print("watch остановлен"))
    finally:
        await client.aclose()
```

Добавить подгруппу `daemon` и команду `up` (в конец файла):

```python
daemon_app = typer.Typer(no_args_is_help=True, help="Фоновый sync-демон (Windows Task Scheduler).")
sync_app.add_typer(daemon_app, name="daemon")


@daemon_app.command("install")
@command
def daemon_install_cmd() -> None:
    """Поставить и запустить фоновый демон (автостарт при входе + рестарт)."""
    emit_data(daemon_mod.install(),
              text_renderer=lambda r: print("✓ демон установлен" if r["ok"] else f"✗ {r.get('error') or r.get('stderr')}"))


@daemon_app.command("uninstall")
@command
def daemon_uninstall_cmd() -> None:
    """Убрать фоновый демон."""
    emit_data(daemon_mod.uninstall(),
              text_renderer=lambda r: print("✓ удалён" if r["ok"] else f"✗ {r.get('error')}"))


@daemon_app.command("status")
@command
def daemon_status_cmd() -> None:
    """Статус фонового демона."""
    emit_data(daemon_mod.status(),
              text_renderer=lambda r: print(f"{'установлен' if r.get('installed') else 'нет'}: {r.get('state')}"))


@sync_app.command("up")
@command
def up_cmd() -> None:
    """Подключиться к хабу: поставить и запустить фоновый демон синка."""
    cfg = load_config()
    if not cfg.api_key or not cfg.base_url:
        emit_data({"ok": False, "error": "не задан api_key/base_url — настрой конфиг"},
                  text_renderer=lambda r: print(f"✗ {r['error']}"))
        raise typer.Exit(1)
    emit_data(daemon_mod.install(),
              text_renderer=lambda r: print("✓ синк-демон запущен (фоновый long-poll)" if r["ok"] else f"✗ {r.get('error') or r.get('stderr')}"))
```

Импортировать `command` из clikit (рядом с `async_command`):

```python
from clikit import async_command, command, emit_data
```

- [ ] **Step 4: GREEN + полный прогон**

Run: `uv run pytest tests/test_sync_daemon_cli.py -v && uv run pytest -q`
Expected: PASS, без регрессий.

- [ ] **Step 5: Commit**

```bash
git add src/atlas/pm/commands/sync.py tests/test_sync_daemon_cli.py
git commit -m "feat(f3g): CLI atlas sync daemon install/uninstall/status + up; watch с логом"
```

---

## Self-Review — покрытие

| Требование | Задача |
|---|---|
| watch не падает на ошибках (retry/backoff) | Task 1 (`watch_loop`) |
| автостарт при входе + авто-рестарт | Task 2 (Scheduled Task `-AtLogOn` + `RestartCount`) |
| фоновый (без окна) | Task 2 (headless VBS `SW_HIDE`) |
| «по умолчанию при подключении к бэку» | Task 3 (`atlas sync up`) |
| наблюдаемость | Task 3 (лог `cache_dir/sync-watch.log`) |

**Граница:** автодёрг `up` при `config set api_key` — не делаем здесь (config-команды atlas не трогаем); `atlas sync up` — явная одна команда подключения. Не-Windows — команды возвращают `{"ok": False}` (демон Windows-only; на других ОС — ручной `atlas sync watch` или systemd позже).

**Placeholder-скан:** весь код дословный. **Type consistency:** `watch_loop(engine, client, *, channel, timeout, on_result, max_backoff, _sleep)`; `daemon.install/uninstall/status(*, run=None) -> dict` с ключами `ok`/`task`/`state`/`installed`.
