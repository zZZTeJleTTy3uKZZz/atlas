"""Тесты pm/ideas.py — extract_idea_backlog + render templates (W45-38)."""
from __future__ import annotations

import textwrap

from atlas.ideas import (
    ensure_ideas_root,
    extract_idea_backlog,
    render_idea_md,
    render_promoted_backlog,
    write_idea_md,
)


# --------------------------------------------------------------------------- #
# extract_idea_backlog                                                        #
# --------------------------------------------------------------------------- #


class TestExtractIdeaBacklog:
    def test_extract_simple_section(self):
        backlog = textwrap.dedent("""\
            # Backlog

            ## По идеям

            ### #foo
            - [ ] **P0** Foo task 1
            - [ ] **P1** Foo task 2

            ### #bar
            - [ ] **P0** Bar task

            ## Общее
            - [ ] cross-cutting
        """)
        extracted, remaining = extract_idea_backlog(backlog, "foo")
        assert extracted is not None
        assert "Foo task 1" in extracted
        assert "Foo task 2" in extracted
        assert "Bar task" not in extracted
        # Remaining не содержит секцию foo, но содержит bar и cross-cutting.
        assert "### #foo" not in remaining
        assert "Bar task" in remaining
        assert "cross-cutting" in remaining

    def test_extract_last_section_before_eof(self):
        """Секция в конце файла без закрывающего ## — извлекается до EOF."""
        backlog = textwrap.dedent("""\
            # Backlog

            ### #foo
            - [ ] last section
        """)
        extracted, remaining = extract_idea_backlog(backlog, "foo")
        assert extracted is not None
        assert "last section" in extracted
        assert "### #foo" not in remaining

    def test_extract_missing_slug_returns_none(self):
        backlog = "# B\n\n### #foo\n- [ ] x\n"
        extracted, remaining = extract_idea_backlog(backlog, "nonexistent")
        assert extracted is None
        assert remaining == backlog

    def test_extract_preserves_structure(self):
        """Заголовки ## уровня сохраняются между секциями ###."""
        backlog = textwrap.dedent("""\
            # B

            ## По идеям

            ### #a
            - [ ] a1

            ### #b
            - [ ] b1
        """)
        extracted, remaining = extract_idea_backlog(backlog, "a")
        assert "## По идеям" in remaining
        assert "### #b" in remaining


# --------------------------------------------------------------------------- #
# render_idea_md                                                              #
# --------------------------------------------------------------------------- #


class TestRenderIdeaMd:
    def test_render_includes_metadata(self):
        md = render_idea_md(
            name="Test Idea",
            slug="test-idea",
            type_slug="business-product",
            priority="P1",
            status_slug="active",
            one_line="What it does",
            owner_tags=["owner"],
            all_tags=["owner", "stack:python"],
        )
        assert "Test Idea" in md
        assert "What it does" in md
        assert "`test-idea`" in md
        assert "business-product" in md
        assert "P1" in md
        assert "`owner`" in md
        assert "Decision criteria" in md  # секция template'а

    def test_render_handles_empty_one_line(self):
        md = render_idea_md(
            name="X", slug="x", type_slug="business-product",
            priority="P2", status_slug="active", one_line="",
        )
        assert "(заполнить one-line)" in md

    def test_render_handles_no_tags(self):
        md = render_idea_md(
            name="X", slug="x", type_slug="business-product",
            priority="P2", status_slug="active", one_line="-",
        )
        # owners_str/tags_str → '—'.
        assert "—" in md


# --------------------------------------------------------------------------- #
# render_promoted_backlog                                                     #
# --------------------------------------------------------------------------- #


def test_render_promoted_backlog_includes_source_note():
    extracted = "### #foo\n- [ ] task\n"
    out = render_promoted_backlog(extracted, "2026-04-29")
    assert "# Backlog" in out
    assert "Перенесено" in out
    assert "2026-04-29" in out
    assert "task" in out


# --------------------------------------------------------------------------- #
# ensure_ideas_root + write_idea_md                                           #
# --------------------------------------------------------------------------- #


def test_ensure_ideas_root_creates_dir_with_readme_and_backlog(tmp_path):
    ideas_dir = ensure_ideas_root(tmp_path)
    assert ideas_dir.exists()
    assert ideas_dir.name == "_Ideas"
    assert (ideas_dir / "README.md").exists()
    assert (ideas_dir / "BACKLOG.md").exists()


def test_ensure_ideas_root_idempotent(tmp_path):
    ideas_dir = ensure_ideas_root(tmp_path)
    # Записать в README кастомный текст
    (ideas_dir / "README.md").write_text("CUSTOM CONTENT", encoding="utf-8")
    ensure_ideas_root(tmp_path)  # повтор — не должен затереть
    assert (ideas_dir / "README.md").read_text(encoding="utf-8") == "CUSTOM CONTENT"


def test_write_idea_md_creates_file(tmp_path):
    ideas_dir = ensure_ideas_root(tmp_path)
    path = write_idea_md(ideas_dir, "my-idea", "# My Idea\n\nContent\n")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# My Idea\n\nContent\n"


def test_write_idea_md_refuses_overwrite_by_default(tmp_path):
    import pytest

    ideas_dir = ensure_ideas_root(tmp_path)
    write_idea_md(ideas_dir, "x", "first")
    with pytest.raises(FileExistsError):
        write_idea_md(ideas_dir, "x", "second")
    # С overwrite=True — перезаписывает.
    write_idea_md(ideas_dir, "x", "second", overwrite=True)
    assert (ideas_dir / "x.md").read_text(encoding="utf-8") == "second"
