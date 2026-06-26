"""Тесты CLI-команд archive engine: archive/unarchive/renew/move/reorganize.

TDD: пишется ДО реализации команд.

Каждый тест использует `tmp_path` как ATLAS_PROJECTS_ROOT (чтобы физические
`mv` работали в изолированной файловой системе).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from typer.testing import CliRunner


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def projects_root(tmp_path, monkeypatch):
    """Корневая директория для физических репозиториев проектов.

    Создаёт пустые поддиректории Clients/Products/Tests/_Inbox/_Archive сразу,
    чтобы тесты не падали на `mkdir -p` нужной родительской директории.
    """
    root = tmp_path / "PROJECT"
    root.mkdir()
    (root / "Clients").mkdir()
    (root / "Products").mkdir()
    (root / "Tests").mkdir()
    (root / "_Inbox").mkdir()
    (root / "_Archive").mkdir()
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture()
def fresh_engine(tmp_path, monkeypatch):
    """Чистая SQLite БД на диске + ATLAS_DB_URL в env."""
    from atlas.db import make_engine
    from atlas.models import Base

    db_path = tmp_path / "atlas.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def seeded_engine(fresh_engine):
    """Чистая БД + полный seed + дополнительные статусы (completed/paused/frozen) +
    project_type 'test' (как добавляет миграция 004)."""
    from datetime import datetime

    from atlas.db import make_session
    from atlas.models import ProjectStatus, ProjectType
    from atlas.seeds import seed_all

    with make_session(fresh_engine) as session:
        seed_all(session)

        # Добавляем статусы из миграции 004 (они не в seed_all, только в миграции)
        extra_statuses = [
            {"slug": "idea", "name": "Идея", "order_idx": 1,
             "description": "Зафиксировано, ничего не начато"},
            {"slug": "planned", "name": "В планах", "order_idx": 3,
             "description": "Решили делать, ещё не стартовали"},
            {"slug": "paused", "name": "На паузе", "order_idx": 7,
             "description": "Временно приостановлен"},
            {"slug": "frozen", "name": "Заморожен", "order_idx": 8,
             "description": "Надолго отложен"},
            {"slug": "completed", "name": "Завершён", "order_idx": 9,
             "description": "Работа закончена"},
        ]
        for s in extra_statuses:
            existing = session.execute(
                select(ProjectStatus).where(ProjectStatus.slug == s["slug"])
            ).scalar_one_or_none()
            if existing is None:
                session.add(ProjectStatus(**s))

        # project_type 'test' из миграции 004
        existing_test = session.execute(
            select(ProjectType).where(ProjectType.slug == "test")
        ).scalar_one_or_none()
        if existing_test is None:
            session.add(ProjectType(
                slug="test",
                name="Экспериментальные проекты",
                description="Проекты в стадии быстрого прототипирования",
                color="#6B7280",
            ))

        # project_type 'inbox' из миграции 005
        existing_inbox = session.execute(
            select(ProjectType).where(ProjectType.slug == "inbox")
        ).scalar_one_or_none()
        if existing_inbox is None:
            session.add(ProjectType(
                slug="inbox",
                name="Inbox",
                description="Материалы на переработку",
                color="#F59E0B",
            ))

        session.commit()

    return fresh_engine


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def app():
    from atlas.commands.projects import projects_app
    return projects_app


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _combined(result) -> str:
    out = result.stdout or ""
    try:
        err = result.stderr or ""
    except (ValueError, AttributeError):
        err = ""
    return out + err


def _add_project_with_path(
    runner, app, projects_root, *,
    name, slug, type_slug="client-project",
    status_slug="experiment", create_dir=True,
):
    """Добавить проект + опционально создать физическую директорию.

    Директория создаётся в соответствующей active-группе по type_slug.
    """
    from atlas.paths import group_path

    local_path: Path | None = None
    if create_dir:
        local_path = group_path(projects_root, type_slug, slug)
        local_path.mkdir(parents=True, exist_ok=True)
        # Положим туда маркерный файл, чтобы можно было проверить что mv
        # перенёс содержимое, а не создал пустую папку.
        (local_path / "README.md").write_text(f"project {slug}", encoding="utf-8")

    args = [
        "add",
        "--name", name,
        "--slug", slug,
        "--type", type_slug,
        "--status", status_slug,
        # Archive-тесты управляют физикой сами через `_add_project_with_path`
        # (mkdir + README) — не даём `add` создавать `_storage/<slug>/` и
        # canonical-файлы поверх.
        "--no-setup-layout",
        "--no-canonical",
    ]
    if local_path is not None:
        args.extend(["--local-path", str(local_path)])
    result = runner.invoke(app, args)
    assert result.exit_code == 0, _combined(result)
    return local_path


def _get_project(engine, slug):
    from atlas.db import make_session
    from atlas.models import Project

    with make_session(engine) as session:
        return session.execute(
            select(Project).where(Project.slug == slug)
        ).scalar_one()


def _get_status_slug(engine, status_id):
    from atlas.db import make_session
    from atlas.models import ProjectStatus

    with make_session(engine) as session:
        ps = session.get(ProjectStatus, status_id)
        return ps.slug if ps else None


# --------------------------------------------------------------------------- #
# archive                                                                     #
# --------------------------------------------------------------------------- #


class TestArchive:
    def test_archive_completed_moves_folder_and_updates_db(
        self, runner, app, seeded_engine, projects_root,
    ):
        src = _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        assert src.exists()

        result = runner.invoke(app, ["archive", "cifro", "--status", "archived"])
        assert result.exit_code == 0, _combined(result)

        # Физика: src больше нет, dst в _Archive/clients/cifro.
        dst = projects_root / "_Archive" / "clients" / "cifro"
        assert not src.exists()
        assert dst.exists()
        assert (dst / "README.md").exists()

        # БД: archived_at, archived_group, status=completed, local_path обновлён.
        proj = _get_project(seeded_engine, "cifro")
        assert proj.archived_at is not None
        assert proj.archived_group == "clients"
        assert _get_status_slug(seeded_engine, proj.status_id) == "archived"
        assert proj.local_path == str(dst)

    def test_archive_cancelled_moves_to_archive(
        self, runner, app, seeded_engine, projects_root,
    ):
        """W45-39: archive --status cancelled — отказ от проекта.
        Раньше тестировались status=paused/frozen — после canon-stcatuses
        VALID_ARCHIVE_STATUSES сужен до {archived, cancelled}."""
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        result = runner.invoke(app, ["archive", "cifro", "--status", "cancelled"])
        assert result.exit_code == 0, _combined(result)
        proj = _get_project(seeded_engine, "cifro")
        assert _get_status_slug(seeded_engine, proj.status_id) == "cancelled"
        assert proj.archived_at is not None

    def test_archive_rejects_active_status(
        self, runner, app, seeded_engine, projects_root,
    ):
        """W45-39: archive отвергает не-архивные статусы (active/paused/experiment)."""
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        result = runner.invoke(app, ["archive", "cifro", "--status", "paused"])
        assert result.exit_code != 0
        # paused — это не «архивный» статус (проект на паузе ≠ закрыт)
        assert "paused" in _combined(result)

    def test_archive_status_archived(self, runner, app, seeded_engine, projects_root):
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        result = runner.invoke(app, ["archive", "cifro", "--status", "archived"])
        assert result.exit_code == 0, _combined(result)
        proj = _get_project(seeded_engine, "cifro")
        assert _get_status_slug(seeded_engine, proj.status_id) == "archived"

    def test_archive_already_archived_errors(
        self, runner, app, seeded_engine, projects_root,
    ):
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        r1 = runner.invoke(app, ["archive", "cifro", "--status", "archived"])
        assert r1.exit_code == 0

        r2 = runner.invoke(app, ["archive", "cifro", "--status", "paused"])
        assert r2.exit_code != 0
        assert "archived" in _combined(r2).lower() or "архив" in _combined(r2).lower()

    def test_archive_invalid_status(self, runner, app, seeded_engine, projects_root):
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        result = runner.invoke(app, ["archive", "cifro", "--status", "bogus"])
        assert result.exit_code != 0

    def test_archive_keep_path_skips_move(
        self, runner, app, seeded_engine, projects_root,
    ):
        """--keep-path → БД update без физического mv."""
        src = _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )

        result = runner.invoke(
            app, ["archive", "cifro", "--status", "archived", "--keep-path"],
        )
        assert result.exit_code == 0, _combined(result)

        # Физика: src остался на месте, dst НЕ создан.
        assert src.exists()
        dst = projects_root / "_Archive" / "clients" / "cifro"
        assert not dst.exists()

        # БД: archived_at установлен, archived_group='clients',
        # local_path НЕ изменён.
        proj = _get_project(seeded_engine, "cifro")
        assert proj.archived_at is not None
        assert proj.archived_group == "clients"
        assert proj.local_path == str(src)

    def test_archive_missing_src_path_still_updates_db(
        self, runner, app, seeded_engine, projects_root,
    ):
        """Если src не существует (был перемещён вручную) — warning + update БД."""
        # Создаём проект без физической папки (create_dir=False),
        # но с local_path указывающим на несуществующее место.
        from atlas.paths import group_path
        phantom = group_path(projects_root, "client-project", "cifro")
        # НЕ создаём phantom.
        result = runner.invoke(app, [
            "add",
            "--name", "Cifro", "--slug", "cifro",
            "--type", "client-project",
            "--local-path", str(phantom),
        ])
        assert result.exit_code == 0, _combined(result)

        result2 = runner.invoke(app, ["archive", "cifro", "--status", "archived"])
        assert result2.exit_code == 0, _combined(result2)

        proj = _get_project(seeded_engine, "cifro")
        assert proj.archived_at is not None
        assert proj.archived_group == "clients"

    def test_archive_creates_action_log(
        self, runner, app, seeded_engine, projects_root,
    ):
        from atlas.db import make_session
        from atlas.models import ActionLog

        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        runner.invoke(app, ["archive", "cifro", "--status", "archived"])

        with make_session(seeded_engine) as session:
            entries = session.execute(
                select(ActionLog).where(ActionLog.action == "project_archived")
            ).scalars().all()
            # Может быть старая запись от add-delete — ищем с details содержащими
            # archived_group/status, чтобы отличить от soft-delete.
            found = [
                e for e in entries
                if e.details_json and "archived_group" in e.details_json
            ]
            assert len(found) >= 1
            details = json.loads(found[0].details_json)
            assert details["archived_group"] == "clients"
            assert details["status"] == "archived"

    def test_archive_client_project_goes_to_clients_subfolder(
        self, runner, app, seeded_engine, projects_root,
    ):
        _add_project_with_path(
            runner, app, projects_root,
            name="C", slug="cif", type_slug="client-project",
        )
        runner.invoke(app, ["archive", "cif", "--status", "archived"])
        assert (projects_root / "_Archive" / "clients" / "cif").exists()

    def test_archive_business_product_goes_to_products_subfolder(
        self, runner, app, seeded_engine, projects_root,
    ):
        _add_project_with_path(
            runner, app, projects_root,
            name="Atlas", slug="atlas-demo", type_slug="business-product",
        )
        runner.invoke(app, ["archive", "atlas-demo", "--status", "archived"])
        assert (projects_root / "_Archive" / "products" / "atlas-demo").exists()

    def test_archive_test_type_goes_to_tests_subfolder(
        self, runner, app, seeded_engine, projects_root,
    ):
        _add_project_with_path(
            runner, app, projects_root,
            name="Spike", slug="spike", type_slug="test",
        )
        runner.invoke(app, ["archive", "spike", "--status", "archived"])
        assert (projects_root / "_Archive" / "tests" / "spike").exists()

    def test_archive_inbox_project_goes_to_archive_inbox(
        self, runner, app, seeded_engine, projects_root,
    ):
        """inbox-проект архивируется в _Archive/inbox/<slug>/."""
        _add_project_with_path(
            runner, app, projects_root,
            name="Raw", slug="raw-item", type_slug="inbox",
        )
        # Физика должна быть в _Inbox/raw-item
        assert (projects_root / "_Inbox" / "raw-item").exists()

        result = runner.invoke(app, ["archive", "raw-item", "--status", "archived"])
        assert result.exit_code == 0, _combined(result)
        assert (projects_root / "_Archive" / "inbox" / "raw-item").exists()
        assert not (projects_root / "_Inbox" / "raw-item").exists()

        proj = _get_project(seeded_engine, "raw-item")
        assert proj.archived_group == "inbox"


# --------------------------------------------------------------------------- #
# unarchive                                                                   #
# --------------------------------------------------------------------------- #


class TestUnarchive:
    def test_unarchive_basic_moves_back_and_sets_active(
        self, runner, app, seeded_engine, projects_root,
    ):
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        runner.invoke(app, ["archive", "cifro", "--status", "archived"])

        result = runner.invoke(app, ["unarchive", "cifro"])
        assert result.exit_code == 0, _combined(result)

        # Физика: проект обратно в Clients/cifro.
        active = projects_root / "Clients" / "cifro"
        archive = projects_root / "_Archive" / "clients" / "cifro"
        assert active.exists()
        assert not archive.exists()
        assert (active / "README.md").exists()

        # БД: archived_at=NULL, archived_group=NULL, status=active.
        proj = _get_project(seeded_engine, "cifro")
        assert proj.archived_at is None
        assert proj.archived_group is None
        assert _get_status_slug(seeded_engine, proj.status_id) == "active"
        assert proj.local_path == str(active)

    def test_unarchive_with_custom_status(
        self, runner, app, seeded_engine, projects_root,
    ):
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        runner.invoke(app, ["archive", "cifro", "--status", "archived"])
        result = runner.invoke(app, ["unarchive", "cifro", "--status", "paused"])
        assert result.exit_code == 0, _combined(result)
        proj = _get_project(seeded_engine, "cifro")
        assert _get_status_slug(seeded_engine, proj.status_id) == "paused"

    def test_unarchive_not_archived_errors(
        self, runner, app, seeded_engine, projects_root,
    ):
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        result = runner.invoke(app, ["unarchive", "cifro"])
        assert result.exit_code != 0

    def test_unarchive_creates_action_log(
        self, runner, app, seeded_engine, projects_root,
    ):
        from atlas.db import make_session
        from atlas.models import ActionLog

        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        runner.invoke(app, ["archive", "cifro", "--status", "archived"])
        runner.invoke(app, ["unarchive", "cifro"])

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "project_unarchived")
            ).scalar_one()
            details = json.loads(entry.details_json)
            assert details["new_status"] == "active"
            assert details["old_status"] == "archived"

    def test_unarchive_type_changed_uses_current_type(
        self, runner, app, seeded_engine, projects_root,
    ):
        """Если project_type изменился между archive и unarchive,
        проект возвращается в новую группу (current type)."""
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        # Archive (идёт в _Archive/clients)
        runner.invoke(app, ["archive", "cifro", "--status", "archived"])

        # Теперь поменяем type в БД напрямую на business-product
        from atlas.db import make_session
        from atlas.models import Project, ProjectType
        with make_session(seeded_engine) as session:
            pt_biz = session.execute(
                select(ProjectType).where(ProjectType.slug == "business-product")
            ).scalar_one()
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            proj.type_id = pt_biz.id
            session.commit()

        # Unarchive — должен вернуть проект в Products/ (а не Clients/).
        result = runner.invoke(app, ["unarchive", "cifro"])
        assert result.exit_code == 0, _combined(result)
        assert (projects_root / "Products" / "cifro").exists()
        assert not (projects_root / "Clients" / "cifro").exists()


# --------------------------------------------------------------------------- #
# renew                                                                       #
# --------------------------------------------------------------------------- #


class TestRenew:
    def test_renew_from_completed_unarchives(
        self, runner, app, seeded_engine, projects_root,
    ):
        """Проект в архиве со status=completed → renew: physical mv + renewal_count+1."""
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        runner.invoke(app, ["archive", "cifro", "--status", "archived"])

        result = runner.invoke(app, ["renew", "cifro"])
        assert result.exit_code == 0, _combined(result)

        proj = _get_project(seeded_engine, "cifro")
        assert proj.renewal_count == 1
        assert proj.archived_at is None
        assert proj.archived_group is None
        assert _get_status_slug(seeded_engine, proj.status_id) == "active"
        assert (projects_root / "Clients" / "cifro").exists()

    def test_renew_active_just_increments(
        self, runner, app, seeded_engine, projects_root,
    ):
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
            status_slug="active",
        )
        result = runner.invoke(app, ["renew", "cifro"])
        assert result.exit_code == 0, _combined(result)

        proj = _get_project(seeded_engine, "cifro")
        assert proj.renewal_count == 1
        assert proj.archived_at is None

    def test_renew_non_client_project_errors(
        self, runner, app, seeded_engine, projects_root,
    ):
        _add_project_with_path(
            runner, app, projects_root,
            name="Atlas", slug="atlas-demo", type_slug="business-product",
        )
        result = runner.invoke(app, ["renew", "atlas-demo"])
        assert result.exit_code != 0
        assert "client-project" in _combined(result).lower() \
            or "client" in _combined(result).lower()

    def test_renew_multiple_times_increments_count(
        self, runner, app, seeded_engine, projects_root,
    ):
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
            status_slug="active",
        )
        runner.invoke(app, ["renew", "cifro"])
        runner.invoke(app, ["renew", "cifro"])
        runner.invoke(app, ["renew", "cifro"])

        proj = _get_project(seeded_engine, "cifro")
        assert proj.renewal_count == 3

    def test_renew_creates_action_log(
        self, runner, app, seeded_engine, projects_root,
    ):
        from atlas.db import make_session
        from atlas.models import ActionLog

        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        runner.invoke(app, ["archive", "cifro", "--status", "archived"])
        runner.invoke(app, ["renew", "cifro"])

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "project_renewed")
            ).scalar_one()
            details = json.loads(entry.details_json)
            assert details["renewal_count_after"] == 1
            assert details["renewal_count_before"] == 0
            assert details["was_archived"] is True
            assert details["previous_status"] == "archived"
            assert details["new_status"] == "active"


# --------------------------------------------------------------------------- #
# move                                                                        #
# --------------------------------------------------------------------------- #


class TestMove:
    def test_move_same_group_no_physical_move(
        self, runner, app, seeded_engine, projects_root,
    ):
        """personal-utility → business-product (обе в products): только БД update."""
        _add_project_with_path(
            runner, app, projects_root,
            name="Utility", slug="utility", type_slug="personal-utility",
        )
        # Создаём в Products/ — потому что personal-utility и business-product
        # обе туда попадают.
        src = projects_root / "Products" / "utility"
        assert src.exists()

        result = runner.invoke(app, ["move", "utility", "--to-type", "business-product"])
        assert result.exit_code == 0, _combined(result)

        # Физика не менялась — src на месте.
        assert src.exists()

        # БД: type изменён.
        from atlas.db import make_session
        from atlas.models import Project, ProjectType
        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "utility")
            ).scalar_one()
            pt = session.get(ProjectType, proj.type_id)
            assert pt.slug == "business-product"

    def test_move_different_group_physical_move(
        self, runner, app, seeded_engine, projects_root,
    ):
        """client-project → business-product: mv из Clients/ в Products/."""
        _add_project_with_path(
            runner, app, projects_root,
            name="Alpha", slug="alpha", type_slug="client-project",
        )
        src = projects_root / "Clients" / "alpha"
        assert src.exists()

        result = runner.invoke(app, ["move", "alpha", "--to-type", "business-product"])
        assert result.exit_code == 0, _combined(result)

        dst = projects_root / "Products" / "alpha"
        assert dst.exists()
        assert not src.exists()
        assert (dst / "README.md").exists()

        # БД: type=business-product, local_path обновлён.
        proj = _get_project(seeded_engine, "alpha")
        assert proj.local_path == str(dst)

    def test_move_archived_forbidden(
        self, runner, app, seeded_engine, projects_root,
    ):
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        runner.invoke(app, ["archive", "cifro", "--status", "archived"])

        result = runner.invoke(app, ["move", "cifro", "--to-type", "business-product"])
        assert result.exit_code != 0
        assert "unarchive" in _combined(result).lower() \
            or "архив" in _combined(result).lower()

    def test_move_unknown_type(self, runner, app, seeded_engine, projects_root):
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        result = runner.invoke(app, ["move", "cifro", "--to-type", "no-such-type"])
        assert result.exit_code != 0

    def test_move_creates_action_log(
        self, runner, app, seeded_engine, projects_root,
    ):
        from atlas.db import make_session
        from atlas.models import ActionLog

        _add_project_with_path(
            runner, app, projects_root,
            name="Alpha", slug="alpha", type_slug="client-project",
        )
        runner.invoke(app, ["move", "alpha", "--to-type", "business-product"])

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "project_type_changed")
            ).scalar_one()
            details = json.loads(entry.details_json)
            assert details["old_type"] == "client-project"
            assert details["new_type"] == "business-product"
            assert details["physical_move"] is True


# --------------------------------------------------------------------------- #
# reorganize                                                                  #
# --------------------------------------------------------------------------- #


class TestReorganize:
    def test_reorganize_dry_run_all_in_sync(
        self, runner, app, seeded_engine, projects_root,
    ):
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        _add_project_with_path(
            runner, app, projects_root,
            name="Atlas", slug="atlas-demo", type_slug="business-product",
        )

        result = runner.invoke(app, ["reorganize"])
        assert result.exit_code == 0, _combined(result)
        combined = _combined(result)
        # По умолчанию dry-run — упоминание об этом должно быть.
        assert "dry" in combined.lower() or "sync" in combined.lower()

    def test_reorganize_detects_db_drift(
        self, runner, app, seeded_engine, projects_root,
    ):
        """local_path в БД ≠ expected_path, но expected_path существует."""
        # Создаём проект с physical местом в Clients/cifro.
        _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )

        # Ломаем local_path в БД: ставим что-то заведомо другое (просто другой слаг).
        from atlas.db import make_session
        from atlas.models import Project
        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            proj.local_path = str(projects_root / "Clients" / "wrong-path")
            session.commit()

        result = runner.invoke(app, ["reorganize"])
        assert result.exit_code == 0
        combined = _combined(result)
        # Должен упомянуть drift или "db-fix" или "1"
        assert "drift" in combined.lower() or "fix" in combined.lower() \
            or "cifro" in combined.lower()

    def test_reorganize_detects_physical_drift(
        self, runner, app, seeded_engine, projects_root,
    ):
        """local_path в БД указывает на место, где физически есть папка,
        но expected_path (по type) — другое."""
        # Создаём проект client-project с физикой в Clients/cifro
        local_path = _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        # Физически переносим в неправильную директорию (Products/cifro)
        import shutil
        wrong_dir = projects_root / "Products" / "cifro"
        shutil.move(str(local_path), str(wrong_dir))

        # И обновим local_path в БД чтобы указывал на wrong_dir (иначе это
        # просто broken — проверим physical-drift детектор).
        from atlas.db import make_session
        from atlas.models import Project
        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            proj.local_path = str(wrong_dir)
            session.commit()

        result = runner.invoke(app, ["reorganize"])
        assert result.exit_code == 0
        combined = _combined(result)
        # Должен сообщить что есть расхождение (move/physical).
        assert "move" in combined.lower() or "physical" in combined.lower() \
            or "cifro" in combined.lower()

    def test_reorganize_apply_fixes_drift(
        self, runner, app, seeded_engine, projects_root,
    ):
        """--apply физически перемещает и обновляет БД."""
        local_path = _add_project_with_path(
            runner, app, projects_root,
            name="Cifro", slug="cifro", type_slug="client-project",
        )
        # Портим физику: переносим в Products (для client-project это drift)
        import shutil
        wrong_dir = projects_root / "Products" / "cifro"
        shutil.move(str(local_path), str(wrong_dir))
        from atlas.db import make_session
        from atlas.models import Project
        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            proj.local_path = str(wrong_dir)
            session.commit()

        result = runner.invoke(app, ["reorganize", "--apply"])
        assert result.exit_code == 0, _combined(result)

        # После --apply: в правильном месте (Clients/cifro), wrong_dir удалён.
        correct_dir = projects_root / "Clients" / "cifro"
        assert correct_dir.exists()
        assert not wrong_dir.exists()

        # БД обновлена.
        proj = _get_project(seeded_engine, "cifro")
        assert proj.local_path == str(correct_dir)

    def test_reorganize_skips_projects_without_local_path(
        self, runner, app, seeded_engine, projects_root,
    ):
        """Проект без local_path — OK, пропускаем, не ломаемся."""
        # Добавляем проект БЕЗ local_path и без физики.
        result = runner.invoke(app, [
            "add",
            "--name", "Virtual", "--slug", "virtual",
            "--type", "business-product",
        ])
        assert result.exit_code == 0, _combined(result)

        result2 = runner.invoke(app, ["reorganize"])
        # Не должен упасть.
        assert result2.exit_code == 0, _combined(result2)
