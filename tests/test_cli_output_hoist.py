"""Output-флаги (--text/--plain/--json/-J) работают в ЛЮБОЙ позиции argv.

`_hoist_output_flags` вынимает их из argv до Typer-парсинга и кладёт режим в env
ATLAS_OUTPUT (clikit читает его) — чинит «No such option: --text» после подкоманды.
"""
from __future__ import annotations

import json

import pytest

from atlas.cli import _apply_command_shortcuts, _hoist_output_flags


@pytest.fixture(autouse=True)
def _clean_env():
    # _hoist пишет os.environ["ATLAS_OUTPUT"] НАПРЯМУЮ (как в проде) — monkeypatch
    # его не откатит, поэтому чистим вручную ДО и ПОСЛЕ, чтобы не протечь в др. тесты.
    import os

    os.environ.pop("ATLAS_OUTPUT", None)
    yield
    os.environ.pop("ATLAS_OUTPUT", None)


def test_text_after_subcommand_hoisted(monkeypatch):
    import os

    rest = _hoist_output_flags(["task", "list", "--project", "atlas", "--text"])
    assert rest == ["task", "list", "--project", "atlas"]
    assert os.environ["ATLAS_OUTPUT"] == "text"


def test_json_after_subcommand_hoisted(monkeypatch):
    import os

    rest = _hoist_output_flags(["task", "list", "--json"])
    assert rest == ["task", "list"]
    assert os.environ["ATLAS_OUTPUT"] == "json"


def test_plain_alias(monkeypatch):
    import os

    _hoist_output_flags(["dashboard", "--plain"])
    assert os.environ["ATLAS_OUTPUT"] == "text"


def test_short_json_flag(monkeypatch):
    import os

    _hoist_output_flags(["task", "list", "-J"])
    assert os.environ["ATLAS_OUTPUT"] == "json"


def test_before_subcommand_still_works(monkeypatch):
    import os

    rest = _hoist_output_flags(["--text", "task", "list"])
    assert rest == ["task", "list"]
    assert os.environ["ATLAS_OUTPUT"] == "text"


def test_json_beats_text(monkeypatch):
    import os

    _hoist_output_flags(["task", "--text", "list", "--json"])
    assert os.environ["ATLAS_OUTPUT"] == "json"


def test_no_flags_no_env(monkeypatch):
    import os

    rest = _hoist_output_flags(["task", "list", "--project", "atlas"])
    assert rest == ["task", "list", "--project", "atlas"]
    assert "ATLAS_OUTPUT" not in os.environ


# --------------------------------------------------------------------------- #
# командный шорткат -D → dashboard                                            #
# --------------------------------------------------------------------------- #


def test_dash_shortcut_bare():
    assert _apply_command_shortcuts(["-D"]) == ["dashboard"]


def test_dash_shortcut_with_args():
    assert _apply_command_shortcuts(["-D", "--project", "atlas"]) == [
        "dashboard", "--project", "atlas",
    ]


def test_no_shortcut_untouched():
    assert _apply_command_shortcuts(["task", "list"]) == ["task", "list"]


def test_dash_command_alias_runs(monkeypatch):
    # `atlas dash` (алиас) == `atlas dashboard`
    import os

    from typer.testing import CliRunner

    from atlas.cli import app

    os.environ.pop("ATLAS_OUTPUT", None)
    r = CliRunner().invoke(app, ["dash", "--json"])
    assert r.exit_code == 0, r.stdout
    json.loads(r.stdout)  # валидный JSON дэша
    os.environ.pop("ATLAS_OUTPUT", None)
