"""Тесты модуля atlas.pm.junctions.

Покрывают:
- is_windows() — детект ОС.
- create_junction(link, target) — обёртка над `mklink /J`.
- remove_junction(link) — удаление ТОЛЬКО junction-ссылки (не таргета).
- is_junction(p) — определение является ли путь junction'ом (reparse-point).
- junction_target(p) — получение цели junction'а.
- SafetyError — выбрасывается при попытке удалить через remove_junction
  реальную папку (не junction).

Тесты делятся на два слоя:
- subprocess-моки — кросс-платформенно проверяют контракт вызовов.
- интеграционные — реально создают junction'ы в tmp_path; пропускаются на
  не-Windows.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="Junctions работают только на Windows."
)


# --------------------------------------------------------------------------- #
# is_windows                                                                  #
# --------------------------------------------------------------------------- #


class TestIsWindows:
    def test_is_windows_returns_bool(self):
        from atlas.pm.junctions import is_windows
        assert isinstance(is_windows(), bool)

    def test_is_windows_matches_platform(self):
        from atlas.pm.junctions import is_windows
        assert is_windows() == (sys.platform == "win32")


# --------------------------------------------------------------------------- #
# SafetyError                                                                 #
# --------------------------------------------------------------------------- #


class TestSafetyError:
    def test_safety_error_is_exception(self):
        from atlas.pm.junctions import SafetyError
        assert issubclass(SafetyError, Exception)


# --------------------------------------------------------------------------- #
# create_junction (subprocess-моки)                                           #
# --------------------------------------------------------------------------- #


class TestCreateJunctionMocked:
    def test_create_junction_calls_mklink(self, tmp_path):
        from atlas.pm import junctions

        target = tmp_path / "real_target"
        target.mkdir()
        link_parent = tmp_path / "logical"
        link_parent.mkdir()
        link = link_parent / "junction_link"

        with patch.object(junctions.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            junctions.create_junction(link, target)

        # Команда должна быть `cmd /c mklink /J "<link>" "<target>"`
        assert mock_run.called
        call_args = mock_run.call_args
        cmd = call_args.args[0]
        assert "cmd" in cmd[0].lower() or cmd[0] == "cmd"
        assert "mklink" in " ".join(cmd)
        assert "/J" in cmd
        # Аргументы link и target присутствуют
        joined = " ".join(cmd)
        assert str(link) in joined
        assert str(target) in joined

    def test_create_junction_raises_if_link_parent_missing(self, tmp_path):
        from atlas.pm.junctions import create_junction

        target = tmp_path / "real_target"
        target.mkdir()
        link = tmp_path / "no_such_parent" / "junction"

        with pytest.raises(Exception) as excinfo:
            create_junction(link, target)
        assert "parent" in str(excinfo.value).lower() or "no such" in str(
            excinfo.value
        ).lower() or "не существ" in str(excinfo.value).lower()

    def test_create_junction_raises_if_link_exists(self, tmp_path):
        from atlas.pm.junctions import create_junction

        target = tmp_path / "real_target"
        target.mkdir()
        link_parent = tmp_path / "logical"
        link_parent.mkdir()
        link = link_parent / "already_here"
        link.mkdir()

        with pytest.raises(Exception) as excinfo:
            create_junction(link, target)
        assert "exist" in str(excinfo.value).lower() or "существ" in str(
            excinfo.value
        ).lower()

    def test_create_junction_raises_if_target_missing(self, tmp_path):
        from atlas.pm.junctions import create_junction

        link_parent = tmp_path / "logical"
        link_parent.mkdir()
        link = link_parent / "junction"
        target = tmp_path / "no_target_here"

        with pytest.raises(Exception) as excinfo:
            create_junction(link, target)
        assert "target" in str(excinfo.value).lower() or "no such" in str(
            excinfo.value
        ).lower() or "не существ" in str(excinfo.value).lower()

    def test_create_junction_raises_if_subprocess_fails(self, tmp_path):
        from atlas.pm import junctions

        target = tmp_path / "real_target"
        target.mkdir()
        link_parent = tmp_path / "logical"
        link_parent.mkdir()
        link = link_parent / "junction"

        with patch.object(junctions.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="permission denied"
            )
            with pytest.raises(Exception) as excinfo:
                junctions.create_junction(link, target)
            assert "permission denied" in str(excinfo.value) or "1" in str(
                excinfo.value
            )


# --------------------------------------------------------------------------- #
# remove_junction safety                                                      #
# --------------------------------------------------------------------------- #


class TestRemoveJunctionSafety:
    def test_remove_junction_refuses_real_directory(self, tmp_path):
        """SAFETY: remove_junction никогда не должен удалять реальную папку."""
        from atlas.pm.junctions import SafetyError, remove_junction

        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        (real_dir / "important.txt").write_text("DO NOT DELETE", encoding="utf-8")

        with pytest.raises(SafetyError):
            remove_junction(real_dir)
        # Папка и содержимое должны остаться нетронутыми.
        assert real_dir.exists()
        assert (real_dir / "important.txt").exists()

    def test_remove_junction_refuses_missing_path(self, tmp_path):
        from atlas.pm.junctions import remove_junction

        with pytest.raises(Exception):
            remove_junction(tmp_path / "does_not_exist")


# --------------------------------------------------------------------------- #
# is_junction (общая логика, без реальных junction'ов)                        #
# --------------------------------------------------------------------------- #


class TestIsJunctionCommon:
    def test_is_junction_returns_false_for_regular_directory(self, tmp_path):
        from atlas.pm.junctions import is_junction
        (tmp_path / "real").mkdir()
        assert is_junction(tmp_path / "real") is False

    def test_is_junction_returns_false_for_missing_path(self, tmp_path):
        from atlas.pm.junctions import is_junction
        assert is_junction(tmp_path / "ghost") is False

    def test_is_junction_returns_false_for_file(self, tmp_path):
        from atlas.pm.junctions import is_junction
        f = tmp_path / "file.txt"
        f.write_text("hello", encoding="utf-8")
        assert is_junction(f) is False


# --------------------------------------------------------------------------- #
# Integration tests — реальные junction'ы (Windows-only)                       #
# --------------------------------------------------------------------------- #


@WINDOWS_ONLY
class TestJunctionsIntegration:
    def test_create_and_detect_junction(self, tmp_path):
        from atlas.pm.junctions import create_junction, is_junction

        target = tmp_path / "storage" / "proj"
        target.mkdir(parents=True)
        (target / "file.txt").write_text("payload", encoding="utf-8")

        link_parent = tmp_path / "Logical"
        link_parent.mkdir()
        link = link_parent / "proj"

        create_junction(link, target)
        assert link.exists()
        assert is_junction(link) is True
        # Через junction видно реальные файлы.
        assert (link / "file.txt").read_text(encoding="utf-8") == "payload"

    def test_junction_target_returns_target(self, tmp_path):
        from atlas.pm.junctions import create_junction, junction_target

        target = tmp_path / "storage" / "proj"
        target.mkdir(parents=True)
        link = tmp_path / "Logical"
        link.mkdir()
        junc = link / "proj"
        create_junction(junc, target)

        resolved = junction_target(junc)
        assert resolved is not None
        # target может быть представлен по-разному (с UNC-префиксом или без),
        # сверяем по resolve(), а не строкой.
        assert Path(resolved).resolve() == target.resolve()

    def test_remove_junction_does_not_delete_target(self, tmp_path):
        """Критический safety-тест: rmdir junction не трогает таргет."""
        from atlas.pm.junctions import (
            create_junction,
            is_junction,
            remove_junction,
        )

        target = tmp_path / "storage" / "proj"
        target.mkdir(parents=True)
        marker = target / "important.txt"
        marker.write_text("MUST SURVIVE", encoding="utf-8")

        link_parent = tmp_path / "Logical"
        link_parent.mkdir()
        link = link_parent / "proj"
        create_junction(link, target)
        assert is_junction(link)

        remove_junction(link)
        # Junction удалён.
        assert not link.exists()
        # Таргет и файл-маркер на месте.
        assert target.exists()
        assert marker.exists()
        assert marker.read_text(encoding="utf-8") == "MUST SURVIVE"

    def test_is_junction_for_real_directory_is_false(self, tmp_path):
        from atlas.pm.junctions import is_junction
        real = tmp_path / "real"
        real.mkdir()
        assert is_junction(real) is False
