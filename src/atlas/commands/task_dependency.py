"""CLI `atlas task depends ...` — задача ждёт другую задачу.

Порядок работ до сих пор жил в голове, и выяснялся он в момент, когда
исполнитель уже взял задачу и упёрся: потерянное время плюс занятая ёмкость.
Внутри одного проекта порядок ещё виден по эпику; настоящая беда — когда задача
клиентского проекта ждёт задачу в ките, и об этом знает только тот, кто заводил
обе.

Что даёт связь:

* **`task start` отказывает**, пока блокирующая задача не закрыта. Не
  предупреждает — именно отказывает: предупреждение, которое можно
  проигнорировать, проигнорируют, и мы вернёмся к порядку в голове;
* **сверка показывает** ждущие задачи отдельной группой — их не надо
  распределять, надо разблокировать.

Причина связи обязательна по смыслу и потому спрашивается флагом: через месяц
«А ждёт Б» без причины невозможно проверить на актуальность, и связь живёт
вечно, даже когда ждать уже нечего.
"""
from __future__ import annotations

from typing import Optional

import typer
from clikit import CliError, command, emit_data
from sqlalchemy import select

from atlas.db import make_engine, make_session, resolve_db_url
from atlas.commands.task import task_app
from atlas.models import Project, Task, TaskDependency

task_depends_app = typer.Typer(
    help="Зависимости задач: что кого ждёт.", no_args_is_help=True
)

#: Статусы, в которых задача уже никого не держит.
ЗАКРЫТЫЕ = ("done", "cancelled")


def _resolve_task(session, ref: str) -> Task:
    """Задача по номеру, слагу или UUID. Понятный отказ вместо пустоты."""
    ref = str(ref).strip()
    задача = None
    if ref.isdigit():
        задача = session.scalar(select(Task).where(Task.number == int(ref)))
    if задача is None:
        задача = session.scalar(select(Task).where(Task.slug == ref))
    if задача is None:
        задача = session.scalar(select(Task).where(Task.id == ref))
    if задача is None:
        raise CliError("task_not_found", f"задача «{ref}» не найдена")
    return задача


def _карточка(session, задача: Task) -> dict:
    проект = session.get(Project, задача.project_id) if задача.project_id else None
    return {
        "number": задача.number,
        "title": задача.title,
        "status": задача.status,
        "project": проект.slug if проект else None,
    }


def _цикл(session, task_id: str, depends_on_id: str) -> list[int]:
    """Приведёт ли новая связь к кругу ожидания. Возвращает путь номеров.

    Круг — не теоретическая беда: две задачи, ждущие друг друга, обе перестают
    браться в работу, и понять почему, глядя на каждую по отдельности,
    невозможно.
    """
    цель = task_id
    путь: list[str] = [depends_on_id]
    посещённые = {depends_on_id}
    очередь = [depends_on_id]

    предки: dict[str, str] = {}
    while очередь:
        текущий = очередь.pop()
        строки = session.scalars(
            select(TaskDependency).where(TaskDependency.task_id == текущий)
        ).all()
        for строка in строки:
            следующий = строка.depends_on_id
            if следующий in посещённые:
                continue
            предки[следующий] = текущий
            if следующий == цель:
                # Разворачиваем путь от цели обратно к началу.
                узел, обратно = следующий, [следующий]
                while узел in предки:
                    узел = предки[узел]
                    обратно.append(узел)
                путь = list(reversed(обратно))
                return [
                    session.get(Task, u).number for u in путь if session.get(Task, u)
                ]
            посещённые.add(следующий)
            очередь.append(следующий)
    return []


def блокирующие(session, задача: Task) -> list[dict]:
    """Незакрытые задачи, которых ждёт эта. Пусто — путь свободен."""
    связи = session.scalars(
        select(TaskDependency).where(TaskDependency.task_id == задача.id)
    ).all()
    итог: list[dict] = []
    for связь in связи:
        держатель = session.get(Task, связь.depends_on_id)
        if держатель is None:
            # Висячая ссылка: задачу удалили. Это не блокировка — держать
            # работу из-за исчезнувшей причины хуже, чем не держать вовсе.
            continue
        if держатель.status in ЗАКРЫТЫЕ:
            continue
        карточка = _карточка(session, держатель)
        карточка["reason"] = связь.reason
        итог.append(карточка)
    return итог


@task_depends_app.command("add")
@command
def add_cmd(
    ref: str = typer.Argument(..., help="задача, которая ЖДЁТ"),
    on: str = typer.Option(..., "--on", help="задача, которую ЖДУТ"),
    reason: Optional[str] = typer.Option(
        None, "--reason", help="зачем ждём — одной фразой"
    ),
) -> None:
    """Объявить, что задача ждёт другую."""
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        ждущая = _resolve_task(session, ref)
        держатель = _resolve_task(session, on)

        if ждущая.id == держатель.id:
            raise CliError("self_dependency", "задача не может ждать саму себя")

        уже = session.scalar(
            select(TaskDependency).where(
                TaskDependency.task_id == ждущая.id,
                TaskDependency.depends_on_id == держатель.id,
            )
        )
        if уже is not None:
            raise CliError(
                "dependency_exists",
                f"#{ждущая.number} уже ждёт #{держатель.number}",
            )

        круг = _цикл(session, ждущая.id, держатель.id)
        if круг:
            raise CliError(
                "dependency_cycle",
                "связь замкнёт круг ожидания: "
                + " → ".join(f"#{н}" for н in круг)
                + f" → #{ждущая.number}. Обе задачи перестали бы браться в работу, "
                "а причина по отдельности не видна",
            )

        session.add(
            TaskDependency(
                task_id=ждущая.id, depends_on_id=держатель.id, reason=reason
            )
        )
        session.commit()
        данные = {
            "action": "linked",
            "task": _карточка(session, ждущая),
            "depends_on": _карточка(session, держатель),
            "reason": reason,
        }
    emit_data(данные)


@task_depends_app.command("remove")
@command
def remove_cmd(
    ref: str = typer.Argument(..., help="задача, которая ждала"),
    on: str = typer.Option(..., "--on", help="задача, которую ждали"),
) -> None:
    """Снять зависимость: ждать больше нечего."""
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        ждущая = _resolve_task(session, ref)
        держатель = _resolve_task(session, on)
        связь = session.scalar(
            select(TaskDependency).where(
                TaskDependency.task_id == ждущая.id,
                TaskDependency.depends_on_id == держатель.id,
            )
        )
        if связь is None:
            raise CliError(
                "dependency_not_found",
                f"#{ждущая.number} не ждёт #{держатель.number}",
            )
        session.delete(связь)
        session.commit()
        данные = {"action": "unlinked", "task": ждущая.number,
                  "depends_on": держатель.number}
    emit_data(данные)


@task_depends_app.command("list")
@command
def list_cmd(
    ref: Optional[str] = typer.Argument(None, help="задача; без неё — все связи"),
) -> None:
    """Кого ждёт задача и кто ждёт её.

    Обе стороны сразу: «кого я жду» отвечает на «почему стою», «кто ждёт меня» —
    на «кого я задерживаю». Второе видно только отсюда, и именно оно объясняет,
    почему закрыть эту задачу важнее, чем кажется.
    """
    engine = make_engine(resolve_db_url())
    with make_session(engine) as session:
        if ref is None:
            связи = session.scalars(select(TaskDependency)).all()
            данные = []
            for связь in связи:
                ждущая = session.get(Task, связь.task_id)
                держатель = session.get(Task, связь.depends_on_id)
                if ждущая is None or держатель is None:
                    continue
                данные.append({
                    "task": _карточка(session, ждущая),
                    "depends_on": _карточка(session, держатель),
                    "reason": связь.reason,
                    "resolved": держатель.status in ЗАКРЫТЫЕ,
                })
            emit_data(данные)
            return

        задача = _resolve_task(session, ref)
        ждёт = []
        for связь in session.scalars(
            select(TaskDependency).where(TaskDependency.task_id == задача.id)
        ).all():
            держатель = session.get(Task, связь.depends_on_id)
            if держатель is None:
                continue
            карточка = _карточка(session, держатель)
            карточка["reason"] = связь.reason
            карточка["resolved"] = держатель.status in ЗАКРЫТЫЕ
            ждёт.append(карточка)

        ждут_её = []
        for связь in session.scalars(
            select(TaskDependency).where(TaskDependency.depends_on_id == задача.id)
        ).all():
            ждущая = session.get(Task, связь.task_id)
            if ждущая is None:
                continue
            карточка = _карточка(session, ждущая)
            карточка["reason"] = связь.reason
            ждут_её.append(карточка)

        данные = {
            "task": _карточка(session, задача),
            "waits_for": ждёт,
            "blocks": ждут_её,
        }
    emit_data(данные)


# Суб-ресурс задачи: `atlas task depends ...`. Подключаем здесь, как это делают
# checklist и member, — иначе точка входа знает про каждый суб-ресурс поимённо.
task_app.add_typer(task_depends_app, name="depends")

__all__ = ["task_depends_app", "блокирующие"]
