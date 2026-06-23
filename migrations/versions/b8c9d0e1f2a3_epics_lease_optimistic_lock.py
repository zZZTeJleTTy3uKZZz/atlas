"""epic lease/claim fields + optimistic lock (version)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-23 14:00:00.000000

Эпик «Групповой lease» — epic-claim с каскадом. Симметрично tasks (миграция
a7b8c9d0e1f2) добавляет на epics:
- lease_owner (TEXT NULL) — кто держит lease (participant).
- lease_session_id (TEXT NULL) — id сессии Claude Code (кто конкретно).
- lease_origin (TEXT NULL) — из какого проекта/cwd взято.
- claimed_at (DATETIME NULL) — когда взял.
- lease_expires_at (DATETIME NULL) — TTL-дедлайн (протухание).
- lock_version (INTEGER NOT NULL, server_default '0') — optimistic-lock
  (SQLAlchemy version_id_col на Epic).

Индексы: idx_epics_lease (lease_owner, lease_expires_at),
idx_epics_lease_expires (lease_expires_at) — для report/reap протухших.

Нюанс SQLite/batch (как в a7b8c9d0e1f2): FK lease_owner → participants.id НЕ
создаётся как именованный constraint (безымянный FK в batch падает) — целостность
держится в ORM. server_default '0' даёт существующим строкам валидную версию.
Lease-поля nullable — безопасно для существующих строк (backfill не нужен).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — lease/version поля на epics."""
    with op.batch_alter_table('epics', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lease_owner', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('lease_session_id', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('lease_origin', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('claimed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('lease_expires_at', sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column('lock_version', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.create_index(
            'idx_epics_lease', ['lease_owner', 'lease_expires_at'], unique=False
        )
        batch_op.create_index(
            'idx_epics_lease_expires', ['lease_expires_at'], unique=False
        )


def downgrade() -> None:
    """Downgrade schema — drop lease/version полей (обратный порядок)."""
    with op.batch_alter_table('epics', schema=None) as batch_op:
        batch_op.drop_index('idx_epics_lease_expires')
        batch_op.drop_index('idx_epics_lease')
        batch_op.drop_column('lock_version')
        batch_op.drop_column('lease_expires_at')
        batch_op.drop_column('claimed_at')
        batch_op.drop_column('lease_origin')
        batch_op.drop_column('lease_session_id')
        batch_op.drop_column('lease_owner')
