"""Pure-logic тесты для analytics-модуля atlas.stats (эпик Dashboard).

Стиль — in-memory SQLite + seed_all + ручная вставка Project/Task/Epic.
Покрываем:
- project_counts: total + разбивка по типу / контрагенту / статусу (#128);
- parse_period: 7d/30d/month/year/from..to + ошибки (#129);
- activity_window: активные проекты/задачи/эпики в окне + фильтры (#129);
- provenance_stats: топ источников/приёмников + доля реализованных (#130);
- git_stats: число коммитов/last push/каденс из git, subprocess замокан (#131).

ВАЖНО: git_stats — единственное место с subprocess, и он МОКАЕТСЯ
(monkeypatch на atlas.stats.run). Никаких реальных git-команд.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from atlas.db import make_engine, make_session
from atlas.models import (
    Base,
    Counterparty,
    Epic,
    Project,
    ProjectStatus,
    ProjectType,
    Task,
)
from atlas.seeds import seed_all


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def session():
    """In-memory SQLite сессия с базовыми сидами."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with make_session(engine) as s:
        seed_all(s)
        yield s


def _type(s, slug="cp", name="Кл") -> ProjectType:
    t = s.execute(select(ProjectType).where(ProjectType.slug == slug)).scalar_one_or_none()
    if t is None:
        t = ProjectType(slug=slug, name=name, default_sync_policy="full")
        s.add(t)
        s.flush()
    return t


def _status(s, slug="act", name="A", order_idx=30) -> ProjectStatus:
    st = s.execute(select(ProjectStatus).where(ProjectStatus.slug == slug)).scalar_one_or_none()
    if st is None:
        st = ProjectStatus(slug=slug, name=name, order_idx=order_idx)
        s.add(st)
        s.flush()
    return st


_PREFIX_SEQ = {"n": 0}


def _project(s, slug, *, type_slug="cp", status_slug="act", owner=None, customer=None,
             last_touched=None, created=None, prefix=None) -> Project:
    t = _type(s, type_slug, type_slug.upper())
    st = _status(s, status_slug, status_slug.upper())
    if prefix is None:
        _PREFIX_SEQ["n"] += 1
        prefix = f"P{_PREFIX_SEQ['n']:02d}"
    p = Project(
        slug=slug, name=slug.title(), type_id=t.id, status_id=st.id,
        priority="P2", one_line_summary="x", prefix=prefix,
        sync_policy="full", owner_id=owner, customer_id=customer,
    )
    if last_touched is not None:
        p.last_touched_at = last_touched
    if created is not None:
        p.created_at = created
    s.add(p)
    s.flush()
    return p


# --------------------------------------------------------------------------- #
# #128 project_counts                                                          #
# --------------------------------------------------------------------------- #


class TestProjectCounts:
    def test_total_counts_non_archived(self, session):
        from atlas.stats import project_counts

        _project(session, "p-one")
        _project(session, "p-two")
        # архивный — не считается в total активных
        arch = _project(session, "p-arch")
        arch.archived_at = datetime(2026, 1, 1)
        session.flush()

        result = project_counts(session)
        assert result["total"] == 2
        assert result["archived"] == 1

    def test_breakdown_by_type(self, session):
        from atlas.stats import project_counts

        _project(session, "p-cp", type_slug="cp")
        _project(session, "p-cp2", type_slug="cp")
        _project(session, "p-kit", type_slug="kit")
        session.flush()

        result = project_counts(session)
        by_type = {r["key"]: r["count"] for r in result["by_type"]}
        assert by_type["cp"] == 2
        assert by_type["kit"] == 1

    def test_breakdown_by_status(self, session):
        from atlas.stats import project_counts

        _project(session, "p-a", status_slug="active")
        _project(session, "p-b", status_slug="active")
        _project(session, "p-c", status_slug="paused")
        session.flush()

        result = project_counts(session)
        by_status = {r["key"]: r["count"] for r in result["by_status"]}
        assert by_status["active"] == 2
        assert by_status["paused"] == 1

    def test_breakdown_by_counterparty(self, session):
        from atlas.stats import project_counts

        cp1 = session.execute(
            select(Counterparty).where(Counterparty.slug == "example-org")
        ).scalar_one()
        cp2 = session.execute(
            select(Counterparty).where(Counterparty.slug == "owner")
        ).scalar_one()
        _project(session, "p-owned1", owner=cp1.id)
        _project(session, "p-owned2", owner=cp1.id)
        _project(session, "p-owned3", owner=cp2.id)
        _project(session, "p-noowner")
        session.flush()

        result = project_counts(session)
        by_owner = {r["key"]: r["count"] for r in result["by_owner"]}
        assert by_owner["example-org"] == 2
        assert by_owner["owner"] == 1
        # без owner — отдельная корзина (None / "—")
        assert by_owner.get("—", by_owner.get(None, 0)) == 1


# --------------------------------------------------------------------------- #
# #129 parse_period                                                           #
# --------------------------------------------------------------------------- #


class TestParsePeriod:
    def test_relative_days(self):
        from atlas.stats import parse_period

        now = datetime(2026, 6, 23, 12, 0, 0)
        start, end = parse_period("7d", now=now)
        assert end == now
        assert start == now - timedelta(days=7)

    def test_relative_30d(self):
        from atlas.stats import parse_period

        now = datetime(2026, 6, 23, 12, 0, 0)
        start, end = parse_period("30d", now=now)
        assert start == now - timedelta(days=30)

    def test_month(self):
        from atlas.stats import parse_period

        now = datetime(2026, 6, 23, 12, 0, 0)
        start, end = parse_period("month", now=now)
        assert start == datetime(2026, 6, 1, 0, 0, 0)
        assert end == now

    def test_year(self):
        from atlas.stats import parse_period

        now = datetime(2026, 6, 23, 12, 0, 0)
        start, end = parse_period("year", now=now)
        assert start == datetime(2026, 1, 1, 0, 0, 0)
        assert end == now

    def test_explicit_range(self):
        from atlas.stats import parse_period

        start, end = parse_period("2026-01-01..2026-03-01")
        assert start == datetime(2026, 1, 1)
        # Голая дата справа разворачивается до конца дня (inclusive), иначе
        # весь последний день диапазона терялся бы (touched <= end).
        assert end == datetime(2026, 3, 1, 23, 59, 59, 999999)

    def test_explicit_range_end_day_inclusive(self):
        """Событие в конечный день диапазона входит в окно (off-by-one fix)."""
        from atlas.stats import parse_period

        start, end = parse_period("2026-01-01..2026-03-01")
        event = datetime(2026, 3, 1, 14, 0, 0)
        assert start <= event <= end

    def test_explicit_range_with_time_kept_as_is(self):
        """Если правая граница задана со временем — конец дня НЕ навешиваем."""
        from atlas.stats import parse_period

        _, end = parse_period("2026-01-01..2026-03-01T08:30:00")
        assert end == datetime(2026, 3, 1, 8, 30, 0)

    def test_invalid_raises(self):
        from atlas.stats import parse_period

        with pytest.raises(ValueError):
            parse_period("garbage")

    def test_invalid_range_raises(self):
        from atlas.stats import parse_period

        with pytest.raises(ValueError):
            parse_period("2026-13-01..2026-99-01")


# --------------------------------------------------------------------------- #
# #129 activity_window                                                        #
# --------------------------------------------------------------------------- #


class TestActivityWindow:
    def test_active_projects_in_window(self, session):
        from atlas.stats import activity_window

        now = datetime(2026, 6, 23, 12, 0, 0)
        # внутри окна (last_touched 3 дня назад)
        _project(session, "p-recent", last_touched=now - timedelta(days=3))
        # вне окна (last_touched 40 дней назад)
        _project(session, "p-old", last_touched=now - timedelta(days=40))
        session.flush()

        start = now - timedelta(days=7)
        result = activity_window(session, start=start, end=now)
        slugs = {p["slug"] for p in result["projects"]}
        assert "p-recent" in slugs
        assert "p-old" not in slugs

    def test_tasks_in_window_by_completed_at(self, session):
        from atlas.stats import activity_window

        now = datetime(2026, 6, 23, 12, 0, 0)
        p = _project(session, "p-tasks", last_touched=now)
        t1 = Task(project_id=p.id, title="done-recent", cpp_description="c",
                  priority="P2", status="done", number=1)
        t1.completed_at = now - timedelta(days=2)
        t2 = Task(project_id=p.id, title="done-old", cpp_description="c",
                  priority="P2", status="done", number=2)
        t2.completed_at = now - timedelta(days=40)
        session.add_all([t1, t2])
        session.flush()

        start = now - timedelta(days=7)
        result = activity_window(session, start=start, end=now)
        assert result["tasks_completed"] == 1

    def test_filter_by_type(self, session):
        from atlas.stats import activity_window

        now = datetime(2026, 6, 23, 12, 0, 0)
        _project(session, "p-cp", type_slug="cp", last_touched=now - timedelta(days=1))
        _project(session, "p-kit", type_slug="kit", last_touched=now - timedelta(days=1))
        session.flush()

        start = now - timedelta(days=7)
        result = activity_window(session, start=start, end=now, type_slug="kit")
        slugs = {p["slug"] for p in result["projects"]}
        assert slugs == {"p-kit"}

    def test_filter_by_tag(self, session):
        from atlas.models import ProjectTag, Tag
        from atlas.stats import activity_window

        now = datetime(2026, 6, 23, 12, 0, 0)
        p_tagged = _project(session, "p-tagged", last_touched=now - timedelta(days=1))
        _project(session, "p-untagged", last_touched=now - timedelta(days=1))
        tag = Tag(slug="hot", name="Hot", category="other")
        session.add(tag)
        session.flush()
        session.add(ProjectTag(project_id=p_tagged.id, tag_id=tag.id))
        session.flush()

        start = now - timedelta(days=7)
        result = activity_window(session, start=start, end=now, tag_slug="hot")
        slugs = {p["slug"] for p in result["projects"]}
        assert slugs == {"p-tagged"}

    def test_archived_project_excluded(self, session):
        from atlas.stats import activity_window

        now = datetime(2026, 6, 23, 12, 0, 0)
        arch = _project(session, "p-arch", last_touched=now - timedelta(days=1))
        arch.archived_at = datetime(2026, 1, 1)
        session.flush()

        start = now - timedelta(days=7)
        result = activity_window(session, start=start, end=now)
        slugs = {p["slug"] for p in result["projects"]}
        assert "p-arch" not in slugs
        assert result["projects_active"] == 0

    def test_archived_task_excluded_from_counts(self, session):
        from atlas.models import Task
        from atlas.stats import activity_window

        now = datetime(2026, 6, 23, 12, 0, 0)
        p = _project(session, "p-arch-task", last_touched=now)
        t = Task(project_id=p.id, title="arch-done", cpp_description="c",
                 priority="P2", status="done", number=1)
        t.completed_at = now - timedelta(days=2)
        t.archived_at = now - timedelta(days=1)
        session.add(t)
        session.flush()

        start = now - timedelta(days=7)
        result = activity_window(session, start=start, end=now)
        assert result["tasks_completed"] == 0


# --------------------------------------------------------------------------- #
# #130 provenance_stats                                                       #
# --------------------------------------------------------------------------- #


class TestProvenanceStats:
    def test_top_sources_and_sinks(self, session):
        from atlas.stats import provenance_stats

        src = _project(session, "src-proj")
        sink = _project(session, "sink-proj")
        # 2 инжектированных задачи из src в sink, одна реализована
        t1 = Task(project_id=sink.id, title="inj1", cpp_description="c",
                  priority="P2", status="done", origin="injected",
                  source_project_id=src.id, number=1)
        t1.completed_at = datetime(2026, 6, 1)
        t2 = Task(project_id=sink.id, title="inj2", cpp_description="c",
                  priority="P2", status="todo", origin="injected",
                  source_project_id=src.id, number=2)
        # нативная задача — не считается в provenance
        t3 = Task(project_id=sink.id, title="nat", cpp_description="c",
                  priority="P2", status="done", origin="native", number=3)
        session.add_all([t1, t2, t3])
        session.flush()

        result = provenance_stats(session)
        sources = {r["slug"]: r["count"] for r in result["top_sources"]}
        sinks = {r["slug"]: r["count"] for r in result["top_sinks"]}
        assert sources["src-proj"] == 2
        assert sinks["sink-proj"] == 2

    def test_realized_share(self, session):
        from atlas.stats import provenance_stats

        src = _project(session, "src-p")
        sink = _project(session, "sink-p")
        t1 = Task(project_id=sink.id, title="i1", cpp_description="c",
                  priority="P2", status="done", origin="injected",
                  source_project_id=src.id, number=1)
        t2 = Task(project_id=sink.id, title="i2", cpp_description="c",
                  priority="P2", status="done", origin="injected",
                  source_project_id=src.id, number=2)
        t3 = Task(project_id=sink.id, title="i3", cpp_description="c",
                  priority="P2", status="todo", origin="injected",
                  source_project_id=src.id, number=3)
        t4 = Task(project_id=sink.id, title="i4", cpp_description="c",
                  priority="P2", status="todo", origin="injected",
                  source_project_id=src.id, number=4)
        session.add_all([t1, t2, t3, t4])
        session.flush()

        result = provenance_stats(session)
        assert result["total_injected"] == 4
        assert result["realized"] == 2
        assert result["realized_share"] == pytest.approx(0.5)

    def test_empty_provenance(self, session):
        from atlas.stats import provenance_stats

        _project(session, "lonely")
        session.flush()
        result = provenance_stats(session)
        assert result["total_injected"] == 0
        assert result["realized_share"] == 0.0
        assert result["top_sources"] == []

    def test_orphan_source_sums_consistent(self, session):
        """#8: орфан source_project_id не рассинхронит total vs sum(top_sources).

        Воспроизводим орфан на отдельном движке с ПОЛНОСТЬЮ отключённым FK
        (PRAGMA можно менять только вне активной транзакции — поэтому свой
        engine, а не общий session-фикстуры): задача с source на удалённый
        проект остаётся в total_injected и должна попадать в бакет '—'.
        """
        from sqlalchemy import event

        from atlas.db import make_engine, make_session
        from atlas.models import Base, Task
        from atlas.seeds import seed_all
        from atlas.stats import provenance_stats

        engine = make_engine("sqlite:///:memory:")

        @event.listens_for(engine, "connect")
        def _fk_off(dbapi_conn, _rec):  # noqa: ANN001
            dbapi_conn.execute("PRAGMA foreign_keys=OFF")

        Base.metadata.create_all(engine)
        with make_session(engine) as s:
            seed_all(s)
            sink = _project(s, "sink-keep")
            s.flush()
            t = Task(project_id=sink.id, title="orphan-inj", cpp_description="c",
                     priority="P2", status="todo", origin="injected",
                     source_project_id="00000000-0000-0000-0000-000000000000",
                     number=1)
            s.add(t)
            s.flush()

            result = provenance_stats(s)
            assert result["total_injected"] == 1
            # сумма по top_sources сходится с total (орфан → бакет '—')
            assert sum(r["count"] for r in result["top_sources"]) == 1
            assert sum(r["count"] for r in result["top_sinks"]) == 1

    def test_archived_injected_task_excluded(self, session):
        from atlas.models import Task
        from atlas.stats import provenance_stats

        src = _project(session, "src-arch")
        sink = _project(session, "sink-arch")
        t = Task(project_id=sink.id, title="arch-inj", cpp_description="c",
                 priority="P2", status="done", origin="injected",
                 source_project_id=src.id, number=1)
        t.completed_at = datetime(2026, 6, 1)
        t.archived_at = datetime(2026, 6, 2)
        session.add(t)
        session.flush()

        result = provenance_stats(session)
        assert result["total_injected"] == 0
        assert result["realized"] == 0
        assert result["top_sources"] == []
        assert result["top_sinks"] == []


# --------------------------------------------------------------------------- #
# #131 git_stats (subprocess мокается)                                        #
# --------------------------------------------------------------------------- #


class TestGitStats:
    def test_returns_none_when_no_path(self):
        from atlas.stats import git_stats

        assert git_stats(None) is None

    def test_returns_error_when_not_git_repo(self, tmp_path, monkeypatch):
        from atlas import stats

        # git rev-parse → ненулевой rc (не репозиторий)
        monkeypatch.setattr(stats, "run", lambda cmd, cwd=None: (128, "", "not a git repo"))
        result = stats.git_stats(str(tmp_path))
        assert result is not None
        assert result.get("is_git") is False

    def test_commit_count_and_cadence(self, tmp_path, monkeypatch):
        from atlas import stats

        # Очередь ответов на git-команды (по порядку вызовов в git_stats).
        responses = {
            "rev-parse": (0, "true\n", ""),
            "rev-list": (0, "42\n", ""),
            # log дат: первый и последний коммит за 10 дней, 42 коммита
            "log-first": (0, "2026-06-01T10:00:00+03:00\n", ""),
            "log-last": (0, "2026-06-11T10:00:00+03:00\n", ""),
        }

        def fake_run(cmd, cwd=None):
            joined = " ".join(cmd)
            if "rev-parse" in joined:
                return responses["rev-parse"]
            if "rev-list" in joined and "--count" in joined:
                return responses["rev-list"]
            if "log" in joined and "--reverse" in joined:
                return responses["log-first"]
            if "log" in joined:
                return responses["log-last"]
            return (0, "", "")

        monkeypatch.setattr(stats, "run", fake_run)
        result = stats.git_stats(str(tmp_path))
        assert result["is_git"] is True
        assert result["commits"] == 42
        # каденс = span / (commits-1) интервалов: 10 дней / 41 ≈ 0.244 дн/коммит
        assert result["cadence_days"] is not None
        assert result["cadence_days"] == pytest.approx(10 / 41, rel=0.05)
        assert result["span_days"] == pytest.approx(10.0, rel=0.01)

    def test_last_commit_at_exposed(self, tmp_path, monkeypatch):
        from atlas import stats

        def fake_run(cmd, cwd=None):
            joined = " ".join(cmd)
            if "rev-parse" in joined:
                return (0, "true\n", "")
            if "rev-list" in joined:
                return (0, "5\n", "")
            if "--reverse" in joined:
                return (0, "2026-06-01T10:00:00+03:00\n", "")
            if "log" in joined:
                return (0, "2026-06-20T10:00:00+03:00\n", "")
            return (0, "", "")

        monkeypatch.setattr(stats, "run", fake_run)
        result = stats.git_stats(str(tmp_path))
        assert result["commits"] == 5
        assert "2026-06-20" in (result["last_commit_at"] or "")

    def test_last_pushed_at_passed_through(self, tmp_path, monkeypatch):
        """#131: last_pushed_at прокидывается из Project, git его не знает."""
        from atlas import stats

        def fake_run(cmd, cwd=None):
            joined = " ".join(cmd)
            if "rev-parse" in joined:
                return (0, "true\n", "")
            if "rev-list" in joined:
                return (0, "3\n", "")
            if "--reverse" in joined:
                return (0, "2026-06-01T10:00:00+03:00\n", "")
            if "log" in joined:
                return (0, "2026-06-10T10:00:00+03:00\n", "")
            return (0, "", "")

        monkeypatch.setattr(stats, "run", fake_run)
        result = stats.git_stats(
            str(tmp_path), last_pushed_at="2026-06-09T12:00:00"
        )
        assert result["last_pushed_at"] == "2026-06-09T12:00:00"

    def test_span_not_negative_on_nonmonotonic_dates(self, tmp_path, monkeypatch):
        """span_days зажимается в 0 при last < first (rebase/cherry-pick)."""
        from atlas import stats

        def fake_run(cmd, cwd=None):
            joined = " ".join(cmd)
            if "rev-parse" in joined:
                return (0, "true\n", "")
            if "rev-list" in joined:
                return (0, "5\n", "")
            if "--reverse" in joined:
                # «первый» reverse-вывод ПОЗЖЕ, чем HEAD → немонотонность
                return (0, "2026-06-20T10:00:00+03:00\n", "")
            if "log" in joined:
                return (0, "2026-06-01T10:00:00+03:00\n", "")
            return (0, "", "")

        monkeypatch.setattr(stats, "run", fake_run)
        result = stats.git_stats(str(tmp_path))
        assert result["span_days"] == 0.0
        assert result["cadence_days"] == 0.0

    def test_not_git_schema_is_complete(self, tmp_path, monkeypatch):
        """«Не репо» отдаёт тот же набор ключей, что и успешная ветка (#14)."""
        from atlas import stats

        monkeypatch.setattr(stats, "run", lambda cmd, cwd=None: (128, "", ""))
        result = stats.git_stats(str(tmp_path))
        assert result["is_git"] is False
        for key in ("commits", "first_commit_at", "last_commit_at",
                    "last_pushed_at", "span_days", "cadence_days"):
            assert key in result
        assert result["commits"] == 0
