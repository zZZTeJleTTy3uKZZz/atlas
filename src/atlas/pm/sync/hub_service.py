"""HubService — application-фасад к backend-хабу для команд Atlas.

Команды (presentation) НЕ инстанцируют BackendClient (адаптер транспорта) и не
управляют его жизненным циклом — они зовут HubService. Сервис инкапсулирует
создание/закрытие клиента на каждый вызов. Так presentation зависит от
абстракции (сервис), а не от конкретного транспорта (Onion).
"""
from __future__ import annotations

from typing import Any

from atlas.appconfig import load_config

from .backend_client import BackendClient


class HubService:
    """Фасад к хабу: provision/patch/link/unlink/import поверх BackendClient."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url
        self._api_key = api_key

    @classmethod
    def from_config(cls) -> "HubService":
        cfg = load_config()
        return cls(cfg.base_url, cfg.api_key)

    @property
    def enabled(self) -> bool:
        """Есть ли api_key — иначе команды не должны дёргать хаб."""
        return bool(self._api_key)

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        client = BackendClient(self._base_url, self._api_key)
        try:
            return await getattr(client, method)(*args, **kwargs)
        finally:
            await client.aclose()

    async def provision_project(self, **kwargs: Any) -> Any:
        return await self._call("provision_project", **kwargs)

    async def import_from_b24(self, **kwargs: Any) -> Any:
        return await self._call("import_from_b24", **kwargs)

    async def patch_project(self, slug: str, **fields: Any) -> Any:
        return await self._call("patch_project", slug, **fields)

    async def link_project(self, slug: str, **kwargs: Any) -> Any:
        return await self._call("link_project", slug, **kwargs)

    async def unlink_project(self, slug: str, **kwargs: Any) -> Any:
        return await self._call("unlink_project", slug, **kwargs)


__all__ = ["HubService"]
