"""Тесты онбординг-блока «пользуйся Atlas» в AGENTS.md / CLAUDE.md (задача #211).

TDD: пишутся ДО реализации хелпера `_ensure_atlas_prompt_block` и его вызова
в канон-флоу `_create_canonical_files`.

ЦКП: при `project add --canonical` / онбординг-флоу Atlas ИДЕМПОТЕНТНО вставляет
в AGENTS.md (и CLAUDE.md при наличии) маркер-делимитированный блок-указатель:
для управления задачами/проектами пользоваться CLI `atlas` и вызывать навык
`atlas`. Повторный прогон НЕ дублирует блок.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from atlas.pm.commands.projects import (
    ATLAS_PROMPT_BLOCK,
    ATLAS_PROMPT_END,
    ATLAS_PROMPT_START,
    CANONICAL_AGENTS_TEMPLATE,
    _ensure_atlas_prompt_block,
)


# --------------------------------------------------------------------------- #
# Хелперы                                                                      #
# --------------------------------------------------------------------------- #


def _count_markers(text: str) -> tuple[int, int]:
    return text.count(ATLAS_PROMPT_START), text.count(ATLAS_PROMPT_END)


# --------------------------------------------------------------------------- #
# _ensure_atlas_prompt_block — юнит-уровень                                    #
# --------------------------------------------------------------------------- #


class TestEnsureAtlasPromptBlock:
    def test_missing_file_skipped(self, tmp_path: Path):
        """Файла нет → ничего не создаём, возвращаем False."""
        path = tmp_path / "CLAUDE.md"
        changed = _ensure_atlas_prompt_block(path)
        assert changed is False
        assert not path.exists()

    def test_appends_block_when_absent(self, tmp_path: Path):
        """Файл есть, блока нет → блок дописан между маркерами."""
        path = tmp_path / "AGENTS.md"
        path.write_text("# AGENTS\n\nКакой-то контент.\n", encoding="utf-8")

        changed = _ensure_atlas_prompt_block(path)

        assert changed is True
        text = path.read_text(encoding="utf-8")
        # исходный контент сохранён
        assert "Какой-то контент." in text
        # блок вставлен ровно один раз
        assert _count_markers(text) == (1, 1)
        assert ATLAS_PROMPT_START in text
        assert ATLAS_PROMPT_END in text
        assert "навык `atlas`" in text

    def test_idempotent_second_run_no_duplicate(self, tmp_path: Path):
        """Повторный прогон НЕ дублирует и НЕ меняет (если уже актуально)."""
        path = tmp_path / "AGENTS.md"
        path.write_text("# AGENTS\n\nКонтент.\n", encoding="utf-8")

        first_changed = _ensure_atlas_prompt_block(path)
        after_first = path.read_text(encoding="utf-8")

        second_changed = _ensure_atlas_prompt_block(path)
        after_second = path.read_text(encoding="utf-8")

        assert first_changed is True
        # второй прогон ничего не меняет → False и идентичный файл
        assert second_changed is False
        assert after_first == after_second
        assert _count_markers(after_second) == (1, 1)

    def test_updates_stale_block_between_markers(self, tmp_path: Path):
        """Если между маркерами устаревший текст — заменяем, не дублируя."""
        path = tmp_path / "AGENTS.md"
        stale = (
            "# AGENTS\n\nШапка.\n\n"
            f"{ATLAS_PROMPT_START}\nстарый текст\n{ATLAS_PROMPT_END}\n\nХвост.\n"
        )
        path.write_text(stale, encoding="utf-8")

        changed = _ensure_atlas_prompt_block(path)

        text = path.read_text(encoding="utf-8")
        assert changed is True
        # ровно одна пара маркеров
        assert _count_markers(text) == (1, 1)
        # старый текст вычищен, актуальный — внутри
        assert "старый текст" not in text
        assert "навык `atlas`" in text
        # окружающий контент сохранён
        assert "Шапка." in text
        assert "Хвост." in text

    def test_preserves_surrounding_content_on_append(self, tmp_path: Path):
        """Дописывание блока не затирает существующий контент."""
        path = tmp_path / "AGENTS.md"
        original = "# Project\n\nline1\nline2\n"
        path.write_text(original, encoding="utf-8")

        _ensure_atlas_prompt_block(path)

        text = path.read_text(encoding="utf-8")
        assert text.startswith(original.rstrip("\n"))

    def test_orphan_start_marker_does_not_lose_content(self, tmp_path: Path):
        """Сиротский START без END не приводит к потере контента (#211, finding 1).

        Сценарий: внешняя порча/ручная правка оставила START без END и текст
        пользователя ПОСЛЕ него. Хелпер не должен затирать этот текст ни на
        первом, ни на повторном прогоне; в итоге — ровно одна валидная пара.
        """
        path = tmp_path / "AGENTS.md"
        path.write_text(
            f"# AGENTS\n\n{ATLAS_PROMPT_START}\nВАЖНЫЙ КОНТЕНТ ПОЛЬЗОВАТЕЛЯ\n",
            encoding="utf-8",
        )

        # run1
        _ensure_atlas_prompt_block(path)
        after1 = path.read_text(encoding="utf-8")
        assert "ВАЖНЫЙ КОНТЕНТ ПОЛЬЗОВАТЕЛЯ" in after1
        # ровно одна валидная пара (сиротский START остаётся как контент → 2 START,
        # но END ровно один — инвариант валидной пары держится после run2)

        # run2 — повторный прогон не теряет контент
        _ensure_atlas_prompt_block(path)
        after2 = path.read_text(encoding="utf-8")
        assert "ВАЖНЫЙ КОНТЕНТ ПОЛЬЗОВАТЕЛЯ" in after2
        assert "навык `atlas`" in after2

    def test_self_heals_two_marker_pairs_to_single(self, tmp_path: Path):
        """Две корректные пары маркеров схлопываются в одну (#211, findings 2/5/9)."""
        path = tmp_path / "AGENTS.md"
        path.write_text(
            f"# AGENTS\n\nШапка.\n\n"
            f"{ATLAS_PROMPT_START}\nold1\n{ATLAS_PROMPT_END}\n\n"
            f"Середина.\n\n"
            f"{ATLAS_PROMPT_START}\nold2\n{ATLAS_PROMPT_END}\n\nХвост.\n",
            encoding="utf-8",
        )

        changed = _ensure_atlas_prompt_block(path)
        text = path.read_text(encoding="utf-8")

        assert changed is True
        # самоисцеление: ровно одна пара
        assert _count_markers(text) == (1, 1)
        # оба устаревших блока вычищены
        assert "old1" not in text
        assert "old2" not in text
        assert "навык `atlas`" in text
        # окружающий контент сохранён
        assert "Шапка." in text
        assert "Середина." in text
        assert "Хвост." in text

    def test_preserves_crlf_line_endings(self, tmp_path: Path):
        """CRLF-файл остаётся CRLF — не нормализуется целиком в LF (#211, 3/6/7)."""
        path = tmp_path / "CLAUDE.md"
        # пишем байтами с CRLF, без universal-newline трансляции
        path.write_bytes(b"# CLAUDE\r\n\r\nMy rules.\r\n")

        changed = _ensure_atlas_prompt_block(path)
        raw = path.read_bytes()

        assert changed is True
        # окончания строк сохранены как CRLF
        assert b"\r\n" in raw
        # нет «голых» LF (каждый \n предварён \r)
        assert raw.replace(b"\r\n", b"") .find(b"\n") == -1
        # блок вписан, исходный контент на месте
        text = raw.decode("utf-8")
        assert ATLAS_PROMPT_START in text
        assert "My rules." in text

    def test_lf_file_stays_lf(self, tmp_path: Path):
        """LF-файл остаётся LF (без внезапного CRLF)."""
        path = tmp_path / "AGENTS.md"
        path.write_bytes(b"# AGENTS\n\nrules.\n")

        _ensure_atlas_prompt_block(path)
        raw = path.read_bytes()

        assert b"\r\n" not in raw
        assert ATLAS_PROMPT_START.encode() in raw


# --------------------------------------------------------------------------- #
# Шаблон AGENTS.md содержит блок                                               #
# --------------------------------------------------------------------------- #


def test_agents_template_contains_block():
    """НОВЫЙ AGENTS.md из шаблона сразу содержит маркер-делимитированный блок."""
    # Шаблон ссылается на блок через плейсхолдер; маркеры появляются в рендере.
    assert "{atlas_prompt_block}" in CANONICAL_AGENTS_TEMPLATE
    # рендер шаблона не падает и блок единичный
    rendered = CANONICAL_AGENTS_TEMPLATE.format(
        atlas_prompt_block=ATLAS_PROMPT_BLOCK,
        name="X",
        slug="x",
        prefix="X",
        priority="P1",
        type_slug="product",
        status_slug="active",
        one_line="ol",
        tags_str="—",
        created_date="2026-06-23",
        logical_rel="Products/x",
    )
    assert rendered.count(ATLAS_PROMPT_START) == 1
    assert rendered.count(ATLAS_PROMPT_END) == 1


# --------------------------------------------------------------------------- #
# Канон-флоу (CLI add --canonical) — интеграция                               #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def isolated_projects_root(tmp_path, monkeypatch):
    root = tmp_path / "PROJECT"
    root.mkdir()
    for sub in ("Clients", "Products", "Tests", "_Inbox", "_Archive", "_storage"):
        (root / sub).mkdir(exist_ok=True)
    monkeypatch.setenv("ATLAS_PROJECTS_ROOT", str(root))
    return root


@pytest.fixture()
def seeded_engine(tmp_path, monkeypatch):
    from atlas.pm.db import make_engine, make_session
    from atlas.pm.models import Base
    from atlas.pm.seeds import seed_all

    db_path = tmp_path / "atlas.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    with make_session(engine) as session:
        seed_all(session)
    return engine


@pytest.fixture()
def runner():
    from typer.testing import CliRunner

    return CliRunner()


@pytest.fixture()
def app():
    from atlas.pm.commands.projects import projects_app

    return projects_app


def test_canonical_add_new_agents_has_block(
    seeded_engine, runner, app, tmp_path, isolated_projects_root
):
    """Новый проект (--canonical): созданный AGENTS.md содержит блок из шаблона."""
    local = tmp_path / "proj_new"
    result = runner.invoke(
        app,
        [
            "add",
            "--canonical",
            "--no-setup-layout",
            "--no-init-git",
            "--no-sync",
            "--name", "Канон Тест",
            "--local-path", str(local),
        ],
    )
    assert result.exit_code == 0, result.output

    agents = local / "AGENTS.md"
    assert agents.exists()
    text = agents.read_text(encoding="utf-8")
    assert text.count(ATLAS_PROMPT_START) == 1
    assert text.count(ATLAS_PROMPT_END) == 1
    assert "навык `atlas`" in text


def test_canonical_add_existing_claude_gets_block(
    seeded_engine, runner, app, tmp_path, isolated_projects_root
):
    """CLAUDE.md существует → блок вставлен туда; не существует → не создаётся."""
    local = tmp_path / "proj_claude"
    local.mkdir(parents=True, exist_ok=True)
    claude = local / "CLAUDE.md"
    claude.write_text("# CLAUDE\n\nправила.\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "add",
            "--canonical",
            "--no-setup-layout",
            "--no-init-git",
            "--no-sync",
            "--name", "Клод Тест",
            "--local-path", str(local),
        ],
    )
    assert result.exit_code == 0, result.output

    ctext = claude.read_text(encoding="utf-8")
    assert ctext.count(ATLAS_PROMPT_START) == 1
    assert "навык `atlas`" in ctext
    assert "правила." in ctext


def test_canonical_add_no_claude_not_created(
    seeded_engine, runner, app, tmp_path, isolated_projects_root
):
    """CLAUDE.md НЕТ → канон-флоу его не создаёт (только дополняет существующий)."""
    local = tmp_path / "proj_noclaude"
    result = runner.invoke(
        app,
        [
            "add",
            "--canonical",
            "--no-setup-layout",
            "--no-init-git",
            "--no-sync",
            "--name", "Без Клода",
            "--local-path", str(local),
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (local / "CLAUDE.md").exists()
    # но AGENTS.md создан и с блоком
    assert (local / "AGENTS.md").exists()


def test_canonical_add_idempotent_run_twice(
    seeded_engine, runner, app, tmp_path, isolated_projects_root
):
    """Повторный канон-прогон по тем же файлам не дублирует блок в AGENTS.md."""
    local = tmp_path / "proj_idem"
    args = [
        "add",
        "--canonical",
        "--no-setup-layout",
        "--no-init-git",
        "--no-sync",
        "--name", "Идемпотент",
        "--local-path", str(local),
    ]
    r1 = runner.invoke(app, args)
    assert r1.exit_code == 0, r1.output
    agents = local / "AGENTS.md"
    first = agents.read_text(encoding="utf-8")

    # повторно прогоняем сам хелпер по уже существующему AGENTS.md
    changed = _ensure_atlas_prompt_block(agents)
    second = agents.read_text(encoding="utf-8")

    assert changed is False
    assert first == second
    assert second.count(ATLAS_PROMPT_START) == 1
