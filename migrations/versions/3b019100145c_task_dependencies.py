"""task_dependencies: задача ждёт задачу

Порядок работ жил в голове, и выяснялся он в момент, когда исполнитель уже взял
задачу и упёрся. Главный случай — межпроектный: задача клиентского проекта ждёт
задачу в ките, и знает об этом только тот, кто заводил обе.

Внешних ключей на `tasks` здесь нет намеренно — ровно как у `milestone_items`:
SQLite в batch-режиме не умеет безымянные FK, а целостность держит слой команд,
который умеет сказать про висячую ссылку понятной фразой вместо отказа драйвера.

Автогенерация в этой миграции подрезана. Она предложила заодно создать десяток
внешних ключей на `tasks`, `epics` и `projects` — те самые, которых в базе нет
ПО РЕШЕНИЮ, а не по забывчивости. Пропустить их сюда значило бы протащить чужое
изменение под видом своего: миграция про зависимости задач обязана менять
только зависимости задач.

Revision ID: 3b019100145c
Revises: 314f581a7fc6
Create Date: 2026-08-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3b019100145c"
down_revision: Union[str, Sequence[str], None] = "314f581a7fc6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("depends_on_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("task_id != depends_on_id", name="ck_task_dep_not_self"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("task_dependencies", schema=None) as batch:
        batch.create_index("idx_task_dep_blocker", ["depends_on_id"], unique=False)
        # Уникальность пары: одна и та же связь, заведённая дважды, дала бы два
        # одинаковых блокера в отказе и два места, где её надо снимать.
        batch.create_index(
            "uq_task_dependencies", ["task_id", "depends_on_id"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("task_dependencies", schema=None) as batch:
        batch.drop_index("uq_task_dependencies")
        batch.drop_index("idx_task_dep_blocker")
    op.drop_table("task_dependencies")
