"""PM-слой notion-task-cli: БД, модели, миграции, синхронизация.

Архитектура описана в:
`.../NP-005_Personal_PM_Infrastructure/ARCHITECTURE.md` §1-3 (Portfolio DB, Project Standard, SSOT).

Схема БД — в `MODEL.md` того же модуля.
"""

from notion_task_cli.pm import db, models

__all__ = ["db", "models"]
