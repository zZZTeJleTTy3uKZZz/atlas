"""CLI-команды `atlas sync ...` — синхронизация с внешним backend-сервисом (F3c)."""
from __future__ import annotations

import typer
from clikit import async_command, command, emit_data

from atlas.appconfig import load_config, resolve_api_key
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.sync import daemon as daemon_mod
from atlas.sync import pull as pull_mod
from atlas.sync import push as push_mod
from atlas.sync.backend_client import BackendClient

sync_app = typer.Typer(no_args_is_help=True, help="Синхронизация Atlas ↔ внешний backend-сервис.")


def _db_url() -> str:
    return resolve_db_url()


@sync_app.command("push")
@async_command
async def push_cmd() -> None:
    """Выгрузить pending-операции из локального outbox на хаб."""
    from atlas.commands.connect import _require_connected
    _require_connected()
    cfg = load_config()
    client = BackendClient(cfg.base_url, resolve_api_key(cfg))
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
    from atlas.commands.connect import _require_connected
    _require_connected()
    cfg = load_config()
    client = BackendClient(cfg.base_url, resolve_api_key(cfg))
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
    from atlas.commands.connect import _require_connected
    _require_connected()
    import asyncio
    import datetime as _dt

    from librarykit.config_util import AppPaths

    cfg = load_config()
    client = BackendClient(cfg.base_url, resolve_api_key(cfg))
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


outbox_app = typer.Typer(no_args_is_help=True, help="Локальная очередь исходящих (outbox): status / prune.")
sync_app.add_typer(outbox_app, name="outbox")


@outbox_app.command("status")
@command
def outbox_status_cmd() -> None:
    """Сводка локального outbox: сколько pending / sent / failed."""
    from sqlalchemy import func, select

    from atlas.models import Outbox

    engine = make_engine(_db_url())
    with make_session(engine) as session:
        rows = session.execute(
            select(Outbox.status, func.count()).group_by(Outbox.status)
        ).all()
    counts = {status: n for status, n in rows}
    emit_data(
        counts,
        text_renderer=lambda c: print(
            " · ".join(f"{k}: {v}" for k, v in c.items()) if c else "outbox пуст"
        ),
    )


@outbox_app.command("prune")
@command
def outbox_prune_cmd(
    sent: bool = typer.Option(True, "--sent/--no-sent", help="Удалить отправленные (sent)."),
    failed: bool = typer.Option(False, "--failed", help="Также удалить проваленные (failed)."),
    all_rows: bool = typer.Option(
        False, "--all", help="Удалить ВСЁ, включая pending (очистить мёртвую очередь #879)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Не спрашивать подтверждение."),
) -> None:
    """Очистить локальный outbox: sent (по умолчанию) / failed / всё (--all).

    Полезно, если очередь копилась без подключённого backend (#879) — с гейтом
    enqueue этого больше не происходит, но накопленное можно удалить здесь."""
    from sqlalchemy import delete, func, select

    from atlas.models import Outbox

    statuses = ["pending", "sent", "failed"] if all_rows else (
        (["sent"] if sent else []) + (["failed"] if failed else [])
    )
    if not statuses:
        emit_data(
            {"pruned": 0},
            text_renderer=lambda r: print("нечего чистить (укажи --sent / --failed / --all)"),
        )
        return

    engine = make_engine(_db_url())
    with make_session(engine) as session:
        total = session.execute(
            select(func.count()).select_from(Outbox).where(Outbox.status.in_(statuses))
        ).scalar_one()
        if total and not yes:  # деструктив → показать дельту и подтвердить
            if not typer.confirm(f"Удалить {total} записей outbox ({', '.join(statuses)})?"):
                emit_data({"pruned": 0, "aborted": True}, text_renderer=lambda r: print("отменено"))
                return
        session.execute(delete(Outbox).where(Outbox.status.in_(statuses)))
        session.commit()
    emit_data(
        {"pruned": total, "statuses": statuses},
        text_renderer=lambda r: print(
            f"✓ удалено {r['pruned']} записей outbox ({', '.join(r['statuses'])})"
        ),
    )


@sync_app.command("up")
@command
def up_cmd() -> None:
    """Подключиться к хабу: поставить и запустить фоновый демон синка."""
    cfg = load_config()
    if not resolve_api_key(cfg) or not cfg.base_url:
        emit_data({"ok": False, "error": "не задан api_key/base_url — настрой конфиг"},
                  text_renderer=lambda r: print(f"✗ {r['error']}"))
        raise typer.Exit(1)
    emit_data(daemon_mod.install(),
              text_renderer=lambda r: print("✓ синк-демон запущен (фоновый long-poll)" if r["ok"] else f"✗ {r.get('error') or r.get('stderr')}"))
