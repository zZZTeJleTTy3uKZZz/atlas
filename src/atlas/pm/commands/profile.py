"""CLI `atlas profile register` — онбординг Atlas-сторов (F4d).

Профиль = отдельный Atlas-стор: у каждого СВОЯ atlas.db и свой ключ, оба
независимо пушат и держат long-poll. Добавление пользователя — ОДНА команда
(не правка кода), строго через данные.

`register <slug> --name "Имя" --scope all|personal [--global-role role]`:
1. берёт ТЕКУЩИЙ active admin-конфиг (base_url + api_key);
2. дёргает POST /api/v1/admin/profiles этим admin-ключом (BackendClient.
   register_profile) → сервер атомарно/идемпотентно заводит портал-стор и
   выпускает ключ нового стора;
3. сохраняет ЛОКАЛЬНЫЙ профиль под env ``ATLAS_PROFILE=<slug>`` (как корневой
   ``--profile``) → ``profiles/<slug>/config.toml`` с выданным api_key,
   portal_id=<slug>, scope;
4. создаёт БД профиля (схема через ``Base.metadata.create_all`` — идемпотентно)
   по пути ``profiles/<slug>/atlas.db`` (resolve_db_url под тем же профилем).

Идемпотентно: повторный register того же slug не падает (сервер вернёт тот же
портал, create_all — no-op на существующих таблицах).
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import typer
from clikit import async_command, emit_data
from clikit.errors import CliError

from atlas.appconfig import AtlasConfig, load_config
from atlas.pm.db import make_engine, resolve_db_url
from atlas.pm.models import Base
from atlas.pm.sync.backend_client import BackendClient

profile_app = typer.Typer(
    no_args_is_help=True,
    help="Онбординг Atlas-сторов: профиль = отдельный стор (своя БД + ключ).",
)


@contextmanager
def _active_profile(slug: str):
    """Временно выставить env ``ATLAS_PROFILE=<slug>`` (как корневой --profile).

    config.save('atlas') и resolve_db_url() читают ATLAS_PROFILE и уводят запись
    в ``profiles/<slug>/`` — так целевой профиль сохраняется в свой каталог.
    Прежнее значение восстанавливается в finally (не протекает в active-сессию).
    """
    prev = os.environ.get("ATLAS_PROFILE")
    os.environ["ATLAS_PROFILE"] = slug
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("ATLAS_PROFILE", None)
        else:
            os.environ["ATLAS_PROFILE"] = prev


@profile_app.command("register")
@async_command
async def register_cmd(
    slug: str = typer.Argument(..., help="Slug нового Atlas-стора (atlas-admin/lichka/…)."),
    name: str = typer.Option(..., "--name", help="Человекочитаемое имя стора."),
    scope: str = typer.Option(
        "all", "--scope", help="Видимость синка стора: all | personal."
    ),
    member: str | None = typer.Option(
        None, "--member",
        help="Slug члена-владельца стора (дефолт = slug стора). Один человек "
             "может иметь несколько сторов — тогда --member один, slug разные.",
    ),
    global_role: str | None = typer.Option(
        None, "--global-role", help="Роль, выдаваемая ключу стора (дефолт admin)."
    ),
) -> None:
    """Завести новый Atlas-стор: ключ от ядра + локальный профиль + его БД.

    ``slug`` — портал-стор; ``--member`` — человек-владелец (по умолчанию совпадает
    со slug). Профиль сохраняется в ``profiles/<slug>/`` со своей БД и ключом.
    """
    cfg = load_config()
    if not cfg.api_key or not cfg.base_url:
        raise CliError(
            "CONFIG",
            "не задан api_key/base_url активного admin-конфига — настрой конфиг "
            "(ATLAS_API_KEY/ATLAS_BASE_URL или config.toml)",
        )

    member_slug = member or slug
    client = BackendClient(cfg.base_url, cfg.api_key)
    try:
        result = await client.register_profile(member_slug, slug, name, scope, global_role)
    finally:
        await client.aclose()

    api_key = result["api_key"]
    portal_id = result.get("portal_slug", slug)

    # Сохранить локальный профиль + создать его БД ПОД env ATLAS_PROFILE=<slug>.
    with _active_profile(slug):
        AtlasConfig(
            base_url=cfg.base_url, api_key=api_key, portal_id=portal_id, scope=scope
        ).save("atlas")
        engine = make_engine(resolve_db_url())
        Base.metadata.create_all(engine)
        engine.dispose()

    # JSON-режим (дефолт) отдаёт api_key агенту; text НЕ печатает ключ открыто.
    emit_data(
        {"portal_id": portal_id, "scope": scope, "api_key": api_key},
        text_renderer=lambda r: print(
            f"✓ стор '{r['portal_id']}' зарегистрирован "
            f"(scope={r['scope']}, ключ сохранён в профиль {r['portal_id']})"
        ),
    )


__all__ = ["profile_app"]
