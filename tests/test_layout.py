"""Тесты модуля atlas.layout.

Тестируют формулы и логику миграции/синхронизации junction-based layout'а:

- get_storage_path(slug) → <root>/_storage/<slug>
- get_logical_path(project) → активная или archive-папка по типу/статусу
- plan_migrate_to_storage(project) — DRY-RUN список шагов
- migrate_to_storage(project) — robocopy/move + create_junction
- sync_logical(project) — junction в правильной логической папке
- verify(project) — диагностика «всё ли хорошо»

Все «тяжёлые» операции (robocopy / mklink / rmdir) — мокаются: реально
тесты тащат только локальные файлы в `tmp_path`.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# --------------------------------------------------------------------------- #
# Helpers — fake Project с минимальным набором полей                          #
# --------------------------------------------------------------------------- #


def _fake_project(
    slug: str,
    *,
    type_slug: str = "client-project",
    archived: bool = False,
    archived_group: str | None = None,
    local_path: str | None = None,
):
    """Лёгкий стенд-объект, имитирующий ORM Project.

    Тесты `layout.py` НЕ должны зависеть от конкретной БД — функции должны
    принимать «дак-подобный» проект с нужными полями.
    """
    return SimpleNamespace(
        id="pid-" + slug,
        slug=slug,
        name=slug,
        type_slug=type_slug,
        archived=archived,
        archived_group=archived_group,
        local_path=local_path,
    )


# --------------------------------------------------------------------------- #
# get_storage_path / get_logical_path                                         #
# --------------------------------------------------------------------------- #


class TestStoragePath:
    def test_get_storage_path(self, monkeypatch, tmp_path):
        from atlas.layout import get_storage_path
        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        result = get_storage_path("cifro")
        assert result == tmp_path.resolve() / "_storage" / "cifro"

    def test_get_storage_path_uses_slug_only(self, monkeypatch, tmp_path):
        from atlas.layout import get_storage_path
        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        # _storage/ не зависит от типа — все проекты лежат в одной плоской
        # директории.
        assert (
            get_storage_path("cif-tool").parent
            == get_storage_path("np-005").parent
            == tmp_path.resolve() / "_storage"
        )


class TestLogicalPath:
    def test_logical_active_client(self, monkeypatch, tmp_path):
        from atlas.layout import get_logical_path
        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        proj = _fake_project("cifro", type_slug="client-project", archived=False)
        assert get_logical_path(proj) == tmp_path.resolve() / "Clients" / "cifro"

    def test_logical_active_business_product(self, monkeypatch, tmp_path):
        from atlas.layout import get_logical_path
        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        proj = _fake_project(
            "np-005", type_slug="business-product", archived=False
        )
        assert get_logical_path(proj) == tmp_path.resolve() / "Products" / "np-005"

    def test_logical_active_test(self, monkeypatch, tmp_path):
        from atlas.layout import get_logical_path
        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        proj = _fake_project("spike", type_slug="test", archived=False)
        assert get_logical_path(proj) == tmp_path.resolve() / "Tests" / "spike"

    def test_logical_active_inbox(self, monkeypatch, tmp_path):
        from atlas.layout import get_logical_path
        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        proj = _fake_project("triage", type_slug="inbox", archived=False)
        assert get_logical_path(proj) == tmp_path.resolve() / "_Inbox" / "triage"

    def test_logical_archived_with_group(self, monkeypatch, tmp_path):
        from atlas.layout import get_logical_path
        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        proj = _fake_project(
            "old-client", type_slug="client-project",
            archived=True, archived_group="clients",
        )
        assert get_logical_path(proj) == (
            tmp_path.resolve() / "_Archive" / "clients" / "old-client"
        )

    def test_logical_archived_falls_back_to_type(self, monkeypatch, tmp_path):
        """archived=True, archived_group=None → группа из type."""
        from atlas.layout import get_logical_path
        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        proj = _fake_project(
            "old-utility", type_slug="personal-utility",
            archived=True, archived_group=None,
        )
        assert get_logical_path(proj) == (
            tmp_path.resolve() / "_Archive" / "products" / "old-utility"
        )


# --------------------------------------------------------------------------- #
# plan_migrate_to_storage                                                     #
# --------------------------------------------------------------------------- #


class TestPlanMigrate:
    def test_plan_emits_steps_when_source_exists(self, monkeypatch, tmp_path):
        from atlas.layout import plan_migrate_to_storage

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        # Создаём «живую» папку проекта
        src = tmp_path / "Clients" / "cifro"
        src.mkdir(parents=True)
        (src / "README.md").write_text("seed", encoding="utf-8")

        proj = _fake_project(
            "cifro", type_slug="client-project",
            archived=False, local_path=str(src),
        )
        plan = plan_migrate_to_storage(proj)
        assert isinstance(plan, list)
        assert len(plan) >= 2
        # должен быть шаг «move/copy в _storage» и «создать junction»
        kinds = [step.get("action") for step in plan]
        assert any("move" in k or "copy" in k for k in kinds)
        assert any("junction" in k for k in kinds)

    def test_plan_marks_already_migrated_when_storage_exists(
        self, monkeypatch, tmp_path,
    ):
        from atlas.layout import plan_migrate_to_storage

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        # Сразу есть _storage/cifro — мигрировать нечего.
        storage = tmp_path / "_storage" / "cifro"
        storage.mkdir(parents=True)

        proj = _fake_project(
            "cifro", type_slug="client-project", archived=False, local_path=None,
        )
        plan = plan_migrate_to_storage(proj)
        # План помечен как already_migrated (одним записанным шагом / специальным
        # маркером).
        statuses = [step.get("status") for step in plan]
        assert "already_migrated" in statuses or any(
            "already" in str(step).lower() for step in plan
        )

    def test_plan_does_not_perform_real_actions(self, monkeypatch, tmp_path):
        """Critical: plan() должен быть DRY-RUN — никаких реальных move/junction."""
        from atlas.layout import plan_migrate_to_storage

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        src = tmp_path / "Clients" / "cifro"
        src.mkdir(parents=True)
        marker = src / "README.md"
        marker.write_text("seed", encoding="utf-8")

        proj = _fake_project(
            "cifro", type_slug="client-project", archived=False,
            local_path=str(src),
        )
        plan_migrate_to_storage(proj)
        # Источник — на месте.
        assert src.exists()
        assert marker.exists()
        # storage не создан
        assert not (tmp_path / "_storage" / "cifro").exists()


# --------------------------------------------------------------------------- #
# migrate_to_storage                                                          #
# --------------------------------------------------------------------------- #


class TestMigrateToStorage:
    def test_already_migrated_returns_status(self, monkeypatch, tmp_path):
        from atlas.layout import migrate_to_storage

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        # Заранее существующий _storage/cifro
        storage = tmp_path / "_storage" / "cifro"
        storage.mkdir(parents=True)

        proj = _fake_project(
            "cifro", type_slug="client-project", archived=False, local_path=None,
        )
        result = migrate_to_storage(proj)
        assert result.get("status") == "already_migrated"
        assert result.get("moved") is False

    def test_move_then_junction(self, monkeypatch, tmp_path):
        """Сценарий: src существует, _storage/<slug> нет → move + junction."""
        from atlas import layout

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))

        src = tmp_path / "Clients" / "cifro"
        src.mkdir(parents=True)
        (src / "data.txt").write_text("payload", encoding="utf-8")

        proj = _fake_project(
            "cifro", type_slug="client-project", archived=False,
            local_path=str(src),
        )

        # Мокаем robocopy/shutil-move через вспомогательную функцию модуля.
        # Реализация может выбирать — но самым удобным контрактом будет
        # `_perform_move(src, dst)` или `_robocopy_move(src, dst)`.
        # Чтобы тест не зависел от внутреннего имени функции, мы патчим
        # `subprocess.run` целиком (для robocopy) И shutil.move (на случай
        # реализации через shutil).
        def fake_move(src_p, dst_p):
            # Имитируем atomic move
            dst_p = Path(dst_p)
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            Path(src_p).rename(dst_p)

        with patch.object(layout, "_perform_storage_move") as move_mock, \
             patch.object(layout, "_create_junction_safe") as junc_mock:
            def _move_side_effect(src_p, dst_p, *, copy_first=False):
                fake_move(src_p, dst_p)
                return {"files_count": 1, "bytes": 0}
            move_mock.side_effect = _move_side_effect
            junc_mock.return_value = None

            result = layout.migrate_to_storage(proj)

        assert result["moved"] is True
        assert result["junction_created"] is True
        assert result["target"] == tmp_path.resolve() / "_storage" / "cifro"
        assert (tmp_path.resolve() / "_storage" / "cifro" / "data.txt").exists()
        # `_create_junction_safe` должен быть вызван с logical_path → storage
        junc_mock.assert_called_once()
        link_arg, target_arg = junc_mock.call_args.args
        assert link_arg == tmp_path.resolve() / "Clients" / "cifro"
        assert target_arg == tmp_path.resolve() / "_storage" / "cifro"

    def test_missing_source_returns_status(self, monkeypatch, tmp_path):
        """src не существует и storage пуст → нечего мигрировать."""
        from atlas.layout import migrate_to_storage

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        proj = _fake_project(
            "ghost", type_slug="client-project", archived=False,
            local_path=str(tmp_path / "Clients" / "ghost"),
        )
        result = migrate_to_storage(proj)
        assert result.get("status") in ("missing_source", "no_source")
        assert result.get("moved") is False


# --------------------------------------------------------------------------- #
# sync_logical                                                                #
# --------------------------------------------------------------------------- #


class TestSyncLogical:
    def test_sync_creates_junction_when_missing(self, monkeypatch, tmp_path):
        from atlas import layout

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        # Storage есть, junction отсутствует.
        storage = tmp_path / "_storage" / "cifro"
        storage.mkdir(parents=True)
        (tmp_path / "Clients").mkdir()

        proj = _fake_project(
            "cifro", type_slug="client-project", archived=False, local_path=None,
        )

        with patch.object(layout, "_create_junction_safe") as junc_mock:
            result = layout.sync_logical(proj)
        junc_mock.assert_called_once()
        link_arg, target_arg = junc_mock.call_args.args
        assert link_arg == tmp_path.resolve() / "Clients" / "cifro"
        assert target_arg == tmp_path.resolve() / "_storage" / "cifro"
        assert result.get("created") is True

    def test_sync_noop_when_correct_junction_exists(self, monkeypatch, tmp_path):
        from atlas import layout

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        storage = tmp_path / "_storage" / "cifro"
        storage.mkdir(parents=True)
        (tmp_path / "Clients").mkdir()

        proj = _fake_project(
            "cifro", type_slug="client-project", archived=False, local_path=None,
        )

        # Изобразим что junction уже существует и таргет правильный.
        with patch.object(layout, "is_junction", return_value=True), \
             patch.object(
                 layout, "junction_target",
                 return_value=tmp_path.resolve() / "_storage" / "cifro",
             ), \
             patch.object(layout, "_create_junction_safe") as junc_mock:
            # logical_path должен «существовать» при наших стабах — создаём пустую папку.
            (tmp_path / "Clients" / "cifro").mkdir()
            result = layout.sync_logical(proj)
        junc_mock.assert_not_called()
        assert result.get("created") is False
        assert result.get("ok") is True

    def test_sync_safety_error_when_real_dir_at_logical_path(
        self, monkeypatch, tmp_path,
    ):
        """В логической папке оказалась реальная директория — НЕ удаляем."""
        from atlas import layout
        from atlas.junctions import SafetyError

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        storage = tmp_path / "_storage" / "cifro"
        storage.mkdir(parents=True)
        # Реальная директория на логическом пути (не junction).
        logical = tmp_path / "Clients" / "cifro"
        logical.mkdir(parents=True)
        (logical / "real.txt").write_text("DO NOT DELETE", encoding="utf-8")

        proj = _fake_project(
            "cifro", type_slug="client-project", archived=False, local_path=None,
        )

        with pytest.raises(SafetyError):
            layout.sync_logical(proj)
        # Папка осталась.
        assert (logical / "real.txt").exists()


# --------------------------------------------------------------------------- #
# verify                                                                      #
# --------------------------------------------------------------------------- #


class TestVerify:
    def test_verify_ok_when_storage_and_junction_correct(
        self, monkeypatch, tmp_path,
    ):
        from atlas import layout

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        storage = tmp_path / "_storage" / "cifro"
        storage.mkdir(parents=True)
        (storage / "file.txt").write_text("ok", encoding="utf-8")
        logical_parent = tmp_path / "Clients"
        logical_parent.mkdir()
        # Создадим обычную директорию и притворимся, что это junction.
        logical = logical_parent / "cifro"
        logical.mkdir()

        proj = _fake_project(
            "cifro", type_slug="client-project", archived=False, local_path=None,
        )
        with patch.object(layout, "is_junction", return_value=True), \
             patch.object(
                 layout, "junction_target",
                 return_value=tmp_path.resolve() / "_storage" / "cifro",
             ):
            result = layout.verify(proj)
        assert result["ok"] is True
        # Все checks ok=True
        for check in result["checks"]:
            assert check["ok"] is True, check

    def test_verify_reports_missing_storage(self, monkeypatch, tmp_path):
        from atlas import layout

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        # storage отсутствует
        proj = _fake_project(
            "ghost", type_slug="client-project", archived=False, local_path=None,
        )
        result = layout.verify(proj)
        assert result["ok"] is False
        # Должен быть хотя бы один check с issue про storage.
        issues = [
            c for c in result["checks"]
            if not c["ok"] and "storage" in c.get("issue", "").lower()
        ]
        assert issues, result

    def test_verify_reports_missing_junction(self, monkeypatch, tmp_path):
        from atlas import layout

        monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(tmp_path))
        storage = tmp_path / "_storage" / "cifro"
        storage.mkdir(parents=True)
        (tmp_path / "Clients").mkdir()

        proj = _fake_project(
            "cifro", type_slug="client-project", archived=False, local_path=None,
        )
        result = layout.verify(proj)
        assert result["ok"] is False
        # Junction отсутствует
        issues = [
            c for c in result["checks"]
            if not c["ok"] and "junction" in c.get("issue", "").lower()
        ]
        assert issues, result
