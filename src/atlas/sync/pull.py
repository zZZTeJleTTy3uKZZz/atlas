"""Входящий синк (хаб → Atlas): poll → apply → курсор ПОСЛЕ применения.

Механика продвижения курсора живёт в ките доставки
(`replicationkit.stream.apply_stream`), здесь остаётся только перевод: событие
хаба → конверт кита, конверт → `apply.apply_event`.

Что было до волны 7 (Atlas #2718). Курсор ставился ОДНИМ присваиванием после
цикла — значением из ответа сервера, то есть по батчу целиком. Пропуск
(`apply_event` штатно возвращает `{'skipped': …}`, когда задача приехала раньше
своего проекта, а пункт чек-листа раньше своей задачи) курсор не останавливал:
он уезжал за пропущенное событие, и переспросить его было уже нечем. Комментарий
в самом коде это признавал — «потеря событий была НЕВИДИМА» — и откладывал
буфер повторов. Буфер не понадобился: достаточно не двигать курсор дальше
первого непримененного, а сервер переотдаст хвост сам.
"""
from __future__ import annotations

from replicationkit.envelope import Envelope
from replicationkit.stream import apply_stream
from sqlalchemy.orm import Session

from atlas.sync import apply, cursor


def _to_envelope(ev: dict, fallback_seq: int) -> Envelope:
    """Событие хаба → конверт кита.

    ``seq`` берётся из события. ``fallback_seq`` нужен для СТАРОГО хаба, который
    номера ещё не проставляет: тогда порядком служит позиция в батче, сдвинутая
    на текущий курсор. Это не «тихая деградация», а честный переход: пока хаб не
    обновлён, канал работает как раньше, а как только номера появились — сам
    начинает считать по ним.
    """
    raw = ev.get("seq")
    try:
        seq = int(raw) if raw is not None else fallback_seq
    except (TypeError, ValueError):
        seq = fallback_seq
    return Envelope(
        seq=seq,
        envelope_id=str(ev.get("id") or ev.get("entity_id") or seq),
        kind=str(ev.get("entity_kind") or "unknown"),
        payload=ev,
    )


def _канал_хаба(channel: str) -> str:
    """Имя канала, под которым лежит курсор ХАБА (а не номер доставки).

    Отдельная строка, а не отдельная таблица: курсор обязан фиксироваться той же
    транзакцией, что и применённые события, и хранилище для этого уже есть.
    """
    return f"{channel}:hub"


def _курсор_хаба(session: Session, channel: str) -> str | None:
    """Хаб принимает только числовой ``seq``; старую ISO-метку сбрасываем.

    Старый курсор доставки в ``channel`` не переносим: он мог относиться к
    другому набору событий. Повторная идемпотентная загрузка безопаснее потери.
    """
    value = cursor.get_cursor(session, _канал_хаба(channel))
    return value if value and value.isdecimal() else None


ПОПЫТОК_ДО_КАРАНТИНА = 3
"""Сколько кругов событию дают на то, чтобы примениться.

Три, а не одна: событие часто не применяется временно — сначала должен приехать
проект, потом задача. Но и не бесконечно: без предела одно неприменимое событие
останавливает приём навсегда, потому что курсор не может его перешагнуть.
"""


def _учесть_пропуски(session: Session, channel: str, report, envelopes) -> set[str]:
    """Записать неудачные попытки и вернуть номера, которые уже в карантине.

    Карантинные события курсор не держат: их отложили осознанно, и они видны
    человеку в `atlas sync quarantine`.
    """
    from atlas.models import SyncQuarantine

    по_номеру = {env.seq: env for env in envelopes}
    причины = {д["seq"]: д.get("reason") for д in report.details}
    карантин: set[int] = set()

    for seq in report.pending:
        env = по_номеру.get(seq)
        if env is None:
            continue
        строка = session.get(SyncQuarantine, env.envelope_id)
        if строка is None:
            строка = SyncQuarantine(
                envelope_id=env.envelope_id,
                channel=channel,
                seq=seq,
                kind=env.kind,
                reason=str(причины.get(seq) or "")[:255],
                occurred_at=str(env.payload.get("occurred_at") or "") or None,
                attempts=1,
            )
            session.add(строка)
        else:
            строка.attempts += 1
            строка.reason = str(причины.get(seq) or строка.reason or "")[:255]
        if строка.attempts >= ПОПЫТОК_ДО_КАРАНТИНА:
            карантин.add(seq)

    # Успешно применённое из карантина уходит: недостающее могли завести.
    применённые = [env for env in envelopes if env.seq not in set(report.pending)]
    for env in применённые:
        строка = session.get(SyncQuarantine, env.envelope_id)
        if строка is not None:
            session.delete(строка)

    session.commit()
    return карантин


def _докуда_дошли(report, envelopes, карантин: set[int]) -> str | None:
    """Номер последнего события, за которое курсору можно уехать.

    Это последнее НЕПРЕРЫВНО применённое — плюс те карантинные, что идут сразу
    за ним: их мы больше не ждём, и держать из-за них курсор значило бы не
    двигаться никогда.
    """
    по_номеру = {env.seq: env for env in sorted(envelopes, key=lambda e: e.seq)}
    достигнуто = report.cursor
    отложенные = set(report.pending)
    for seq in sorted(по_номеру):
        if seq <= достигнуто:
            continue
        if seq in отложенные and seq not in карантин:
            break  # ещё ждём: за это событие уезжать рано
        достигнуто = seq
    return str(достигнуто) if достигнуто in по_номеру else None


async def pull_once(
    session: Session, client, *, channel: str = "atlas", timeout: float = 25.0,
    scope: str = "all",
) -> dict:
    """Один цикл: long-poll событий позже курсора → применить → продвинуть курсор.

    ``client`` — объект с async ``poll_events(since, *, timeout, scope)``
    (BackendClient), возвращающим ``{events: [...], cursor: str|None}``.
    ``scope`` — профиль видимости: ``all`` (все) | ``personal`` (мои задачи).
    → ``{applied, cursor}`` плюс ``skipped``/``pending``, если что-то отложено.
    """
    # ДВА РАЗНЫХ КУРСОРА, и путать их нельзя.
    #
    # Курсоры доставки и хаба разделены по каналам. У обоих теперь числовой
    # формат, но переносить один в другой нельзя: наборы событий различаются.
    since = _курсор_хаба(session, channel)
    resp = await client.poll_events(since, timeout=timeout, scope=scope)
    events = resp.get("events") or []
    store = cursor.store(session)
    base = store.get(channel)
    envelopes = [_to_envelope(ev, base + i) for i, ev in enumerate(events, start=1)]

    report = apply_stream(
        envelopes,
        lambda env: apply.apply_event(session, env.payload) or {},
        cursor_store=store,
        channel=channel,
        commit=session.commit,
    )
    # Курсор хаба двигается до последнего НЕПРЕРЫВНО применённого события —
    # ровно так же, как кит двигает свой номер доставки. Два соседних решения
    # были бы хуже:
    #
    #   * двигать до max(cursor из ответа) — пропущенное событие уедет за спину
    #     курсора и больше никогда не приедет, то есть потеряется молча;
    #   * не двигать вовсе, пока есть пропуски, — и одно неприменимое событие
    #     останавливает синк навсегда. Так и случилось: три задачи из проекта,
    #     которого нет на этой машине, держали курсор, и одни и те же 197
    #     событий применялись по кругу при каждом запуске.
    карантин = _учесть_пропуски(session, channel, report, envelopes)
    достигнутое = _докуда_дошли(report, envelopes, карантин)
    if достигнутое:
        cursor.set_cursor(session, _канал_хаба(channel), str(достигнутое))
        session.commit()
    elif resp.get("cursor") and not events:
        # Пустой круг: принимаем только числовой курсор нынешнего хаба.
        if str(resp["cursor"]).isdecimal():
            cursor.set_cursor(session, _канал_хаба(channel), str(resp["cursor"]))
            session.commit()

    out: dict = {
        "applied": report.applied,
        "cursor": _курсор_хаба(session, channel),
        "seq": cursor.get_cursor(session, channel),
    }
    if report.skipped:
        # Видимость потери: сколько отложено и КАКИЕ номера придётся переспросить.
        out["skipped"] = report.skipped
        out["pending"] = report.pending[:50]
        out["skipped_details"] = report.details[:10]
    if карантин:
        # Отдельно от skipped: эти события больше не ждут, и знать о них надо.
        out["quarantined"] = len(карантин)
    return out


async def watch_loop(
    engine, client, *, channel: str = "atlas", timeout: float = 25.0,
    scope: str = "all", on_result=None, max_backoff: float = 60.0, _sleep=None,
) -> None:
    """Бесконечный устойчивый цикл синка: сетевые/HTTP-ошибки НЕ валят цикл —
    логируются через on_result и ретраятся с экспоненциальным backoff
    (сброс при успехе). KeyboardInterrupt/CancelledError пробрасываются (стоп).

    ЦИКЛ ДВУСТОРОННИЙ. Сначала отдаём накопленное, потом слушаем чужое. Раньше
    здесь был только приём, и это был самый дорогой вид поломки — молчаливый:
    демон работал, лог рос, ошибок не было, а исходящая очередь копилась. Замер
    на живой машине 08.09.2026 — 530 записей в очереди при последней успешной
    отправке тремя днями раньше. Человек видел зелёный демон и был уверен, что
    его задачи уехали на второй компьютер.

    ПОЧЕМУ ОТДАЁМ ПЕРВЫМ. Приём применяет чужие изменения к нашей базе; если
    сначала принять, а потом отдать, наши несохранённые правки уедут поверх уже
    применённого чужого и разница между машинами станет заметна не сразу.
    Отдать своё до того, как принял чужое, — единственный порядок, при котором
    обе стороны видят одно и то же состояние.

    Отказ отправки НЕ мешает приёму: очередь переживает разрыв по построению
    (записи остаются `pending`), а вот пропущенный приём означает устаревшую
    базу прямо сейчас.
    """
    import asyncio

    from atlas.db import make_session
    from atlas.sync.push import push_pending

    sleep = _sleep or asyncio.sleep
    backoff = 1.0
    while True:
        try:
            отправлено = None
            try:
                with make_session(engine) as session:
                    отправлено = await push_pending(session, client)
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception as exc:  # noqa: BLE001 — отказ отдачи не мешает приёму
                отправлено = {"push_error": str(exc)}
            with make_session(engine) as session:
                result = await pull_once(session, client, channel=channel,
                                         timeout=timeout, scope=scope)
            if отправлено and отправлено.get("sent"):
                result = {**result, "sent": отправлено["sent"]}
            elif отправлено and отправлено.get("push_error"):
                result = {**result, "push_error": отправлено["push_error"]}
            backoff = 1.0
            if on_result is not None:
                on_result(result)
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001 — устойчивость важнее точечной обработки
            if on_result is not None:
                on_result({"error": str(exc), "retry_in": backoff})
            await sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


__all__ = ["pull_once", "watch_loop"]
