"""tags and archive engine

Revision ID: d88bf4f8a629
Revises: c55f75e76e5b
Create Date: 2026-04-24 19:35:21.046582

Что добавляет:
- tags: справочник тегов (owner/stack/domain/other).
- project_tags: M:N с ondelete=CASCADE.
- projects.renewal_count (INT NOT NULL DEFAULT 0).
- projects.archived_group (TEXT NULL с CHECK IN ('clients','products','tests')).
- Индексы: idx_tags_category, idx_project_tags_tag.
- Seed: project_type 'test', project_statuses (idea, research, planned,
  paused, frozen, completed).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd88bf4f8a629'
down_revision: Union[str, Sequence[str], None] = 'c55f75e76e5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --------------------------------------------------------------------------- #
# Seed payload                                                                #
# --------------------------------------------------------------------------- #

_PROJECT_TYPE_TEST = {
    "slug": "test",
    "name": "Экспериментальные проекты",
    "description": (
        "Проекты в стадии быстрого прототипирования / проверки гипотез (папка Tests/)"
    ),
    "color": "#6B7280",
}

_NEW_PROJECT_STATUSES = [
    {
        "slug": "idea",
        "name": "Идея",
        "order_idx": 1,
        "description": "Зафиксировано, ничего не начато",
    },
    {
        "slug": "research",
        "name": "Ресёрч",
        "order_idx": 2,
        "description": "Идёт изучение / deep research перед принятием решения",
    },
    {
        "slug": "planned",
        "name": "В планах",
        "order_idx": 3,
        "description": "Решили делать, ещё не стартовали",
    },
    {
        "slug": "paused",
        "name": "На паузе",
        "order_idx": 7,
        "description": "Временно приостановлен; есть причина возврата",
    },
    {
        "slug": "frozen",
        "name": "Заморожен",
        "order_idx": 8,
        "description": "Надолго отложен; низкая вероятность возврата, но остался канон",
    },
    {
        "slug": "completed",
        "name": "Завершён",
        "order_idx": 9,
        "description": (
            "Работа закончена (разовая); обычно не возвращаемся, "
            "но для клиентов возможен renew"
        ),
    },
]


# --------------------------------------------------------------------------- #
# Upgrade                                                                     #
# --------------------------------------------------------------------------- #


def upgrade() -> None:
    """Upgrade schema."""
    # 1. tags ---------------------------------------------------------------
    op.create_table(
        'tags',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('slug', sa.String(length=50), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('color', sa.String(length=20), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "category IN ('owner','stack','domain','other')",
            name='ck_tags_category',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )
    with op.batch_alter_table('tags', schema=None) as batch_op:
        batch_op.create_index('idx_tags_category', ['category'], unique=False)

    # 2. project_tags -------------------------------------------------------
    op.create_table(
        'project_tags',
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('tag_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['project_id'], ['projects.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['tag_id'], ['tags.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('project_id', 'tag_id'),
    )
    with op.batch_alter_table('project_tags', schema=None) as batch_op:
        batch_op.create_index('idx_project_tags_tag', ['tag_id'], unique=False)

    # 3. projects.renewal_count + projects.archived_group -------------------
    # renewal_count добавляем с server_default='0' чтобы NOT NULL не падал на
    # существующих строках. После заполнения дефолт оставляем — на уровне ORM
    # default=0 тоже стоит.
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'renewal_count',
                sa.Integer(),
                nullable=False,
                server_default='0',
            )
        )
        batch_op.add_column(
            sa.Column('archived_group', sa.String(length=20), nullable=True)
        )
        batch_op.create_check_constraint(
            'ck_projects_archived_group',
            "archived_group IS NULL OR archived_group IN ('clients','products','tests')",
        )

    # 4. Seed data ----------------------------------------------------------
    # Используем bulk_insert на лёгких ad-hoc таблицах (без привязки к моделям).
    now = datetime.utcnow()

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
    op.bulk_insert(
        project_types_tbl,
        [
            {
                'id': str(uuid.uuid4()),
                'slug': _PROJECT_TYPE_TEST['slug'],
                'name': _PROJECT_TYPE_TEST['name'],
                'description': _PROJECT_TYPE_TEST['description'],
                'color': _PROJECT_TYPE_TEST['color'],
                'is_archived': 0,
                'created_at': now,
            }
        ],
    )

    project_statuses_tbl = sa.table(
        'project_statuses',
        sa.column('id', sa.String),
        sa.column('slug', sa.String),
        sa.column('name', sa.String),
        sa.column('description', sa.Text),
        sa.column('order_idx', sa.Integer),
        sa.column('created_at', sa.DateTime),
    )
    op.bulk_insert(
        project_statuses_tbl,
        [
            {
                'id': str(uuid.uuid4()),
                'slug': ps['slug'],
                'name': ps['name'],
                'description': ps['description'],
                'order_idx': ps['order_idx'],
                'created_at': now,
            }
            for ps in _NEW_PROJECT_STATUSES
        ],
    )


# --------------------------------------------------------------------------- #
# Downgrade                                                                   #
# --------------------------------------------------------------------------- #


def downgrade() -> None:
    """Downgrade schema."""
    # 1. Удаляем seed-строки -------------------------------------------------
    op.execute(
        sa.text("DELETE FROM project_statuses WHERE slug IN "
                "('idea','research','planned','paused','frozen','completed')")
    )
    op.execute(sa.text("DELETE FROM project_types WHERE slug = 'test'"))

    # 2. projects: откатываем колонки + CHECK
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_constraint('ck_projects_archived_group', type_='check')
        batch_op.drop_column('archived_group')
        batch_op.drop_column('renewal_count')

    # 3. project_tags
    with op.batch_alter_table('project_tags', schema=None) as batch_op:
        batch_op.drop_index('idx_project_tags_tag')
    op.drop_table('project_tags')

    # 4. tags
    with op.batch_alter_table('tags', schema=None) as batch_op:
        batch_op.drop_index('idx_tags_category')
    op.drop_table('tags')
