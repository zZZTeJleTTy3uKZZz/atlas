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

    async def aclose(self) -> None:
        """Закрыть нижележащий HTTP-клиент."""
        await self._http.aclose()


__all__ = ["BackendClient"]
