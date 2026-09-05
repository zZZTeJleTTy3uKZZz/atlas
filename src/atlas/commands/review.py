"""CLI `atlas review` — сверка портфеля одной командой.

Смысл: НЕ ходить в систему глазами. Ответ на вопрос «как дела» и на вопрос
«где мы себя обманываем» — в одном месте:

* что ждёт МЕНЯ (приёмка) и что просрочено — то, ради чего меня дёргают;
* что сейчас крутится у агентов — сколько параллельно;
* дыры планирования: проект без контрольных точек, КТ без критерия, без даты,
  без состава, без приёмщика. Именно эти дыры позже всплывают как «проект
  тянется, а где он — непонятно».

Отдельно — размер очереди синхронизации: она копится независимо от того,
доезжает ли, и однажды тихо встала на 4683 записях.
"""
from __future__ import annotations

import typer
from clikit import command, emit_data
from sqlalchemy import func, select

from atlas._time import local_now
from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import (
    Milestone,
    MilestoneItem,
    Outbox,
    Project,
    ProjectStatus,
    ProjectType,
    Task,
)

review_app = typer.Typer(no_args_is_help=False, help="Сверка портфеля: что ждёт меня и где дыры.")

# Состояния, в которых КТ уже не требует внимания.
_CLOSED = ("client_accepted", "cancelled")

#: Сколько дней без движения превращают активный проект в забытый.
STALE_PROJECT_DAYS = 30


def _db_url() -> str:
    return resolve_db_url()


def _tracked_projects(session, *, everything: bool) -> dict[str, Project]:
    """Проекты, которые ВЕДУТСЯ как проекты, а не просто существуют.

    Требовать паспорт и контрольные точки от каждого кита и CLI-навыка — это
    ровно то усложнение, ради ухода от которого система и строится: в портфеле
    под сотню активных записей, а обязательства перед людьми есть у единиц.

    Под сверку попадает проект, если выполнено хотя бы одно:
      * это клиентский проект (там всегда есть обязательства и сроки);
      * у него уже есть хоть одна живая контрольная точка;
      * у него начат паспорт (кто-то сознательно завёл его в контур).

    `--all` снимает фильтр — на случай общей ревизии портфеля.
    """
    rows = list(
        session.execute(
            select(Project)
            .join(ProjectStatus, Project.status_id == ProjectStatus.id)
            .where(Project.archived_at.is_(None), ProjectStatus.slug == "active")
        ).scalars()
    )
    if everything:
        return {p.id: p for p in rows}

    with_ms = {
        pid
        for (pid,) in session.execute(
            select(Milestone.project_id).where(Milestone.archived_at.is_(None)).distinct()
        ).all()
    }
    client_type_ids = {
        tid
        for (tid,) in session.execute(
            select(ProjectType.id).where(ProjectType.slug == "client-project")
        ).all()
    }

    def _tracked(p: Project) -> bool:
        if p.id in with_ms:
            return True
        # Модуль контейнера — часть чужого проекта, а не самостоятельное
        # обязательство: у «Перетяжка · Калькулятор» нет своего срока сдачи
        # и своего приёмщика, они у родителя. Иначе сверка тонет в модулях.
        if p.parent_id is not None:
            return False
        if p.type_id in client_type_ids:
            return True
        return bool(p.point_a or p.point_b or p.done_criteria or p.hard_deadline)

    return {p.id: p for p in rows if _tracked(p)}


def _collect(session, *, project_filter: str | None, everything: bool) -> dict:
    projects = _tracked_projects(session, everything=everything)
    if project_filter:
        projects = {
            pid: p
            for pid, p in projects.items()
            if p.slug == project_filter or pid == project_filter
        }

    milestones = list(
        session.execute(
            select(Milestone).where(
                Milestone.archived_at.is_(None),
                Milestone.project_id.in_(projects.keys()) if projects else False,
            )
        ).scalars()
    )

    item_counts = dict(
        session.execute(
            select(MilestoneItem.milestone_id, func.count(MilestoneItem.id)).group_by(
                MilestoneItem.milestone_id
            )
        ).all()
    )

    today = local_now()
    waiting_me: list[dict] = []
    running: list[dict] = []
    blocked: list[dict] = []
    overdue: list[dict] = []
    gaps: list[dict] = []

    with_milestones: set[str] = set()

    for ms in milestones:
        proj = projects.get(ms.project_id)
        if proj is None:
            continue
        if ms.state not in _CLOSED:
            with_milestones.add(ms.project_id)
        ref = {
            "milestone": ms.slug or ms.id,
            "project": proj.slug,
            "title": ms.title,
            "due_date": ms.due_date.date().isoformat() if ms.due_date else None,
            "state": ms.state,
        }

        if ms.state == "ready_for_review":
            waiting_me.append(ref)
        elif ms.state in ("ai_running", "ready_for_ai"):
            running.append(ref)
        elif ms.state == "blocked":
            blocked.append(ref)

        # Сравниваем ДАТЫ, а не моменты: КТ со сроком «сегодня» не просрочена,
        # даже если сейчас час ночи и календарная дата формально уже наступила.
        if (
            ms.due_date is not None
            and ms.due_date.date() < today.date()
            and ms.state not in _CLOSED
        ):
            overdue.append({**ref, "days": (today.date() - ms.due_date.date()).days})

        if ms.state in _CLOSED:
            continue
        missing = []
        if not ms.criterion:
            missing.append("критерий")
        if ms.due_date is None:
            missing.append("дата")
        if ms.acceptor_id is None:
            missing.append("приёмщик")
        if item_counts.get(ms.id, 0) == 0:
            missing.append("состав")
        if missing:
            gaps.append({**ref, "missing": missing})

    no_milestones = [
        {"project": p.slug, "name": p.name}
        for pid, p in projects.items()
        if pid not in with_milestones
    ]

    no_passport = []
    for p in projects.values():
        missing = []
        if not p.point_b:
            missing.append("точка Б")
        if not p.done_criteria:
            missing.append("критерий завершения")
        if p.hard_deadline is None:
            missing.append("крайний срок")
        if missing:
            no_passport.append({"project": p.slug, "missing": missing})

    # Забытые: числятся активными, но никто их не трогал. Статус не меняем —
    # проект может стоять по договорённости с клиентом; но молча забытый проект
    # и осознанно приостановленный выглядят в системе одинаково, и это надо
    # разводить руками, а не догадками.
    stale_projects = []
    for p in projects.values():
        touched = p.last_touched_at or p.updated_at
        if touched is None:
            continue
        days = (today - touched).days
        if days >= STALE_PROJECT_DAYS:
            stale_projects.append({"project": p.slug, "days": days})
    stale_projects.sort(key=lambda r: -r["days"])

    outbox_pending = session.execute(
        select(func.count(Outbox.id)).where(Outbox.status == "pending")
    ).scalar_one()

    return {
        "projects_active": len(projects),
        "waiting_tasks": _ждущие_задачи(session),
        "waiting_me": waiting_me,
        "running": running,
        "blocked": blocked,
        "overdue": overdue,
        "gaps": gaps,
        "projects_without_milestones": no_milestones,
        "projects_without_passport": no_passport,
        "stale_projects": stale_projects,
        "outbox_pending": outbox_pending,
    }


def _ждущие_задачи(session) -> list[dict]:
    """Открытые задачи, которые ждут другую незакрытую задачу (#2429).

    Отдельная группа, а не строчка в общем списке: такие задачи не надо
    распределять — их надо разблокировать, и это другое действие. Пока они
    лежали вперемешку с обычными, за них периодически брались, упирались и
    возвращали обратно.

    Межпроектные показываем с обеих сторон именами проектов: чаще всего держит
    задача в ките, а стоит клиентская, и по номерам это не читается.
    """
    from atlas.commands.task_dependency import ЗАКРЫТЫЕ  # noqa: PLC0415
    from atlas.models import TaskDependency  # noqa: PLC0415

    итог: list[dict] = []
    связи = session.scalars(select(TaskDependency)).all()
    for связь in связи:
        ждущая = session.get(Task, связь.task_id)
        держатель = session.get(Task, связь.depends_on_id)
        if ждущая is None or держатель is None:
            continue
        if ждущая.status in ЗАКРЫТЫЕ or держатель.status in ЗАКРЫТЫЕ:
            continue
        проект_ж = session.get(Project, ждущая.project_id) if ждущая.project_id else None
        проект_д = (
            session.get(Project, держатель.project_id) if держатель.project_id else None
        )
        итог.append({
            "task": ждущая.number,
            "title": ждущая.title,
            "project": проект_ж.slug if проект_ж else None,
            "waits_for": держатель.number,
            "blocker_title": держатель.title,
            "blocker_project": проект_д.slug if проект_д else None,
            "reason": связь.reason,
        })
    итог.sort(key=lambda р: (р["blocker_project"] or "", р["waits_for"]))
    return итог


def _render(d: dict) -> None:
    print(f"Активных проектов: {d['projects_active']}")
    print(
        f"  в работе у агентов — {len(d['running'])} · "
        f"ждут моей приёмки — {len(d['waiting_me'])} · "
        f"заблокировано — {len(d['blocked'])} · "
        f"просрочено — {len(d['overdue'])}"
    )

    def _block(title: str, rows: list[dict], fmt) -> None:
        if not rows:
            return
        print(f"\n{title}")
        for r in rows:
            print("  · " + fmt(r))

    _block(
        "Ждут другую задачу (разблокировать, а не распределять):",
        d.get("waiting_tasks") or [],
        lambda r: (
            f"#{r['task']} {r['project']} — {r['title'][:45]} "
            f"ждёт #{r['waits_for']} ({r['blocker_project']})"
            + (f": {r['reason']}" if r.get("reason") else "")
        ),
    )
    _block(
        "Ждут меня:",
        d["waiting_me"],
        lambda r: f"{r['project']}/{r['milestone']} — {r['title']}",
    )
    _block(
        "Просрочено:",
        d["overdue"],
        lambda r: f"{r['project']}/{r['milestone']} — {r['days']} дн. — {r['title']}",
    )
    _block(
        "Заблокировано:",
        d["blocked"],
        lambda r: f"{r['project']}/{r['milestone']} — {r['title']}",
    )
    _block(
        "Дыры в планировании:",
        d["gaps"],
        lambda r: f"{r['project']}/{r['milestone']}: нет {', '.join(r['missing'])}",
    )
    _block(
        "Проекты без контрольных точек:",
        d["projects_without_milestones"],
        lambda r: f"{r['project']} — {r['name']}",
    )
    _block(
        "Давно без движения:",
        d.get("stale_projects") or [],
        lambda r: f"{r['project']} — {r['days']} дн. Довести или приостановить осознанно",
    )
    _block(
        "Проекты без паспорта:",
        d["projects_without_passport"],
        lambda r: f"{r['project']}: нет {', '.join(r['missing'])}",
    )

    if d["outbox_pending"] > 500:
        print(
            f"\n⚠ Очередь синхронизации: {d['outbox_pending']} записей ждут отправки — "
            "транспорт стоит, разобрать до включения."
        )


@review_app.callback(invoke_without_command=True)
@command
def review_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", help="Сверка по одному проекту."),
    everything: bool = typer.Option(
        False, "--all", help="Все активные проекты, а не только ведущиеся как проекты."
    ),
) -> None:
    """Сверка: что ждёт меня, что в работе, где дыры планирования."""
    if ctx.invoked_subcommand is not None:
        return
    engine = make_engine(_db_url())
    with make_session(engine) as session:
        emit_data(
            _collect(session, project_filter=project, everything=everything),
            text_renderer=_render,
        )
