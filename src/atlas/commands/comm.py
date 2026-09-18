"""CLI `atlas comm ...` — точки коммуникации: встречи, созвоны, заметки.

Отвечает на вопрос, которого база раньше не понимала: **что вообще было по
этому клиенту**. Задачи и решения хранились, а разговор, из которого они
родились, оставался файлом в папке или сообщением в чате — и каждая новая сессия
начинала с чистого листа.

Три уровня, потому что одна встреча кормит несколько проектов:

* коммуникация — сам разговор, с путём к файлу расшифровки;
* кусок — часть разговора, отнесённая к проекту (их у проекта может быть
  несколько: тему бросают и возвращаются к ней);
* факт — решение, срок, риск или задача, вынутые из куска, с дословной цитатой.

Текст в базу не переносится: он живёт в файле рядом с проектом, а база держит
структуру и путь. Цитата у факта — чтобы разбор можно было проверить поиском по
исходнику, не спрашивая человека.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from clikit import CliError, command, emit_data
from sqlalchemy import func, or_, select

from atlas.db import make_engine, make_session, resolve_db_url
from atlas.models import (
    Communication,
    CommunicationFact,
    CommunicationPart,
    Counterparty,
    Project,
    Task,
)

comm_app = typer.Typer(
    help="Точки коммуникации: встречи, созвоны, переписка, заметки.", no_args_is_help=True
)

KINDS = ("meeting", "call", "chat", "voice_note", "presentation")
SIDES = ("internal", "client")
FACT_KINDS = ("status", "decision", "checkpoint", "risk", "task", "open")


def _session():
    return make_session(make_engine(resolve_db_url()))


def _project_or_die(session, ref: str) -> Project:
    found = session.execute(
        select(Project).where(Project.slug == ref)
    ).scalar_one_or_none() or session.get(Project, ref)
    if found is None:
        raise CliError("not_found", f"Проект '{ref}' не найден.")
    return found


def _comm_or_die(session, ref: str) -> Communication:
    found = session.get(Communication, ref)
    if found is None:
        # По заголовку — чтобы не заставлять человека носить с собой uuid.
        found = session.execute(
            select(Communication).where(Communication.title.like(f"%{ref}%"))
        ).scalars().first()
    if found is None:
        raise CliError("not_found", f"Коммуникация '{ref}' не найдена.")
    return found


def _view(session, c: Communication, с_кусками: bool = False) -> dict:
    куски = session.execute(
        select(CommunicationPart).where(CommunicationPart.communication_id == c.id)
        .order_by(CommunicationPart.position)
    ).scalars().all()
    данные = {
        "id": c.id, "kind": c.kind, "side": c.side,
        "occurred_at": c.occurred_at.isoformat() if c.occurred_at else None,
        "title": c.title, "summary": c.summary, "source_path": c.source_path,
        "status": c.status, "parts": len(куски),
    }
    if not с_кусками:
        return данные
    проекты = {p.id: p.slug for p in session.execute(select(Project)).scalars()}
    данные["part_list"] = []
    for ч in куски:
        факты = session.execute(
            select(CommunicationFact).where(CommunicationFact.part_id == ч.id)
        ).scalars().all()
        данные["part_list"].append({
            "id": ч.id, "project": проекты.get(ч.project_id), "subject": ч.subject,
            "tiles": [ч.tile_from, ч.tile_to],
            "facts": [{"id": f.id, "kind": f.fact_kind, "text": f.text, "quote": f.quote,
                       "owner": f.owner, "due": f.due, "task_id": f.task_id}
                      for f in факты],
        })
    return данные


@comm_app.command("add")
@command
def comm_add(
    title: str = typer.Option(..., "--title", help="о чём разговор"),
    kind: str = typer.Option("meeting", "--kind", help="|".join(KINDS)),
    side: str = typer.Option("client", "--side", help="|".join(SIDES)),
    occurred: Optional[str] = typer.Option(None, "--date", help="когда (ГГГГ-ММ-ДД), по умолчанию сегодня"),
    source: Optional[str] = typer.Option(None, "--source", help="путь к файлу расшифровки"),
    counterparty: Optional[str] = typer.Option(None, "--counterparty", help="слаг контрагента"),
    summary: Optional[str] = typer.Option(None, "--summary"),
) -> None:
    """Завести точку коммуникации."""
    if kind not in KINDS:
        raise CliError("bad_kind", f"kind: {', '.join(KINDS)}")
    if side not in SIDES:
        raise CliError("bad_side", f"side: {', '.join(SIDES)}")
    with _session() as session:
        cp = None
        if counterparty:
            cp = session.execute(
                select(Counterparty).where(Counterparty.slug == counterparty)
            ).scalar_one_or_none()
            if cp is None:
                raise CliError("not_found", f"Контрагент '{counterparty}' не найден.")
        c = Communication(
            kind=kind, side=side, title=title, summary=summary,
            source_path=source, counterparty_id=cp.id if cp else None,
            occurred_at=datetime.fromisoformat(occurred) if occurred else datetime.now(),
        )
        session.add(c)
        session.commit()
        emit_data(_view(session, c))


@comm_app.command("part")
@command
def comm_part(
    comm: str = typer.Argument(..., help="id или часть заголовка коммуникации"),
    project: Optional[str] = typer.Option(None, "--project", help="слаг проекта"),
    subject: Optional[str] = typer.Option(None, "--subject", help="как назвали вслух"),
    tiles: Optional[str] = typer.Option(None, "--tiles", help="границы в расшифровке, «120-194»"),
    body: Optional[str] = typer.Option(None, "--body"),
) -> None:
    """Добавить кусок коммуникации и отнести его к проекту."""
    with _session() as session:
        c = _comm_or_die(session, comm)
        p = _project_or_die(session, project) if project else None
        a = b = None
        if tiles:
            куски = tiles.replace("T", "").split("-")
            a = int(куски[0])
            b = int(куски[-1])
        сколько = session.execute(
            select(func.count()).select_from(CommunicationPart)
            .where(CommunicationPart.communication_id == c.id)
        ).scalar_one()
        ч = CommunicationPart(
            communication_id=c.id, project_id=p.id if p else None, subject=subject,
            tile_from=a, tile_to=b, body=body, position=сколько,
        )
        session.add(ч)
        session.commit()
        emit_data({"id": ч.id, "communication": c.id, "project": p.slug if p else None,
                   "subject": subject, "tiles": [a, b]})


@comm_app.command("fact")
@command
def comm_fact(
    part: str = typer.Argument(..., help="id куска"),
    kind: str = typer.Option(..., "--kind", help="|".join(FACT_KINDS)),
    text: str = typer.Option(..., "--text"),
    quote: Optional[str] = typer.Option(None, "--quote", help="дословно, как прозвучало"),
    owner: Optional[str] = typer.Option(None, "--owner"),
    due: Optional[str] = typer.Option(None, "--due"),
) -> None:
    """Записать факт, вынутый из куска: решение, срок, риск, задачу."""
    if kind not in FACT_KINDS:
        raise CliError("bad_kind", f"kind: {', '.join(FACT_KINDS)}")
    with _session() as session:
        ч = session.get(CommunicationPart, part)
        if ч is None:
            raise CliError("not_found", f"Кусок '{part}' не найден.")
        f = CommunicationFact(part_id=ч.id, fact_kind=kind, text=text, quote=quote,
                              owner=owner, due=due)
        session.add(f)
        session.commit()
        emit_data({"id": f.id, "part": ч.id, "kind": kind, "text": text})


@comm_app.command("link")
@command
def comm_link(
    fact: str = typer.Argument(..., help="id факта"),
    task: str = typer.Option(..., "--task", help="номер или слаг задачи"),
) -> None:
    """Связать факт с заведённой задачей.

    Заполняется ТОЛЬКО когда задачу действительно завели: разбор задач не
    ставит, решение о постановке остаётся за человеком.
    """
    with _session() as session:
        f = session.get(CommunicationFact, fact)
        if f is None:
            raise CliError("not_found", f"Факт '{fact}' не найден.")
        t = None
        if task.isdigit():
            t = session.execute(
                select(Task).where(Task.number == int(task))
            ).scalar_one_or_none()
        if t is None:
            t = session.execute(
                select(Task).where(Task.slug == task)
            ).scalar_one_or_none() or session.get(Task, task)
        if t is None:
            raise CliError("not_found", f"Задача '{task}' не найдена.")
        f.task_id = t.id
        session.commit()
        emit_data({"fact": f.id, "task": t.number, "title": t.title})


@comm_app.command("list")
@command
def comm_list(
    project: Optional[str] = typer.Option(None, "--project", help="слаг проекта"),
    counterparty: Optional[str] = typer.Option(None, "--counterparty"),
    side: Optional[str] = typer.Option(None, "--side", help="|".join(SIDES)),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Что было: по проекту, по контрагенту или всё подряд, свежее первым."""
    with _session() as session:
        q = select(Communication).order_by(Communication.occurred_at.desc())
        if project:
            p = _project_or_die(session, project)
            свои = select(CommunicationPart.communication_id).where(
                CommunicationPart.project_id == p.id
            )
            q = q.where(Communication.id.in_(свои))
        if counterparty:
            cp = session.execute(
                select(Counterparty).where(Counterparty.slug == counterparty)
            ).scalar_one_or_none()
            if cp is None:
                raise CliError("not_found", f"Контрагент '{counterparty}' не найден.")
            q = q.where(Communication.counterparty_id == cp.id)
        if side:
            q = q.where(Communication.side == side)
        строки = session.execute(q.limit(limit)).scalars().all()
        emit_data([_view(session, c) for c in строки])


@comm_app.command("show")
@command
def comm_show(ref: str = typer.Argument(..., help="id или часть заголовка")) -> None:
    """Карточка коммуникации: куски по проектам и факты каждого."""
    with _session() as session:
        emit_data(_view(session, _comm_or_die(session, ref), с_кусками=True))


@comm_app.command("search")
@command
def comm_search(
    текст: str = typer.Argument(..., help="что искать"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Найти по тексту куска, заголовку или выжимке.

    Простой поиск подстрокой, без индекса: на портфеле в сотни коммуникаций
    этого достаточно, а семантический поиск живёт отдельным слоем.
    """
    шаблон = f"%{текст}%"
    with _session() as session:
        свои = select(CommunicationPart.communication_id).where(
            or_(CommunicationPart.body.like(шаблон), CommunicationPart.subject.like(шаблон))
        )
        q = select(Communication).where(
            or_(Communication.title.like(шаблон), Communication.summary.like(шаблон),
                Communication.id.in_(свои))
        ).order_by(Communication.occurred_at.desc()).limit(limit)
        emit_data([_view(session, c) for c in session.execute(q).scalars()])


@comm_app.command("import")
@command
def comm_import(
    файл: str = typer.Argument(..., help="digests.json из разбора встречи"),
    title: str = typer.Option(..., "--title", help="название встречи"),
    occurred: Optional[str] = typer.Option(None, "--date", help="когда (ГГГГ-ММ-ДД)"),
    source: Optional[str] = typer.Option(None, "--source", help="путь к расшифровке"),
    kind: str = typer.Option("meeting", "--kind"),
    side: str = typer.Option("internal", "--side"),
    map_file: Optional[str] = typer.Option(None, "--map", help="JSON: subject → слаг проекта"),
) -> None:
    """Загрузить готовый разбор встречи: куски по проектам и факты с цитатами.

    Разбор уже отдаёт ровно эти поля, поэтому загрузка — механика без модели.
    Соответствие «как назвали вслух → проект» берётся из карты: вывести «Стас» =
    «ХПЛ — Питер» из текста нельзя, это знание человека.
    """
    блоки = json.loads(Path(файл).read_text(encoding="utf-8"))
    карта = json.loads(Path(map_file).read_text(encoding="utf-8")) if map_file else {}
    ПОЛЯ = (("status", "status"), ("decisions", "decision"), ("checkpoints", "checkpoint"),
            ("risks", "risk"), ("open", "open"))
    with _session() as session:
        c = Communication(
            kind=kind, side=side, title=title, source_path=source,
            occurred_at=datetime.fromisoformat(occurred) if occurred else datetime.now(),
        )
        session.add(c)
        session.flush()
        кусков = фактов = 0
        for i, b in enumerate(блоки):
            subject = (b.get("subject") or b.get("name") or "").strip()
            slug = b.get("project") or карта.get(subject)
            p = None
            if slug:
                p = session.execute(
                    select(Project).where(Project.slug == slug)
                ).scalar_one_or_none()
            части = b.get("parts") or [{"from": b.get("from"), "to": b.get("to")}]
            ч = CommunicationPart(
                communication_id=c.id, project_id=p.id if p else None, subject=subject,
                tile_from=части[0].get("from"), tile_to=части[-1].get("to"),
                body=b.get("summary"), position=i,
            )
            session.add(ч)
            session.flush()
            кусков += 1
            цитаты = [q for q in (b.get("quotes") or []) if isinstance(q, str)]
            for поле, вид in ПОЛЯ:
                значение = b.get(поле)
                for текст in ([значение] if isinstance(значение, str) else (значение or [])):
                    if not текст:
                        continue
                    session.add(CommunicationFact(
                        part_id=ч.id, fact_kind=вид, text=текст,
                        quote=цитаты[0] if цитаты else None))
                    фактов += 1
            for t in (b.get("tasks") or []):
                session.add(CommunicationFact(
                    part_id=ч.id, fact_kind="task", text=t.get("text", ""),
                    quote=цитаты[0] if цитаты else None,
                    owner=t.get("owner"), due=t.get("due")))
                фактов += 1
        session.commit()
        emit_data({"communication": c.id, "title": c.title,
                   "parts": кусков, "facts": фактов})
