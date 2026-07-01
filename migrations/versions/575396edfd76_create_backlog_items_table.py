"""create backlog_items table

Revision ID: 575396edfd76
Revises: ad1bb3559baf
Create Date: 2026-06-30 13:56:01.941101

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '575396edfd76'
down_revision: Union[str, Sequence[str], None] = 'ad1bb3559baf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создать таблицу backlog_items — пул идей-интейка (DB-first, лёгкая запись)."""
    op.create_table(
        "backlog_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("priority", sa.String(length=3), nullable=True),
        sa.Column("md_path", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="open", nullable=False),
        sa.Column("converted_kind", sa.String(length=20), nullable=True),
        sa.Column("converted_ref", sa.String(length=100), nullable=True),
        sa.Column("source", sa.String(length=20), server_default="native", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
        sa.CheckConstraint(
            "status IN ('open','converted','archived')",
            name="ck_backlog_items_status",
        ),
        sa.CheckConstraint(
            "converted_kind IS NULL OR converted_kind IN ('task','project')",
            name="ck_backlog_items_converted_kind",
        ),
    )
    op.create_index("idx_backlog_items_project", "backlog_items", ["project_id"])
    op.create_index("idx_backlog_items_status", "backlog_items", ["status"])


def downgrade() -> None:
    """Снести таблицу backlog_items."""
    op.drop_index("idx_backlog_items_status", table_name="backlog_items")
    op.drop_index("idx_backlog_items_project", table_name="backlog_items")
    op.drop_table("backlog_items")
