"""BackendClient.patch_project — generic PATCH полей проекта через httpx_mock.

CLI знает только generic backend-операции (push/poll events, register_profile,
patch_project). provision/import-b24/link/unlink убраны — маршрутизация во
внешние системы/порталы это зона backend-сервиса, не CLI.
"""
import json


async def test_patch_project_sends_fields_and_api_key(httpx_mock):
    from atlas.sync.backend_client import BackendClient

    httpx_mock.add_response(
        method="PATCH", url="http://t/api/v1/projects/mediyka",
        json={"slug": "mediyka", "visibility": "personal"},
    )
    c = BackendClient("http://t", "K")
    res = await c.patch_project(
        "mediyka", visibility="personal", owner_slug="me", lead_slug="owner"
    )

    req = httpx_mock.get_request()
    assert req.method == "PATCH"
    assert str(req.url) == "http://t/api/v1/projects/mediyka"
    assert json.loads(req.content) == {
        "visibility": "personal", "owner_slug": "me", "lead_slug": "owner"
    }
    assert req.headers["X-API-Key"] == "K"
    assert res == {"slug": "mediyka", "visibility": "personal"}
    await c.aclose()
