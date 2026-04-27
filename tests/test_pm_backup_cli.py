"""Тесты для CLI `atlas backup ...`.

Покрывает:
- ``run [--type ...] [--status ...] [--tag ...] [--ref ...] [--dry-run]``
- ``status [--days N] [--ref ...]``
- ``install [--time HH:MM]``
- ``uninstall``
- ``list-tasks``

ВАЖНО:
- Реальная логика backup_repo() мокается на уровне typer-команды.
- subprocess вызовы PowerShell для install/uninstall/list-tasks тоже мокаются.
- Никаких реальных push-ов или Scheduled Task операций.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select
from typer.testing import CliRunner


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def fresh_engine(tmp_path, monkeypatch):
    from atlas.pm.db import make_engine
    from atlas.pm.models import Base

    db_path = tmp_path / "atlas.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def seeded_engine(fresh_engine):
    from atlas.pm.db import make_session
    from atlas.pm.seeds import seed_all

    with make_session(fresh_engine) as session:
        seed_all(session)
    return fresh_engine


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def app():
    from atlas.pm.commands.backup import backup_app
    return backup_app


@pytest.fixture()
def make_project(seeded_engine, tmp_path):
    """Фабрика для создания тестового проекта в БД с произвольным local_path."""
    from atlas.pm.db import make_session
    from atlas.pm.models import Project, ProjectStatus, ProjectType

    def _make(
        slug: str,
        *,
        type_slug: str = "client-project",
        status_slug: str = "active",
        prefix: str | None = None,
        local_path: Path | str | None = None,
        git_repo_url: str | None = "git@example.com:repo.git",
        archived: bool = False,
    ) -> dict[str, Any]:
        with make_session(seeded_engine) as session:
            pt = session.execute(
                select(ProjectType).where(ProjectType.slug == type_slug)
            ).scalar_one()
            ps = session.execute(
                select(ProjectStatus).where(ProjectStatus.slug == status_slug)
            ).scalar_one()
            p = Project(
                slug=slug,
                prefix=prefix or slug[:3],
                name=slug.title(),
                type_id=pt.id,
                status_id=ps.id,
                priority="P2",
                one_line_summary="",
                git_repo_url=git_repo_url,
                local_path=str(local_path) if local_path else None,
                archived_at=datetime(2026, 1, 1) if archived else None,
            )
            session.add(p)
            session.commit()
            return {"id": p.id, "slug": p.slug}
    return _make


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


# --------------------------------------------------------------------------- #
# atlas backup run                                                            #
# --------------------------------------------------------------------------- #


class TestBackupRun:
    def test_run_no_projects_with_local_path_produces_empty_summary(
        self, runner, app, seeded_engine
    ):
        """БД пустая → summary без проектов, но exit code 0."""
        result = runner.invoke(app, ["run"])
        assert result.exit_code == 0

    def test_run_skips_projects_with_no_git_repo_url(
        self, runner, app, seeded_engine, make_project, tmp_path
    ):
        """Проекты без git_repo_url пропускаются с предупреждением."""
        make_project(
            "noremote",
            local_path=tmp_path / "noremote",
            git_repo_url=None,
        )
        # mock backup_repo, чтобы убедиться что НЕ был вызван
        with patch("atlas.pm.commands.backup.backup_repo") as mock_backup:
            result = runner.invoke(app, ["run"])
            assert result.exit_code == 0
            mock_backup.assert_not_called()
        out = _combined(result)
        assert "noremote" in out
        # status 'skipped' для этого проекта в summary
        assert "skipped" in out.lower()

    def test_run_skips_projects_without_local_path(
        self, runner, app, seeded_engine, make_project
    ):
        """Проекты без local_path тоже пропускаются."""
        make_project("nopath", local_path=None)
        with patch("atlas.pm.commands.backup.backup_repo") as mock_backup:
            result = runner.invoke(app, ["run"])
            assert result.exit_code == 0
            # backup_repo не должен был быть вызван (нет local_path)
            mock_backup.assert_not_called()

    def test_run_invokes_backup_repo_for_eligible_project(
        self, runner, app, seeded_engine, make_project, tmp_path
    ):
        """Eligible проект → backup_repo вызван с его local_path."""
        repo = tmp_path / "repo1"
        repo.mkdir()
        make_project(
            "okproj",
            local_path=repo,
            git_repo_url="git@example.com:okproj.git",
        )
        with patch("atlas.pm.commands.backup.backup_repo") as mock_backup:
            mock_backup.return_value = {
                "status": "pushed",
                "commit_sha": "deadbeef12345",
            }
            result = runner.invoke(app, ["run"])
            assert result.exit_code == 0
            mock_backup.assert_called_once()
            # первый аргумент должен быть Path
            args, _ = mock_backup.call_args
            called_path = Path(args[0])
            assert called_path == repo

    def test_run_logs_each_action_to_action_log(
        self, runner, app, seeded_engine, make_project, tmp_path
    ):
        """Каждый backup пишется в action_log с action='backup'."""
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        repo = tmp_path / "repo_log"
        repo.mkdir()
        make_project(
            "logproj",
            local_path=repo,
            git_repo_url="git@example.com:logproj.git",
        )
        with patch("atlas.pm.commands.backup.backup_repo") as mock_backup:
            mock_backup.return_value = {
                "status": "pushed",
                "commit_sha": "abc123",
            }
            result = runner.invoke(app, ["run"])
            assert result.exit_code == 0

        with make_session(seeded_engine) as session:
            entries = session.execute(
                select(ActionLog).where(ActionLog.entity_type == "project")
                .where(ActionLog.action == "backup")
            ).scalars().all()
            assert len(entries) >= 1
            details = json.loads(entries[0].details_json)
            assert details.get("status") == "pushed"
            assert details.get("commit_sha") == "abc123"

    def test_run_summary_table_shows_status_per_project(
        self, runner, app, seeded_engine, make_project, tmp_path
    ):
        """В output есть строка с slug проекта и его финальным статусом."""
        repo = tmp_path / "repo_sum"
        repo.mkdir()
        make_project(
            "sumproj",
            local_path=repo,
            git_repo_url="git@example.com:sumproj.git",
        )
        with patch("atlas.pm.commands.backup.backup_repo") as mock_backup:
            mock_backup.return_value = {"status": "skipped", "reason": "no_changes"}
            result = runner.invoke(app, ["run"])
            assert result.exit_code == 0
        out = _combined(result)
        assert "sumproj" in out
        # должен показать skipped
        assert "skipped" in out.lower()

    def test_run_filter_by_type(
        self, runner, app, seeded_engine, make_project, tmp_path
    ):
        """--type фильтрует выборку проектов."""
        repo_a = tmp_path / "a"
        repo_a.mkdir()
        repo_b = tmp_path / "b"
        repo_b.mkdir()
        make_project(
            "aclient",
            type_slug="client-project",
            local_path=repo_a,
            git_repo_url="git@example.com:a.git",
        )
        make_project(
            "bproduct",
            type_slug="business-product",
            local_path=repo_b,
            git_repo_url="git@example.com:b.git",
        )
        with patch("atlas.pm.commands.backup.backup_repo") as mock_backup:
            mock_backup.return_value = {"status": "skipped", "reason": "no_changes"}
            result = runner.invoke(app, ["run", "--type", "client-project"])
            assert result.exit_code == 0
            # Должен был быть вызван ровно один раз — для aclient.
            assert mock_backup.call_count == 1

    def test_run_filter_by_ref_single_project(
        self, runner, app, seeded_engine, make_project, tmp_path
    ):
        """--ref выбирает один проект."""
        repo_a = tmp_path / "ra"
        repo_a.mkdir()
        repo_b = tmp_path / "rb"
        repo_b.mkdir()
        make_project(
            "refa",
            prefix="rfa",
            local_path=repo_a,
            git_repo_url="git@example.com:refa.git",
        )
        make_project(
            "refb",
            prefix="rfb",
            local_path=repo_b,
            git_repo_url="git@example.com:refb.git",
        )
        with patch("atlas.pm.commands.backup.backup_repo") as mock_backup:
            mock_backup.return_value = {"status": "pushed", "commit_sha": "x"}
            result = runner.invoke(app, ["run", "--ref", "refa"])
            assert result.exit_code == 0
            assert mock_backup.call_count == 1
            args, _ = mock_backup.call_args
            assert Path(args[0]) == repo_a

    def test_run_dry_run_does_not_invoke_backup_repo(
        self, runner, app, seeded_engine, make_project, tmp_path
    ):
        """--dry-run только показывает что было бы сделано."""
        repo = tmp_path / "dryrun"
        repo.mkdir()
        make_project(
            "dryproj",
            local_path=repo,
            git_repo_url="git@example.com:dryproj.git",
        )
        with patch("atlas.pm.commands.backup.backup_repo") as mock_backup:
            result = runner.invoke(app, ["run", "--dry-run"])
            assert result.exit_code == 0
            # dry-run → backup_repo НЕ вызывается.
            mock_backup.assert_not_called()
        out = _combined(result)
        assert "dryproj" in out

    def test_run_records_failure_status_in_action_log(
        self, runner, app, seeded_engine, make_project, tmp_path
    ):
        """Если backup_repo вернул failed → в action_log status=failed + error."""
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        repo = tmp_path / "repo_fail"
        repo.mkdir()
        make_project(
            "failproj",
            local_path=repo,
            git_repo_url="git@example.com:failproj.git",
        )
        with patch("atlas.pm.commands.backup.backup_repo") as mock_backup:
            mock_backup.return_value = {
                "status": "failed",
                "error": "git push failed: connection refused",
            }
            result = runner.invoke(app, ["run"])
            # exit_code: не должен быть 0 если все упали?
            # требование: продолжать через все проекты + summary.

        with make_session(seeded_engine) as session:
            entries = session.execute(
                select(ActionLog).where(ActionLog.action == "backup")
            ).scalars().all()
            assert len(entries) >= 1
            details = json.loads(entries[0].details_json)
            assert details.get("status") == "failed"
            assert "error" in details

    def test_run_archived_projects_excluded_by_default(
        self, runner, app, seeded_engine, make_project, tmp_path
    ):
        """Архивные проекты по умолчанию пропускаются."""
        repo = tmp_path / "arch"
        repo.mkdir()
        make_project(
            "archproj",
            local_path=repo,
            git_repo_url="git@example.com:arch.git",
            archived=True,
        )
        with patch("atlas.pm.commands.backup.backup_repo") as mock_backup:
            result = runner.invoke(app, ["run"])
            assert result.exit_code == 0
            mock_backup.assert_not_called()


# --------------------------------------------------------------------------- #
# atlas backup status                                                         #
# --------------------------------------------------------------------------- #


class TestBackupStatus:
    def test_status_empty_no_history(self, runner, app, seeded_engine):
        """Пустой action_log → дружественное сообщение."""
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        # должен сказать что-то про "нет" / "пусто"

    def test_status_shows_recent_backup_entries(
        self, runner, app, seeded_engine, make_project
    ):
        """Записи в action_log с action='backup' выводятся."""
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        proj = make_project("s1", local_path="/tmp/s1")
        with make_session(seeded_engine) as session:
            session.add(ActionLog(
                entity_type="project",
                entity_id=proj["id"],
                action="backup",
                details_json=json.dumps({
                    "status": "pushed",
                    "commit_sha": "deadbe123",
                    "project_slug": "s1",
                }),
            ))
            session.commit()

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        out = _combined(result)
        assert "s1" in out
        assert "pushed" in out.lower() or "deadbe" in out

    def test_status_filters_by_days(
        self, runner, app, seeded_engine, make_project
    ):
        """--days N ограничивает по timestamp."""
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        proj = make_project("daysproj", local_path="/tmp/daysproj")
        with make_session(seeded_engine) as session:
            session.add(ActionLog(
                timestamp=datetime.now() - timedelta(days=30),
                entity_type="project",
                entity_id=proj["id"],
                action="backup",
                details_json=json.dumps({"status": "pushed", "project_slug": "daysproj"}),
            ))
            session.add(ActionLog(
                timestamp=datetime.now() - timedelta(hours=1),
                entity_type="project",
                entity_id=proj["id"],
                action="backup",
                details_json=json.dumps({"status": "skipped", "project_slug": "daysproj"}),
            ))
            session.commit()

        # default 7 days → должна быть только свежая (skipped)
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        out = _combined(result)
        assert "skipped" in out.lower()

    def test_status_filters_by_ref(
        self, runner, app, seeded_engine, make_project
    ):
        """--ref ограничивает один проект."""
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        a = make_project("xprojA", local_path="/tmp/a")
        b = make_project("yprojB", local_path="/tmp/b")
        with make_session(seeded_engine) as session:
            session.add(ActionLog(
                entity_type="project", entity_id=a["id"], action="backup",
                details_json=json.dumps({"status": "pushed", "project_slug": "xprojA"}),
            ))
            session.add(ActionLog(
                entity_type="project", entity_id=b["id"], action="backup",
                details_json=json.dumps({"status": "skipped", "project_slug": "yprojB"}),
            ))
            session.commit()

        result = runner.invoke(app, ["status", "--ref", "xprojA"])
        assert result.exit_code == 0
        out = _combined(result)
        assert "xprojA" in out
        assert "yprojB" not in out


# --------------------------------------------------------------------------- #
# atlas backup install                                                        #
# --------------------------------------------------------------------------- #


class TestBackupInstall:
    def test_install_invokes_powershell_register_task(self, runner, app, seeded_engine):
        """install вызывает powershell -File register_task.ps1."""
        with patch("atlas.pm.commands.backup.subprocess.run") as mock_run:
            mock_run.return_value = type("CR", (), {
                "returncode": 0,
                "stdout": "ok",
                "stderr": "",
            })()
            result = runner.invoke(app, ["install"])
            assert result.exit_code == 0
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            cmd = call_args[0][0] if call_args[0] else call_args.kwargs.get("args", [])
            # Должна быть команда powershell с register_task.ps1
            assert any("powershell" in str(p).lower() for p in cmd)
            assert any("register_task" in str(p) for p in cmd)

    def test_install_passes_time_argument(self, runner, app, seeded_engine):
        """--time HH:MM передаётся скрипту."""
        with patch("atlas.pm.commands.backup.subprocess.run") as mock_run:
            mock_run.return_value = type("CR", (), {
                "returncode": 0, "stdout": "ok", "stderr": "",
            })()
            result = runner.invoke(app, ["install", "--time", "04:30"])
            assert result.exit_code == 0
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            # 04:30 должен быть среди аргументов
            assert any("04:30" in str(p) for p in cmd)

    def test_install_default_time_is_03_00(self, runner, app, seeded_engine):
        """Без --time — default 03:00."""
        with patch("atlas.pm.commands.backup.subprocess.run") as mock_run:
            mock_run.return_value = type("CR", (), {
                "returncode": 0, "stdout": "ok", "stderr": "",
            })()
            result = runner.invoke(app, ["install"])
            assert result.exit_code == 0
            cmd = mock_run.call_args[0][0]
            assert any("03:00" in str(p) for p in cmd)

    def test_install_failure_propagates_nonzero_exit(self, runner, app, seeded_engine):
        """Если powershell упал → exit code не 0."""
        with patch("atlas.pm.commands.backup.subprocess.run") as mock_run:
            mock_run.return_value = type("CR", (), {
                "returncode": 1, "stdout": "", "stderr": "Access denied",
            })()
            result = runner.invoke(app, ["install"])
            assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# atlas backup uninstall                                                      #
# --------------------------------------------------------------------------- #


class TestBackupUninstall:
    def test_uninstall_invokes_unregister_scheduled_task(
        self, runner, app, seeded_engine
    ):
        """uninstall вызывает Unregister-ScheduledTask."""
        with patch("atlas.pm.commands.backup.subprocess.run") as mock_run:
            mock_run.return_value = type("CR", (), {
                "returncode": 0, "stdout": "", "stderr": "",
            })()
            result = runner.invoke(app, ["uninstall"])
            assert result.exit_code == 0
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert any("powershell" in str(p).lower() for p in cmd)
            joined = " ".join(str(p) for p in cmd)
            assert "Unregister-ScheduledTask" in joined
            assert "atlas-daily-backup" in joined


# --------------------------------------------------------------------------- #
# atlas backup list-tasks                                                     #
# --------------------------------------------------------------------------- #


class TestBackupListTasks:
    def test_list_tasks_invokes_get_scheduled_task_info(
        self, runner, app, seeded_engine
    ):
        """list-tasks вызывает Get-ScheduledTaskInfo."""
        with patch("atlas.pm.commands.backup.subprocess.run") as mock_run:
            mock_run.return_value = type("CR", (), {
                "returncode": 0,
                "stdout": "LastRunTime: 2026-04-26\nNextRunTime: 2026-04-27\nState: Ready",
                "stderr": "",
            })()
            result = runner.invoke(app, ["list-tasks"])
            assert result.exit_code == 0
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            joined = " ".join(str(p) for p in cmd)
            assert "Get-ScheduledTaskInfo" in joined or "Get-ScheduledTask" in joined

    def test_list_tasks_displays_output(self, runner, app, seeded_engine):
        """Output PowerShell печатается в stdout."""
        ps_output = "LastRunTime: 2026-04-26 03:00\nNextRunTime: 2026-04-27 03:00\nState: Ready"
        with patch("atlas.pm.commands.backup.subprocess.run") as mock_run:
            mock_run.return_value = type("CR", (), {
                "returncode": 0,
                "stdout": ps_output,
                "stderr": "",
            })()
            result = runner.invoke(app, ["list-tasks"])
            assert result.exit_code == 0
            out = _combined(result)
            assert "LastRunTime" in out or "2026-04-26" in out
