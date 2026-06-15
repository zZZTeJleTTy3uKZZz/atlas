"""F3g: watch_loop — устойчивый цикл pull (ошибки не валят, retry с backoff)."""
import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from atlas.pm.models import Base
from atlas.pm.sync import pull


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'w.db'}")
    Base.metadata.create_all(eng)
    return eng


class _Flaky:
    """poll_events: 1-й раз бросает (сеть), потом пусто; на 4-м — Cancelled (стоп)."""
    def __init__(self):
        self.n = 0

    async def poll_events(self, since=None, *, timeout=25.0, scope="all"):
        self.n += 1
        if self.n == 1:
            raise RuntimeError("network down")
        if self.n >= 4:
            raise asyncio.CancelledError
        return {"events": [], "cursor": None}

    async def aclose(self):
        pass


async def test_watch_loop_retries_on_error(engine):
    results = []
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    with pytest.raises(asyncio.CancelledError):
        await pull.watch_loop(
            engine, _Flaky(), timeout=0.1,
            on_result=results.append, _sleep=fake_sleep,
        )
    # была хотя бы одна ошибка-результат и хотя бы один backoff-sleep
    assert any("error" in r for r in results)
    assert sleeps and sleeps[0] >= 1.0
    # после ошибки цикл продолжился (были и успешные pull-результаты)
    assert any("applied" in r for r in results)
