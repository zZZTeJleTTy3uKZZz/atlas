"""sprints table + tasks.sprint_id

Revision ID: 13f6db0144ed
Revises: b8c9d0e1f2a3
Create Date: 2026-06-29 11:58:45.211414

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '13f6db0144ed'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создать таблицу sprints + колонку tasks.sprint_id.

    NB: автоген SQLite добавлял спурьёзные FK на epics/projects/tasks (SQLite не
    хранит имена FK → alembic считал их «отсутствующими») — убраны. Делаем только
    intended-изменения. tasks.sprint_id добавляем прямым ADD COLUMN (SQLite это
    поддерживает; batch-пересоздание tasks ради nullable-колонки избыточно/рискованно).
    """
    op.create_table(
        'sprints',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('slug', sa.String(length=100), nullable=True),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('goal', sa.Text(), nullable=True),
        sa.Column('starts_at', sa.DateTime(), nullable=True),
        sa.Column('ends_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='planning', nullable=False),
        sa.Column('planned_velocity', sa.Integer(), nullable=True),
        sa.Column('retro_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('archived_at', sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('planning','active','closed','cancelled')",
            name='ck_sprints_status',
        ),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )
    op.create_index('idx_sprints_project', 'sprints', ['project_id'], unique=False)
    op.add_column('tasks', sa.Column('sprint_id', sa.String(length=36), nullable=True))


def downgrade() -> None:
    """Откат: убрать tasks.sprint_id + таблицу sprints."""
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('sprint_id')
    op.drop_index('idx_sprints_project', table_name='sprints')
    op.drop_table('sprints')
