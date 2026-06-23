"""Pure-logic аналитика портфеля Atlas (эпик Dashboard / статистика).

Read-only функции, считающие сводки по существующим моделям PM-БД и git —
БЕЗ нового стора/таблиц. Каждая функция чистая и unit-тестируема (typer/clikit
тут нет — это слой ниже CLI).

Функции:
- ``project_counts(session)`` — всего проектов + разбивка по типу /
  контрагенту (owner) / статусу (#128).
- ``parse_period(spec, *, now=None)`` — ``7d|30d|month|year|<from..to>`` →
  ``(start, end)`` naive datetime (#129).
- ``activity_window(session, *, start, end, ...)`` — активность в окне:
  проекты (last_touched_at/created_at), задачи (created/completed), эпики (#129).
- ``provenance_stats(session)`` — топ проектов-источников и -приёмников
  инжектированных задач + доля реализованных (done / всего) (#130).
- ``git_stats(local_path)`` — число коммитов, последний коммит и каденс
  (дней на коммит) из git-репозитория по local_path. subprocess через
  ``run`` (мокается в тестах) (#131).

Соглашение по «разбивкам»: каждая возвращается списком dict
``{"key": <slug|—>, "count": int}`` (json-консистентно; рендер таблиц —
в CLI-слое).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from atlas.pm._time import local_now
from atlas.pm.git_backend import run
from atlas.pm.models import (
    Counterparty,
    Epic,
    Project,
    ProjectStatus,
    ProjectTag,
    ProjectType,
    Tag,
    Task,
)

# Статусы задач, считающиеся «реализованными» для provenance-доли.
REALIZED_TASK_STATUSES = ("done",)

# Бакет для записей без ключа (без owner / source и т.п.) в разбивках.
NO_KEY = "—"


# --------------------------------------------------------------------------- #
# #128 project_counts                                                          #
# --------------------------------------------------------------------------- #


def project_counts(session: Session) -> dict[str, Any]:
    """Всего активных проектов + разбивки по типу / owner / статусу.

    Считаются только entity_kind='project' (идеи/inbox — не проекты портфеля).
    ``total`` — НЕ архивные (archived_at IS NULL); ``archived`` — отдельный
    счётчик архивных. Разбивки берутся по активным.
    """
    base = (
        select(Project)
        .where(Project.entity_kind == "project")
    )

    total = session.execute(
        select(func.count())
        .select_from(Project)
        .where(Project.entity_kind == "project", Project.archived_at.is_(None))
    ).scalar_one()

    archived = session.execute(
        select(func.count())
        .select_from(Project)
        .where(Project.entity_kind == "project", Project.archived_at.is_not(None))
    ).scalar_one()

    # by_type
    type_rows = session.execute(
        select(ProjectType.slug, func.count(Project.id))
        .join(ProjectType, Project.type_id == ProjectType.id)
        .where(Project.entity_kind == "project", Project.archived_at.is_(None))
        .group_by(ProjectType.slug)
        .order_by(func.count(Project.id).desc(), ProjectType.slug)
    ).all()
    by_type = [{"key": slug, "count": count} for slug, count in type_rows]

    # by_status
    status_rows = session.execute(
        select(ProjectStatus.slug, func.count(Project.id))
        .join(ProjectStatus, Project.status_id == ProjectStatus.id)
        .where(Project.entity_kind == "project", Project.archived_at.is_(None))
        .group_by(ProjectStatus.slug)
        .order_by(func.count(Project.id).desc(), ProjectStatus.slug)
    ).all()
    by_status = [{"key": slug, "count": count} for slug, count in status_rows]

    # by_owner (контрагент-владелец; NULL → бакет NO_KEY)
    by_owner = _counterparty_breakdown(session, Project.owner_id)
    # by_customer (контрагент-заказчик)
    by_customer = _counterparty_breakdown(session, Project.customer_id)

    return {
        "total": total,
        "archived": archived,
        "by_type": by_type,
        "by_status": by_status,
        "by_owner": by_owner,
        "by_customer": by_customer,
    }


def _counterparty_breakdown(session: Session, fk_column) -> list[dict[str, Any]]:
    """Разбивка активных проектов по контрагенту (owner или customer FK).

    NULL FK → бакет ``NO_KEY`` ('—'). Сортировка по убыванию count.
    """
    rows = session.execute(
        select(Counterparty.slug, func.count(Project.id))
        .select_from(Project)
        .join(Counterparty, fk_column == Counterparty.id, isouter=True)
        .where(Project.entity_kind == "project", Project.archived_at.is_(None))
        .group_by(Counterparty.slug)
    ).all()
    out: list[dict[str, Any]] = []
    for slug, count in rows:
        out.append({"key": slug if slug is not None else NO_KEY, "count": count})
    out.sort(key=lambda r: (-r["count"], r["key"]))
    return out


# --------------------------------------------------------------------------- #
# #129 parse_period                                                           #
# --------------------------------------------------------------------------- #

_RELATIVE_RE = re.compile(r"^(\d+)d$")
_RANGE_SEP = ".."


def parse_period(spec: str, *, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """Распарсить спецификацию периода в ``(start, end)`` naive datetime.

    Поддержка:
    - ``"<N>d"`` — последние N дней: ``(now - N days, now)``.
    - ``"month"`` — с первого числа текущего месяца до now.
    - ``"year"`` — с 1 января текущего года до now.
    - ``"YYYY-MM-DD..YYYY-MM-DD"`` — явный диапазон (start, end включительно
      как границы). Для границы, заданной голой датой (без времени), start
      нормализуется к 00:00:00, а end — к концу дня (23:59:59.999999), чтобы
      конечный день целиком входил в окно (сравнение ``touched <= end``). Если
      граница задана со временем (``THH:MM:SS``) — берётся как есть.

    ``now`` по умолчанию — ``local_now()`` (offset из конфига). Невалидная
    спецификация → ``ValueError``.
    """
    if not spec:
        raise ValueError("Пустая спецификация периода.")
    spec = spec.strip()
    base = now if now is not None else local_now()

    # Явный диапазон
    if _RANGE_SEP in spec:
        left, right = spec.split(_RANGE_SEP, 1)
        left_raw, right_raw = left.strip(), right.strip()
        try:
            start = _parse_date(left_raw)
            end = _parse_date(right_raw)
        except ValueError as exc:
            raise ValueError(f"Невалидный диапазон '{spec}': {exc}") from exc
        # Голая дата справа → расширяем до конца дня (иначе теряется весь
        # последний день: 00:00:00 отсекает события после полуночи).
        if _is_date_only(right_raw):
            end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
        if start > end:
            raise ValueError(f"Начало диапазона позже конца: {spec}")
        return start, end

    # Относительные N дней
    m = _RELATIVE_RE.match(spec)
    if m:
        days = int(m.group(1))
        return base - timedelta(days=days), base

    if spec == "month":
        start = base.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, base

    if spec == "year":
        start = base.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, base

    raise ValueError(
        f"Невалидный период '{spec}'. Ожидается: <N>d | month | year | "
        "YYYY-MM-DD..YYYY-MM-DD."
    )


def _parse_date(raw: str) -> datetime:
    """'YYYY-MM-DD' (или 'YYYY-MM-DDTHH:MM:SS') → naive datetime."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError(f"не дата: '{raw}'")


def _is_date_only(raw: str) -> bool:
    """True, если строка — голая дата 'YYYY-MM-DD' без компонента времени."""
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------- #
# #129 activity_window                                                        #
# --------------------------------------------------------------------------- #


def activity_window(
    session: Session,
    *,
    start: datetime,
    end: datetime,
    type_slug: Optional[str] = None,
    owner_slug: Optional[str] = None,
    customer_slug: Optional[str] = None,
    tag_slug: Optional[str] = None,
) -> dict[str, Any]:
    """Сводка активности портфеля в окне ``[start, end]``.

    «Активный проект» — у которого last_touched_at попадает в окно (или
    created_at, если last_touched_at пуст). Архивные проекты (archived_at IS
    NOT NULL) исключаются — консистентно с project_counts. Фильтры (опц.):
    - ``type_slug`` — только проекты этого типа;
    - ``owner_slug`` / ``customer_slug`` — по контрагенту;
    - ``tag_slug`` — только проекты с этим тегом (ProjectTag→Tag по slug).

    Возвращает:
    - ``projects`` — список активных проектов в окне (slug + last_touched);
    - ``projects_active`` — их число;
    - ``tasks_created`` — задач создано в окне (created_at, без архивных);
    - ``tasks_completed`` — задач завершено в окне (completed_at, без архивных);
    - ``epics_created`` — эпиков создано в окне (created_at).
    """
    touched = func.coalesce(Project.last_touched_at, Project.created_at)
    proj_stmt = (
        select(Project)
        .where(
            Project.entity_kind == "project",
            Project.archived_at.is_(None),
            touched >= start,
            touched <= end,
        )
        .order_by(touched.desc())
    )
    proj_stmt = _apply_project_filters(
        session, proj_stmt, type_slug, owner_slug, customer_slug, tag_slug
    )
    projects = session.execute(proj_stmt).scalars().all()
    project_data = [
        {
            "slug": p.slug,
            "name": p.name,
            "last_touched_at": (
                p.last_touched_at.isoformat() if p.last_touched_at
                else (p.created_at.isoformat() if p.created_at else None)
            ),
        }
        for p in projects
    ]
    project_ids = {p.id for p in projects}

    # Задачи: created/completed в окне. Если задан фильтр проектов — ограничиваем
    # подсчёт задач теми же проектами (множество активных в окне может не
    # совпадать с проектами задач; для счётчиков фильтр по проекту применяем
    # через тот же набор фильтров, но по всем проектам типа/контрагента).
    filtered_project_ids = _filtered_project_ids(
        session, type_slug, owner_slug, customer_slug, tag_slug
    )

    tasks_created = _count_tasks_in_window(
        session, Task.created_at, start, end, filtered_project_ids
    )
    tasks_completed = _count_tasks_in_window(
        session, Task.completed_at, start, end, filtered_project_ids
    )
    epics_created = _count_epics_in_window(
        session, start, end, filtered_project_ids
    )

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "projects": project_data,
        "projects_active": len(project_data),
        "tasks_created": tasks_created,
        "tasks_completed": tasks_completed,
        "epics_created": epics_created,
    }


def _apply_project_filters(
    session, stmt, type_slug, owner_slug, customer_slug, tag_slug=None
):
    """Навесить join'ы/where для type/owner/customer/tag на проектный select."""
    if type_slug is not None:
        stmt = stmt.join(ProjectType, Project.type_id == ProjectType.id).where(
            ProjectType.slug == type_slug
        )
    if owner_slug is not None:
        owner_cp = aliased(Counterparty)
        stmt = stmt.join(owner_cp, Project.owner_id == owner_cp.id).where(
            owner_cp.slug == owner_slug
        )
    if customer_slug is not None:
        cust_cp = aliased(Counterparty)
        stmt = stmt.join(cust_cp, Project.customer_id == cust_cp.id).where(
            cust_cp.slug == customer_slug
        )
    if tag_slug is not None:
        stmt = (
            stmt.join(ProjectTag, ProjectTag.project_id == Project.id)
            .join(Tag, Tag.id == ProjectTag.tag_id)
            .where(Tag.slug == tag_slug)
        )
    return stmt


def _filtered_project_ids(
    session: Session,
    type_slug: Optional[str],
    owner_slug: Optional[str],
    customer_slug: Optional[str],
    tag_slug: Optional[str] = None,
) -> Optional[set[str]]:
    """Множество project_id, удовлетворяющих фильтрам, или None если фильтров нет.

    None означает «без ограничения по проекту» (считаем по всем). Архивные
    проекты исключаются (консистентно с proj_stmt в activity_window).
    """
    if (
        type_slug is None
        and owner_slug is None
        and customer_slug is None
        and tag_slug is None
    ):
        return None
    stmt = select(Project.id).where(
        Project.entity_kind == "project", Project.archived_at.is_(None)
    )
    stmt = _apply_project_filters(
        session, stmt, type_slug, owner_slug, customer_slug, tag_slug
    )
    return set(session.execute(stmt).scalars().all())


def _count_tasks_in_window(
    session: Session,
    column,
    start: datetime,
    end: datetime,
    project_ids: Optional[set[str]],
) -> int:
    stmt = (
        select(func.count())
        .select_from(Task)
        .where(
            Task.archived_at.is_(None),
            column.is_not(None),
            column >= start,
            column <= end,
        )
    )
    if project_ids is not None:
        if not project_ids:
            return 0
        stmt = stmt.where(Task.project_id.in_(project_ids))
    return session.execute(stmt).scalar_one()


def _count_epics_in_window(
    session: Session,
    start: datetime,
    end: datetime,
    project_ids: Optional[set[str]],
) -> int:
    stmt = (
        select(func.count())
        .select_from(Epic)
        .where(Epic.created_at >= start, Epic.created_at <= end)
    )
    if project_ids is not None:
        if not project_ids:
            return 0
        stmt = stmt.where(Epic.project_id.in_(project_ids))
    return session.execute(stmt).scalar_one()


# --------------------------------------------------------------------------- #
# #130 provenance_stats                                                       #
# --------------------------------------------------------------------------- #


def provenance_stats(session: Session, *, limit: int = 10) -> dict[str, Any]:
    """Provenance-аналитика инжектированных задач (Task.source_project_id).

    - ``top_sources`` — проекты-ИСТОЧНИКИ (откуда инжектировали задачи),
      по убыванию числа задач.
    - ``top_sinks`` — проекты-ПРИЁМНИКИ (куда инжектировали), по убыванию.
    - ``total_injected`` — всего задач с непустым source_project_id.
    - ``realized`` — из них в статусе done.
    - ``realized_share`` — realized / total_injected (0.0 если пусто).

    «Инжектированной» считается любая задача с заполненным source_project_id
    (это сильнее, чем origin: split тоже несёт source). Архивные (soft-deleted)
    задачи исключаются — консистентно с `pm task list` и project_counts.

    top_sources/top_sinks используют LEFT join (isouter): если проект-источник
    был hard-удалён, его задачи не выпадают из топа, а собираются в бакет
    ``NO_KEY`` ('—'), и тогда сумма count по топу сходится с total_injected.
    """
    injected_filter = (
        Task.source_project_id.is_not(None) & Task.archived_at.is_(None)
    )

    total_injected = session.execute(
        select(func.count()).select_from(Task).where(injected_filter)
    ).scalar_one()

    realized = session.execute(
        select(func.count())
        .select_from(Task)
        .where(injected_filter, Task.status.in_(REALIZED_TASK_STATUSES))
    ).scalar_one()

    realized_share = (realized / total_injected) if total_injected else 0.0

    # top_sources: группировка по source_project_id → slug проекта-источника.
    # LEFT join, чтобы орфаны (hard-deleted source) попадали в бакет NO_KEY.
    src_proj = aliased(Project)
    src_key = func.coalesce(src_proj.slug, NO_KEY)
    source_rows = session.execute(
        select(src_key, func.count(Task.id))
        .select_from(Task)
        .join(src_proj, Task.source_project_id == src_proj.id, isouter=True)
        .where(injected_filter)
        .group_by(src_key)
        .order_by(func.count(Task.id).desc(), src_key)
        .limit(limit)
    ).all()
    top_sources = [{"slug": slug, "count": count} for slug, count in source_rows]

    # top_sinks: группировка по project_id → slug проекта-приёмника.
    sink_proj = aliased(Project)
    sink_key = func.coalesce(sink_proj.slug, NO_KEY)
    sink_rows = session.execute(
        select(sink_key, func.count(Task.id))
        .select_from(Task)
        .join(sink_proj, Task.project_id == sink_proj.id, isouter=True)
        .where(injected_filter)
        .group_by(sink_key)
        .order_by(func.count(Task.id).desc(), sink_key)
        .limit(limit)
    ).all()
    top_sinks = [{"slug": slug, "count": count} for slug, count in sink_rows]

    return {
        "total_injected": total_injected,
        "realized": realized,
        "realized_share": realized_share,
        "top_sources": top_sources,
        "top_sinks": top_sinks,
    }


# --------------------------------------------------------------------------- #
# #131 git_stats (subprocess через run, мокается в тестах)                     #
# --------------------------------------------------------------------------- #


def _not_git(path: Optional[str], last_pushed_at: Optional[str]) -> dict[str, Any]:
    """Единая «не-git» схема: тот же набор ключей, что и в успешной ветке.

    Нужна, чтобы JSON-консьюмер видел стабильный набор полей независимо от
    причины (нет local_path / каталог не репо).
    """
    return {
        "is_git": False,
        "path": path,
        "commits": 0,
        "first_commit_at": None,
        "last_commit_at": None,
        "last_pushed_at": last_pushed_at,
        "span_days": None,
        "cadence_days": None,
    }


def git_stats(
    local_path: Optional[str],
    *,
    last_pushed_at: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Git-производная статистика по репозиторию проекта (по local_path).

    Шеллит ``git -C <path> ...`` через ``run`` (одна точка для мокинга):
    - ``git rev-parse --is-inside-work-tree`` — проверка, что это репо;
    - ``git rev-list --count HEAD`` — число коммитов;
    - ``git log --reverse --format=%cI`` (первый) и
      ``git log -1 --format=%cI`` (последний) — даты для каденса.

    ``last_pushed_at`` (iso|None) пробрасывается из ``Project.git_last_pushed_at``
    (реальный «последний пуш», ЦКП #131) — git log про пуши ничего не знает,
    поэтому значение приходит из CLI-слоя, а не из subprocess.

    Возвращает:
    - ``None`` если ``local_path`` не задан.
    - не-git схему (``is_git=False`` + полный набор ключей с None/0), если
      каталог не git-репо.
    - иначе ``{"is_git": True, "commits": int, "first_commit_at": iso|None,
      "last_commit_at": iso|None, "last_pushed_at": iso|None,
      "span_days": float|None, "cadence_days": float|None}``.

    ``cadence_days`` — среднее число дней на коммит (span / (commits-1)).
    Если коммитов < 2 — None (каденс не определён). ``span_days`` не может быть
    отрицательным (немонотонные committer-даты после rebase зажимаются в 0).
    """
    if not local_path:
        return None

    rc, out, _ = run(["git", "-C", local_path, "rev-parse", "--is-inside-work-tree"])
    if rc != 0 or out.strip() != "true":
        return _not_git(local_path, last_pushed_at)

    rc, out, _ = run(["git", "-C", local_path, "rev-list", "--count", "HEAD"])
    commits = 0
    if rc == 0:
        try:
            commits = int(out.strip())
        except ValueError:
            commits = 0

    first_at: Optional[str] = None
    last_at: Optional[str] = None
    if commits > 0:
        rc_f, out_f, _ = run(
            ["git", "-C", local_path, "log", "--reverse", "--format=%cI"]
        )
        if rc_f == 0:
            first_line = out_f.strip().splitlines()[0] if out_f.strip() else ""
            first_at = first_line or None
        rc_l, out_l, _ = run(
            ["git", "-C", local_path, "log", "-1", "--format=%cI"]
        )
        if rc_l == 0:
            last_at = out_l.strip() or None

    span_days: Optional[float] = None
    cadence_days: Optional[float] = None
    if first_at and last_at and commits > 1:
        try:
            d_first = datetime.fromisoformat(first_at)
            d_last = datetime.fromisoformat(last_at)
            # Немонотонные committer-даты (rebase/cherry-pick, checkout не-HEAD)
            # могут дать last < first → зажимаем span в 0, чтобы не печатать
            # «Период (дней): -19.0».
            span = max(0.0, (d_last - d_first).total_seconds() / 86400.0)
            span_days = round(span, 3)
            cadence_days = round(span / (commits - 1), 4) if span > 0 else 0.0
        except ValueError:
            span_days = None
            cadence_days = None

    return {
        "is_git": True,
        "path": local_path,
        "commits": commits,
        "first_commit_at": first_at,
        "last_commit_at": last_at,
        "last_pushed_at": last_pushed_at,
        "span_days": span_days,
        "cadence_days": cadence_days,
    }
