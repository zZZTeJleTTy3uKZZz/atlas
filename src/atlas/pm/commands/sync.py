"""CLI-команды `atlas sync ...` — синхронизация с backend-хабом (F3c)."""
from __future__ import annotations

import os

import typer
from clikit import async_command, emit_data

from atlas.appconfig import load_config
from atlas.pm.db import DEFAULT_DB_PATH, make_engine, make_session
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
