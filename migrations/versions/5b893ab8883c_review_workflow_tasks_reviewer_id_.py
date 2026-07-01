"""review workflow: tasks.reviewer_id + comments table

Revision ID: 5b893ab8883c
Revises: 13f6db0144ed
Create Date: 2026-06-29 18:49:24.206993

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b893ab8883c'
down_revision: Union[str, Sequence[str], None] = '13f6db0144ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """tasks.reviewer_id + таблица comments. Спурьёзные FK автогена (SQLite не
    хранит имена FK) убраны — только intended-изменения. reviewer_id — прямой
    ADD COLUMN (SQLite поддерживает)."""
    op.add_column('tasks', sa.Column('reviewer_id', sa.String(length=36), nullable=True))
    op.create_table(
        'comments',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('task_id', sa.String(length=36), nullable=False),
        sa.Column('author_id', sa.String(length=36), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('kind', sa.String(length=20), server_default='comment', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('comment','submit','approve','reject','reopen')",
            name='ck_comments_kind',
        ),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['author_id'], ['participants.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_comments_task', 'comments', ['task_id'], unique=False)


def downgrade() -> None:
    """Откат: убрать comments + tasks.reviewer_id."""
    op.drop_index('idx_comments_task', table_name='comments')
    op.drop_table('comments')
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_column('reviewer_id')
