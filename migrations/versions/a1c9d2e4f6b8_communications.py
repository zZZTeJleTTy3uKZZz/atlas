"""точки коммуникации: встречи, созвоны, заметки и то, что из них вынули

Revision ID: a1c9d2e4f6b8
Revises: 3b019100145c
Create Date: 2026-09-08

Задачи и решения в базе были, а разговора, из которого они родились, — нет: он
оставался файлом в папке проекта или сообщением в чате, и вопрос «что вообще
было по этому клиенту» не имел ответа.

Три таблицы, а не одна, потому что одна встреча кормит несколько проектов и
разговор о клиенте идёт не подряд: коммуникация целиком → её куски по проектам →
факты, вынутые из куска, с дословной цитатой.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c9d2e4f6b8"
down_revision: Union[str, Sequence[str], None] = "3b019100145c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "communications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        # Текст живёт в файле: он версионируется с проектом и не редактируется.
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("counterparty_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="parsed", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["counterparty_id"], ["counterparties.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('meeting','call','chat','voice_note','presentation')",
            name="ck_communications_kind",
        ),
        sa.CheckConstraint("side IN ('internal','client')", name="ck_communications_side"),
        sa.CheckConstraint(
            "status IN ('raw','parsed','reviewed')", name="ck_communications_status"
        ),
    )
    op.create_index("idx_communications_when", "communications", ["occurred_at"])
    op.create_index(
        "idx_communications_counterparty", "communications", ["counterparty_id"]
    )

    op.create_table(
        "communication_parts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("communication_id", sa.String(length=36), nullable=False),
        # null допустим: кусок бывает не разложен по проектам сразу.
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("tile_from", sa.Integer(), nullable=True),
        sa.Column("tile_to", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["communication_id"], ["communications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_comm_parts_comm", "communication_parts", ["communication_id"])
    op.create_index("idx_comm_parts_project", "communication_parts", ["project_id"])

    op.create_table(
        "communication_facts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("part_id", sa.String(length=36), nullable=False),
        sa.Column("fact_kind", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        # Цитата проверяется поиском по исходнику — численная проверка честности
        # пересказа, не требующая человека.
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("owner", sa.String(length=100), nullable=True),
        sa.Column("due", sa.String(length=100), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("milestone_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["part_id"], ["communication_parts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["milestone_id"], ["milestones.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "fact_kind IN ('status','decision','checkpoint','risk','task','open')",
            name="ck_comm_facts_kind",
        ),
    )
    op.create_index("idx_comm_facts_part", "communication_facts", ["part_id"])
    op.create_index("idx_comm_facts_task", "communication_facts", ["task_id"])


def downgrade() -> None:
    op.drop_table("communication_facts")
    op.drop_table("communication_parts")
    op.drop_table("communications")
