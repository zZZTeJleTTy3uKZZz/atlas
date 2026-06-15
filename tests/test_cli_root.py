"""F3a: root-CLI собран через clikit.build_root_app — version/--json/субкоманды."""
from typer.testing import CliRunner

from atlas.cli import app

runner = CliRunner()


def test_version_command_json_default():
    # clikit-дефолт вывода — json: `atlas version` → {"version": "0.1.0"}.
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout
    assert '"version"' in result.stdout


def test_help_lists_existing_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "task" in result.stdout
    assert "project" in result.stdout
    assert "idea" in result.stdout
    assert "sync" in result.stdout


def test_text_flag_switches_human_output():
    result = runner.invoke(app, ["--text", "version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout
