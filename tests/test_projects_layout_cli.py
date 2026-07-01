"""Тесты CLI-команд `atlas projects layout ...` (sub-typer-app).

Команды:
- ``init <ref>``         — однократный перенос проекта в `_storage/<slug>/`
                           + создание junction в logical_path.
- ``sync <ref>``         — пересоздание junction в правильную логическую папку.
- ``verify [<ref>]``     — диагностика layout (storage/junction).
- ``migrate-all``        — bulk init по всем проектам (с фильтрами).
- ``list-storage``       — overview всех физических `_storage/<slug>/`.

TDD: тесты пишутся ДО реализации.

Все «тяжёлые» операции (robocopy / mklink / rmdir / shutil.move) — мокаются:
никаких реальных перемещений на путях outside `tmp_path`.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from typer.testing import CliRunner


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def projects_root(tmp_path, monkeypatch):
    """Корневая директория для физического layout.

    Создаёт активные подгруппы Clients/Products/Tests/_Inbox + _Archive,
    чтобы CLI не падал на отсутствие parent.
    """
    root = tmp_path / "PROJECT"
    root.mkdir()
    for sub in ("Clients", "Products", "Tests", "_Inbox", "_Archive"):
        (root / sub).mkdir()
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture()
def fresh_engine(tmp_path, monkeypatch):
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
    """Полный seed + дополнительные статусы для archive flows."""
    from atlas.db import make_session
    from atlas.models import ProjectStatus, ProjectType
    from atlas.seeds import seed_all

    with make_session(fresh_engine) as session:
        seed_all(session)
        extra_statuses = [
            {"slug": "idea", "name": "Идея", "order_idx": 1,
             "description": "Зафиксировано"},
            {"slug": "planned", "name": "В планах", "order_idx": 3,
             "description": "Решили"},
            {"slug": "paused", "name": "На паузе", "order_idx": 7,
             "description": "Пауза"},
            {"slug": "frozen", "name": "Заморожен", "order_idx": 8,
             "description": "Надолго"},
            {"slug": "completed", "name": "Завершён", "order_idx": 9,
             "description": "Готово"},
        ]
        for s in extra_statuses:
            existing = session.execute(
                select(ProjectStatus).where(ProjectStatus.slug == s["slug"])
            ).scalar_one_or_none()
            if existing is None:
                session.add(ProjectStatus(**s))

        if session.execute(
            select(ProjectType).where(ProjectType.slug == "test")
        ).scalar_one_or_none() is None:
            session.add(ProjectType(
                slug="test",
                name="Test",
                description="Experimental",
                color="#6B7280",
            ))

        if session.execute(
            select(ProjectType).where(ProjectType.slug == "inbox")
        ).scalar_one_or_none() is None:
            session.add(ProjectType(
                slug="inbox",
                name="Inbox",
                description="Inbox",
                color="#F59E0B",
            ))
        session.commit()

    return fresh_engine


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def app():
    """Подзаголовок: только `layout` sub-typer."""
    from atlas.commands.projects_layout import layout_app
    return layout_app


@pytest.fixture()
def parent_app():
    """Полное приложение projects (для проверки регистрации саб-аппа)."""
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


def _add_project(
    runner, projects_app, projects_root, *,
    slug, type_slug="client-project", create_dir=True, name=None,
    status_slug="experiment",
):
    """Добавить проект через `atlas projects add` + опционально создать physical
    директорию по logical-пути."""
    from atlas.paths import group_path

    name = name or f"Project {slug}"
    local_path: Path | None = None
    if create_dir:
        local_path = group_path(projects_root, type_slug, slug)
        local_path.mkdir(parents=True, exist_ok=True)
        (local_path / "README.md").write_text(f"# {slug}\n", encoding="utf-8")

    args = [
        "add",
        "--name", name,
        "--slug", slug,
        "--type", type_slug,
        "--status", status_slug,
        # layout tests сами проверяют migrate-to-storage flow — не даём
        # `add` создавать `_storage/<slug>/` авто-mode'ом.
        "--no-setup-layout",
        "--no-canonical",
    ]
    if local_path:
        args.extend(["--local-path", str(local_path)])
    result = runner.invoke(projects_app, args)
    assert result.exit_code == 0, _combined(result)
    return local_path


def _fake_perform_move(src, dst, *, copy_first=False):
    """Подменяет реальный robocopy: просто переименовывает src → dst."""
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return {"files_count": 1, "bytes": 0}


def _fake_create_junction(link, target):
    """Подмена `_create_junction_safe`: создаёт пустую папку как «junction»."""
    link = Path(link)
    target = Path(target)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.mkdir(exist_ok=True)


# --------------------------------------------------------------------------- #
# Registration: `atlas projects layout ...` доступен                          #
# --------------------------------------------------------------------------- #


class TestRegistration:
    def test_layout_app_registered_under_projects(self, runner, parent_app):
        """`atlas projects layout --help` должен работать."""
        result = runner.invoke(parent_app, ["layout", "--help"])
        assert result.exit_code == 0, _combined(result)
        text = _combined(result)
        # Ищем хотя бы одну из команд.
        assert any(cmd in text for cmd in ("init", "sync", "verify", "migrate-all"))

    def test_layout_help_lists_subcommands(self, runner, app):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        text = _combined(result)
        for cmd in ("init", "sync", "verify", "migrate-all", "list-storage"):
            assert cmd in text


# --------------------------------------------------------------------------- #
# init                                                                        #
# --------------------------------------------------------------------------- #


class TestInit:
    def test_init_dry_run_prints_plan(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """`init <ref> --dry-run` печатает план и не делает физических операций."""
        src = _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project",
        )
        assert src.exists()

        result = runner.invoke(app, ["init", "cifro", "--dry-run"])
        assert result.exit_code == 0, _combined(result)
        # storage не должен появиться
        assert not (projects_root / "_storage" / "cifro").exists()
        assert src.exists()
        assert (src / "README.md").exists()

    def test_init_default_moves_and_creates_junction(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """Без --copy-first → robocopy /MOVE + create_junction.

        Используем fake `_perform_storage_move` и `_create_junction_safe`,
        чтобы не дёргать реальный robocopy/mklink.
        """
        from atlas import layout

        src = _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project",
        )
        assert src.exists()

        with patch.object(layout, "_perform_storage_move", side_effect=_fake_perform_move), \
             patch.object(layout, "_create_junction_safe", side_effect=_fake_create_junction):
            result = runner.invoke(app, ["init", "cifro", "--confirm"])
        assert result.exit_code == 0, _combined(result)

        storage = projects_root / "_storage" / "cifro"
        assert storage.exists()
        assert (storage / "README.md").exists()
        # Logical путь — это «junction» (в нашем фейке — пустая папка).
        logical = projects_root / "Clients" / "cifro"
        assert logical.exists()

    def test_init_copy_first_uses_copy_branch(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """`--copy-first` пробрасывает copy_first=True в _perform_storage_move."""
        from atlas import layout

        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project",
        )

        with patch.object(layout, "_perform_storage_move", side_effect=_fake_perform_move) as move_mock, \
             patch.object(layout, "_create_junction_safe", side_effect=_fake_create_junction):
            result = runner.invoke(
                app, ["init", "cifro", "--copy-first", "--confirm"]
            )

        assert result.exit_code == 0, _combined(result)
        # Проверяем что copy_first=True был передан.
        assert move_mock.called
        kwargs = move_mock.call_args.kwargs
        assert kwargs.get("copy_first") is True

    def test_init_already_migrated_errors(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """Если local_path уже junction — error «уже мигрирован, используй sync»."""
        from atlas import layout

        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project",
        )

        with patch.object(layout, "is_junction", return_value=True):
            result = runner.invoke(app, ["init", "cifro", "--confirm"])
        assert result.exit_code != 0
        text = _combined(result).lower()
        assert "sync" in text or "уже" in text or "migrated" in text

    def test_init_storage_already_exists_errors(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """Если `_storage/<slug>/` уже существует — error."""
        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project",
        )
        # Пред-создать storage.
        (projects_root / "_storage" / "cifro").mkdir(parents=True)

        result = runner.invoke(app, ["init", "cifro", "--confirm"])
        assert result.exit_code != 0
        text = _combined(result).lower()
        assert "_storage" in text or "exists" in text or "существует" in text

    def test_init_local_path_missing_errors(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """Если local_path не существует — error."""
        from atlas.paths import group_path

        # Создаём проект с local_path, но саму папку НЕ создаём.
        phantom = group_path(projects_root, "client-project", "ghost")
        runner.invoke(parent_app, [
            "add",
            "--name", "Ghost", "--slug", "ghost",
            "--type", "client-project",
            "--local-path", str(phantom),
        ])

        result = runner.invoke(app, ["init", "ghost", "--confirm"])
        assert result.exit_code != 0

    def test_init_no_junction_skips_junction_creation(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """`--no-junction` — только move в storage, без junction."""
        from atlas import layout

        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project",
        )

        with patch.object(layout, "_perform_storage_move", side_effect=_fake_perform_move), \
             patch.object(layout, "_create_junction_safe", side_effect=_fake_create_junction) as junc_mock:
            result = runner.invoke(
                app, ["init", "cifro", "--confirm", "--no-junction"]
            )
        assert result.exit_code == 0, _combined(result)
        junc_mock.assert_not_called()

    def test_init_writes_action_log(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """init пишет запись в action_log."""
        from atlas import layout
        from atlas.db import make_session
        from atlas.models import ActionLog

        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project",
        )

        with patch.object(layout, "_perform_storage_move", side_effect=_fake_perform_move), \
             patch.object(layout, "_create_junction_safe", side_effect=_fake_create_junction):
            runner.invoke(app, ["init", "cifro", "--confirm"])

        with make_session(seeded_engine) as session:
            entries = session.execute(
                select(ActionLog).where(
                    ActionLog.action.like("%layout%")
                )
            ).scalars().all()
            # Хотя бы одна запись про layout.
            assert len(entries) >= 1


# --------------------------------------------------------------------------- #
# sync                                                                        #
# --------------------------------------------------------------------------- #


class TestSync:
    def test_sync_creates_junction_at_expected_path(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """Storage есть, junction отсутствует — sync создаёт junction."""
        from atlas import layout

        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project", create_dir=False,
        )
        # Pre-condition: storage существует.
        (projects_root / "_storage" / "cifro").mkdir(parents=True)

        with patch.object(layout, "_create_junction_safe", side_effect=_fake_create_junction) as junc_mock:
            result = runner.invoke(app, ["sync", "cifro"])
        assert result.exit_code == 0, _combined(result)
        junc_mock.assert_called()
        link_arg, target_arg = junc_mock.call_args.args
        assert Path(link_arg) == projects_root / "Clients" / "cifro"
        assert Path(target_arg) == projects_root / "_storage" / "cifro"

    def test_sync_dry_run_does_nothing(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """`--dry-run` не вызывает _create_junction_safe и не пишет в БД."""
        from atlas import layout

        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project", create_dir=False,
        )
        (projects_root / "_storage" / "cifro").mkdir(parents=True)

        with patch.object(layout, "_create_junction_safe") as junc_mock, \
             patch.object(layout, "remove_junction") as rm_mock:
            result = runner.invoke(app, ["sync", "cifro", "--dry-run"])
        assert result.exit_code == 0, _combined(result)
        junc_mock.assert_not_called()
        rm_mock.assert_not_called()

    def test_sync_updates_local_path(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """После sync `project.local_path` должен указывать на logical."""
        from atlas import layout
        from atlas.db import make_session
        from atlas.models import Project

        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project", create_dir=False,
        )
        (projects_root / "_storage" / "cifro").mkdir(parents=True)

        with patch.object(layout, "_create_junction_safe", side_effect=_fake_create_junction):
            result = runner.invoke(app, ["sync", "cifro"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            expected = projects_root / "Clients" / "cifro"
            assert Path(proj.local_path) == expected


# --------------------------------------------------------------------------- #
# verify                                                                      #
# --------------------------------------------------------------------------- #


class TestVerify:
    def test_verify_single_project_ok(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """verify <ref> возвращает exit 0 если всё корректно."""
        from atlas import layout

        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project", create_dir=False,
        )
        # Сценарий: storage существует + есть «junction» (мокаем).
        (projects_root / "_storage" / "cifro").mkdir(parents=True)
        (projects_root / "Clients" / "cifro").mkdir(parents=True)

        with patch.object(layout, "is_junction", return_value=True), \
             patch.object(
                 layout, "junction_target",
                 return_value=projects_root / "_storage" / "cifro",
             ):
            result = runner.invoke(app, ["verify", "cifro"])
        assert result.exit_code == 0, _combined(result)

    def test_verify_single_project_failure(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """Когда storage отсутствует — exit 1, в выводе есть проблема."""
        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project", create_dir=False,
        )

        result = runner.invoke(app, ["verify", "cifro"])
        assert result.exit_code != 0
        text = _combined(result).lower()
        assert "storage" in text or "issue" in text or "проблем" in text

    def test_verify_all_projects_ok(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """`verify` без <ref> сканирует все проекты в БД."""
        from atlas import layout

        # Один проект — корректный.
        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project", create_dir=False,
        )
        (projects_root / "_storage" / "cifro").mkdir(parents=True)
        (projects_root / "Clients" / "cifro").mkdir(parents=True)

        with patch.object(layout, "is_junction", return_value=True), \
             patch.object(
                 layout, "junction_target",
                 return_value=projects_root / "_storage" / "cifro",
             ):
            result = runner.invoke(app, ["verify"])
        assert result.exit_code == 0, _combined(result)

    def test_verify_all_with_one_broken(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """Если хоть один проект сломан — exit 1."""
        from atlas import layout

        # Cifro корректный.
        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project", create_dir=False,
        )
        (projects_root / "_storage" / "cifro").mkdir(parents=True)
        (projects_root / "Clients" / "cifro").mkdir(parents=True)

        # Ghost — сломанный (нет storage).
        _add_project(
            runner, parent_app, projects_root,
            slug="ghost", type_slug="business-product", create_dir=False,
        )

        with patch.object(layout, "is_junction", return_value=True), \
             patch.object(
                 layout, "junction_target",
                 return_value=projects_root / "_storage" / "cifro",
             ):
            result = runner.invoke(app, ["verify"])
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# migrate-all                                                                 #
# --------------------------------------------------------------------------- #


class TestMigrateAll:
    def test_migrate_all_default_is_dry_run(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """Без `--confirm` migrate-all всегда dry-run (safety)."""
        from atlas import layout

        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project",
        )

        with patch.object(layout, "_perform_storage_move", side_effect=_fake_perform_move) as move_mock, \
             patch.object(layout, "_create_junction_safe", side_effect=_fake_create_junction):
            result = runner.invoke(app, ["migrate-all"])
        assert result.exit_code == 0, _combined(result)
        # Никаких реальных move без --confirm.
        move_mock.assert_not_called()

    def test_migrate_all_with_confirm_migrates_each(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """С `--confirm` все подходящие проекты мигрируются."""
        from atlas import layout

        for slug in ("cifro", "atlas-demo"):
            _add_project(
                runner, parent_app, projects_root,
                slug=slug,
                type_slug="client-project" if slug == "cifro" else "business-product",
            )

        with patch.object(layout, "_perform_storage_move", side_effect=_fake_perform_move) as move_mock, \
             patch.object(layout, "_create_junction_safe", side_effect=_fake_create_junction):
            result = runner.invoke(app, ["migrate-all", "--confirm"])
        assert result.exit_code == 0, _combined(result)
        assert move_mock.call_count == 2

    def test_migrate_all_filter_by_type(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """`--type client-project` мигрирует только клиентские."""
        from atlas import layout

        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project",
        )
        _add_project(
            runner, parent_app, projects_root,
            slug="atlas-demo", type_slug="business-product",
        )

        with patch.object(layout, "_perform_storage_move", side_effect=_fake_perform_move) as move_mock, \
             patch.object(layout, "_create_junction_safe", side_effect=_fake_create_junction):
            result = runner.invoke(
                app, ["migrate-all", "--confirm", "--type", "client-project"]
            )
        assert result.exit_code == 0, _combined(result)
        # Только cifro замигрирован.
        assert move_mock.call_count == 1

    def test_migrate_all_summary_table(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """Summary table должен содержать счётчики migrated/skipped/failed."""
        from atlas import layout

        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project",
        )

        with patch.object(layout, "_perform_storage_move", side_effect=_fake_perform_move), \
             patch.object(layout, "_create_junction_safe", side_effect=_fake_create_junction):
            result = runner.invoke(app, ["migrate-all", "--confirm"])
        assert result.exit_code == 0, _combined(result)
        text = _combined(result).lower()
        assert "migrated" in text or "мигриров" in text
        assert "skipped" in text or "пропущ" in text


# --------------------------------------------------------------------------- #
# list-storage                                                                #
# --------------------------------------------------------------------------- #


class TestListStorage:
    def test_list_storage_lists_projects(
        self, runner, app, parent_app, seeded_engine, projects_root,
    ):
        """list-storage печатает таблицу slug | physical | logical | status | type."""
        _add_project(
            runner, parent_app, projects_root,
            slug="cifro", type_slug="client-project", create_dir=False,
        )
        # Создадим _storage/cifro для info.
        (projects_root / "_storage" / "cifro").mkdir(parents=True)

        result = runner.invoke(app, ["list-storage"])
        assert result.exit_code == 0, _combined(result)
        text = _combined(result)
        assert "cifro" in text

    def test_list_storage_empty_db(
        self, runner, app, seeded_engine, projects_root,
    ):
        """list-storage не падает на пустой БД."""
        result = runner.invoke(app, ["list-storage"])
        assert result.exit_code == 0, _combined(result)
