"""CLI `atlas milestone ...` — контрольные точки. На clikit (--json по умолчанию).

Контрольная точка — ОБЯЗАТЕЛЬСТВО перед человеком: дата + артефакт + приёмщик.
Это не эпик: эпик отвечает на «что мы строим», КТ — на «что и когда мы
предъявляем». Состав КТ (эпики и задачи) — членство, а не иерархия: одна и та
же работа может входить и в промежуточное демо, и в финальную сдачу.

Конвейер состояний — человек ставит цель, агент работает, человек принимает:

    planning ─► ready_for_ai ─► ai_running ─► ready_for_review
                                                    │
                                    human_verified ◄┘
                                          │
                                    client_accepted

Возврат: reject → ai_running (агент дорабатывает) либо planning, если состав
пуст. Тупики блокера: block/unblock.
"""
from __future__ import annotations

import json
from datetime import datetime

import typer
from clikit import CliError, command, emit_data
from sqlalchemy import select

from atlas._time import local_now
from atlas.appconfig import default_actor
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import (
    ActionLog,
    Epic,
    Milestone,
    MilestoneItem,
    Participant,
    Project,
    Task,
)
from atlas.slugs import (
    AmbiguousRefError,
    SlugGenerationError,
    generate_unique_slug,
    resolve_project_ref,
    slugify_text,
)

milestone_app = typer.Typer(
    no_args_is_help=True,
    help="Контрольные точки — обязательства перед человеком (дата, артефакт, приёмка).",
)

_DEFAULT_ACTOR_SLUG = default_actor()

# Состояния конвейера. Порядок значим: индекс = позиция в конвейере.
STATES = (
    "planning",
    "ready_for_ai",
    "ai_running",
    "ready_for_review",
    "human_verified",
    "client_accepted",
)
TERMINAL_STATES = {"client_accepted", "cancelled"}
OUTCOMES = {"confirmed", "adjusted", "dropped"}

# Кто ставит состояние. Агент не может объявить работу принятой, человек не
# должен вручную двигать её «за агента» — поэтому переходы описаны явно.
ALLOWED_FROM = {
    "ready_for_ai": {"planning"},
    "ai_running": {"ready_for_ai", "ready_for_review", "blocked"},
    "ready_for_review": {"ai_running", "ready_for_ai"},
    "human_verified": {"ready_for_review"},
    "client_accepted": {"human_verified", "ready_for_review"},
    "blocked": {"planning", "ready_for_ai", "ai_running", "ready_for_review"},
    "cancelled": {"planning", "ready_for_ai", "ai_running", "ready_for_review", "blocked"},
}


def _db_url() -> str:
    return resolve_db_url()


def _resolve_project_or_die(session, ref: str) -> Project:
    try:
        proj = resolve_project_ref(session, ref)
    except AmbiguousRefError as exc:
        raise CliError("ambiguous_ref", str(exc))
    if proj is None:
        raise CliError("not_found", f"Проект '{ref}' не найден.")
    return proj


def _resolve_participant(session, slug: str | None) -> Participant | None:
    if slug is None:
        return None
    return session.execute(
        select(Participant).where(Participant.slug == slug)
    ).scalar_one_or_none()


def _milestone_owner(session, slug: str):
    return session.execute(
        select(Milestone).where(Milestone.slug == slug)
    ).scalar_one_or_none()


def _resolve_slug(session, *, slug: str | None, title: str, prefix: str) -> str | None:
    """Свободный slug или внятный отказ (как в epic/task: явный занятый — ошибка)."""
    if slug is not None:
        base = f"{prefix}-{slugify_text(slug)}" if not slug.startswith(prefix) else slug
        if _milestone_owner(session, base) is not None:
            raise CliError("slug_taken", f"Slug '{base}' уже занят другой КТ.")
        return base
    base = f"{prefix}-{slugify_text(title)}"
    if not base or base == f"{prefix}-":
        return None
    try:
        return generate_unique_slug(
            base, lambda s: _milestone_owner(session, s) is not None
        )
    except SlugGenerationError as exc:
        raise CliError("slug_generation_failed", str(exc))


def _resolve_milestone_or_die(session, ref: str) -> Milestone:
    ms = session.execute(
        select(Milestone).where(Milestone.slug == ref)
    ).scalar_one_or_none()
    if ms is None:
        ms = session.get(Milestone, ref)
    if ms is None:
        raise CliError("not_found", f"Контрольная точка '{ref}' не найдена.")
    return ms


def _parse_date(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise CliError("invalid_date", f"{field}: ожидается YYYY-MM-DD, получено '{value}'.")


def _log(
    session,
    ms: Milestone,
    action: str,
    details: dict,
    *,
    actor_slug: str | None = None,
    on_behalf_of: str | None = None,
) -> None:
    """Записать переход: кто выполнил и от чьего имени.

    Это два разных вопроса, и одним полем они не выражаются. Агент может
    закрыть точку сам — тогда ответственность на нём. А может закрыть от моего
    имени: «мы с клиентом созвонились, утвердили» — руки агентские, подпись
    моя. Если не различать, то через месяц по журналу не понять, кто на самом
    деле принимал решение, а это ровно то, ради чего журнал и ведётся.
    """
    actor = _resolve_participant(session, actor_slug or _DEFAULT_ACTOR_SLUG)
    if on_behalf_of:
        details = {**details, "on_behalf_of": on_behalf_of}
    session.add(
        ActionLog(
            actor_id=actor.id if actor else None,
            entity_type="milestone",
            entity_id=ms.id,
            action=action,
            details_json=json.dumps(details, ensure_ascii=False, default=str),
        )
    )


def _spawn_followups(
    session,
    ms: Milestone,
    titles: list[str],
    *,
    include_in_milestone: bool,
) -> list[dict]:
    """Завести задачи-доработки, всплывшие на приёмке.

    Приёмка почти никогда не бывает бинарной: либо приняли и тут же назвали,
    что доделать, либо не приняли и назвали, что мешает закрыть. Если эти
    хотелки остаются в переписке, они возвращаются через неделю как «мы же
    договаривались» — уже без срока и без цены.

    Разница между двумя случаями существенная. Доработки после принятия —
    новая работа, они в состав закрытой точки не входят (иначе точка,
    которую приняли, снова окажется незакрытой). Доработки после отказа
    входят: без них точку не закрыть, и сверка должна это видеть.
    """
    from atlas.appconfig import load_config
    from atlas.commands.task import _create_one_task

    project = session.get(Project, ms.project_id)
    cfg = load_config()
    created = []
    for title in titles:
        row = _create_one_task(
            session,
            cfg,
            {
                "project": project.slug,
                "title": title,
                "cpp": f"Доработка по контрольной точке «{ms.title}»: {title}",
                "status": "todo",
            },
            idx=len(created) + 1,
        )
        created.append(row)
        if include_in_milestone:
            task = session.execute(
                select(Task).where(Task.slug == row["slug"])
            ).scalar_one()
            session.add(
                MilestoneItem(milestone_id=ms.id, item_kind="task", item_id=task.id)
            )
    return created


def _items(session, ms: Milestone) -> list[MilestoneItem]:
    return list(
        session.execute(
            select(MilestoneItem).where(MilestoneItem.milestone_id == ms.id)
        ).scalars()
    )


def _composition_status(session, ms: Milestone) -> dict:
    """Состав КТ и его закрытость. КТ готова к сдаче, когда закрыт весь состав."""
    total = 0
    closed = 0
    open_items: list[str] = []
    for it in _items(session, ms):
        total += 1
        if it.item_kind == "task":
            task = session.get(Task, it.item_id)
            if task is None:
                continue
            if task.status in ("done", "cancelled"):
                closed += 1
            else:
                open_items.append(f"task:{task.slug or task.id}")
        else:
            epic = session.get(Epic, it.item_id)
            if epic is None:
                continue
            if epic.status in ("done", "closed", "cancelled"):
                closed += 1
            else:
                open_items.append(f"epic:{epic.slug or epic.id}")
    return {"total": total, "closed": closed, "open": open_items}


def _transition(session, ms: Milestone, target: str) -> None:
    allowed = ALLOWED_FROM.get(target)
    if allowed is None:
        raise CliError("invalid_state", f"Неизвестное состояние '{target}'.")
    if ms.state == target:
        raise CliError("already_in_state", f"КТ уже в состоянии '{target}'.")
    if ms.state not in allowed:
        raise CliError(
            "invalid_transition",
            f"Из '{ms.state}' нельзя в '{target}'. Допустимые источники: {sorted(allowed)}.",
        )
    ms.state = target
    ms.updated_at = local_now()


def _view(session, ms: Milestone, *, with_composition: bool = False) -> dict:
    proj = session.get(Project, ms.project_id)
    acceptor = session.get(Participant, ms.acceptor_id) if ms.acceptor_id else None
    data = {
        "slug": ms.slug,
        "id": ms.id,
        "title": ms.title,
        "project": proj.slug if proj else None,
        "state": ms.state,
        "criterion": ms.criterion,
        "due_date": ms.due_date.date().isoformat() if ms.due_date else None,
        "acceptor": acceptor.slug if acceptor else None,
        "artifact_url": ms.artifact_url,
        "outcome": ms.outcome,
        "track": ms.track,
    }
    if with_composition:
        data["composition"] = _composition_status(session, ms)
    return data


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


@milestone_app.command("add")
@command
def add_cmd(
    project: str = typer.Option(..., "--project", help="Project ref (slug | UUID)"),
    title: str = typer.Option(
        ...,
        "--title",
        help="Формула: [артефакт] + [кому] + [критерий готовности].",
    ),
    criterion: str | None = typer.Option(
        None, "--criterion", help="Что должно быть правдой, чтобы КТ была сдана."
    ),
    due: str | None = typer.Option(None, "--due", help="Дата КТ, YYYY-MM-DD."),
    acceptor: str | None = typer.Option(
        None, "--acceptor", help="Participant slug: кто вправе закрыть."
    ),
    track: str | None = typer.Option(
        None, "--track", help="Трек/компонент большого продукта (backend, web, cli)."
    ),
    slug: str | None = typer.Option(None, "--slug"),
) -> None:
    """Завести контрольную точку."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        proj = _resolve_project_or_die(session, project)
        acc = _resolve_participant(session, acceptor)
        if acceptor is not None and acc is None:
            raise CliError("not_found", f"Участник '{acceptor}' не найден.")

        final_slug = _resolve_slug(
            session, slug=slug, title=title, prefix=proj.prefix or "ms"
        )
        ms = Milestone(
            project_id=proj.id,
            title=title,
            slug=final_slug,
            criterion=criterion,
            due_date=_parse_date(due, "--due"),
            acceptor_id=acc.id if acc else None,
            track=track,
        )
        session.add(ms)
        session.flush()
        _log(session, ms, "milestone_created", {"slug": ms.slug, "project": proj.slug})
        session.commit()
        emit_data(
            _view(session, ms),
            text_renderer=lambda d: print(f"✓ КТ {d['slug'] or d['id']} — {d['title']}"),
        )


@milestone_app.command("list")
@command
def list_cmd(
    project: str | None = typer.Option(None, "--project"),
    state: str | None = typer.Option(None, "--state", help="Фильтр по состоянию."),
    show_all: bool = typer.Option(
        False, "--all", help="Показать и завершённые (по умолчанию — только живые)."
    ),
) -> None:
    """Список контрольных точек."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        stmt = select(Milestone).where(Milestone.archived_at.is_(None))
        if project:
            proj = _resolve_project_or_die(session, project)
            stmt = stmt.where(Milestone.project_id == proj.id)
        if state:
            stmt = stmt.where(Milestone.state == state)
        elif not show_all:
            stmt = stmt.where(Milestone.state.notin_(tuple(TERMINAL_STATES)))
        rows = list(session.execute(stmt.order_by(Milestone.due_date)).scalars())
        data = [_view(session, ms, with_composition=True) for ms in rows]

        def _render(items: list[dict]) -> None:
            if not items:
                print("Контрольных точек нет.")
                return
            for d in items:
                comp = d.get("composition") or {}
                print(
                    f"{d['state']:16} {d['slug'] or d['id']:32} "
                    f"{d['due_date'] or '—':12} "
                    f"{comp.get('closed', 0)}/{comp.get('total', 0)}  {d['title']}"
                )

        emit_data(data, text_renderer=_render)


@milestone_app.command("get")
@command
def get_cmd(ref: str = typer.Argument(..., help="slug | id контрольной точки")) -> None:
    """Карточка контрольной точки."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        emit_data(_view(session, ms, with_composition=True))


@milestone_app.command("update")
@command
def update_cmd(
    ref: str = typer.Argument(...),
    title: str | None = typer.Option(None, "--title"),
    criterion: str | None = typer.Option(None, "--criterion"),
    due: str | None = typer.Option(None, "--due"),
    acceptor: str | None = typer.Option(None, "--acceptor"),
    track: str | None = typer.Option(None, "--track"),
    artifact: str | None = typer.Option(
        None, "--artifact", help="Ссылка на доказательство: демо, стенд, документ, коммит."
    ),
) -> None:
    """Правка полей контрольной точки."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        if title is not None:
            ms.title = title
        if criterion is not None:
            ms.criterion = criterion
        if due is not None:
            ms.due_date = _parse_date(due, "--due")
        if track is not None:
            ms.track = track
        if artifact is not None:
            ms.artifact_url = artifact
        if acceptor is not None:
            acc = _resolve_participant(session, acceptor)
            if acc is None:
                raise CliError("not_found", f"Участник '{acceptor}' не найден.")
            ms.acceptor_id = acc.id
        ms.updated_at = local_now()
        _log(session, ms, "milestone_updated", {"slug": ms.slug})
        session.commit()
        emit_data(_view(session, ms))


# --------------------------------------------------------------------------- #
# Состав
# --------------------------------------------------------------------------- #


@milestone_app.command("include")
@command
def include_cmd(
    ref: str = typer.Argument(..., help="slug | id контрольной точки"),
    epic: str | None = typer.Option(None, "--epic", help="Эпик (slug | id)"),
    task: str | None = typer.Option(None, "--task", help="Задача (slug | id)"),
) -> None:
    """Включить эпик или задачу в состав контрольной точки."""
    if (epic is None) == (task is None):
        raise CliError("bad_args", "Укажи ровно одно: --epic или --task.")
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        if epic is not None:
            obj = session.execute(
                select(Epic).where(Epic.slug == epic)
            ).scalar_one_or_none() or session.get(Epic, epic)
            kind = "epic"
        else:
            obj = session.execute(
                select(Task).where(Task.slug == task)
            ).scalar_one_or_none() or session.get(Task, task)
            kind = "task"
        if obj is None:
            raise CliError("not_found", f"{kind} '{epic or task}' не найден.")
        if obj.project_id != ms.project_id:
            raise CliError(
                "cross_project",
                f"{kind} принадлежит другому проекту — состав КТ не выходит за проект.",
            )
        exists = session.execute(
            select(MilestoneItem).where(
                MilestoneItem.milestone_id == ms.id,
                MilestoneItem.item_kind == kind,
                MilestoneItem.item_id == obj.id,
            )
        ).scalar_one_or_none()
        if exists is not None:
            raise CliError("already_included", f"{kind} уже в составе этой КТ.")
        session.add(
            MilestoneItem(milestone_id=ms.id, item_kind=kind, item_id=obj.id)
        )
        _log(session, ms, "milestone_item_included", {"kind": kind, "ref": obj.slug})
        session.commit()
        emit_data(_view(session, ms, with_composition=True))


@milestone_app.command("exclude")
@command
def exclude_cmd(
    ref: str = typer.Argument(...),
    epic: str | None = typer.Option(None, "--epic"),
    task: str | None = typer.Option(None, "--task"),
) -> None:
    """Убрать эпик или задачу из состава контрольной точки."""
    if (epic is None) == (task is None):
        raise CliError("bad_args", "Укажи ровно одно: --epic или --task.")
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        kind = "epic" if epic is not None else "task"
        model = Epic if kind == "epic" else Task
        needle = epic or task
        obj = session.execute(
            select(model).where(model.slug == needle)
        ).scalar_one_or_none() or session.get(model, needle)
        if obj is None:
            raise CliError("not_found", f"{kind} '{needle}' не найден.")
        item = session.execute(
            select(MilestoneItem).where(
                MilestoneItem.milestone_id == ms.id,
                MilestoneItem.item_kind == kind,
                MilestoneItem.item_id == obj.id,
            )
        ).scalar_one_or_none()
        if item is None:
            raise CliError("not_included", f"{kind} не входит в состав этой КТ.")
        session.delete(item)
        _log(session, ms, "milestone_item_excluded", {"kind": kind, "ref": obj.slug})
        session.commit()
        emit_data(_view(session, ms, with_composition=True))


# --------------------------------------------------------------------------- #
# Конвейер
# --------------------------------------------------------------------------- #


@milestone_app.command("ready")
@command
def ready_cmd(ref: str = typer.Argument(...)) -> None:
    """planning → ready_for_ai: отдать контрольную точку в работу агенту.

    Инвариант: без критерия, даты и состава агент не поймёт, что делать и
    когда считать сделанным — поэтому переход закрыт.
    """
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        missing = []
        if not ms.criterion:
            missing.append("критерий (--criterion)")
        if ms.due_date is None:
            missing.append("дата (--due)")
        comp = _composition_status(session, ms)
        if comp["total"] == 0:
            missing.append("состав (milestone include)")
        if missing:
            raise CliError(
                "not_ready",
                "Нельзя отдать в работу, не хватает: " + ", ".join(missing),
            )
        _transition(session, ms, "ready_for_ai")
        _log(session, ms, "milestone_ready_for_ai", {"slug": ms.slug})
        session.commit()
        emit_data(_view(session, ms, with_composition=True))


@milestone_app.command("start")
@command
def start_cmd(ref: str = typer.Argument(...)) -> None:
    """ready_for_ai → ai_running: агент взял контрольную точку в цикл."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        _transition(session, ms, "ai_running")
        _log(session, ms, "milestone_started", {"slug": ms.slug})
        session.commit()
        emit_data(_view(session, ms))


@milestone_app.command("submit")
@command
def submit_cmd(
    ref: str = typer.Argument(...),
    artifact: str | None = typer.Option(
        None, "--artifact", help="Ссылка на доказательство прохождения."
    ),
    force: bool = typer.Option(
        False, "--force", help="Сдать при незакрытом составе (осознанно)."
    ),
) -> None:
    """→ ready_for_review: работа готова, нужен человек.

    Артефакт обязателен: «сделано» без доказательства проверить нельзя.
    """
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        if artifact is not None:
            ms.artifact_url = artifact
        if not ms.artifact_url:
            raise CliError(
                "no_artifact",
                "Нужна ссылка на артефакт: --artifact (демо, стенд, документ, коммит).",
            )
        comp = _composition_status(session, ms)
        if comp["open"] and not force:
            raise CliError(
                "composition_open",
                "Состав не закрыт: " + ", ".join(comp["open"]) + ". Либо доделать, либо --force.",
            )
        _transition(session, ms, "ready_for_review")
        ms.submitted_at = local_now()
        _log(
            session,
            ms,
            "milestone_submitted",
            {"slug": ms.slug, "artifact": ms.artifact_url, "forced": force},
        )
        session.commit()
        emit_data(_view(session, ms, with_composition=True))


_ACTOR_HELP = "Кто выполняет действие (slug участника). По умолчанию — я."
_BEHALF_HELP = (
    "От чьего имени. Агент закрывает точку не сам по себе, а по итогу "
    "разговора человека с клиентом — подпись остаётся человеческой."
)


@milestone_app.command("verify")
@command
def verify_cmd(
    ref: str = typer.Argument(...),
    actor: str | None = typer.Option(None, "--actor", help=_ACTOR_HELP),
    on_behalf_of: str | None = typer.Option(
        None, "--on-behalf-of", help=_BEHALF_HELP
    ),
) -> None:
    """ready_for_review → human_verified: я посмотрел, готово к показу."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        _transition(session, ms, "human_verified")
        _log(
            session,
            ms,
            "milestone_verified",
            {"slug": ms.slug},
            actor_slug=actor,
            on_behalf_of=on_behalf_of,
        )
        session.commit()
        emit_data(_view(session, ms))


@milestone_app.command("accept")
@command
def accept_cmd(
    ref: str = typer.Argument(...),
    outcome: str = typer.Option(
        "confirmed", "--outcome", help="confirmed | adjusted | dropped"
    ),
    note: str | None = typer.Option(None, "--note"),
    followup: list[str] = typer.Option(
        None,
        "--followup",
        help="Доработка, всплывшая на приёмке. Можно несколько — станут задачами.",
    ),
    actor: str | None = typer.Option(None, "--actor", help=_ACTOR_HELP),
    on_behalf_of: str | None = typer.Option(
        None, "--on-behalf-of", help=_BEHALF_HELP
    ),
) -> None:
    """→ client_accepted: контрольная точка сдана и принята.

    Клиент ничего не нажимает сам — приёмку фиксирует тот, кто с ним
    разговаривал. Поэтому здесь же принимаются доработки: «приняли, но
    доделать вот это» — самый частый исход, и он не должен теряться.
    """
    if outcome not in OUTCOMES:
        raise CliError("invalid_outcome", f"Допустимо: {sorted(OUTCOMES)}.")
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        _transition(session, ms, "client_accepted")
        ms.outcome = outcome
        ms.accepted_at = local_now()

        # Доработки после принятия — новая работа. В состав закрытой точки они
        # не входят: иначе принятая точка снова окажется незакрытой.
        spawned = (
            _spawn_followups(session, ms, list(followup), include_in_milestone=False)
            if followup
            else []
        )

        _log(
            session,
            ms,
            "milestone_accepted",
            {
                "slug": ms.slug,
                "outcome": outcome,
                "note": note,
                "followups": [t["slug"] for t in spawned],
            },
            actor_slug=actor,
            on_behalf_of=on_behalf_of,
        )
        session.commit()
        view = _view(session, ms)
        view["followups"] = spawned
        emit_data(view)


@milestone_app.command("reject")
@command
def reject_cmd(
    ref: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason", "-m", help="Что не так — обязательно."),
    followup: list[str] = typer.Option(
        None,
        "--followup",
        help="Что доделать, чтобы закрыть. Станет задачами в составе точки.",
    ),
    actor: str | None = typer.Option(None, "--actor", help=_ACTOR_HELP),
    on_behalf_of: str | None = typer.Option(
        None, "--on-behalf-of", help=_BEHALF_HELP
    ),
) -> None:
    """ready_for_review → ai_running: вернуть на доработку с причиной."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        if ms.state != "ready_for_review":
            raise CliError(
                "invalid_transition",
                f"Вернуть можно только из 'ready_for_review', сейчас '{ms.state}'.",
            )
        _transition(session, ms, "ai_running")

        # Эти доработки входят в состав точки: без них её не закрыть, и сверка
        # должна видеть, чего именно не хватает.
        spawned = (
            _spawn_followups(session, ms, list(followup), include_in_milestone=True)
            if followup
            else []
        )

        _log(
            session,
            ms,
            "milestone_rejected",
            {
                "slug": ms.slug,
                "reason": reason,
                "followups": [t["slug"] for t in spawned],
            },
            actor_slug=actor,
            on_behalf_of=on_behalf_of,
        )
        session.commit()
        view = _view(session, ms)
        view["followups"] = spawned
        emit_data(view)


@milestone_app.command("block")
@command
def block_cmd(
    ref: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason", "-m"),
) -> None:
    """Пометить контрольную точку заблокированной с причиной."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        _transition(session, ms, "blocked")
        _log(session, ms, "milestone_blocked", {"slug": ms.slug, "reason": reason})
        session.commit()
        emit_data(_view(session, ms))


@milestone_app.command("cancel")
@command
def cancel_cmd(
    ref: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason", "-m", help="Почему снимаем — обязательно."),
) -> None:
    """Снять контрольную точку: обязательство больше не актуально.

    Не то же самое, что принять или заблокировать: точка не сдаётся и не ждёт —
    её просто больше нет в плане. Причина обязательна, иначе через месяц никто
    не вспомнит, почему обязательство исчезло.
    """
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        _transition(session, ms, "cancelled")
        ms.outcome = "dropped"
        _log(session, ms, "milestone_cancelled", {"slug": ms.slug, "reason": reason})
        session.commit()
        emit_data(_view(session, ms))


@milestone_app.command("unblock")
@command
def unblock_cmd(ref: str = typer.Argument(...)) -> None:
    """Снять блокировку — вернуть в работу агенту."""
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        ms = _resolve_milestone_or_die(session, ref)
        if ms.state != "blocked":
            raise CliError("not_blocked", f"КТ не заблокирована (состояние '{ms.state}').")
        _transition(session, ms, "ai_running")
        _log(session, ms, "milestone_unblocked", {"slug": ms.slug})
        session.commit()
        emit_data(_view(session, ms))
