"""add git fields to projects

Revision ID: 237c08c450f6
Revises: a2950921f1c4
Create Date: 2026-04-25 20:09:00.261484

Что добавляет:
- 5 git-related колонок в `projects`:
    - `git_remote_url TEXT NULL`           — URL удалённого репозитория.
    - `git_default_branch TEXT NOT NULL DEFAULT 'main'` — имя default ветки.
    - `git_provider TEXT NULL`             — 'gitlab' | 'github' | NULL.
    - `git_initialized_at DATETIME NULL`   — момент `atlas projects git init`.
    - `git_last_pushed_at DATETIME NULL`   — момент последнего push.
- CHECK ck_projects_git_provider — пропускает 'gitlab', 'github', NULL.

Логическое обоснование:
- git_remote_url + git_provider — два связанных поля: provider маркирует,
  через какой backend ходить (glab vs gh), URL — собственно путь до репо.
- git_default_branch — храним явно, потому что при init задаём ветку в local
  git config, и при `status`/`push` нужно знать имя ветки без git-запроса.
- timestamps (initialized_at / last_pushed_at) — для audittrail и UI.

Roundtrip: upgrade → downgrade → upgrade — clean.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '237c08c450f6'
down_revision: Union[str, Sequence[str], None] = 'a2950921f1c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# --------------------------------------------------------------------------- #
# Upgrade                                                                     #
# --------------------------------------------------------------------------- #


def upgrade() -> None:
    """Upgrade schema — add 5 git fields + CHECK on git_provider."""
    # SQLite: ALTER TABLE ADD COLUMN — поддерживается напрямую (без batch),
    # но CHECK constraint мы хотим добавить аккуратно — батчем.
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('git_remote_url', sa.String(length=500), nullable=True),
        )
        batch_op.add_column(
            sa.Column(
                'git_default_branch',
                sa.String(length=100),
                nullable=False,
                server_default='main',
            ),
        )
        batch_op.add_column(
            sa.Column('git_provider', sa.String(length=20), nullable=True),
        )
        batch_op.add_column(
            sa.Column('git_initialized_at', sa.DateTime(), nullable=True),
        )
        batch_op.add_column(
            sa.Column('git_last_pushed_at', sa.DateTime(), nullable=True),
        )
        batch_op.create_check_constraint(
            'ck_projects_git_provider',
            "git_provider IS NULL OR git_provider IN ('gitlab','github')",
        )


# --------------------------------------------------------------------------- #
# Downgrade                                                                   #
# --------------------------------------------------------------------------- #


def downgrade() -> None:
    """Downgrade schema — drop CHECK + 5 git fields."""
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_constraint('ck_projects_git_provider', type_='check')
        batch_op.drop_column('git_last_pushed_at')
        batch_op.drop_column('git_initialized_at')
        batch_op.drop_column('git_provider')
        batch_op.drop_column('git_default_branch')
        batch_op.drop_column('git_remote_url')
