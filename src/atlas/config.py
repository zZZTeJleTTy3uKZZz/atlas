"""Настройки и идентификаторы data_sources / людей."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT / ".env", Path.home() / ".notion-task-cli.env"):
    if candidate.exists():
        load_dotenv(candidate, override=False)
        break


# data_source ID — актуальные на 2026-04-22
DS_TASKS = "123d853b-90be-481a-b8a4-1e7eef45c416"
DS_PROJECTS = "a1b2a598-c57e-4fc7-a217-0ec2d2785898"
DS_EMPLOYEES = "727f8037-1c54-4280-806b-ccd5f1b80f75"
DS_FILES = "1a7bfce2-404c-8081-8448-000b34d72b47"


@dataclass
class Settings:
    token: str
    notion_version: str = "2025-09-03"
    portal_tz: str = "Europe/Moscow"
    # Page id владельца CLI (для --mine). Можно не задавать — тогда
    # резолвится по env SELF_EMPLOYEE_NAME или SELF_EMPLOYEE_ID.
    self_employee_id: str | None = None
    self_employee_name: str | None = "Дима"


def load_settings() -> Settings:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise SystemExit(
            "NOTION_TOKEN не задан. Положите в .env или export NOTION_TOKEN=..."
        )
    return Settings(
        token=token,
        notion_version=os.environ.get("NOTION_VERSION", "2025-09-03"),
        portal_tz=os.environ.get("PORTAL_TZ", "Europe/Moscow"),
        self_employee_id=os.environ.get("SELF_EMPLOYEE_ID"),
        self_employee_name=os.environ.get("SELF_EMPLOYEE_NAME", "Дима"),
    )
