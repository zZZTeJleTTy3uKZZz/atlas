"""Триаж задач: категоризация + stale-детект (забытые active-задачи)."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from atlas._time import local_now
from atlas.db import make_engine, make_session
from atlas.models import Base, Project, ProjectStatus, ProjectType, Task
from atlas.seeds import seed_all
from atlas.triage import build_triage

runner = CliRunner()


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'triage.db'}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    with make_session(eng) as s:
        seed_all(s)
        pt = s.execute(select(ProjectType)).scalars().first()
        ps = s.execute(select(ProjectStatus)).scalars().first()
        s.add(Project(slug="p", name="P", type_id=pt.id, status_id=ps.id,
                      priority="P2", one_line_summary="x", prefix="P"))
        s.commit()
    return eng


def _task(session, *, status="in_progress", updated=None) -> Task:
    p = session.execute(select(Project)).scalars().first()
    t = Task(project_id=p.id, title=f"T-{status}", cpp_description="c",
             priority="P2", status=status, updated_at=updated)
    session.add(t); session.flush()
    return t


def test_triage_categorizes_open(engine):
    with make_session(engine) as s:
        _task(s, status="in_progress")
        _task(s, status="blocked")
        _task(s, status="review")
        _task(s, status="todo")
        _task(s, status="done")        # терминальная — не в open
        s.commit()
        d = build_triage(s)
        assert d["total_open"] == 4   # backlog убран; open = todo+in_progress+review+blocked
        assert d["counts"]["in_progress"] == 1 and d["counts"]["blocked"] == 1
        assert len(d["in_progress"]) == 1 and len(d["blocked"]) == 1 and len(d["review"]) == 1


def test_stale_detects_forgotten_active(engine):
    now = local_now()
    with make_session(engine) as s:
        _task(s, status="in_progress", updated=now - timedelta(days=10))  # забыта
        _task(s, status="in_progress", updated=now)                        # свежая
        _task(s, status="todo", updated=now - timedelta(days=30))          # todo открыта, но не active → не stale
        s.commit()
        d = build_triage(s, stale_days=7)
        assert len(d["stale"]) == 1
        assert d["stale"][0]["age_days"] >= 10


def test_stale_threshold_configurable(engine):
    now = local_now()
    with make_session(engine) as s:
        _task(s, status="review", updated=now - timedelta(days=3))
        s.commit()
        assert len(build_triage(s, stale_days=7)["stale"]) == 0   # 3 < 7
        assert len(build_triage(s, stale_days=2)["stale"]) == 1   # 3 > 2


def test_cli_triage(engine):
    from atlas.cli import app
    with make_session(engine) as s:
        _task(s, status="in_progress", updated=local_now() - timedelta(days=20))
        s.commit()
    r = runner.invoke(app, ["task", "triage", "--days", "7"])
    assert r.exit_code == 0, r.stdout
    d = json.loads(r.stdout)
    assert d["total_open"] == 1 and len(d["stale"]) == 1


# --------------------------------------------------------------------------- #
# triage --install / --uninstall (#306) — daily Scheduled Task                #
# --------------------------------------------------------------------------- #


def _mock_cr(returncode=0, stdout="ok", stderr=""):
    return type("CR", (), {"returncode": returncode, "stdout": stdout,
                           "stderr": stderr})()


def test_triage_install_invokes_powershell_register_script():
    from unittest.mock import patch

    from atlas.cli import app
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_cr()
        r = runner.invoke(app, ["task", "triage", "--install"])
        assert r.exit_code == 0, r.stdout
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert any("powershell" in str(p).lower() for p in cmd)
        assert any("register_triage_task" in str(p) for p in cmd)
        # default time 09:00
        assert any("09:00" in str(p) for p in cmd)


def test_triage_install_passes_time():
    from unittest.mock import patch

    from atlas.cli import app
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_cr()
        r = runner.invoke(app, ["task", "triage", "--install", "--time", "08:15"])
        assert r.exit_code == 0, r.stdout
        cmd = mock_run.call_args[0][0]
        assert any("08:15" in str(p) for p in cmd)


def test_triage_uninstall_invokes_unregister():
    from unittest.mock import patch

    from atlas.cli import app
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_cr()
        r = runner.invoke(app, ["task", "triage", "--uninstall"])
        assert r.exit_code == 0, r.stdout
        cmd = mock_run.call_args[0][0]
        flat = " ".join(str(p) for p in cmd)
        assert "Unregister-ScheduledTask" in flat
        assert "atlas-daily-triage" in flat


def test_triage_install_failure_nonzero_exit():
    from unittest.mock import patch

    from atlas.cli import app
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_cr(returncode=1, stdout="", stderr="Access denied")
        r = runner.invoke(app, ["task", "triage", "--install"])
        assert r.exit_code != 0


def test_triage_install_and_uninstall_mutually_exclusive():
    from atlas.cli import app
    r = runner.invoke(app, ["task", "triage", "--install", "--uninstall"])
    assert r.exit_code != 0
