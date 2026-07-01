"""add hypotheses ledger

Revision ID: b1d0100413fc
Revises: f3f1a2b3c4d5
Create Date: 2026-06-20 10:00:00.000000

Atlas Hypothesis Ledger (Подсистема 1 hypothesis-lab).
Создаёт таблицу `hypotheses` — реестр гипотез + эффективность (success-rate).

Написана ВРУЧНУЮ (не autogenerate), чтобы не загрязнять diff дрейфом БД.
FK: project_id → projects.id (NOT NULL), task_id → tasks.id (ondelete SET NULL,
nullable). CHECK на status/confidence/verdict + 5 индексов.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b1d0100413fc'
down_revision: Union[str, Sequence[str], None] = 'f3f1a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'hypotheses',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('number', sa.Integer(), nullable=True),
        sa.Column('slug', sa.String(length=100), nullable=True),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('task_id', sa.String(length=36), nullable=True),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('statement', sa.Text(), nullable=True),
        sa.Column('metric', sa.String(length=200), nullable=True),
        sa.Column('baseline', sa.String(length=100), nullable=True),
        sa.Column('target', sa.String(length=100), nullable=True),
        sa.Column('method', sa.Text(), nullable=True),
        sa.Column('result_value', sa.String(length=100), nullable=True),
        sa.Column('delta', sa.String(length=100), nullable=True),
        sa.Column('verdict', sa.String(length=20), nullable=True),
        sa.Column('lesson', sa.Text(), nullable=True),
        sa.Column('consolidated_into', sa.Text(), nullable=True),
        sa.Column('confidence', sa.String(length=3), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('tested_at', sa.DateTime(), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('archived_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft','testing','measured','closed')",
            name='ck_hypotheses_status',
        ),
        sa.CheckConstraint(
            "confidence IN ('H','M','L')",
            name='ck_hypotheses_confidence',
        ),
        sa.CheckConstraint(
            "verdict IS NULL OR verdict IN ('accept','reject','iterate','inconclusive')",
            name='ck_hypotheses_verdict',
        ),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('number'),
        sa.UniqueConstraint('slug'),
    )
    with op.batch_alter_table('hypotheses', schema=None) as batch_op:
        batch_op.create_index('idx_hypotheses_project', ['project_id'], unique=False)
        batch_op.create_index('idx_hypotheses_task', ['task_id'], unique=False)
        batch_op.create_index('idx_hypotheses_status', ['status'], unique=False)
        batch_op.create_index('idx_hypotheses_verdict', ['verdict'], unique=False)
        batch_op.create_index('idx_hypotheses_created', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('hypotheses', schema=None) as batch_op:
        batch_op.drop_index('idx_hypotheses_created')
        batch_op.drop_index('idx_hypotheses_verdict')
        batch_op.drop_index('idx_hypotheses_status')
        batch_op.drop_index('idx_hypotheses_task')
        batch_op.drop_index('idx_hypotheses_project')

    op.drop_table('hypotheses')
