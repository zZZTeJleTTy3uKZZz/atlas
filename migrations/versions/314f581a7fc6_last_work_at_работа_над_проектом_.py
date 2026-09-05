"""last_work_at: работа над проектом отдельно от правки карточки

Revision ID: 314f581a7fc6
Revises: 99b816fd1c7a
Create Date: 2026-08-29 19:26:28.975963

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '314f581a7fc6'
down_revision: Union[str, Sequence[str], None] = '99b816fd1c7a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Отдельное поле под работу над проектом.

    last_touched_at обновляется при любой правке карточки — из-за этого проект,
    которому вчера поставили git, выглядел свежим, а клиент, по чьему модулю
    работа шла сегодня, числился заброшенным с июля. Разводим два смысла.
    """
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("last_work_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("last_work_at")
