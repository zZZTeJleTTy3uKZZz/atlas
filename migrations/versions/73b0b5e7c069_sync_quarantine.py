"""sync_quarantine: отложенные события приёма

Автогенерация вместе с таблицей предложила расставить полтора десятка внешних
ключей на epics/tasks/projects — расхождение живой базы с моделями, накопленное
раньше и к этой правке отношения не имеющее. Тащить его сюда значило бы менять
схему боевых баз заодно, под чужим именем ревизии; оставлена только новая
таблица.

Revision ID: 73b0b5e7c069
Revises: a1c9d2e4f6b8
Create Date: 2026-09-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "73b0b5e7c069"
down_revision: Union[str, Sequence[str], None] = "a1c9d2e4f6b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sync_quarantine",
        sa.Column("envelope_id", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=50), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("occurred_at", sa.String(length=40), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("envelope_id"),
    )


def downgrade() -> None:
    op.drop_table("sync_quarantine")
