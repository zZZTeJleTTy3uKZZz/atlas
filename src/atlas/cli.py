"""Atlas CLI — локальный PM портфеля проектов + синхронизация с внешним backend-сервисом.

RESTful-канон: ресурс → глагол, подчинённые ресурсы вложены в родителя.
project (+ git/layout/tag/member) / task (+ member/checklist/handoff/lifecycle-
глаголы) / epic (+ worktree) / sprint / hypothesis / person / type / status /
tag / backlog / issue / log / backup / config / sync / profile. Notion-legacy
команды убраны (синк идёт через ядро-хаб).
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from clikit import build_root_app

from .commands.log import log_app
from .commands.backlog import backlog_app
from .commands.backup import backup_app
from .commands.config import config_app
from .commands.epic import epic_app
from .commands import epic_worktree as _epic_worktree  # noqa: F401  # навешивает `epic worktree`
from .commands.hypothesis import hypothesis_app
from .commands.issue import issue_app  # импорт также навешивает `task handoff` на task_app
from .commands.participants import app as participants_app
from .commands.task import task_app
from .commands import task_lease as _task_lease  # noqa: F401  # регистрирует lease-команды на task_app
from .commands import member as _member  # noqa: F401  # навешивает `task member` на task_app
from .commands import checklist as _checklist  # noqa: F401  # навешивает `task checklist` на task_app
from .commands.connect import backend_app
from .commands.dashboard import dashboard_cmd
from .commands.init import init_cmd
from .commands.setup import session_hook_cmd, setup_cmd
from .commands.upgrade import update_cmd
from .commands.profile import profile_app
from .commands.projects import projects_app
from .commands.sprint import sprint_app
from .commands.stats import stats_app
from .commands.statuses import app as statuses_app
from .commands.sync import sync_app
from .commands.tags import app as tags_app
from .commands.types import app as types_app

try:
    _ATLAS_VERSION = _pkg_version("atlas-pm")  # dist-имя на PyPI (import-пакет — atlas)
except PackageNotFoundError:  # editable / не установлен — читаем версию из кода
    try:
        from atlas import __version__ as _ATLAS_VERSION
    except Exception:  # pragma: no cover
        _ATLAS_VERSION = "0.0.0"

app = build_root_app(
    "atlas",
    version=_ATLAS_VERSION,
    help="Atlas — PM портфеля проектов + синхронизация с хабом (--json по умолчанию).",
)

# Команды-сущности — в единственном числе, единообразно.
app.add_typer(projects_app, name="project")          # проекты портфеля (CRUD, теги, архив)
app.add_typer(task_app, name="task")             # задачи (CRUD, ЦКП)
app.add_typer(epic_app, name="epic")                 # эпики (тематическая группировка)
app.add_typer(sprint_app, name="sprint")             # спринты (Scrum-тайм-боксы) + velocity/board
app.add_typer(hypothesis_app, name="hypothesis")     # гипотезы (Atlas Hypothesis Ledger)
app.add_typer(participants_app, name="person")       # люди портфеля (реестр; доменная модель — Participant)
app.add_typer(types_app, name="type")                # справочник типов проектов
app.add_typer(statuses_app, name="status")           # справочник lifecycle-статусов
app.add_typer(tags_app, name="tag")                  # теги проектов
app.add_typer(backlog_app, name="backlog")           # пул идей-интейка (DB-first) → convert в task/project
app.add_typer(issue_app, name="issue")               # структурированные жалобы (bug/feature/handoff) + валидатор
app.add_typer(log_app, name="log")                   # журнал: list (обогащённо, кто/что/проект) + raw (сырой append-only audit)
app.add_typer(backup_app, name="backup")             # бэкап портфеля
app.add_typer(config_app, name="config")             # конфиг (show/get/set) — онбординг
app.add_typer(sync_app, name="sync")                 # синхронизация с внешним backend-сервисом
app.add_typer(profile_app, name="profile")           # онбординг Atlas-сторов (профиль = стор)
app.add_typer(backend_app, name="backend")           # backend connect | disconnect | status (внешний синк)
app.add_typer(stats_app, name="stats")               # аналитика портфеля (overview/period/provenance/git)
app.command("dashboard")(dashboard_cmd)              # операционный board: статусы/in-flight/внимание/по проектам
app.command("dash")(dashboard_cmd)                   # короткий алиас dashboard (ещё короче — `atlas -D`)
app.command("init")(init_cmd)                        # Atlas-дисциплина в агентские файлы (CLAUDE.md/AGENTS.md/...)
app.command("setup")(setup_cmd)                      # turnkey onboarding: правила (init) + Claude SessionStart-хук триажа
app.command("session-hook", hidden=True)(session_hook_cmd)  # встроенный SessionStart-хук (ставит `atlas setup`)
app.command("update")(update_cmd)                    # самообновление с PyPI (uv tool/pipx/pip); --from-git — legacy pipx-reinstall


#: Глобальные флаги режима вывода, которые должны работать В ЛЮБОЙ ПОЗИЦИИ
#: (clikit-callback ловит их только ДО подкоманды — частая боль `atlas task list
#: --text` → «No such option»). ``main`` вынимает их из argv ЗАРАНЕЕ и кладёт режим
#: в env ``ATLAS_OUTPUT`` (его читает clikit ``init_output_mode``), а dashboard/init
#: — через ``_want_json``. json приоритетнее text (как в clikit).
_JSON_FLAGS = frozenset({"--json", "-J"})
_TEXT_FLAGS = frozenset({"--text", "--plain"})


def _hoist_output_flags(argv: list[str]) -> list[str]:
    """Вынуть output-флаги из argv (любая позиция) → env ATLAS_OUTPUT; вернуть остаток."""
    import os

    rest: list[str] = []
    mode: str | None = None
    hoisting = True
    for tok in argv:
        # [20] POSIX end-of-options: после `--` токены — ЗНАЧЕНИЯ/позиционные, а не
        # флаги. Раньше хойст вырезал --json/--text из ЛЮБОЙ позиции, поэтому
        # `atlas config set key -- --json` (значение, равное флагу) молча съедался
        # и не доходил до Typer, а режим вывода переключался.
        if tok == "--":
            hoisting = False
            rest.append(tok)
            continue
        if hoisting and tok in _JSON_FLAGS:
            mode = "json"  # json перебивает text
        elif hoisting and tok in _TEXT_FLAGS and mode != "json":
            mode = "text"
        else:
            rest.append(tok)
    if mode is not None:
        os.environ["ATLAS_OUTPUT"] = mode
    return rest


#: Короткие командные шорткаты верхнего уровня (clikit-callback своих коротких
#: опций для подкоманд не даёт, поэтому транслируем в argv ДО Typer).
_COMMAND_SHORTCUTS = {"-D": "dashboard"}


def _apply_command_shortcuts(argv: list[str]) -> list[str]:
    """`-D` → команда `dashboard` (короткий вызов дэша), сохраняя прочие аргументы.

    `atlas -D` / `atlas -D --project p` / `atlas -D --json` → `atlas dashboard …`.
    Срабатывает, только если в argv нет иной подкоманды перед шорткатом."""
    for short, cmd in _COMMAND_SHORTCUTS.items():
        if short in argv:
            rest = [a for a in argv if a != short]
            return [cmd, *rest]
    return argv


def main() -> None:
    """Entry point: поднять output-флаги + командные шорткаты, затем Typer-app."""
    import sys

    argv = _apply_command_shortcuts(_hoist_output_flags(sys.argv[1:]))
    sys.argv = [sys.argv[0], *argv]
    app()


if __name__ == "__main__":
    main()
