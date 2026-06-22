"""add provenance fields to tasks and epics

Revision ID: c1d2e3f4a5b6
Revises: b1d0100413fc
Create Date: 2026-06-22 12:00:00.000000

Provenance: происхождение задач/эпиков (нативные vs инжектированные из
проекта-источника). Одинаковые 5 колонок на tasks и epics + description у
epics (у tasks description уже есть).

Колонки (tasks И epics):
- source_project_id (TEXT NULL) — проект-источник (NULL = нативная).
- origin (TEXT NOT NULL, server_default 'native') —
  native|injected|imported|split; CHECK ck_tasks_origin / ck_epics_origin.
- rationale (TEXT NULL) — почему/по какому принципу заведена.
- injected_by (TEXT NULL) — кто инжектировал (participant).
- injected_at (DATETIME NULL) — когда инжектировал.
Дополнительно epics: description (TEXT NULL).

Индексы: idx_tasks_source_project, idx_epics_source_project.

Нюанс SQLite/batch (как в 52cc9ef055b0): FK source_project_id → projects.id
и injected_by → participants.id НЕ создаются как constraint — безымянный FK в
batch падает ("Constraint must have a name"), а целостность держится в коде/ORM
(на уровне моделей ForeignKey оставлен для метаданных). Добавляем только
колонки + индексы + CHECK origin.

Все колонки nullable либо имеют server_default='native' (origin) — безопасно
для существующих строк.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b1d0100413fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — provenance-поля на tasks и epics."""
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('source_project_id', sa.String(length=36), nullable=True),
        )
        batch_op.add_column(
            sa.Column(
                'origin',
                sa.String(length=20),
                nullable=False,
                server_default='native',
            ),
        )
        batch_op.add_column(sa.Column('rationale', sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column('injected_by', sa.String(length=36), nullable=True),
        )
        batch_op.add_column(sa.Column('injected_at', sa.DateTime(), nullable=True))
        batch_op.create_check_constraint(
            'ck_tasks_origin',
            "origin IN ('native','injected','imported','split')",
        )
        batch_op.create_index('idx_tasks_source_project', ['source_project_id'], unique=False)

    with op.batch_alter_table('epics', schema=None) as batch_op:
        batch_op.add_column(sa.Column('description', sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column('source_project_id', sa.String(length=36), nullable=True),
        )
        batch_op.add_column(
            sa.Column(
                'origin',
                sa.String(length=20),
                nullable=False,
                server_default='native',
            ),
        )
        batch_op.add_column(sa.Column('rationale', sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column('injected_by', sa.String(length=36), nullable=True),
        )
        batch_op.add_column(sa.Column('injected_at', sa.DateTime(), nullable=True))
        batch_op.create_check_constraint(
            'ck_epics_origin',
            "origin IN ('native','injected','imported','split')",
        )
        batch_op.create_index('idx_epics_source_project', ['source_project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema — drop provenance-полей (обратный порядок)."""
    with op.batch_alter_table('epics', schema=None) as batch_op:
        batch_op.drop_index('idx_epics_source_project')
        batch_op.drop_constraint('ck_epics_origin', type_='check')
        batch_op.drop_column('injected_at')
        batch_op.drop_column('injected_by')
        batch_op.drop_column('rationale')
        batch_op.drop_column('origin')
        batch_op.drop_column('source_project_id')
        batch_op.drop_column('description')

    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index('idx_tasks_source_project')
        batch_op.drop_constraint('ck_tasks_origin', type_='check')
        batch_op.drop_column('injected_at')
        batch_op.drop_column('injected_by')
        batch_op.drop_column('rationale')
        batch_op.drop_column('origin')
        batch_op.drop_column('source_project_id')
