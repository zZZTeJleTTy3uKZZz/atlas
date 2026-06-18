"""HubService — фасад к backend-хабу: команды зовут сервис, не клиента."""
import pytest

import atlas.pm.sync.hub_service as hs


@pytest.mark.asyncio
async def test_hub_service_delegates_and_closes(monkeypatch):
    closed = {"v": False}
    calls = []

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def provision_project(self, **kw):
            calls.append(("provision", kw))
            return {"backend_id": "core-1", "notion_page_id": "np-1"}

        async def patch_project(self, slug, **kw):
            calls.append(("patch", slug, kw))
            return {"slug": slug}

        async def aclose(self):
            closed["v"] = True

    monkeypatch.setattr(hs, "BackendClient", _FakeClient)
    svc = hs.HubService("http://t", "KEY")
    assert svc.enabled is True

    res = await svc.provision_project(slug="x", name="X")
    assert res["backend_id"] == "core-1"
    await svc.patch_project("x", visibility="personal")

    assert closed["v"] is True          # клиент закрыт сервисом
    assert calls[0][0] == "provision"
    assert calls[1][0] == "patch"


def test_hub_service_disabled_without_key():
    svc = hs.HubService("http://t", "")
    assert svc.enabled is False
