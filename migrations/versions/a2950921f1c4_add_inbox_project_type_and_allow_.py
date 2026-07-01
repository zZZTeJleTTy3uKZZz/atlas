"""add inbox project_type and allow archived_group inbox

Revision ID: a2950921f1c4
Revises: d88bf4f8a629
Create Date: 2026-04-24 21:44:01.916552

Что добавляет:
- Расширяет CHECK constraint `ck_projects_archived_group` — теперь принимает
  также значение 'inbox' (в дополнение к clients/products/tests).
- Seed нового project_type 'inbox' — материалы на переработку. Физический
  layout: PROJECT/_Inbox/<slug>/.

Логическое обоснование:
- inbox — особая зона портфеля для сырых артефактов, которые ждут разбора
  (через агентов или вручную) перед инкорпорацией в навык/продукт.
- Маппинг TYPE_TO_GROUP['inbox'] = 'inbox', GROUP_FOLDER_NAMES['inbox'] = '_Inbox'
  живёт в src/atlas/pm/paths.py (код, не схема БД).

Roundtrip: upgrade → downgrade → upgrade — clean.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a2950921f1c4'
down_revision: Union[str, Sequence[str], None] = 'd88bf4f8a629'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --------------------------------------------------------------------------- #
# Seed payload                                                                #
# --------------------------------------------------------------------------- #

_PROJECT_TYPE_INBOX = {
    "slug": "inbox",
    "name": "Inbox",
    "description": (
        "Материалы на переработку — сырые артефакты, извлечённые из других "
        "проектов, которые ждут разбора (через агентов или вручную) перед "
        "инкорпорацией в навык/продукт. Физический layout: PROJECT/_Inbox/<slug>/."
    ),
    "color": "#F59E0B",
}


# --------------------------------------------------------------------------- #
# Upgrade                                                                     #
# --------------------------------------------------------------------------- #


def upgrade() -> None:
    """Upgrade schema."""
    # 1. CHECK constraint archived_group — расширяем до 4 значений ---------
    # SQLite не умеет ALTER TABLE ... ALTER CONSTRAINT; batch_alter_table
    # делает пересборку таблицы под капотом.
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_constraint('ck_projects_archived_group', type_='check')
        batch_op.create_check_constraint(
            'ck_projects_archived_group',
            "archived_group IS NULL OR archived_group IN "
            "('clients','products','tests','inbox')",
        )

    # 2. Seed нового project_type --------------------------------------------
    project_types_tbl = sa.table(
        'project_types',
        sa.column('id', sa.String),
        sa.column('slug', sa.String),
        sa.column('name', sa.String),
        sa.column('description', sa.Text),
        sa.column('color', sa.String),
        sa.column('is_archived', sa.Integer),
        sa.column('created_at', sa.DateTime),
    )
    # datetime.utcnow() OK для миграций — у alembic своя жизнь, наш msk_now
    # тут недоступен (avoid app imports в ревизиях).
    now = datetime.datetime.utcnow()
    op.bulk_insert(
        project_types_tbl,
        [
            {
                'id': str(uuid.uuid4()),
                'slug': _PROJECT_TYPE_INBOX['slug'],
                'name': _PROJECT_TYPE_INBOX['name'],
                'description': _PROJECT_TYPE_INBOX['description'],
                'color': _PROJECT_TYPE_INBOX['color'],
                'is_archived': 0,
                'created_at': now,
            }
        ],
    )


# --------------------------------------------------------------------------- #
# Downgrade                                                                   #
# --------------------------------------------------------------------------- #


def downgrade() -> None:
    """Downgrade schema."""
    # 1. Удалить seed строку -------------------------------------------------
    op.execute(sa.text("DELETE FROM project_types WHERE slug = 'inbox'"))

    # 2. Вернуть старый CHECK ------------------------------------------------
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_constraint('ck_projects_archived_group', type_='check')
        batch_op.create_check_constraint(
            'ck_projects_archived_group',
            "archived_group IS NULL OR archived_group IN "
            "('clients','products','tests')",
        )
