"""CLI `atlas log ...` — журнал событий портфеля (поверх action_log).

Объединяет прежние top-level `atlas logs` (обогащённый вид) и
`atlas action-log list` (сырой append-only audit) в один ресурс:

- ``atlas log list`` — обогащённо: кто / что (заголовок) / проект / приоритет
  (через ``atlas.logs.build_logs``); человеку Rich, агенту ``--json``.
- ``atlas log raw``  — сырой audit: записи ``ActionLog`` как есть, с фильтрами.

Тела команд переиспользуются из модулей-владельцев (``logs``/``action_log``),
здесь только сборка ресурса-группы.
"""
from __future__ import annotations

import typer

from atlas.commands.action_log import list_cmd as _raw_list_cmd
from atlas.commands.logs import logs_cmd as _enriched_cmd

log_app = typer.Typer(
    no_args_is_help=True,
    help="Журнал событий портфеля: list (обогащённо) + raw (сырой append-only audit).",
)

# `atlas log list` — обогащённый журнал (бывш. `atlas logs`).
log_app.command("list")(_enriched_cmd)
# `atlas log raw` — сырой append-only audit (бывш. `atlas action-log list`).
log_app.command("raw")(_raw_list_cmd)
