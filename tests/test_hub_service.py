"""HubService — фасад к внешнему backend-сервису: команды зовут сервис, не клиента."""
import pytest

import atlas.sync.hub_service as hs


@pytest.mark.asyncio
async def test_hub_service_delegates_and_closes(monkeypatch):
    closed = {"v": False}
    calls = []

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def patch_project(self, slug, **kw):
            calls.append(("patch", slug, kw))
            return {"slug": slug}

        async def aclose(self):
            closed["v"] = True

    monkeypatch.setattr(hs, "BackendClient", _FakeClient)
    svc = hs.HubService("http://t", "KEY")
    assert svc.enabled is True

    res = await svc.patch_project("x", visibility="personal")
    assert res["slug"] == "x"

    assert closed["v"] is True          # клиент закрыт сервисом per-call
    assert calls[0][0] == "patch"


def test_hub_service_disabled_without_key():
    svc = hs.HubService("http://t", "")
    assert svc.enabled is False
