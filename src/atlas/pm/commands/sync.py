"""CLI-команды `atlas sync ...` — синхронизация с backend-хабом (F3c)."""
from __future__ import annotations

import os

import typer
from clikit import async_command, command, emit_data

from atlas.appconfig import load_config
from atlas.pm.db import DEFAULT_DB_PATH, make_engine, make_session
from atlas.pm.sync import daemon as daemon_mod
from atlas.pm.sync import pull as pull_mod
from atlas.pm.sync import push as push_mod
from atlas.pm.sync.backend_client import BackendClient

sync_app = typer.Typer(no_args_is_help=True, help="Синхронизация Atlas ↔ backend-хаб.")


def _db_url() -> str:
    return os.environ.get("ATLAS_DB_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


@sync_app.command("push")
@async_command
async def push_cmd() -> None:
    """Выгрузить pending-операции из локального outbox на хаб."""
    cfg = load_config()
    client = BackendClient(cfg.base_url, cfg.api_key)
    engine = make_engine(_db_url())
    try:
        with make_session(engine) as session:
            result = await push_mod.push_pending(session, client)
    finally:
        await client.aclose()
    emit_data(result, text_renderer=lambda r: print(f"sent: {r['sent']}"))


@sync_app.command("pull")
@async_command
async def pull_cmd(
    timeout: float = typer.Option(25.0, "--timeout", help="Таймаут long-poll, сек."),
) -> None:
    """Один цикл входящего синка: применить события с хаба локально."""
    cfg = load_config()
    client = BackendClient(cfg.base_url, cfg.api_key)
    engine = make_engine(_db_url())
    try:
        with make_session(engine) as session:
            result = await pull_mod.pull_once(session, client, timeout=timeout, scope=cfg.scope)
    finally:
        await client.aclose()
    emit_data(result, text_renderer=lambda r: print(f"applied: {r['applied']}"))


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
        await pull_mod.watch_loop(engine, client, timeout=timeout, scope=cfg.scope, on_result=_log)
    except (KeyboardInterrupt, asyncio.CancelledError):
        emit_data({"stopped": True}, text_renderer=lambda r: print("watch остановлен"))
    finally:
        await client.aclose()


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
