"""BackendClient.provision_project/import_from_b24/patch/link/unlink — через httpx_mock.

После миграции транспорта на adapterkit/librarykit клиент ходит реальным httpx
(перехват ``httpx_mock``), а не duck-фейком с ``.post/.patch/.delete``. Проверяем
URL/метод/тело + заголовок ``X-API-Key`` и пост-обработку возвратов.
"""
import json

import pytest

from atlas.pm.sync.backend_client import BackendClient


async def test_provision_project_posts_and_returns_ids(httpx_mock):
    httpx_mock.add_response(
        method="POST", url="http://t/api/v1/projects",
        json=[{"id": "core-1", "notion_page_id": "np-1"}],
    )
    c = BackendClient("http://t", "KEY")
    res = await c.provision_project(
        slug="mediyka", name="Медийка", kind="direction",
        owner_slug="me", lead_slug="dmitry", visibility="personal",
        notion_kind="личный", sync_target_slugs=["notion-pragmat", "atlas-dmitry"],
    )
    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert str(req.url) == "http://t/api/v1/projects"
    assert body["slug"] == "mediyka"
    assert body["lead_slug"] == "dmitry"
    assert body["owner_slug"] == "me"
    assert body["visibility"] == "personal"
    assert body["provision_notion"] is True
    assert body["notion_kind"] == "личный"
    assert body["sync_target_slugs"] == ["notion-pragmat", "atlas-dmitry"]
    assert req.headers["X-API-Key"] == "KEY"
    assert res == {"backend_id": "core-1", "notion_page_id": "np-1"}
    await c.aclose()


async def test_import_from_b24_posts_and_returns(httpx_mock):
    httpx_mock.add_response(
        method="POST", url="http://t/api/v1/projects/import-from-b24",
        json=[{"id": "core-1", "notion_page_id": "np-1"}],
    )
    c = BackendClient("http://t", "K")
    res = await c.import_from_b24(group_id=99, notion_kind="клиентский",
                                 lead_slug="dmitry", sync_target_slugs=["b24-exs"])
    req = httpx_mock.get_request()
    body = json.loads(req.content)
    assert str(req.url) == "http://t/api/v1/projects/import-from-b24"
    assert body["group_id"] == 99
    assert body["notion_kind"] == "клиентский"
    assert body["lead_slug"] == "dmitry"
    assert body["sync_target_slugs"] == ["b24-exs"]
    assert res["backend_id"] == "core-1"
    assert res["notion_page_id"] == "np-1"
    await c.aclose()


async def test_patch_link_unlink_project(httpx_mock):
    httpx_mock.add_response(
        method="PATCH", url="http://t/api/v1/projects/mediyka",
        json={"slug": "mediyka", "visibility": "personal"},
    )
    httpx_mock.add_response(
        method="POST", url="http://t/api/v1/projects/mediyka/links", json={"ok": True}
    )
    httpx_mock.add_response(
        method="DELETE", url="http://t/api/v1/projects/mediyka/links/b24-exs",
        json={"removed": 1},
    )
    c = BackendClient("http://t", "K")
    await c.patch_project("mediyka", visibility="personal", owner_slug="me", lead_slug="dmitry")
    await c.link_project("mediyka", portal_slug="notion-pragmat", external_id="np-1")
    await c.unlink_project("mediyka", portal_slug="b24-exs")

    reqs = httpx_mock.get_requests()
    methods = {(r.method, str(r.url)) for r in reqs}
    assert ("PATCH", "http://t/api/v1/projects/mediyka") in methods
    assert ("POST", "http://t/api/v1/projects/mediyka/links") in methods
    assert ("DELETE", "http://t/api/v1/projects/mediyka/links/b24-exs") in methods
    # тело PATCH несёт переданные поля; X-API-Key проставлен авторизацией
    patch_req = next(r for r in reqs if r.method == "PATCH")
    assert json.loads(patch_req.content) == {
        "visibility": "personal", "owner_slug": "me", "lead_slug": "dmitry"
    }
    assert patch_req.headers["X-API-Key"] == "K"
    await c.aclose()
