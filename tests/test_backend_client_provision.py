"""BackendClient.provision_project — POST /api/v1/projects (авто-раскладка)."""
import pytest

from atlas.pm.sync.backend_client import BackendClient


class _FakeHttp:
    def __init__(self):
        self.calls = []

    async def post(self, path, *, json=None, headers=None):
        self.calls.append((path, json, headers))
        return [{"id": "core-1", "notion_page_id": "np-1"}]

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_provision_project_posts_and_returns_ids():
    http = _FakeHttp()
    c = BackendClient("http://t", "KEY", http=http)
    res = await c.provision_project(
        slug="mediyka", name="Медийка", kind="direction",
        owner_slug="me", lead_slug="dmitry", visibility="personal",
        notion_kind="личный", sync_target_slugs=["notion-pragmat", "atlas-dmitry"],
    )
    path, body, headers = http.calls[0]
    assert path == "/api/v1/projects"
    assert body["slug"] == "mediyka"
    assert body["lead_slug"] == "dmitry"
    assert body["owner_slug"] == "me"
    assert body["visibility"] == "personal"
    assert body["provision_notion"] is True
    assert body["notion_kind"] == "личный"
    assert body["sync_target_slugs"] == ["notion-pragmat", "atlas-dmitry"]
    assert headers["X-API-Key"] == "KEY"
    assert res == {"backend_id": "core-1", "notion_page_id": "np-1"}
