"""Интеграция `atlas projects ...` <-> `atlas tags ...` (NP-005).

Покрытие: `projects add --tag`, `projects list --tag` (AND-фильтр),
`projects add-tags`, `projects remove-tags`, `projects get` показывает Tags.

TDD: эти тесты пишутся ДО расширения projects.py.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def isolated_projects_root(tmp_path, monkeypatch):
    """Defensive isolation: ATLAS_PROJECTS_ROOT → tmp_path. Если когда-нибудь
    тест пройдёт `add` без `--no-setup-layout`, junction'ы пойдут в tmp,
    а не в реальный fs пользователя."""
    root = tmp_path / "PROJECT"
    root.mkdir()
    for sub in ("Clients", "Products", "Tests", "_Inbox", "_Archive", "_storage"):
        (root / sub).mkdir(exist_ok=True)
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(root))
    return root


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
def projects_app():
    from atlas.pm.commands.projects import projects_app
    return projects_app


@pytest.fixture()
def tags_app():
    from atlas.pm.commands.tags import app as tags_app
    return tags_app


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


def _add_tag(runner, tags_app, *args):
    return runner.invoke(tags_app, ["add", *args])


def _add_project(runner, projects_app, *args):
    """Tags-тесты не нуждаются в storage/junction/canonical файлах —
    выключаем их, чтобы тесты не пытались писать в реальный fs."""
    return runner.invoke(
        projects_app,
        ["add", "--no-setup-layout", "--no-canonical", *args],
    )


def _make_tag_set(runner, tags_app):
    """Создаёт набор тегов используемый несколькими тестами."""
    _add_tag(runner, tags_app, "--name", "Bitrix24", "--category", "stack", "--slug", "b24")
    _add_tag(runner, tags_app, "--name", "Notion", "--category", "stack", "--slug", "notion")
    _add_tag(runner, tags_app, "--name", "CRM", "--category", "domain", "--slug", "crm")
    _add_tag(runner, tags_app, "--name", "Cifro", "--category", "owner", "--slug", "cifro-owner")


# --------------------------------------------------------------------------- #
# projects add --tag                                                          #
# --------------------------------------------------------------------------- #


class TestProjectsAddWithTags:
    def test_projects_add_with_tags(self, runner, projects_app, tags_app, seeded_engine):
        """--tag прикрепляет теги через resolve_tag_ref."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Project, ProjectTag

        _make_tag_set(runner, tags_app)
        result = _add_project(
            runner, projects_app,
            "--name", "Cifro Portal", "--type", "client-project", "--slug", "cifro",
            "--tag", "b24", "--tag", "notion",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            links = session.execute(
                select(ProjectTag).where(ProjectTag.project_id == proj.id)
            ).scalars().all()
            assert len(links) == 2

    def test_projects_add_with_qualified_tag(self, runner, projects_app, tags_app, seeded_engine):
        """`stack:b24` (category:slug) синтаксис."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Project, ProjectTag, Tag

        _make_tag_set(runner, tags_app)
        result = _add_project(
            runner, projects_app,
            "--name", "Z", "--type", "client-project", "--slug", "zz",
            "--tag", "stack:b24",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "zz")
            ).scalar_one()
            rows = session.execute(
                select(Tag)
                .join(ProjectTag, ProjectTag.tag_id == Tag.id)
                .where(ProjectTag.project_id == proj.id)
            ).scalars().all()
            assert {t.slug for t in rows} == {"b24"}

    def test_projects_add_with_nonexistent_tag(self, runner, projects_app, tags_app, seeded_engine):
        """Несуществующий тег → error с подсказкой."""
        _make_tag_set(runner, tags_app)
        result = _add_project(
            runner, projects_app,
            "--name", "Z", "--type", "client-project", "--slug", "zz",
            "--tag", "nonexistent",
        )
        assert result.exit_code != 0
        combined = _combined(result).lower()
        assert "nonexistent" in combined or "tag" in combined

    def test_projects_add_logs_tags_in_details(self, runner, projects_app, tags_app, seeded_engine):
        """action_log для project_created содержит tag-slugs."""
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        _make_tag_set(runner, tags_app)
        _add_project(
            runner, projects_app,
            "--name", "Cifro", "--type", "client-project", "--slug", "cifro",
            "--tag", "b24",
        )

        with make_session(seeded_engine) as session:
            entries = session.execute(
                select(ActionLog)
                .where(ActionLog.action == "project_created")
                .order_by(ActionLog.timestamp.desc())
            ).scalars().all()
            # ожидаем хотя бы одну запись с деталями которые содержат теги
            found = False
            for e in entries:
                details = json.loads(e.details_json or "{}")
                if details.get("tags"):
                    found = True
                    assert "b24" in details["tags"]
                    break
            assert found


# --------------------------------------------------------------------------- #
# projects list --tag                                                         #
# --------------------------------------------------------------------------- #


class TestProjectsListFilterByTag:
    def test_projects_list_filter_by_tag(self, runner, projects_app, tags_app, seeded_engine):
        """Один --tag: показывает только проекты с этим тегом."""
        _make_tag_set(runner, tags_app)
        _add_project(
            runner, projects_app,
            "--name", "Cifro", "--type", "client-project", "--slug", "cifro",
            "--tag", "b24",
        )
        _add_project(
            runner, projects_app,
            "--name", "Other", "--type", "client-project", "--slug", "other",
        )

        result = runner.invoke(projects_app, ["list", "--tag", "b24"])
        assert result.exit_code == 0, _combined(result)
        out = result.stdout.lower()
        assert "cifro" in out
        assert "other" not in out

    def test_projects_list_filter_by_two_tags_and(self, runner, projects_app, tags_app, seeded_engine):
        """Два --tag: AND-семантика."""
        _make_tag_set(runner, tags_app)
        _add_project(
            runner, projects_app,
            "--name", "Both", "--type", "client-project", "--slug", "both",
            "--tag", "b24", "--tag", "notion",
        )
        _add_project(
            runner, projects_app,
            "--name", "OnlyB24", "--type", "client-project", "--slug", "onlyb24",
            "--tag", "b24",
        )

        result = runner.invoke(projects_app, ["list", "--tag", "b24", "--tag", "notion"])
        assert result.exit_code == 0, _combined(result)
        out = result.stdout.lower()
        assert "both" in out
        assert "onlyb24" not in out

    def test_projects_list_type_and_tag_combined(self, runner, projects_app, tags_app, seeded_engine):
        """--type client-project + --tag b24 → пересечение."""
        _make_tag_set(runner, tags_app)
        _add_project(
            runner, projects_app,
            "--name", "Client", "--type", "client-project", "--slug", "clip",
            "--tag", "b24",
        )
        _add_project(
            runner, projects_app,
            "--name", "Biz", "--type", "business-product", "--slug", "bizp",
            "--tag", "b24",
        )
        _add_project(
            runner, projects_app,
            "--name", "ClientNoTag", "--type", "client-project", "--slug", "clin",
        )

        result = runner.invoke(
            projects_app,
            ["list", "--type", "client-project", "--tag", "b24"],
        )
        assert result.exit_code == 0, _combined(result)
        out = result.stdout.lower()
        assert "clip" in out
        assert "bizp" not in out
        assert "clin" not in out


# --------------------------------------------------------------------------- #
# projects add-tags                                                           #
# --------------------------------------------------------------------------- #


class TestProjectsAddTagsCommand:
    def test_projects_add_tags_command(self, runner, projects_app, tags_app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Project, ProjectTag

        _make_tag_set(runner, tags_app)
        _add_project(
            runner, projects_app,
            "--name", "Cifro", "--type", "client-project", "--slug", "cifro",
        )

        result = runner.invoke(
            projects_app,
            ["add-tags", "cifro", "--tag", "b24", "--tag", "notion"],
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            links = session.execute(
                select(ProjectTag).where(ProjectTag.project_id == proj.id)
            ).scalars().all()
            assert len(links) == 2

    def test_projects_add_tags_duplicate_idempotent(self, runner, projects_app, tags_app, seeded_engine):
        """Повторный add-tags того же тега не ошибка и не создаёт дубликат."""
        from atlas.pm.db import make_session
        from atlas.pm.models import Project, ProjectTag

        _make_tag_set(runner, tags_app)
        _add_project(
            runner, projects_app,
            "--name", "Cifro", "--type", "client-project", "--slug", "cifro",
            "--tag", "b24",
        )

        result = runner.invoke(
            projects_app,
            ["add-tags", "cifro", "--tag", "b24"],
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            count = session.execute(
                select(func.count()).select_from(ProjectTag)
                .where(ProjectTag.project_id == proj.id)
            ).scalar()
            assert count == 1

    def test_projects_add_tags_logs_action(self, runner, projects_app, tags_app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        _make_tag_set(runner, tags_app)
        _add_project(
            runner, projects_app,
            "--name", "Cifro", "--type", "client-project", "--slug", "cifro",
        )
        runner.invoke(
            projects_app,
            ["add-tags", "cifro", "--tag", "b24", "--tag", "notion"],
        )

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog)
                .where(ActionLog.action == "project_tags_added")
                .order_by(ActionLog.timestamp.desc())
            ).scalars().first()
            assert entry is not None
            details = json.loads(entry.details_json)
            slugs = details.get("tag_slugs") or details.get("tags") or []
            assert "b24" in slugs
            assert "notion" in slugs


# --------------------------------------------------------------------------- #
# projects remove-tags                                                        #
# --------------------------------------------------------------------------- #


class TestProjectsRemoveTagsCommand:
    def test_projects_remove_tags_command(self, runner, projects_app, tags_app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import Project, ProjectTag

        _make_tag_set(runner, tags_app)
        _add_project(
            runner, projects_app,
            "--name", "Cifro", "--type", "client-project", "--slug", "cifro",
            "--tag", "b24", "--tag", "notion",
        )

        result = runner.invoke(
            projects_app,
            ["remove-tags", "cifro", "--tag", "b24"],
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            count = session.execute(
                select(func.count()).select_from(ProjectTag)
                .where(ProjectTag.project_id == proj.id)
            ).scalar()
            assert count == 1  # остался только notion

    def test_projects_remove_nonexistent_tag_graceful(self, runner, projects_app, tags_app, seeded_engine):
        """remove-tags тега которого нет у проекта → graceful, без падения."""
        _make_tag_set(runner, tags_app)
        _add_project(
            runner, projects_app,
            "--name", "Cifro", "--type", "client-project", "--slug", "cifro",
            "--tag", "b24",
        )

        result = runner.invoke(
            projects_app,
            ["remove-tags", "cifro", "--tag", "notion"],
        )
        # graceful: не падаем
        assert result.exit_code == 0

    def test_projects_remove_tags_logs_action(self, runner, projects_app, tags_app, seeded_engine):
        from atlas.pm.db import make_session
        from atlas.pm.models import ActionLog

        _make_tag_set(runner, tags_app)
        _add_project(
            runner, projects_app,
            "--name", "Cifro", "--type", "client-project", "--slug", "cifro",
            "--tag", "b24",
        )
        runner.invoke(
            projects_app,
            ["remove-tags", "cifro", "--tag", "b24"],
        )

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog)
                .where(ActionLog.action == "project_tags_removed")
                .order_by(ActionLog.timestamp.desc())
            ).scalars().first()
            assert entry is not None
            details = json.loads(entry.details_json)
            slugs = details.get("tag_slugs") or details.get("tags") or []
            assert "b24" in slugs


# --------------------------------------------------------------------------- #
# projects get shows tags                                                     #
# --------------------------------------------------------------------------- #


class TestProjectsGetShowsTags:
    def test_projects_get_shows_tags_section(self, runner, projects_app, tags_app, seeded_engine):
        """get отображает секцию Tags: category | slug | name | color."""
        _make_tag_set(runner, tags_app)
        _add_project(
            runner, projects_app,
            "--name", "Cifro", "--type", "client-project", "--slug", "cifro",
            "--tag", "b24", "--tag", "owner:cifro-owner",
        )

        result = runner.invoke(projects_app, ["get", "cifro"])
        assert result.exit_code == 0, _combined(result)
        out = result.stdout.lower()
        assert "tag" in out
        assert "b24" in out
        assert "cifro-owner" in out
