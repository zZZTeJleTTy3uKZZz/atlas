"""Курсор pull-канала (SyncCursor).

С волны 7 (Atlas #2718) курсор — МОНОТОННЫЙ НОМЕР события, а не ISO-метка
времени. Причина в спеке волны и проверяется её же сценарием: пятьсот событий
с одинаковой меткой времени по метке неразличимы, и выборка `occurred_at >
since` отдаёт из них одно. Номер различает все пятьсот.

В базе поле осталось строкой (`sync_cursors.cursor`), и это не небрежность:
старый курсор-метка уже лежит у живых сторов, менять тип колонки ради этого
незачем, а `replicationkit.parse_cursor` читает нечисловое значение как «начать
сначала» — канал переприменит хвост (это безопасно: применение идемпотентно) и
дальше поедет по номерам.
"""
from __future__ import annotations

from replicationkit.cursor import CallableCursorStore
from sqlalchemy.orm import Session

from atlas.models import SyncCursor


def get_cursor(session: Session, channel: str) -> str | None:
    sc = session.get(SyncCursor, channel)
    return sc.cursor if sc is not None else None


def set_cursor(session: Session, channel: str, value: str | None) -> None:
    sc = session.get(SyncCursor, channel)
    if sc is None:
        sc = SyncCursor(channel=channel, cursor=value)
        session.add(sc)
    else:
        sc.cursor = value


def store(session: Session) -> CallableCursorStore:
    """Порт кита доставки поверх таблицы `sync_cursors`.

    Перенос курсора в файл на диске (как у клиента навыков) здесь был бы шагом
    назад: у Atlas курсор обязан фиксироваться ТОЙ ЖЕ транзакцией, что и
    применённые события, — иначе падение между коммитом данных и записью файла
    оставит их рассинхронизированными. Общая механика продвижения при этом та
    же самая, она в ките.
    """
    return CallableCursorStore(
        lambda channel: get_cursor(session, channel),
        lambda channel, value: set_cursor(session, channel, value),
    )


__all__ = ["get_cursor", "set_cursor", "store"]
