"""create issues table

Revision ID: e05f29093f6e
Revises: 575396edfd76
Create Date: 2026-06-30 15:54:25.624651

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e05f29093f6e'
down_revision: Union[str, Sequence[str], None] = '575396edfd76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создать таблицу issues — структурированные жалобы/handoff (issuekit)."""
    op.create_table(
        "issues",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="open", nullable=False),
        sa.Column("author_id", sa.String(length=36), nullable=True),
        sa.Column("target_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["participants.id"]),
        sa.ForeignKeyConstraint(["target_id"], ["participants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
        sa.CheckConstraint("kind IN ('bug','feature','handoff')", name="ck_issues_kind"),
        sa.CheckConstraint("status IN ('open','resolved','wontfix')", name="ck_issues_status"),
    )
    op.create_index("idx_issues_task", "issues", ["task_id"])
    op.create_index("idx_issues_status", "issues", ["status"])


def downgrade() -> None:
    """Снести таблицу issues."""
    op.drop_index("idx_issues_status", table_name="issues")
    op.drop_index("idx_issues_task", table_name="issues")
    op.drop_table("issues")
