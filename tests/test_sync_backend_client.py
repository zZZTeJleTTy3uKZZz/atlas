"""F3a: BackendClient — клиент к внешнему backend-сервису (X-API-Key) поверх adapterkit/librarykit."""
import pytest

from atlas.sync.backend_client import BackendClient


async def test_push_events_sends_api_key(httpx_mock):
    httpx_mock.add_response(
        method="POST", url="http://hub/api/v1/events", json={"accepted": 1}
    )
    client = BackendClient("http://hub", "secret123")
    result = await client.push_events([{"op": "create", "entity_kind": "task"}])
    assert result == {"accepted": 1}
    req = httpx_mock.get_request()
    assert req.headers["X-API-Key"] == "secret123"
    await client.aclose()


async def test_poll_events_passes_since_and_timeout(httpx_mock):
    httpx_mock.add_response(method="GET", json={"events": [], "cursor": None})
    client = BackendClient("http://hub", "k")
    result = await client.poll_events("2026-06-14T00:00:00", timeout=5.0)
    assert result == {"events": [], "cursor": None}
    url = str(httpx_mock.get_request().url)
    assert "since=2026" in url
    assert "timeout=5" in url
    await client.aclose()


async def test_poll_events_without_since_omits_param(httpx_mock):
    httpx_mock.add_response(method="GET", json={"events": [], "cursor": None})
    client = BackendClient("http://hub", "k")
    await client.poll_events(None, timeout=1.0)
    url = str(httpx_mock.get_request().url)
    assert "since=" not in url
    await client.aclose()


async def test_register_profile_posts_admin_key_and_body(httpx_mock):
    httpx_mock.add_response(
        method="POST", url="http://hub/api/v1/admin/profiles",
        json={"member_slug": "owner", "portal_slug": "atlas-admin", "api_key": "k2"},
    )
    client = BackendClient("http://hub", "adminsecret")
    result = await client.register_profile("owner", "atlas-admin", "Админ", "all")
    assert result["api_key"] == "k2" and result["portal_slug"] == "atlas-admin"
    req = httpx_mock.get_request()
    assert req.headers["X-API-Key"] == "adminsecret"
    import json
    body = json.loads(req.content)
    # тело строго по контракту ядра ProfileIn — member_slug + portal_slug раздельны
    assert body == {"member_slug": "owner", "portal_slug": "atlas-admin",
                    "name": "Админ", "scope": "all"}
    await client.aclose()


async def test_register_profile_includes_global_role_when_set(httpx_mock):
    httpx_mock.add_response(
        method="POST", url="http://hub/api/v1/admin/profiles",
        json={"member_slug": "p", "portal_slug": "p", "api_key": "k"},
    )
    client = BackendClient("http://hub", "s")
    await client.register_profile("p", "p", "Имя", "personal", global_role="executor")
    import json
    body = json.loads(httpx_mock.get_request().content)
    assert body["global_role"] == "executor"
    assert body["scope"] == "personal"
    await client.aclose()
