"""CLI `atlas counterparty ...` — контрагенты: с кем у нас отношения.

Уровень над проектом. Разница принципиальная: у контрагента нет срока и точки Б,
он не «завершается» — отношения либо есть, либо нет. Поэтому он не проект, хотя
до сих пор им притворялся: «Перетяжка» была одновременно клиентом и проектом, и
второй проект того же клиента класть было некуда, а вопрос «что у нас по Славе»
не имел ответа.

Сущность и связи в базе существовали давно (`counterparties`, `owner_id`,
`customer_id`), но управлять ими было нечем — поэтому механизм стоял мёртвым: два
контрагента на сто пятьдесят проектов и ни одного заказчика.

Роли у проекта две, и они разные:

* **владелец** (`--owner`) — чей это проект, кто им распоряжается;
* **заказчик** (`--customer`) — для кого он делается, кто принимает.

У клиентской работы они расходятся: владелец мы, заказчик клиент. У своих
продуктов совпадают.
"""
from __future__ import annotations

import typer
from clikit import CliError, command, emit_data
from sqlalchemy import func, select

from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import Counterparty, Project
from atlas.slugs import slugify_text

counterparty_app = typer.Typer(
    help="Контрагенты: клиенты, я сам, компания.", no_args_is_help=True
)

KINDS = ("person", "company")


def _db_url() -> str:
    return resolve_db_url()


def _resolve_or_die(session, ref: str) -> Counterparty:
    found = session.execute(
        select(Counterparty).where(Counterparty.slug == ref)
    ).scalar_one_or_none()
    if found is None:
        found = session.get(Counterparty, ref)
    if found is None:
        known = [
            c.slug for c in session.execute(select(Counterparty)).scalars()
        ]
        raise CliError(
            "not_found",
            f"Контрагент '{ref}' не найден. Известные: {', '.join(sorted(known)) or '—'}",
        )
    return found


def _view(session, cp: Counterparty) -> dict:
    projects = list(
        session.execute(
            select(Project).where(
                (Project.owner_id == cp.id) | (Project.customer_id == cp.id)
            )
        ).scalars()
    )
    return {
        "slug": cp.slug,
        "name": cp.name,
        "kind": cp.kind,
        "git_namespace": cp.git_namespace,
        "projects": sorted(p.slug for p in projects),
        "projects_count": len(projects),
    }


@counterparty_app.command("add")
@command
def add_cmd(
    name: str = typer.Argument(..., help="Название: «Слава (Перетяжка)»"),
    slug: str | None = typer.Option(None, "--slug", help="Авто из названия."),
    kind: str = typer.Option("person", "--kind", help=" | ".join(KINDS)),
    git_namespace: str | None = typer.Option(None, "--git-namespace"),
) -> None:
    """Завести контрагента."""
    if kind not in KINDS:
        raise CliError("bad_kind", f"Допустимо: {', '.join(KINDS)}.")
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        final_slug = slug or slugify_text(name)
        if not final_slug:
            raise CliError("slug_gen", "Не удалось собрать слаг — задай --slug.")
        exists = session.execute(
            select(Counterparty).where(Counterparty.slug == final_slug)
        ).scalar_one_or_none()
        if exists is not None:
            raise CliError("slug_taken", f"Слаг '{final_slug}' занят.")

        cp = Counterparty(
            slug=final_slug, name=name, kind=kind, git_namespace=git_namespace
        )
        session.add(cp)
        session.commit()
        emit_data(_view(session, cp))


@counterparty_app.command("list")
@command
def list_cmd(
    kind: str | None = typer.Option(None, "--kind", help="Фильтр: person | company"),
) -> None:
    """Кто у нас есть и сколько за каждым проектов."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        query = select(Counterparty)
        if kind:
            query = query.where(Counterparty.kind == kind)
        rows = [
            _view(session, cp)
            for cp in session.execute(query.order_by(Counterparty.slug)).scalars()
        ]
        emit_data(rows)


@counterparty_app.command("get")
@command
def get_cmd(ref: str = typer.Argument(..., help="slug | id")) -> None:
    """Карточка контрагента и его проекты."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        emit_data(_view(session, _resolve_or_die(session, ref)))


@counterparty_app.command("update")
@command
def update_cmd(
    ref: str = typer.Argument(...),
    name: str | None = typer.Option(None, "--name"),
    kind: str | None = typer.Option(None, "--kind"),
    git_namespace: str | None = typer.Option(None, "--git-namespace"),
) -> None:
    """Поправить карточку."""
    if kind is not None and kind not in KINDS:
        raise CliError("bad_kind", f"Допустимо: {', '.join(KINDS)}.")
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        cp = _resolve_or_die(session, ref)
        if name is not None:
            cp.name = name
        if kind is not None:
            cp.kind = kind
        if git_namespace is not None:
            cp.git_namespace = git_namespace
        session.commit()
        emit_data(_view(session, cp))


@counterparty_app.command("attach")
@command
def attach_cmd(
    ref: str = typer.Argument(..., help="Контрагент: slug | id"),
    project: str = typer.Option(..., "--project", help="Проект: slug"),
    role: str = typer.Option(
        "customer", "--role", help="customer (для кого делаем) | owner (чей проект)"
    ),
) -> None:
    """Привязать проект к контрагенту.

    Заказчик и владелец — разные роли: у клиентской работы владелец мы, а
    заказчик клиент. Путать их нельзя, иначе «наши проекты» и «проекты клиента»
    сольются в один список.
    """
    if role not in ("customer", "owner"):
        raise CliError("bad_role", "Допустимо: customer | owner.")
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        cp = _resolve_or_die(session, ref)
        proj = session.execute(
            select(Project).where(Project.slug == project)
        ).scalar_one_or_none()
        if proj is None:
            raise CliError("not_found", f"Проект '{project}' не найден.")
        if role == "customer":
            proj.customer_id = cp.id
        else:
            proj.owner_id = cp.id
        session.commit()
        emit_data({"counterparty": cp.slug, "project": proj.slug, "role": role})


@counterparty_app.command("orphans")
@command
def orphans_cmd() -> None:
    """Проекты без принадлежности — чьи они, непонятно.

    Отдельная команда, потому что это главная дыра: пока у проекта нет
    контрагента, вопрос «что у нас по клиенту» не имеет ответа, а работа по
    нему невидима на уровне отношений.
    """
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        rows = list(
            session.execute(
                select(Project)
                .where(Project.customer_id.is_(None), Project.owner_id.is_(None))
                .where(Project.archived_at.is_(None))
                .order_by(Project.slug)
            ).scalars()
        )
        total = session.execute(select(func.count()).select_from(Project)).scalar()
        emit_data(
            {
                "orphans": [p.slug for p in rows],
                "orphans_count": len(rows),
                "projects_total": total,
            }
        )
