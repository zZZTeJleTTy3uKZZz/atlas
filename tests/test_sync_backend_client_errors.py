"""BackendClient × ErrorMap: HTTP-коды хаба → доменные ошибки librarykit.

Choke-point ``HttpClient`` мапит не-2xx ответ в доменное исключение ещё внутри
клиента (``DEFAULT_ERROR_MAP``): 401→SessionExpired, 403→Blocked, 404→NotFound,
429→RateLimited, 5xx→ServerError. Проверяем, что эти исключения всплывают из
публичных методов ``BackendClient``.

Для детерминизма и скорости 429-кейс ходит через клиент с ``RetryPolicy(total=0)``
(без повторов — иначе транспорт ретраит 429 и ждёт backoff).
"""
import pytest
from librarykit.auth import TokenAuth
from librarykit.errmap import DEFAULT_ERROR_MAP
from librarykit.errors import NotFound, RateLimited, SessionExpired
from librarykit.retry import RetryPolicy
from librarykit.transport import HttpClient, HttpxTransport

from atlas.pm.sync.backend_client import BackendClient


def _no_retry_client(base_url: str, api_key: str) -> HttpClient:
    """Choke-point без повторов (``total=0``) — 429/5xx не ретраятся."""
    transport = HttpxTransport(base_url, retry=RetryPolicy(total=0))
    auth = TokenAuth(api_key, scheme="", header="X-API-Key")
    return HttpClient(transport, auth, DEFAULT_ERROR_MAP)


async def test_404_raises_not_found(httpx_mock):
    httpx_mock.add_response(method="PATCH", status_code=404, json={"detail": "no"})
    client = BackendClient("http://hub", "k")
    with pytest.raises(NotFound):
        await client.patch_project("missing", visibility="personal")
    await client.aclose()


async def test_401_raises_session_expired(httpx_mock):
    # DEFAULT_ERROR_MAP мапит 401 → SessionExpired (роль AuthExpired).
    httpx_mock.add_response(method="GET", status_code=401, json={"detail": "bad key"})
    client = BackendClient("http://hub", "wrong")
    with pytest.raises(SessionExpired):
        await client.poll_events(None, timeout=1.0)
    await client.aclose()


async def test_429_raises_rate_limited(httpx_mock):
    httpx_mock.add_response(
        method="POST", status_code=429, headers={"Retry-After": "1"}, json={"detail": "slow"}
    )
    client = BackendClient("http://hub", "k", client=_no_retry_client("http://hub", "k"))
    with pytest.raises(RateLimited):
        await client.push_events([{"op": "create", "entity_kind": "task"}])
    await client.aclose()
