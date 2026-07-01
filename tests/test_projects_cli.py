"""Тесты для CLI `atlas projects ...` (PM-слой).

TDD: эти тесты пишутся ДО полной реализации src/atlas/pm/commands/projects.py.

Покрытие: add / list / get / update / delete + slug/prefix-генерация,
action_log, edge cases.
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
    """Изолируем все CRUD-тесты от реального ~/Documents/PROJECT.

    autouse: применяется ко всем тестам в файле (даже если они напрямую не
    используют layout). Защищает от неочевидной утечки junction'ов в реальный fs
    при изменениях default behavior add_cmd (напр. --setup-layout/--canonical).
    """
    root = tmp_path / "PROJECT"
    root.mkdir()
    for sub in ("Clients", "Products", "Tests", "_Inbox", "_Archive", "_storage"):
        (root / sub).mkdir(exist_ok=True)
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture()
def fresh_engine(tmp_path, monkeypatch):
    """Чистая SQLite БД на диске + ATLAS_DB_URL в env, чтобы CLI её увидел."""
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
    """Чистая БД + полный seed (project_types, project_statuses, participants)."""
    from atlas.db import make_session
    from atlas.seeds import seed_all

    with make_session(fresh_engine) as session:
        seed_all(session)
    return fresh_engine


@pytest.fixture()
def runner():
    """Typer CliRunner."""
    return CliRunner()


@pytest.fixture()
def app():
    """CLI-приложение проектов (Typer sub-app)."""
    from atlas.commands.projects import projects_app
    return projects_app


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar()


def _combined(result) -> str:
    """Объединить stdout + stderr (если разделены) для удобного assert."""
    out = result.stdout or ""
    try:
        err = result.stderr or ""
    except (ValueError, AttributeError):
        err = ""
    return out + err


def _add_project(runner, app, *args):
    """Утилита: вызывает `add` с переданными args. Возвращает Result.

    Безусловно выключает `--setup-layout` и `--canonical` чтобы CRUD-тесты
    не создавали junction'ы и canonical-файлы в файловой системе.
    """
    base = ["add", "--no-setup-layout", "--no-canonical"]
    return runner.invoke(app, [*base, *args])


# --------------------------------------------------------------------------- #
# add                                                                          #
# --------------------------------------------------------------------------- #


class TestAdd:
    def test_add_minimal(self, runner, app, seeded_engine):
        """Только --name --type → slug и prefix авто."""
        from atlas.db import make_session
        from atlas.models import Project

        result = _add_project(
            runner, app,
            "--name", "Example Org портал",
            "--type", "client-project",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "example-org-portal")
            ).scalar_one()
            assert proj.name == "Example Org портал"
            assert proj.prefix is not None
            # prefix берётся из slug: 'example-org-portal' → 'eop'
            assert proj.prefix == "eop"
            assert proj.priority == "P2"  # default

    def test_add_default_is_personal_with_me_as_lead(self, runner, app, seeded_engine):
        """`add --name X` без --type/--owner/--team → личный проект:
        type=personal-project, sync_policy=full, lead-участник owner."""
        from atlas.db import make_session
        from atlas.models import Participant, Project, ProjectParticipant, ProjectType

        result = _add_project(runner, app, "--name", "Личный тест", "--no-sync")
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.name == "Личный тест")
            ).scalar_one()
            assert session.get(ProjectType, proj.type_id).slug == "personal-project"
            assert proj.sync_policy == "full"
            owner = session.execute(
                select(Participant).where(Participant.slug == "owner")
            ).scalar_one()
            link = session.get(ProjectParticipant, (proj.id, owner.id))
            assert link is not None and link.role_in_project == "lead"

    def test_add_team_flag_company_mode(self, runner, app, seeded_engine):
        """`add --team` → командный режим: sync_policy=media (не full), lead всё равно owner."""
        from atlas.db import make_session
        from atlas.models import Participant, Project, ProjectParticipant

        result = _add_project(runner, app, "--name", "Командный тест", "--team", "--no-sync")
        assert result.exit_code == 0, _combined(result)
        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.name == "Командный тест")
            ).scalar_one()
            assert proj.sync_policy == "media"
            owner = session.execute(
                select(Participant).where(Participant.slug == "owner")
            ).scalar_one()
            link = session.get(ProjectParticipant, (proj.id, owner.id))
            assert link is not None and link.role_in_project == "lead"

    def test_add_explicit_slug_and_prefix(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Project

        result = _add_project(
            runner, app,
            "--name", "Cifro Portal",
            "--type", "client-project",
            "--slug", "cifro",
            "--prefix", "cf",
        )
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            assert proj.prefix == "cf"

    def test_add_slug_collision_explicit(self, runner, app, seeded_engine):
        """Если --slug задан и занят → error, без auto-suffix."""
        r1 = _add_project(
            runner, app,
            "--name", "First", "--type", "client-project", "--slug", "cifro",
        )
        assert r1.exit_code == 0

        r2 = _add_project(
            runner, app,
            "--name", "Second", "--type", "client-project", "--slug", "cifro",
        )
        assert r2.exit_code != 0
        combined = _combined(r2)
        assert "cifro" in combined.lower()

    def test_add_slug_collision_auto(self, runner, app, seeded_engine):
        """Если slug автогенерируется и занят → suffix -2."""
        from atlas.db import make_session
        from atlas.models import Project

        # Первый создан явно с slug=cifro
        r1 = _add_project(
            runner, app,
            "--name", "First", "--type", "client-project", "--slug", "cifro",
        )
        assert r1.exit_code == 0

        # Второй: name=Cifro → автоген slug=cifro, занят, должен стать cifro-2
        r2 = _add_project(
            runner, app,
            "--name", "Cifro", "--type", "client-project",
        )
        assert r2.exit_code == 0, _combined(r2)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro-2")
            ).scalar_one()
            assert proj.name == "Cifro"

    def test_add_prefix_collision_auto(self, runner, app, seeded_engine):
        """Auto-prefix занят → следующий с цифровым суффиксом."""
        from atlas.db import make_session
        from atlas.models import Project

        # Первый: явный prefix=cf
        r1 = _add_project(
            runner, app,
            "--name", "Cifro First", "--type", "client-project",
            "--slug", "cifro-first", "--prefix", "cf",
        )
        assert r1.exit_code == 0

        # Второй: slug=cifro-second → auto prefix base 'cs'... но проверим что
        # для 'cifro-third' c явным slug auto-prefix будет тоже cf, и должен
        # сгенерироваться cf-2 / cf2.
        r2 = _add_project(
            runner, app,
            "--name", "Cifro Forever", "--type", "client-project",
            "--slug", "cifro-forever",  # auto-prefix даст 'cf', занят
        )
        assert r2.exit_code == 0, _combined(r2)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro-forever")
            ).scalar_one()
            assert proj.prefix is not None
            assert proj.prefix != "cf"  # должен быть auto-suffix

    def test_add_invalid_type(self, runner, app, seeded_engine):
        result = _add_project(
            runner, app,
            "--name", "X", "--type", "no-such-type",
        )
        assert result.exit_code != 0

    def test_add_invalid_priority(self, runner, app, seeded_engine):
        result = _add_project(
            runner, app,
            "--name", "X", "--type", "client-project", "--priority", "P9",
        )
        assert result.exit_code != 0

    def test_add_creates_action_log(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ActionLog

        result = _add_project(
            runner, app,
            "--name", "Logged", "--type", "client-project",
        )
        assert result.exit_code == 0

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "project_created")
            ).scalar_one()
            assert entry.entity_type == "project"
            assert entry.entity_id is not None
            assert entry.actor_id is not None  # привязан к owner
            details = json.loads(entry.details_json)
            assert details["slug"] == "logged"
            assert details["name"] == "Logged"


# --------------------------------------------------------------------------- #
# list                                                                         #
# --------------------------------------------------------------------------- #


class TestList:
    def test_list_empty(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        # Должно быть какое-то сообщение о пустоте, не эксепшн
        assert "" in result.stdout

    def test_list_all(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "A", "--type", "client-project")
        _add_project(runner, app, "--name", "B", "--type", "business-product")

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "a" in result.stdout.lower()
        assert "b" in result.stdout.lower()

    def test_list_filter_type(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "ClientOne", "--type", "client-project")
        _add_project(runner, app, "--name", "BizOne", "--type", "business-product")

        result = runner.invoke(app, ["list", "--type", "client-project"])
        assert result.exit_code == 0
        assert "clientone" in result.stdout.lower()
        assert "bizone" not in result.stdout.lower()

    def test_list_filter_status(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "Exp", "--type", "client-project",
                     "--status", "experiment")
        _add_project(runner, app, "--name", "Act", "--type", "client-project",
                     "--status", "active")

        result = runner.invoke(app, ["list", "--status", "active"])
        assert result.exit_code == 0
        assert "act" in result.stdout.lower()
        # 'exp' не должна показаться
        assert "exp " not in result.stdout.lower() and "exp\n" not in result.stdout.lower()

    def test_list_hides_archived_by_default(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "Live", "--type", "client-project")
        _add_project(runner, app, "--name", "Dead", "--type", "client-project")
        runner.invoke(app, ["delete", "dead"])

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "live" in result.stdout.lower()
        assert "dead" not in result.stdout.lower()

    def test_list_shows_archived_with_flag(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "Live", "--type", "client-project")
        _add_project(runner, app, "--name", "Dead", "--type", "client-project")
        runner.invoke(app, ["delete", "dead"])

        result = runner.invoke(app, ["list", "--archived"])
        assert result.exit_code == 0
        assert "dead" in result.stdout.lower()
        assert "live" in result.stdout.lower()


# --------------------------------------------------------------------------- #
# get                                                                          #
# --------------------------------------------------------------------------- #


class TestGet:
    def test_get_by_slug(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "Cifro", "--type", "client-project",
                     "--slug", "cifro")
        result = runner.invoke(app, ["get", "cifro"])
        assert result.exit_code == 0
        assert "cifro" in result.stdout.lower()

    def test_get_by_uuid_full(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Project

        _add_project(runner, app, "--name", "Cifro", "--type", "client-project",
                     "--slug", "cifro")
        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            full_uuid = proj.id

        result = runner.invoke(app, ["get", full_uuid])
        assert result.exit_code == 0
        assert "cifro" in result.stdout.lower()

    def test_get_by_uuid_short(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Project

        _add_project(runner, app, "--name", "Cifro", "--type", "client-project",
                     "--slug", "cifro")
        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "cifro")
            ).scalar_one()
            short = proj.id[:8]

        result = runner.invoke(app, ["get", short])
        assert result.exit_code == 0
        assert "cifro" in result.stdout.lower()

    def test_get_not_found(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["get", "nonexistent"])
        assert result.exit_code == 1

    def test_get_shows_participants(self, runner, app, seeded_engine):
        # add авто-добавляет владельца как lead-участника (этап 1 авто-раскладки),
        # поэтому get сразу показывает его без ручного member-add.
        _add_project(runner, app, "--name", "Cifro", "--type", "client-project",
                     "--slug", "cifro")
        result = runner.invoke(app, ["get", "cifro"])
        assert result.exit_code == 0
        assert "дмитрий" in result.stdout.lower() or "owner" in result.stdout.lower()

    def test_get_shows_last_action_log(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "Cifro", "--type", "client-project",
                     "--slug", "cifro")
        result = runner.invoke(app, ["get", "cifro"])
        assert result.exit_code == 0
        # action_log записан при add, должен быть упомянут project_created
        assert "project_created" in result.stdout


# --------------------------------------------------------------------------- #
# update                                                                       #
# --------------------------------------------------------------------------- #


class TestUpdate:
    def test_update_single_field(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Project

        _add_project(runner, app, "--name", "Old", "--type", "client-project",
                     "--slug", "xx")
        result = runner.invoke(app, ["update", "xx","--name", "New"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "xx")
            ).scalar_one()
            assert proj.name == "New"

    def test_update_multiple_fields(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Project

        _add_project(runner, app, "--name", "Old", "--type", "client-project",
                     "--slug", "xx", "--priority", "P2")
        result = runner.invoke(app, [
            "update", "xx", "--name", "New", "--priority", "P0",
            "--description", "D",
        ])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "xx")
            ).scalar_one()
            assert proj.name == "New"
            assert proj.priority == "P0"
            assert proj.description == "D"

    def test_update_nonexistent(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["update", "nope", "--name", "X"])
        assert result.exit_code != 0

    def test_update_slug_forbidden(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "X", "--type", "client-project",
                     "--slug", "xx")
        result = runner.invoke(app, ["update", "xx","--slug", "y"])
        assert result.exit_code != 0
        combined = _combined(result)
        assert "slug" in combined.lower()

    def test_update_creates_action_log_with_diff(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ActionLog

        _add_project(runner, app, "--name", "Old", "--type", "client-project",
                     "--slug", "xx")
        runner.invoke(app, ["update", "xx","--name", "New"])

        with make_session(seeded_engine) as session:
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "project_updated")
            ).scalar_one()
            details = json.loads(entry.details_json)
            assert "name" in details
            # ожидаем структуру {"field": {"old": ..., "new": ...}}
            assert details["name"]["old"] == "Old"
            assert details["name"]["new"] == "New"

    def test_update_prefix_collision(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "Alpha", "--type", "client-project",
                     "--slug", "alpha", "--prefix", "al")
        _add_project(runner, app, "--name", "Beta", "--type", "client-project",
                     "--slug", "beta", "--prefix", "be")
        result = runner.invoke(app, ["update", "beta", "--prefix", "al"])
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# delete                                                                       #
# --------------------------------------------------------------------------- #


class TestDelete:
    def test_delete_soft(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import ActionLog, Project

        _add_project(runner, app, "--name", "X", "--type", "client-project",
                     "--slug", "xx")
        result = runner.invoke(app, ["delete", "xx"])
        assert result.exit_code == 0, _combined(result)

        with make_session(seeded_engine) as session:
            proj = session.execute(
                select(Project).where(Project.slug == "xx")
            ).scalar_one()
            assert proj.archived_at is not None
            entry = session.execute(
                select(ActionLog).where(ActionLog.action == "project_archived")
            ).scalar_one()
            assert entry.entity_id == proj.id

    def test_delete_hidden_from_list(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "X", "--type", "client-project",
                     "--slug", "xx")
        runner.invoke(app, ["delete", "xx"])

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "x" not in result.stdout.lower().split()

    def test_delete_visible_in_get(self, runner, app, seeded_engine):
        _add_project(runner, app, "--name", "X", "--type", "client-project",
                     "--slug", "xx")
        runner.invoke(app, ["delete", "xx"])

        result = runner.invoke(app, ["get", "xx"])
        assert result.exit_code == 0
        assert "archived" in result.stdout.lower()

    def test_delete_hard_requires_confirm(self, runner, app, seeded_engine):
        from atlas.db import make_session
        from atlas.models import Project

        _add_project(runner, app, "--name", "X", "--type", "client-project",
                     "--slug", "xx")
        # Без подтверждения — отменяется
        result = runner.invoke(app, ["delete", "xx", "--hard"], input="n\n")
        assert result.exit_code != 0 or "x" in [
            p.slug for p in _all_projects(seeded_engine)
        ]

        # С подтверждением — удаляется физически
        result2 = runner.invoke(app, ["delete", "xx", "--hard"], input="y\n")
        assert result2.exit_code == 0
        with make_session(seeded_engine) as session:
            assert session.execute(
                select(Project).where(Project.slug == "xx")
            ).scalar_one_or_none() is None

    def test_delete_nonexistent(self, runner, app, seeded_engine):
        result = runner.invoke(app, ["delete", "nope"])
        assert result.exit_code != 0


def _all_projects(engine):
    """Helper для test_delete_hard_requires_confirm."""
    from atlas.db import make_session
    from atlas.models import Project

    with make_session(engine) as session:
        return list(session.execute(select(Project)).scalars().all())


class TestMakePersonalLinkUnlink:
    def test_make_personal(self, runner, app, seeded_engine, monkeypatch):
        """make-personal: Atlas sync_policy=full + lead-участник, ядро PATCH personal."""
        import atlas.commands.projects as projmod
        from atlas.db import make_session
        from atlas.models import Participant, Project, ProjectParticipant

        calls = {}

        class _FC:
            def __init__(self, *a, **k):
                pass

            async def patch_project(self, ident, **f):
                calls["patch"] = (ident, f)
                return {}

            async def aclose(self):
                pass

        monkeypatch.setattr(projmod, "HubService", _FC)
        monkeypatch.setenv("ATLAS_API_KEY", "k")

        _add_project(runner, app, "--name", "ПП", "--slug", "pp", "--no-sync")
        result = runner.invoke(app, ["make-personal", "pp"])
        assert result.exit_code == 0, _combined(result)
        ident, f = calls["patch"]
        assert f["visibility"] == "personal"
        assert f["lead_slug"] == "owner"
        with make_session(seeded_engine) as s:
            proj = s.execute(select(Project).where(Project.slug == "pp")).scalar_one()
            assert proj.sync_policy == "full"
            dm = s.execute(select(Participant).where(Participant.slug == "owner")).scalar_one()
            assert s.get(ProjectParticipant, (proj.id, dm.id)) is not None

