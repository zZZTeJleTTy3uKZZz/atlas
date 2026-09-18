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


allowlist_app = typer.Typer(no_args_is_help=True, help="Разрешённые проекты исходящего синка.")
sync_app.add_typer(allowlist_app, name="allowlist")


@allowlist_app.command("set")
@command
def allowlist_set_cmd(
    project: list[str] = typer.Option(..., "--project", help="Проект; можно повторять."),
) -> None:
    """Включить исходящий синк только для перечисленных проектов."""
    from atlas.appconfig import AtlasConfig
    from atlas.models import Project
    from sqlalchemy import select

    slugs = list(dict.fromkeys(project))
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        known = set(session.execute(
            select(Project.slug).where(Project.slug.in_(slugs))
        ).scalars())
    missing = sorted(set(slugs) - known)
    if missing:
        raise ValueError(f"projects not found: {', '.join(missing)}")
    cfg = load_config()
    AtlasConfig(**{**cfg.model_dump(), "sync_projects": slugs}).save("atlas")
    emit_data({"projects": slugs}, text_renderer=lambda r: print(
        f"разрешено проектов: {len(r['projects'])}"
    ))


@allowlist_app.command("add")
@command
def allowlist_add_cmd(slug: str = typer.Argument(..., help="Slug нового проекта.")) -> None:
    """Добавить один проект к текущему списку без переноса остальных."""
    cfg = load_config()
    if not cfg.sync_projects:
        raise ValueError("сначала задайте исходный список через sync allowlist set")
    allowlist_set_cmd(project=[*cfg.sync_projects, slug])


@allowlist_app.command("list")
@command
def allowlist_list_cmd() -> None:
    """Показать разрешённые проекты; пустой список означает старый режим всех."""
    selected = load_config().sync_projects
    emit_data({"projects": selected, "all": not selected})


@sync_app.command("seed")
@command
def seed_cmd(
    project: list[str] = typer.Option(..., "--project", help="Проект; можно указать несколько раз."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Показать объём без записи."),
    include_closed: bool = typer.Option(
        False, "--include-closed", help="Также отправить завершённые и отменённые задачи.",
    ),
) -> None:
    """Поставить открытые задачи выбранных проектов в очередь синхронизации.

    Повторите команду, когда подключаете новый проект; остальные проекты и
    старая failed-очередь не затрагиваются. После неё выполните `sync push`.
    """
    from atlas.commands.connect import _require_connected
    from atlas.sync.seed import seed_tasks

    _require_connected()
    cfg = load_config()
    if not cfg.portal_id:
        raise ValueError("portal_id не задан в конфигурации Atlas")
    selected = set(cfg.sync_projects)
    if selected and (missing := set(project) - selected):
        raise ValueError(f"сначала разрешите проекты: {', '.join(sorted(missing))}")
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        results = [
            seed_tasks(
                session, slug, portal_id=cfg.portal_id,
                dry_run=dry_run, include_closed=include_closed,
            )
            for slug in dict.fromkeys(project)
        ]
        if not dry_run:
            session.commit()
    emit_data(
        {"projects": results, "total_queued": sum(r["queued"] for r in results),
         "dry_run": dry_run},
        text_renderer=lambda r: print(
            f"проектов: {len(r['projects'])}, задач в очередь: {r['total_queued']}"
        ),
    )


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
            result = await push_mod.push_pending(
                session, client, projects=set(cfg.sync_projects) or None,
            )
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


@sync_app.command("bootstrap")
@async_command
async def bootstrap_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Показать хвост без записи."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Подтвердить пропуск старой ленты."),
) -> None:
    """Пропустить прежнюю ленту перед загрузкой выбранных проектов.

    Сначала создайте резервную копию локальной БД. Старые события больше не
    применятся автоматически; затем выполните `sync seed` на исходной машине.
    """
    from atlas.commands.connect import _require_connected
    from atlas.sync.bootstrap import bootstrap_cursor

    _require_connected()
    if not dry_run and not yes and not typer.confirm(
        "Пропустить старую входящую ленту до текущего конца?"
    ):
        raise typer.Exit(1)
    cfg = load_config()
    client = BackendClient(cfg.base_url, resolve_api_key(cfg))
    engine = make_engine(_db_url())
    try:
        with make_session(engine) as session:
            result = await bootstrap_cursor(session, client, dry_run=dry_run)
    finally:
        await client.aclose()
    emit_data(result)


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
        await pull_mod.watch_loop(
            engine, client, timeout=timeout, scope=cfg.scope, on_result=_log,
            projects=set(cfg.sync_projects) or None,
        )
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


@outbox_app.command("retry")
@command
def outbox_retry_cmd() -> None:
    """Вернуть проваленные записи в очередь.

    Нужна потому, что без неё разрыв связи означает ТИХУЮ ПОТЕРЮ задачи: пять
    неудачных попыток подряд — и запись уходит в `failed` навсегда, а `prune`
    умеет только выбросить её. Ровно так пропала задача, созданная в момент
    обрыва VPN: локально она есть, на хабе её нет, и никто об этом не узнал бы.

    Попытки обнуляются: причина отказа была внешней, и наказывать за неё запись
    незачем.
    """
    from sqlalchemy import select, update

    from atlas.models import Outbox

    engine = make_engine(_db_url())
    with make_session(engine) as session:
        сколько = len(
            session.execute(
                select(Outbox.id).where(Outbox.status == "failed")
            ).all()
        )
        session.execute(
            update(Outbox)
            .where(Outbox.status == "failed")
            .values(status="pending", attempts=0)
        )
        session.commit()
    emit_data(
        {"requeued": сколько},
        text_renderer=lambda r: print(f"вернулось в очередь: {r['requeued']}"),
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


quarantine_app = typer.Typer(
    no_args_is_help=True,
    help="События, которые не удалось применить: list / clear.",
)
sync_app.add_typer(quarantine_app, name="quarantine")


@quarantine_app.command("list")
@command
def quarantine_list_cmd() -> None:
    """Что не доехало и почему.

    Существует потому, что молчаливый карантин был бы не лучше молчаливой
    остановки: событие отложили, синк поехал дальше — и человеку надо где-то
    увидеть, чего именно у него нет.
    """
    from sqlalchemy import select

    from atlas.models import SyncQuarantine

    engine = make_engine(_db_url())
    with make_session(engine) as session:
        строки = list(
            session.execute(
                select(SyncQuarantine).order_by(SyncQuarantine.occurred_at)
            ).scalars()
        )
        данные = [
            {
                "envelope_id": с.envelope_id,
                "kind": с.kind,
                "reason": с.reason,
                "occurred_at": с.occurred_at,
                "attempts": с.attempts,
            }
            for с in строки
        ]

    def показать(итог: list[dict]) -> None:
        if not итог:
            print("карантин пуст")
            return
        for з in итог:
            print(
                f"{з['occurred_at'] or '—':<28} {з['kind'] or '?':<8} "
                f"попыток {з['attempts']:<3} {з['reason'] or ''}"
            )

    emit_data(данные, text_renderer=показать)


@quarantine_app.command("clear")
@command
def quarantine_clear_cmd() -> None:
    """Забыть отложенное: события перестанут числиться недоехавшими.

    Курсор при этом НЕ отматывается: очистка — про список для человека, а не
    про повторную доставку. Чтобы получить события заново, нужен сброс курсора.
    """
    from sqlalchemy import delete

    from atlas.models import SyncQuarantine

    engine = make_engine(_db_url())
    with make_session(engine) as session:
        итог = session.execute(delete(SyncQuarantine))
        session.commit()
    emit_data(
        {"cleared": итог.rowcount or 0},
        text_renderer=lambda r: print(f"забыто записей: {r['cleared']}"),
    )
