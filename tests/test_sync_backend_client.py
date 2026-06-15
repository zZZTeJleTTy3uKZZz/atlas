"""F3a: BackendClient — клиент к backend-хабу (X-API-Key) поверх clikit.HttpClient."""
import pytest

from atlas.pm.sync.backend_client import BackendClient


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
