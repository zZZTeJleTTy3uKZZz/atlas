"""Доменный клиент к backend-хабу (notion-api-b24) поверх clikit.HttpClient.

Auth — заголовком ``X-API-Key`` (бэк резолвит principal по ключу). clikit
HttpClient сам по себе шлёт ``Authorization: Bearer`` только при заданном
access_token; мы его НЕ задаём, а кладём ``X-API-Key`` в extra-заголовки
каждого запроса. Доменные методы: ``push_events`` (POST /events — пройдёт
через оркестратор → фанаут) и ``poll_events`` (GET /events/poll — long-poll).
"""
from __future__ import annotations

from typing import Any

from clikit import HttpClient

EVENTS_PATH = "/api/v1/events"
POLL_PATH = "/api/v1/events/poll"
ADMIN_PROFILES_PATH = "/api/v1/admin/profiles"
PROJECTS_PATH = "/api/v1/projects"


class BackendClient:
    """Клиент к хабу. ``http`` можно внедрить (для тестов/переиспользования)."""

    def __init__(
        self, base_url: str, api_key: str, *, http: HttpClient | None = None
    ) -> None:
        self._http = http or HttpClient(base_url)
        self._api_key = api_key

    def _auth(self) -> dict[str, str]:
        return {"X-API-Key": self._api_key}

    async def push_events(self, events: list[dict[str, Any]]) -> Any:
        """Отправить события на хаб (батч). Возвращает JSON ответа ``/events``."""
        return await self._http.post(EVENTS_PATH, json=events, headers=self._auth())

    async def poll_events(
        self, since: str | None = None, *, timeout: float = 25.0, scope: str = "all"
    ) -> Any:
        """Long-poll событий позже курсора ``since``. ``scope='personal'`` —
        только задачи, где я в участниках (профиль «мои задачи»); ``all`` — все."""
        params: dict[str, Any] = {"timeout": timeout, "scope": scope}
        if since is not None:
            params["since"] = since
        return await self._http.get(POLL_PATH, params=params, headers=self._auth())

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
        return await self._http.post(ADMIN_PROFILES_PATH, json=body, headers=self._auth())

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
        resp = await self._http.post(PROJECTS_PATH, json=body, headers=self._auth())
        data = resp[0] if isinstance(resp, list) else resp
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
        resp = await self._http.post(
            f"{PROJECTS_PATH}/import-from-b24", json=body, headers=self._auth()
        )
        data = resp[0] if isinstance(resp, list) else resp
        return {
            "backend_id": data.get("backend_id") or data.get("id"),
            "notion_page_id": data.get("notion_page_id"),
            "name": data.get("name"),
            "slug": data.get("slug"),
        }

    async def patch_project(self, slug: str, **fields: Any) -> Any:
        """Правка модели проекта в ядре без docker exec (PATCH /projects/{slug}).
        Поля: name/visibility/status/owner_slug/lead_slug/sync_target_slugs/…"""
        return await self._http.patch(
            f"{PROJECTS_PATH}/{slug}", json=fields, headers=self._auth()
        )

    async def link_project(self, slug: str, *, portal_slug: str, external_id: str) -> Any:
        """Привязать проект к сущности портала (entity_link) через API."""
        return await self._http.post(
            f"{PROJECTS_PATH}/{slug}/links",
            json={"portal_slug": portal_slug, "external_id": external_id},
            headers=self._auth(),
        )

    async def unlink_project(self, slug: str, *, portal_slug: str) -> Any:
        """Снять связь проекта с порталом (entity_link) через API."""
        return await self._http.delete(
            f"{PROJECTS_PATH}/{slug}/links/{portal_slug}", headers=self._auth()
        )

    async def aclose(self) -> None:
        """Закрыть нижележащий HTTP-клиент."""
        await self._http.aclose()


__all__ = ["BackendClient"]
