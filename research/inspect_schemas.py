"""Одноразовый разведывательный скрипт: дампит схемы ключевых data_sources
в research/schemas/. Запускается руками, результат коммитится как reference."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv


load_dotenv(Path(__file__).parents[1] / ".env")

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_VERSION = os.environ.get("NOTION_VERSION", "2025-09-03")

DATA_SOURCES = {
    "tasks":     "123d853b-90be-481a-b8a4-1e7eef45c416",  # _Задачи
    "projects":  "a1b2a598-c57e-4fc7-a217-0ec2d2785898",  # _Проекты
    "employees": "727f8037-1c54-4280-806b-ccd5f1b80f75",  # _Сотрудники
    "files":     "1a7bfce2-404c-8081-8448-000b34d72b47",  # _Файлы клиентов
    "orders":    "40f9b95a-c0f7-4f4c-9f9c-ce5e7f3e8657",  # _Заказы
}

OUT = Path(__file__).parent / "schemas"
OUT.mkdir(exist_ok=True)


def main() -> None:
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
    }
    with httpx.Client(headers=headers, timeout=30) as c:
        for name, ds in DATA_SOURCES.items():
            r = c.get(f"https://api.notion.com/v1/data_sources/{ds}")
            r.raise_for_status()
            data = r.json()
            (OUT / f"{name}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            props = data.get("properties", {})
            print(f"{name} ({ds}) — {len(props)} fields")
            for pname, pdef in props.items():
                t = pdef.get("type")
                extra = ""
                if t == "relation":
                    rel = pdef.get("relation", {})
                    extra = f" → {rel.get('data_source_id') or rel.get('database_id')}"
                elif t == "select":
                    opts = [o.get("name") for o in pdef.get("select", {}).get("options", [])]
                    extra = f" [{', '.join(opts[:6])}{'...' if len(opts) > 6 else ''}]"
                elif t == "status":
                    opts = [o.get("name") for o in pdef.get("status", {}).get("options", [])]
                    extra = f" [{', '.join(opts)}]"
                print(f"  - {pname}: {t}{extra}")
            print()


if __name__ == "__main__":
    main()
