"""F3b: миграции применяются и откатываются на временной БД."""
import subprocess
import sys
from pathlib import Path

ATLAS = Path(__file__).resolve().parents[1]


def _alembic(args, db_url):
    env = {"ATLAS_DB_URL": db_url}
    import os
    full_env = {**os.environ, **env}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ATLAS, env=full_env, capture_output=True, text=True,
    )


def test_upgrade_head_then_downgrade_base(tmp_path):
    db = tmp_path / "mig.db"
    url = f"sqlite:///{db}"
    up = _alembic(["upgrade", "head"], url)
    assert up.returncode == 0, up.stderr
    # таблицы и сиды на месте
    import sqlite3
    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sync_policies", "counterparties"} <= names
    n = conn.execute("SELECT COUNT(*) FROM sync_policies").fetchone()[0]
    assert n == 4
    conn.close()
    down = _alembic(["downgrade", "base"], url)
    assert down.returncode == 0, down.stderr
