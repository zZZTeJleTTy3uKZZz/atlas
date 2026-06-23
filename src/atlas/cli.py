"""Atlas CLI — локальный PM портфеля проектов + синхронизация с backend-хабом.

Единый стиль команд (единственное число): project / task / epic / checklist /
member / participant / type / status / tag / idea / inbox / action-log /
backup / sync. Notion-legacy команды убраны (синк идёт через ядро-хаб).
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from clikit import build_root_app

from .commands.action_log import app as action_log_app
from .commands.backup import backup_app
from .commands.checklist import checklist_app
from .commands.epic import epic_app
from .commands.hypothesis import hypothesis_app
from .commands.ideas import ideas_app
from .commands.inbox import inbox_app
from .commands.member import member_app
from .commands.participants import app as participants_app
from .commands.task import task_app
from .commands import task_lease as _task_lease  # noqa: F401  # регистрирует lease-команды на task_app
from .commands.profile import profile_app
from .commands.projects import projects_app
from .commands.stats import dashboard_cmd, stats_app
from .commands.statuses import app as statuses_app
from .commands.sync import sync_app
from .commands.tags import app as tags_app
from .commands.types import app as types_app

try:
    _ATLAS_VERSION = _pkg_version("atlas")
except PackageNotFoundError:  # pragma: no cover — пакет не установлен (редкий случай)
    _ATLAS_VERSION = "0.0.0"

app = build_root_app(
    "atlas",
    version=_ATLAS_VERSION,
    help="Atlas — PM портфеля проектов + синхронизация с хабом (--json по умолчанию).",
)

# Команды-сущности — в единственном числе, единообразно.
app.add_typer(projects_app, name="project")          # проекты портфеля (CRUD, теги, архив)
app.add_typer(task_app, name="task")             # задачи (CRUD, ЦКП)
app.add_typer(epic_app, name="epic")                 # эпики (вехи/спринты)
app.add_typer(hypothesis_app, name="hypothesis")     # гипотезы (Atlas Hypothesis Ledger)
app.add_typer(checklist_app, name="checklist")       # чек-листы задач
app.add_typer(member_app, name="member")             # участники задачи (роли)
app.add_typer(participants_app, name="participant")  # участники портфеля
app.add_typer(types_app, name="type")                # справочник типов проектов
app.add_typer(statuses_app, name="status")           # справочник lifecycle-статусов
app.add_typer(tags_app, name="tag")                  # теги проектов
app.add_typer(ideas_app, name="idea")                # инкубатор идей (entity_kind=idea)
app.add_typer(inbox_app, name="inbox")               # входящие на разбор (entity_kind=inbox)
app.add_typer(action_log_app, name="action-log")     # аудит (append-only)
app.add_typer(backup_app, name="backup")             # бэкап портфеля
app.add_typer(sync_app, name="sync")                 # синхронизация с backend-хабом
app.add_typer(profile_app, name="profile")           # онбординг Atlas-сторов (профиль = стор)
app.add_typer(stats_app, name="stats")               # аналитика портфеля (overview/period/provenance/git)
app.command("dashboard")(dashboard_cmd)              # объединённый обзор (counts+activity+provenance+git)


if __name__ == "__main__":
    app()
