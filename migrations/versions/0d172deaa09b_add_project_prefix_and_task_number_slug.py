"""add project prefix and task number slug

Revision ID: 0d172deaa09b
Revises: 0a6b3db9f107
Create Date: 2026-04-24 02:54:26.693479

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0d172deaa09b'
down_revision: Union[str, Sequence[str], None] = '0a6b3db9f107'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('prefix', sa.String(length=5), nullable=True))
        batch_op.create_unique_constraint('uq_projects_prefix', ['prefix'])

    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('number', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('slug', sa.String(length=100), nullable=True))
        batch_op.create_unique_constraint('uq_tasks_slug', ['slug'])
        batch_op.create_unique_constraint('uq_tasks_number', ['number'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_constraint('uq_tasks_number', type_='unique')
        batch_op.drop_constraint('uq_tasks_slug', type_='unique')
        batch_op.drop_column('slug')
        batch_op.drop_column('number')

    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_constraint('uq_projects_prefix', type_='unique')
        batch_op.drop_column('prefix')
