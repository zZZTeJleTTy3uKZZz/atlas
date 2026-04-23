"""Тонкая обёртка над Notion API: httpx + ретрай 429/5xx + пагинация."""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Iterator

import httpx


log = logging.getLogger(__name__)

BASE = "https://api.notion.com/v1"


class NotionError(RuntimeError):
    def __init__(self, status: int, code: str | None, message: str) -> None:
        super().__init__(f"Notion {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class NotionClient:
    def __init__(self, token: str, *, version: str = "2025-09-03") -> None:
        self._http = httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": version,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "NotionClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ---- low-level ----

    def _request(self, method: str, path: str, *, json: Any = None) -> dict[str, Any]:
        url = f"{BASE}{path}"
        for attempt in range(4):
            resp = self._http.request(method, url, json=json)
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                delay = float(resp.headers.get("Retry-After", 1 + attempt))
                log.warning("notion %s %s → %s, retry in %ss", method, path, resp.status_code, delay)
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                except Exception:
                    body = {}
                raise NotionError(
                    resp.status_code,
                    body.get("code"),
                    body.get("message") or resp.text,
                )
            return resp.json()
        raise NotionError(resp.status_code, "retries_exhausted", resp.text)

    # ---- high-level ----

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self._request("GET", f"/pages/{page_id}")

    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/pages/{page_id}", json={"properties": properties})

    def archive_page(self, page_id: str, *, archived: bool = True) -> dict[str, Any]:
        return self._request("PATCH", f"/pages/{page_id}", json={"archived": archived})

    def create_page(
        self,
        *,
        data_source_id: str,
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        body = {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
        }
        return self._request("POST", "/pages", json=body)

    def query_data_source(
        self,
        data_source_id: str,
        *,
        filter: dict[str, Any] | None = None,  # noqa: A002
        sorts: list[dict[str, Any]] | None = None,
        page_size: int = 100,
    ) -> Iterator[dict[str, Any]]:
        """Пагинированный обход; yield каждой страницы."""
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": page_size}
            if filter is not None:
                body["filter"] = filter
            if sorts is not None:
                body["sorts"] = sorts
            if cursor:
                body["start_cursor"] = cursor
            resp = self._request(
                "POST", f"/data_sources/{data_source_id}/query", json=body
            )
            for item in resp.get("results", []):
                yield item
            if not resp.get("has_more"):
                return
            cursor = resp.get("next_cursor")

    def query_all(
        self,
        data_source_id: str,
        *,
        filter: dict[str, Any] | None = None,  # noqa: A002
        sorts: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        return list(self.query_data_source(data_source_id, filter=filter, sorts=sorts))
