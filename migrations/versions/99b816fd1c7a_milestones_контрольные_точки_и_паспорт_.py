"""milestones: контрольные точки и паспорт проекта

Контрольная точка — обязательство перед человеком (дата + артефакт + приёмщик),
в отличие от эпика (модуль разработки). Связь эпиков и задач с КТ — членство
многие-ко-многим через `milestone_items`, без владения в любую сторону.

Паспорт проекта (точка А → точка Б + критерий завершения + бюджет времени +
крайний срок) — то, без чего проект нельзя увести в производство.

Autogenerate дополнительно предлагал создать FK на projects.parent_id,
tasks.epic_id/sprint_id/reviewer_id/lease_owner/source_project_id/injected_by —
это ложное срабатывание: эти связи намеренно живут только на ORM-уровне
(batch-режим SQLite не умеет безымянные FK). Из миграции они убраны.

Revision ID: 99b816fd1c7a
Revises: b1c4e7f20a91
Create Date: 2026-08-29 01:25:39.733338

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '99b816fd1c7a'
down_revision: Union[str, Sequence[str], None] = 'b1c4e7f20a91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'milestones',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('slug', sa.String(length=100), nullable=True),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('criterion', sa.Text(), nullable=True),
        sa.Column('due_date', sa.DateTime(), nullable=True),
        sa.Column('acceptor_id', sa.String(length=36), nullable=True),
        sa.Column('artifact_url', sa.String(length=1000), nullable=True),
        sa.Column(
            'state',
            sa.String(length=20),
            server_default='planning',
            nullable=False,
        ),
        sa.Column('outcome', sa.String(length=20), nullable=True),
        sa.Column('track', sa.String(length=100), nullable=True),
        sa.Column('b24_task_id', sa.String(length=100), nullable=True),
        sa.Column('backend_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.Column('archived_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('planning','ready_for_ai','ai_running','ready_for_review',"
            "'human_verified','client_accepted','blocked','cancelled')",
            name='ck_milestones_state',
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('confirmed','adjusted','dropped')",
            name='ck_milestones_outcome',
        ),
        sa.ForeignKeyConstraint(
            ['acceptor_id'], ['participants.id'], name='fk_milestones_acceptor'
        ),
        sa.ForeignKeyConstraint(
            ['project_id'], ['projects.id'], name='fk_milestones_project'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug', name='uq_milestones_slug'),
    )
    op.create_index('idx_milestones_project', 'milestones', ['project_id'])
    op.create_index('idx_milestones_state', 'milestones', ['state'])
    op.create_index('idx_milestones_due', 'milestones', ['due_date'])

    op.create_table(
        'milestone_items',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('milestone_id', sa.String(length=36), nullable=False),
        sa.Column('item_kind', sa.String(length=10), nullable=False),
        sa.Column('item_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "item_kind IN ('epic','task')", name='ck_milestone_items_kind'
        ),
        sa.ForeignKeyConstraint(
            ['milestone_id'],
            ['milestones.id'],
            name='fk_milestone_items_milestone',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_milestone_items_ms', 'milestone_items', ['milestone_id'])
    op.create_index(
        'uq_milestone_items',
        'milestone_items',
        ['milestone_id', 'item_kind', 'item_id'],
        unique=True,
    )

    # Паспорт проекта. batch_alter_table обязателен: SQLite не умеет ALTER.
    with op.batch_alter_table('projects') as batch_op:
        batch_op.add_column(sa.Column('point_a', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('point_b', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('done_criteria', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('appetite_days', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('hard_deadline', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('b24_item_id', sa.String(length=100), nullable=True))
        batch_op.add_column(
            sa.Column('b24_entity_type_id', sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('projects') as batch_op:
        batch_op.drop_column('b24_entity_type_id')
        batch_op.drop_column('b24_item_id')
        batch_op.drop_column('hard_deadline')
        batch_op.drop_column('appetite_days')
        batch_op.drop_column('done_criteria')
        batch_op.drop_column('point_b')
        batch_op.drop_column('point_a')

    op.drop_index('uq_milestone_items', table_name='milestone_items')
    op.drop_index('idx_milestone_items_ms', table_name='milestone_items')
    op.drop_table('milestone_items')

    op.drop_index('idx_milestones_due', table_name='milestones')
    op.drop_index('idx_milestones_state', table_name='milestones')
    op.drop_index('idx_milestones_project', table_name='milestones')
    op.drop_table('milestones')
