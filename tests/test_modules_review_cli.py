"""CLI-тесты по находкам ревью Контейнеров (#163/#126/#127).

Покрывают исправления:
- #1/#12: container_logical предпочитает реальный local_path контейнера;
- #5: add --json payload self-describing (parent / is_module);
- #6: add --parent --setup-layout с неразложенным контейнером → отказ;
- #9/#13: модуль = свой локальный git-репо без ручной работы;
- #10: идемпотентность _ensure_gitignore_modules для эквивалентных записей;
- #2/#8/#14: update --parent / --no-parent переносит junction + чистит старый;
- #4: archive/unarchive модуля не уносит junction в _Archive;
- #7: move модуля не выносит junction в type-группу;
- #17: рекурсивный резолв глубоко вложенных контейнеров.

Все junction/git мокаются — реальную ФС вне tmp_path не трогаем.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from typer.testing import CliRunner


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "PROJECT"
    root.mkdir()
    for sub in ("Clients", "Products", "Tests", "_Inbox", "_Archive", "_storage"):
        (root / sub).mkdir(exist_ok=True)
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture()
def fresh_engine(tmp_path, monkeypatch):
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
    from atlas.db import make_session
    from atlas.seeds import seed_all

    with make_session(fresh_engine) as session:
        seed_all(session)
    return fresh_engine


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def app():
    from atlas.commands.projects import projects_app
    return projects_app


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _combined(result) -> str:
    out = result.stdout or ""
    try:
        err = result.stderr or ""
    except (ValueError, AttributeError):
        err = ""
    exc = str(result.exception) if result.exception else ""
    return out + err + exc


class FakeJunctions:
    """Реестр fake-junction'ов: link(str) → target(Path).

    Имитирует create/remove/is_junction/junction_target поверх реальной ФС
    (создаёт каталоги-маркеры), чтобы CLI-физику можно было проверять без
    Windows reparse-points.
    """

    def __init__(self) -> None:
        self.registry: dict[str, Path] = {}

    def create(self, link, target):
        link = Path(link)
        target = Path(target)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.mkdir(exist_ok=True)
        self.registry[str(link.resolve())] = target

    def remove(self, link):
        link = Path(link)
        key = str(link.resolve())
        self.registry.pop(key, None)
        if link.exists():
            try:
                link.rmdir()
            except OSError:
                pass

    def is_junction(self, p):
        return str(Path(p).resolve()) in self.registry

    def target(self, p):
        return self.registry.get(str(Path(p).resolve()))


def _patch_junctions(fake: FakeJunctions):
    from atlas.commands import projects as projects_mod
    return [
        patch.object(projects_mod, "create_junction", side_effect=fake.create),
        patch.object(projects_mod, "remove_junction", side_effect=fake.remove),
        patch.object(projects_mod, "is_junction", side_effect=fake.is_junction),
        patch.object(projects_mod, "junction_target", side_effect=fake.target),
    ]


def _run(runner, app, *args):
    return runner.invoke(app, list(args))


def _project_by_slug(engine, slug):
    from atlas.db import make_session
    from atlas.models import Project

    with make_session(engine) as session:
        return session.execute(
            select(Project).where(Project.slug == slug)
        ).scalar_one_or_none()


# --------------------------------------------------------------------------- #
# #1/#12 — container_logical предпочитает реальный local_path контейнера      #
# --------------------------------------------------------------------------- #


class TestContainerCustomLocalPath:
    def test_module_lands_under_container_real_local_path(
        self, runner, app, seeded_engine, projects_root
    ):
        custom = projects_root / "ELSEWHERE" / "mycontainer"
        custom.mkdir(parents=True, exist_ok=True)

        # Контейнер с КАСТОМНЫМ local_path (не по type-формуле).
        res = _run(
            runner, app, "add", "--name", "Cont", "--slug", "cont",
            "--type", "business-product", "--local-path", str(custom),
            "--no-setup-layout", "--no-canonical", "--no-sync",
        )
        assert res.exit_code == 0, _combined(res)

        fake = FakeJunctions()
        patches = _patch_junctions(fake)
        with patches[0], patches[1], patches[2], patches[3]:
            res = _run(
                runner, app, "add", "--name", "Mod", "--slug", "mod",
                "--type", "business-product", "--parent", "cont",
                "--setup-layout", "--no-canonical", "--no-sync",
            )
        assert res.exit_code == 0, _combined(res)

        # Модуль должен лечь под РЕАЛЬНЫЙ контейнер, не в фантомный Products/cont.
        mod = _project_by_slug(seeded_engine, "mod")
        expected = custom / "modules" / "mod"
        assert Path(mod.local_path) == expected, mod.local_path
        assert (custom / "modules" / "mod").exists()
        assert not (projects_root / "Products" / "cont").exists()


# --------------------------------------------------------------------------- #
# #5 — add --json self-describing                                             #
# --------------------------------------------------------------------------- #


class TestAddPayloadParentMarker:
    def test_module_payload_has_parent_and_is_module(
        self, runner, app, seeded_engine, projects_root
    ):
        _run(
            runner, app, "add", "--name", "Cont", "--slug", "cont",
            "--type", "business-product", "--no-setup-layout",
            "--no-canonical", "--no-sync",
        )
        (projects_root / "Products" / "cont").mkdir(parents=True, exist_ok=True)

        fake = FakeJunctions()
        patches = _patch_junctions(fake)
        with patches[0], patches[1], patches[2], patches[3]:
            res = _run(
                runner, app, "add", "--name", "Mod", "--slug", "mod",
                "--type", "business-product", "--parent", "cont",
                "--setup-layout", "--no-canonical", "--no-sync",
            )
        assert res.exit_code == 0, _combined(res)
        data = json.loads(res.stdout)
        assert data["parent"] == "cont"
        assert data["is_module"] is True
        assert "container_gitignore_updated" in data  # стабильная форма

    def test_standalone_payload_parent_null(
        self, runner, app, seeded_engine, projects_root
    ):
        fake = FakeJunctions()
        patches = _patch_junctions(fake)
        with patches[0], patches[1], patches[2], patches[3]:
            res = _run(
                runner, app, "add", "--name", "Solo", "--slug", "solo",
                "--type", "business-product", "--setup-layout",
                "--no-canonical", "--no-sync",
            )
        assert res.exit_code == 0, _combined(res)
        data = json.loads(res.stdout)
        assert data["parent"] is None
        assert data["is_module"] is False
        assert data["container_gitignore_updated"] is None


# --------------------------------------------------------------------------- #
# #6 — add --parent --setup-layout с неразложенным контейнером → отказ        #
# --------------------------------------------------------------------------- #


class TestContainerNotLaidOut:
    def test_reject_when_container_logical_absent_and_no_storage(
        self, runner, app, seeded_engine, projects_root
    ):
        # Контейнер создан БЕЗ layout: нет _storage/cont, нет Products/cont.
        _run(
            runner, app, "add", "--name", "Cont", "--slug", "cont",
            "--type", "business-product", "--no-setup-layout",
            "--no-canonical", "--no-sync",
        )
        # НЕ создаём container_logical-папку → фантом был бы создан → отказ.
        fake = FakeJunctions()
        patches = _patch_junctions(fake)
        with patches[0], patches[1], patches[2], patches[3]:
            res = _run(
                runner, app, "add", "--name", "Mod", "--slug", "mod",
                "--type", "business-product", "--parent", "cont",
                "--setup-layout", "--no-canonical", "--no-sync",
            )
        assert res.exit_code != 0, _combined(res)
        assert "не разложен" in _combined(res) or "layout init" in _combined(res)
        # Фантомная папка modules/ не создана.
        assert not (projects_root / "Products" / "cont" / "modules").exists()


# --------------------------------------------------------------------------- #
# #9/#13 — модуль = свой локальный git-репо без ручной работы                 #
# --------------------------------------------------------------------------- #


class TestModuleLocalGit:
    def test_module_storage_gets_local_git(
        self, runner, app, seeded_engine, projects_root
    ):
        _run(
            runner, app, "add", "--name", "Cont", "--slug", "cont",
            "--type", "business-product", "--no-setup-layout",
            "--no-canonical", "--no-sync",
        )
        (projects_root / "Products" / "cont").mkdir(parents=True, exist_ok=True)

        fake = FakeJunctions()
        patches = _patch_junctions(fake)
        with patches[0], patches[1], patches[2], patches[3]:
            res = _run(
                runner, app, "add", "--name", "Mod", "--slug", "mod",
                "--type", "business-product", "--parent", "cont",
                "--setup-layout", "--no-canonical", "--no-sync",
            )
        assert res.exit_code == 0, _combined(res)
        data = json.loads(res.stdout)
        assert data.get("module_git_initialized") is True
        # .git реально создан в storage модуля.
        assert (projects_root / "_storage" / "mod" / ".git").exists()


# --------------------------------------------------------------------------- #
# #10 — идемпотентность gitignore для эквивалентных записей                   #
# --------------------------------------------------------------------------- #


class TestGitignoreIdempotency:
    @pytest.mark.parametrize("existing", ["/modules/\n", "modules\n", "modules/*\n"])
    def test_equivalent_marker_no_duplicate(self, tmp_path, existing):
        from atlas.commands.projects import _ensure_gitignore_modules

        gi = tmp_path / ".gitignore"
        gi.write_text(existing, encoding="utf-8")
        changed = _ensure_gitignore_modules(tmp_path)
        assert changed is False
        assert gi.read_text(encoding="utf-8") == existing

    def test_exact_marker_no_duplicate(self, tmp_path):
        from atlas.commands.projects import _ensure_gitignore_modules

        gi = tmp_path / ".gitignore"
        gi.write_text("modules/\n", encoding="utf-8")
        assert _ensure_gitignore_modules(tmp_path) is False


# --------------------------------------------------------------------------- #
# #2/#8/#14 — update --parent / --no-parent переносит junction + чистит старый #
# --------------------------------------------------------------------------- #


def _setup_two_containers_and_module(runner, app, projects_root):
    """Контейнеры conta, contb (с реальными logical-папками) + модуль mod в conta."""
    for slug in ("conta", "contb"):
        _run(
            runner, app, "add", "--name", slug, "--slug", slug,
            "--type", "business-product", "--no-setup-layout",
            "--no-canonical", "--no-sync",
        )
        (projects_root / "Products" / slug).mkdir(parents=True, exist_ok=True)

    fake = FakeJunctions()
    patches = _patch_junctions(fake)
    with patches[0], patches[1], patches[2], patches[3]:
        res = _run(
            runner, app, "add", "--name", "Mod", "--slug", "mod",
            "--type", "business-product", "--parent", "conta",
            "--setup-layout", "--no-canonical", "--no-sync",
        )
    assert res.exit_code == 0, _combined(res)
    return fake


class TestReparentRelocation:
    def test_reparent_moves_junction_and_updates_local_path(
        self, runner, app, seeded_engine, projects_root
    ):
        fake = _setup_two_containers_and_module(runner, app, projects_root)
        old_junction = projects_root / "Products" / "conta" / "modules" / "mod"
        assert old_junction.exists()

        patches = _patch_junctions(fake)
        with patches[0], patches[1], patches[2], patches[3]:
            res = _run(runner, app, "update", "mod", "--parent", "contb")
        assert res.exit_code == 0, _combined(res)

        mod = _project_by_slug(seeded_engine, "mod")
        new_junction = projects_root / "Products" / "contb" / "modules" / "mod"
        # local_path обновлён на новый контейнер.
        assert Path(mod.local_path) == new_junction, mod.local_path
        # Новый junction есть, старый снят.
        assert new_junction.exists()
        assert not old_junction.exists()

    def test_no_parent_moves_junction_to_type_group(
        self, runner, app, seeded_engine, projects_root
    ):
        fake = _setup_two_containers_and_module(runner, app, projects_root)
        old_junction = projects_root / "Products" / "conta" / "modules" / "mod"
        assert old_junction.exists()

        patches = _patch_junctions(fake)
        with patches[0], patches[1], patches[2], patches[3]:
            res = _run(runner, app, "update", "mod", "--no-parent")
        assert res.exit_code == 0, _combined(res)

        mod = _project_by_slug(seeded_engine, "mod")
        type_group = projects_root / "Products" / "mod"
        assert Path(mod.local_path) == type_group, mod.local_path
        assert type_group.exists()
        assert not old_junction.exists()


# --------------------------------------------------------------------------- #
# #4 — archive/unarchive модуля не уносит junction в _Archive                 #
# --------------------------------------------------------------------------- #


class TestModuleArchiveStaysPut:
    def test_archive_module_keeps_junction_under_container(
        self, runner, app, seeded_engine, projects_root
    ):
        _run(
            runner, app, "add", "--name", "Cont", "--slug", "cont",
            "--type", "business-product", "--no-setup-layout",
            "--no-canonical", "--no-sync",
        )
        (projects_root / "Products" / "cont").mkdir(parents=True, exist_ok=True)
        fake = FakeJunctions()
        patches = _patch_junctions(fake)
        with patches[0], patches[1], patches[2], patches[3]:
            _run(
                runner, app, "add", "--name", "Mod", "--slug", "mod",
                "--type", "business-product", "--parent", "cont",
                "--setup-layout", "--no-canonical", "--no-sync",
            )
        junction = projects_root / "Products" / "cont" / "modules" / "mod"
        assert junction.exists()

        res = _run(runner, app, "archive", "mod", "--status", "archived")
        assert res.exit_code == 0, _combined(res)

        mod = _project_by_slug(seeded_engine, "mod")
        # local_path не уехал в _Archive — остался под контейнером.
        assert Path(mod.local_path) == junction, mod.local_path
        assert junction.exists()
        assert not (projects_root / "_Archive" / "products" / "mod").exists()


# --------------------------------------------------------------------------- #
# #7 — move модуля не выносит junction в type-группу                          #
# --------------------------------------------------------------------------- #


class TestModuleMoveStaysPut:
    def test_move_module_keeps_junction_under_container(
        self, runner, app, seeded_engine, projects_root
    ):
        _run(
            runner, app, "add", "--name", "Cont", "--slug", "cont",
            "--type", "business-product", "--no-setup-layout",
            "--no-canonical", "--no-sync",
        )
        (projects_root / "Products" / "cont").mkdir(parents=True, exist_ok=True)
        fake = FakeJunctions()
        patches = _patch_junctions(fake)
        with patches[0], patches[1], patches[2], patches[3]:
            _run(
                runner, app, "add", "--name", "Mod", "--slug", "mod",
                "--type", "business-product", "--parent", "cont",
                "--setup-layout", "--no-canonical", "--no-sync",
            )
        junction = projects_root / "Products" / "cont" / "modules" / "mod"
        assert junction.exists()

        # Смена типа на client-project (другая группа Clients) — для модуля
        # физика НЕ должна меняться.
        res = _run(runner, app, "move", "mod", "--to-type", "client-project")
        assert res.exit_code == 0, _combined(res)

        mod = _project_by_slug(seeded_engine, "mod")
        assert Path(mod.local_path) == junction, mod.local_path
        assert junction.exists()
        assert not (projects_root / "Clients" / "mod").exists()


# --------------------------------------------------------------------------- #
# #17 — рекурсивный резолв глубоко вложенных контейнеров                      #
# --------------------------------------------------------------------------- #


class TestDeepNesting:
    def test_three_level_nesting_resolves_full_path(self, seeded_engine):
        """super → cont → mod: путь mod собирается через все уровни."""
        from atlas.layout import resolve_container_logical, get_logical_path

        # duck-typed views с local_path=None (формула).
        def make_view(slug, parent_id):
            return type("V", (), {
                "slug": slug,
                "type_slug": "business-product",
                "archived": False,
                "archived_group": None,
                "parent_id": parent_id,
                "local_path": None,
            })()

        views = {
            "super": make_view("super", None),
            "cont": make_view("cont", "super"),
            "mod": make_view("mod", "cont"),
        }

        def resolver(pid):
            return views.get(pid)

        root = Path("/root")
        container_logical = resolve_container_logical(
            views["mod"], resolver, root=root
        )
        # mod → modules/ контейнера cont, который сам modules/ super.
        mod_path = get_logical_path(
            views["mod"], root=root, container_logical=container_logical
        )
        expected = (
            root / "Products" / "super" / "modules" / "cont" / "modules" / "mod"
        )
        assert mod_path == expected, mod_path

    def test_cycle_guard_returns_none(self, seeded_engine):
        from atlas.layout import resolve_container_logical

        a = type("V", (), {
            "slug": "a", "type_slug": "business-product", "archived": False,
            "archived_group": None, "parent_id": "b", "local_path": None,
        })()
        b = type("V", (), {
            "slug": "b", "type_slug": "business-product", "archived": False,
            "archived_group": None, "parent_id": "a", "local_path": None,
        })()
        reg = {"a": a, "b": b}
        # Циклический FK не должен зациклить — резолв завершается (None путь).
        result = resolve_container_logical(a, lambda pid: reg.get(pid), root=Path("/r"))
        # Не падаем и возвращаем какой-то путь/None — главное завершиться.
        assert result is None or isinstance(result, Path)
