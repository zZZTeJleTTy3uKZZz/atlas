"""Issue-сущность (Фаза 2): issue add/list/show/resolve/template + task handoff.

Блокирующая валидация полноты через issuekit (неполную жалобу/передачу не пускаем).
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from atlas.cli import app
from atlas.db import make_engine, make_session
from atlas.models import (
    Base,
    Issue,
    Participant,
    Project,
    ProjectStatus,
    ProjectType,
    Task,
)
from atlas.seeds import seed_all

runner = CliRunner()

# Полное handoff-тело (все required-секции issuekit handoff).
_FULL_HANDOFF = """\
# [Передача] доделать миграцию
## Что сделано
написал модель + миграцию
## Что осталось
команды + тесты
## Ожидаемый результат (ЦКП)
atlas issue add работает, тесты зелёные
## Как проверить / воспроизвести
uv run pytest tests/test_issue_cli.py
## Контекст и версии
ветка dev, python 3.11, БД ~/.atlas/atlas.db
"""

_FULL_BUG = """\
# [Баг] падает
## Что сломалось
crash
## Ожидал
ok
## Получил
no
## Версии
lib 1.0 · py 3.11
## Минимальный пример
```py
import x
```
## Полный traceback
```
Traceback ...
```
"""


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'atlas.db'}"
    monkeypatch.setenv("ATLAS_DB_URL", url)
    monkeypatch.setenv("ATLAS_ACTOR", "alice")
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    with make_session(eng) as s:
        seed_all(s)
        pt = s.execute(select(ProjectType).where(ProjectType.slug == "personal-project")).scalar_one()
        st = s.execute(select(ProjectStatus).where(ProjectStatus.slug == "active")).scalar_one()
        p = Project(slug="acme", name="Acme", type_id=pt.id, status_id=st.id,
                    priority="P2", one_line_summary="x", prefix="ACM")
        s.add(p)
        s.add_all([
            Participant(slug="alice", name="Alice", kind="human"),
            Participant(slug="bob", name="Bob", kind="ai_agent"),
        ])
        s.flush()
        s.add(Task(number=1, slug="acm-t1", project_id=p.id, title="Задача",
                   cpp_description="ЦКП", priority="P2", status="todo"))
        s.commit()
    return eng


def _json(r):
    return json.loads(r.stdout)


# --- template ---

def test_template_prints(engine):
    r = runner.invoke(app, ["issue", "template", "--kind", "handoff"])
    assert r.exit_code == 0, r.stdout
    assert "Ожидаемый результат" in r.stdout


# --- issue add (blocking validation) ---

def test_add_full_bug_ok(tmp_path, engine):
    f = tmp_path / "b.md"
    f.write_text(_FULL_BUG, encoding="utf-8")
    r = runner.invoke(app, ["--json", "issue", "add", "--kind", "bug", "--title", "Баг",
                            "--body-file", str(f), "--actor", "alice"])
    assert r.exit_code == 0, r.stdout
    assert _json(r)["kind"] == "bug"


def test_add_incomplete_blocked(tmp_path, engine):
    f = tmp_path / "b.md"
    f.write_text("# [Баг] x\n## Что сломалось\nпадает", encoding="utf-8")
    r = runner.invoke(app, ["issue", "add", "--kind", "bug", "--title", "X",
                            "--body-file", str(f), "--actor", "alice"])
    assert r.exit_code != 0  # неполная → блок


# --- list / show / resolve ---

def test_list_and_resolve(tmp_path, engine):
    f = tmp_path / "b.md"
    f.write_text(_FULL_BUG, encoding="utf-8")
    add = _json(runner.invoke(app, ["--json", "issue", "add", "--kind", "bug",
                                    "--title", "ListMe", "--body-file", str(f), "--actor", "alice"]))
    rows = _json(runner.invoke(app, ["--json", "issue", "list"]))
    assert any(x["ref"] == add["ref"] for x in rows)
    r = runner.invoke(app, ["--json", "issue", "resolve", add["ref"]])
    assert r.exit_code == 0 and _json(r)["status"] == "resolved"


# --- task handoff (the multi-agent core) ---

def test_handoff_full_reassigns_and_creates_issue(tmp_path, engine):
    f = tmp_path / "h.md"
    f.write_text(_FULL_HANDOFF, encoding="utf-8")
    r = runner.invoke(app, ["--json", "task", "handoff", "1", "--to", "bob",
                            "--body-file", str(f), "--actor", "alice"])
    assert r.exit_code == 0, r.stdout
    d = _json(r)
    assert d["to"] == "bob" and d["task"] == 1
    with make_session(engine) as s:
        task = s.execute(select(Task).where(Task.number == 1)).scalar_one()
        bob = s.execute(select(Participant).where(Participant.slug == "bob")).scalar_one()
        assert task.assignee_id == bob.id  # переназначена
        issue = s.execute(select(Issue).where(Issue.kind == "handoff")).scalar_one()
        assert issue.task_id == task.id and issue.target_id == bob.id


def test_handoff_incomplete_blocked(tmp_path, engine):
    f = tmp_path / "h.md"
    f.write_text("# Передача\n## Что сделано\nкое-что", encoding="utf-8")
    r = runner.invoke(app, ["task", "handoff", "1", "--to", "bob",
                            "--body-file", str(f), "--actor", "alice"])
    assert r.exit_code != 0  # неполная передача → блок
    with make_session(engine) as s:
        # задача НЕ переназначена (handoff не прошёл)
        cnt = s.execute(select(Issue)).scalars().all()
        assert len(cnt) == 0
