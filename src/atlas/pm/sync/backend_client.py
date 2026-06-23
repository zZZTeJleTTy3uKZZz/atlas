"""Доменный клиент к backend-хабу (notion-api-b24) поверх adapterkit/librarykit.

Транспорт канона S-kits: ``HttpxTransport`` (httpx + RetryPolicy с header-driven
повторами 429/5xx) → ``HttpClient`` (choke-point: apply auth → transport → map
errors через ``DEFAULT_ERROR_MAP``) → ``BaseAdapter`` с декларативной
endpoint-таблицей. Auth — заголовком ``X-API-Key`` (``TokenAuth`` со ``scheme=""``):
ключ ставится автоматически на каждый запрос, per-call заголовки не нужны.

Доменные методы сохраняют публичный фасад прежнего клиента (их по утиному типу
зовут ``push.py``/``pull.py``/``hub_service.py``/``profile.py``): ``push_events``
(POST /events — пройдёт через оркестратор → фанаут), ``poll_events`` (GET
/events/poll — long-poll), ``register_profile``, ``provision_project``,
``import_from_b24``, ``patch_project``, ``link_project``, ``unlink_project``.

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
    "provision_project": Endpoint("provision_project", "POST", PROJECTS_PATH),
    "import_from_b24": Endpoint(
        "import_from_b24", "POST", f"{PROJECTS_PATH}/import-from-b24"
    ),
    "patch_project": Endpoint(
        "patch_project", "PATCH", f"{PROJECTS_PATH}/{{slug}}",
        required_params=("slug",),
    ),
    "link_project": Endpoint(
        "link_project", "POST", f"{PROJECTS_PATH}/{{slug}}/links",
        required_params=("slug",),
    ),
    "unlink_project": Endpoint(
        "unlink_project", "DELETE", f"{PROJECTS_PATH}/{{slug}}/links/{{portal_slug}}",
        required_params=("slug", "portal_slug"),
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

    async def provision_project(
        self, *, slug: str, name: str, kind: str, owner_slug: str, lead_slug: str,
        visibility: str, notion_kind: str, sync_target_slugs: list[str],
    ) -> dict[str, Any]:
        """Разложить проект в ядре: core_project + lead + Notion-страница + связи.

        POST ``/api/v1/projects`` с provisioning-полями. Возвращает
        ``{'backend_id', 'notion_page_id'}`` — Atlas проставляет их локально,
        чтобы проект был связан со всеми тремя системами."""
        body: dict[str, Any] = {
            "slug": slug, "name": name, "kind": kind, "owner_slug": owner_slug,
            "lead_slug": lead_slug, "visibility": visibility,
            "provision_notion": True, "notion_kind": notion_kind,
            "sync_target_slugs": sync_target_slugs, "atlas_slug": slug,
        }
        resp = await self._request("provision_project", json=body)
        payload = resp.json()
        data = payload[0] if isinstance(payload, list) else payload
        return {
            "backend_id": data.get("id") or data.get("backend_id"),
            "notion_page_id": data.get("notion_page_id"),
        }

    async def import_from_b24(
        self, *, group_id: int, notion_kind: str = "клиентский",
        lead_slug: str | None = "dmitry", sync_target_slugs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Втянуть группу Б24 в ядро+Notion (POST /projects/import-from-b24).
        Возвращает {'backend_id','notion_page_id','name','slug'}."""
        body: dict[str, Any] = {"group_id": group_id, "notion_kind": notion_kind}
        if lead_slug is not None:
            body["lead_slug"] = lead_slug
        if sync_target_slugs is not None:
            body["sync_target_slugs"] = sync_target_slugs
        resp = await self._request("import_from_b24", json=body)
        payload = resp.json()
        data = payload[0] if isinstance(payload, list) else payload
        return {
            "backend_id": data.get("backend_id") or data.get("id"),
            "notion_page_id": data.get("notion_page_id"),
            "name": data.get("name"),
            "slug": data.get("slug"),
        }

    async def patch_project(self, slug: str, **fields: Any) -> Any:
        """Правка модели проекта в ядре без docker exec (PATCH /projects/{slug}).
        Поля: name/visibility/status/owner_slug/lead_slug/sync_target_slugs/…"""
        resp = await self._request("patch_project", slug=slug, json=fields)
        return resp.json()

    async def link_project(self, slug: str, *, portal_slug: str, external_id: str) -> Any:
        """Привязать проект к сущности портала (entity_link) через API."""
        resp = await self._request(
            "link_project", slug=slug,
            json={"portal_slug": portal_slug, "external_id": external_id},
        )
        return resp.json()

    async def unlink_project(self, slug: str, *, portal_slug: str) -> Any:
        """Снять связь проекта с порталом (entity_link) через API."""
        resp = await self._request(
            "unlink_project", slug=slug, portal_slug=portal_slug
        )
        return resp.json()


__all__ = ["BackendClient"]
