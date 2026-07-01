"""collapse sprint_id into epic_id on tasks

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-23 12:00:00.000000

Схлопывание дублирующей колонки tasks.sprint_id в tasks.epic_id.

Предыстория: у tasks исторически было ДВЕ колонки — sprint_id (String(36),
БЕЗ FK, индекс idx_tasks_sprint) и epic_id (String(36), FK→epics.id). CLI-флаг
--sprint писал в безфковый sprint_id, а правильный epic_id из CLI не
выставлялся. На боевой все непустые sprint_id содержат СЛАГИ эпиков
(epics.slug), epic_id при этом пуст. Концепт «sprint» == «epic».

upgrade:
1. Backfill: epic_id := (SELECT e.id FROM epics e WHERE e.slug = tasks.sprint_id)
   для строк, где sprint_id задан, а epic_id ещё пуст (slug → epic_id).
2. drop_index idx_tasks_sprint, drop_column sprint_id.
3. create_index idx_tasks_epic по epic_id.

downgrade (восстановление дубль-колонки):
1. add_column sprint_id + create_index idx_tasks_sprint.
2. Backfill обратно: sprint_id := (SELECT e.slug FROM epics e WHERE e.id =
   tasks.epic_id).
3. drop_index idx_tasks_epic.

Нюанс SQLite/batch (как в provenance c1d2e3f4a5b6 / canon d4e5f6a7b8c9):
epic_id уже существует с FK→epics.id (создан в исходной схеме), мы только
добавляем/убираем индекс idx_tasks_epic и колонку sprint_id. Безымянные FK в
batch падают — поэтому новых FK не создаём; epic_id FK уже на месте.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade — backfill slug→epic_id, затем drop sprint_id + idx_tasks_epic."""
    # 1. Backfill epic_id из sprint_id (значения sprint_id = epics.slug).
    op.execute(
        "UPDATE tasks SET epic_id = ("
        "  SELECT e.id FROM epics e WHERE e.slug = tasks.sprint_id"
        ") WHERE sprint_id IS NOT NULL AND epic_id IS NULL"
    )

    # 2. Дроп индекса + колонки sprint_id, создание idx_tasks_epic.
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index("idx_tasks_sprint")
        batch_op.drop_column("sprint_id")
        batch_op.create_index("idx_tasks_epic", ["epic_id"], unique=False)


def downgrade() -> None:
    """Downgrade — вернуть sprint_id (+idx_tasks_sprint), backfill epic_id→slug."""
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("sprint_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_index("idx_tasks_sprint", ["sprint_id"], unique=False)
        batch_op.drop_index("idx_tasks_epic")

    # Backfill обратно: sprint_id := slug эпика по epic_id.
    op.execute(
        "UPDATE tasks SET sprint_id = ("
        "  SELECT e.slug FROM epics e WHERE e.id = tasks.epic_id"
        ") WHERE epic_id IS NOT NULL AND sprint_id IS NULL"
    )
