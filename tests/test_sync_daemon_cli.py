"""F3g: CLI atlas sync daemon/up подключены."""
from typer.testing import CliRunner

from atlas.cli import app

runner = CliRunner()


def test_sync_help_has_daemon_and_up():
    res = runner.invoke(app, ["sync", "--help"])
    assert res.exit_code == 0
    assert "daemon" in res.stdout
    assert "up" in res.stdout


def test_daemon_help_has_subcommands():
    res = runner.invoke(app, ["sync", "daemon", "--help"])
    assert res.exit_code == 0
    assert "install" in res.stdout
    assert "uninstall" in res.stdout
    assert "status" in res.stdout
