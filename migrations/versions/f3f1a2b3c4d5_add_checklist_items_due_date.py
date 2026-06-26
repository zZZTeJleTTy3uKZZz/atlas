"""add checklist_items.due_date for checklist sync

Revision ID: f3f1a2b3c4d5
Revises: 0b8a8a1bb61b
Create Date: 2026-06-19 12:00:00.000000

F3f: двунаправленный синк пунктов чек-листа Atlas ↔ ядро.
Добавляет checklist_items.due_date (nullable) — Atlas-сторона due-поля,
которое на проводе едет как payload["due"] (ISO "YYYY-MM-DD").
batch_alter_table — как в остальных миграциях (SQLite не умеет ALTER COLUMN).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f3f1a2b3c4d5'
down_revision: Union[str, Sequence[str], None] = '0b8a8a1bb61b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('checklist_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('due_date', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('checklist_items', schema=None) as batch_op:
        batch_op.drop_column('due_date')
