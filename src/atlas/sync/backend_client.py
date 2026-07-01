"""Доменный клиент к внешнему backend-сервису поверх adapterkit/librarykit.

Транспорт канона S-kits: ``HttpxTransport`` (httpx + RetryPolicy с header-driven
повторами 429/5xx) → ``HttpClient`` (choke-point: apply auth → transport → map
errors через ``DEFAULT_ERROR_MAP``) → ``BaseAdapter`` с декларативной
endpoint-таблицей. Auth — заголовком ``X-API-Key`` (``TokenAuth`` со ``scheme=""``):
ключ ставится автоматически на каждый запрос, per-call заголовки не нужны.

Доменные методы — GENERIC интерфейс к backend (без знания о внешних системах/
порталах): ``push_events`` (POST /events — backend сам решает фанаут наружу),
``poll_events`` (GET /events/poll — long-poll), ``register_profile``,
``patch_project`` (PATCH generic-полей). Маршрутизация в Notion/Б24/порталы,
provision и import — зона backend-сервиса, не CLI.

Ошибки HTTP мапятся в доменные исключения ``librarykit.errors`` ещё в choke-point
``HttpClient`` (401→SessionExpired, 403→Blocked, 404→NotFound, 429→RateLimited,
5xx→ServerError) — наружу всплывают они, а не сырой не-2xx ответ.
"""
from __future__ import annotations

from typing import Any

from adapterkit.base import BaseAdapter, HttpClientLike
from adapterkit.contract import Endpoint
from librarykit.auth import TokenAuth
from librarykit.errmap import DEFAULT_ERROR_MAP
from librarykit.retry import RetryPolicy
from librarykit.transport import HttpClient, HttpxTransport

EVENTS_PATH = "/api/v1/events"
POLL_PATH = "/api/v1/events/poll"
ADMIN_PROFILES_PATH = "/api/v1/admin/profiles"
PROJECTS_PATH = "/api/v1/projects"

#: Декларативная endpoint-таблица хаба (8 операций). Пути с ``{placeholder}``
#: подставляются из переданных params; ``required_params`` валидируются до сети.
_ENDPOINTS: dict[str, Endpoint] = {
    "push_events": Endpoint("push_events", "POST", EVENTS_PATH),
    "poll_events": Endpoint("poll_events", "GET", POLL_PATH),
    "register_profile": Endpoint("register_profile", "POST", ADMIN_PROFILES_PATH),
    "patch_project": Endpoint(
        "patch_project", "PATCH", f"{PROJECTS_PATH}/{{slug}}",
        required_params=("slug",),
    ),
}


def _build_client(base_url: str, api_key: str) -> tuple[HttpClient, HttpxTransport]:
    """Собрать choke-point ``HttpClient`` к хабу с X-API-Key авторизацией.

    ``TokenAuth(api_key, scheme="", header="X-API-Key")`` ставит голый ключ в
    заголовок ``X-API-Key`` на каждый запрос (без ``Bearer``-префикса). Повторы
    429/5xx и разбор ``Retry-After`` — на ``HttpxTransport`` через ``RetryPolicy``.
    Маппинг кодов в доменные ошибки — ``DEFAULT_ERROR_MAP``.

    Возвращает и транспорт отдельно: ``HttpClient`` не закрывает httpx-клиент сам
    (нет ``aclose``), поэтому закрытие ``AsyncClient`` делается через транспорт.
    """
    transport = HttpxTransport(base_url, retry=RetryPolicy())
    auth = TokenAuth(api_key, scheme="", header="X-API-Key")
    return HttpClient(transport, auth, DEFAULT_ERROR_MAP), transport


class BackendClient(BaseAdapter):
    """Клиент к хабу поверх ``BaseAdapter`` (endpoint-table + choke-point HttpClient).

    ``client`` можно внедрить (seam для тестов/переиспользования) — любой
    ``HttpClientLike`` (реальный ``librarykit.HttpClient`` в проде, фейк с методом
    ``request`` в тестах). Без него собирается дефолтный ``HttpClient`` к
    ``base_url`` с ``X-API-Key``-авторизацией.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        client: HttpClientLike | None = None,
    ) -> None:
        self._api_key = api_key
        # Ссылка на нижележащий транспорт — только когда клиент собрали мы сами:
        # его (а не choke-point HttpClient, у которого нет aclose) и закрываем.
        self._transport: HttpxTransport | None = None
        if client is None:
            client, self._transport = _build_client(base_url, api_key)
        super().__init__(client, endpoints=_ENDPOINTS)

    async def aclose(self) -> None:
        """Закрыть нижележащий httpx-транспорт (если клиент собрали мы сами).

        ``librarykit.HttpClient`` сам httpx-клиент не закрывает (нет ``aclose``),
        поэтому закрываем владеемый ``HttpxTransport``. При внедрённом ``client``
        делегируем best-effort в ``BaseAdapter.aclose`` (закроет, если у фейка
        есть ``aclose``)."""
        if self._transport is not None:
            await self._transport.aclose()
        else:
            await super().aclose()

    async def push_events(self, events: list[dict[str, Any]]) -> Any:
        """Отправить события на хаб (батч). Возвращает JSON ответа ``/events``."""
        resp = await self._request("push_events", json=events)
        return resp.json()

    async def poll_events(
        self, since: str | None = None, *, timeout: float = 25.0, scope: str = "all"
    ) -> Any:
        """Long-poll событий позже курсора ``since``. ``scope='personal'`` —
        только задачи, где я в участниках (профиль «мои задачи»); ``all`` — все.

        Возвращает ``{'events': [...], 'cursor': str|None}``."""
        params: dict[str, Any] = {"timeout": timeout, "scope": scope}
        if since is not None:
            params["since"] = since
        resp = await self._request("poll_events", **params)
        return resp.json()

    async def register_profile(
        self, member_slug: str, portal_slug: str, name: str, scope: str,
        global_role: str | None = None,
    ) -> Any:
        """Онбординг нового Atlas-стора (профиля) ТЕКУЩИМ admin-ключом.

        POST ``/api/v1/admin/profiles`` → сервер атомарно/идемпотентно заводит
        члена (``member_slug``) + портал-стор (``portal_slug``, system=atlas) и
        выпускает ключ нового стора. Тело строго по контракту ядра ``ProfileIn``
        (member_slug/portal_slug — РАЗНЫЕ: один человек может иметь несколько
        сторов). Возвращает JSON ``{'member_slug', 'portal_slug', 'api_key'}``
        (raw-ключ показывается один раз)."""
        body: dict[str, Any] = {
            "member_slug": member_slug, "portal_slug": portal_slug,
            "name": name, "scope": scope,
        }
        if global_role is not None:
            body["global_role"] = global_role
        resp = await self._request("register_profile", json=body)
        return resp.json()

    async def patch_project(self, slug: str, **fields: Any) -> Any:
        """Правка generic-полей проекта на backend (PATCH /projects/{slug}).

        Поля: name/visibility/status/owner_slug/lead_slug. NB: маршрутизация во
        внешние системы/порталы — НЕ дело CLI (backend сам решает фанаут)."""
        resp = await self._request("patch_project", slug=slug, json=fields)
        return resp.json()


__all__ = ["BackendClient"]
