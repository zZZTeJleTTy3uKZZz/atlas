"""canon types: storage_group on project_types + parent_id on projects

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f4a5b6
Create Date: 2026-06-23 10:00:00.000000

Канон типов проектов — свод хардкод-источников в БД:
- project_types.storage_group (TEXT NULL) — физическая группа размещения
  типа на диске (clients|products|tests|inbox). Свод paths.TYPE_TO_GROUP.
  Backfill существующих строк по текущей карте TYPE_TO_GROUP:
    client-project → clients
    test           → tests
    inbox          → inbox
    остальное      → products
- projects.parent_id (TEXT NULL) — родительский проект (иерархия портфеля).
  Индекс idx_projects_parent.

Нюанс SQLite/batch (как в c1d2e3f4a5b6 provenance): FK parent_id → projects.id
НЕ создаётся как DB-constraint — безымянный FK в batch падает ("Constraint
must have a name"); целостность держится на ORM-уровне (ForeignKey в модели
оставлен для метаданных). Добавляем только колонку + индекс.

Все колонки nullable — безопасно для существующих строк.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Текущая карта paths.TYPE_TO_GROUP (свод в БД на момент миграции).
# Дублируем явно, а не импортируем — миграция должна быть стабильна к
# будущим правкам paths.py.
TYPE_TO_GROUP: dict[str, str] = {
    "client-project": "clients",
    "business-product": "products",
    "personal-utility": "products",
    "personal-project": "products",
    "shared-infrastructure": "products",
    "test": "tests",
    "inbox": "inbox",
}


def upgrade() -> None:
    """Upgrade schema — storage_group + parent_id + backfill."""
    with op.batch_alter_table("project_types", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("storage_group", sa.String(length=20), nullable=True)
        )

    # Backfill storage_group по текущей карте TYPE_TO_GROUP (по slug).
    # client-project→clients, test→tests, inbox→inbox, остальное→products.
    bind = op.get_bind()
    explicit_slugs = list(TYPE_TO_GROUP.keys())
    for slug, group in TYPE_TO_GROUP.items():
        bind.execute(
            sa.text(
                "UPDATE project_types SET storage_group = :grp WHERE slug = :slug"
            ),
            {"grp": group, "slug": slug},
        )
    # Все прочие типы (не в явной карте) → products.
    placeholders = ", ".join(f":s{i}" for i in range(len(explicit_slugs)))
    params = {f"s{i}": s for i, s in enumerate(explicit_slugs)}
    bind.execute(
        sa.text(
            "UPDATE project_types SET storage_group = 'products' "
            f"WHERE slug NOT IN ({placeholders})"
        ),
        params,
    )

    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("parent_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_index("idx_projects_parent", ["parent_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema — drop parent_id + storage_group (обратный порядок)."""
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_index("idx_projects_parent")
        batch_op.drop_column("parent_id")

    with op.batch_alter_table("project_types", schema=None) as batch_op:
        batch_op.drop_column("storage_group")
