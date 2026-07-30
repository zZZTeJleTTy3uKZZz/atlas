"""#1301: обратная конвертация task/epic → backlog с сохранением структуры.

Добавляет в ``backlog_items``:

- ``parent_id``   — самоссылка: у задач эпика, уведённых в пул, родителем
                    становится backlog-запись самого эпика. Без неё каскад
                    разваливается: возврат эпика не знает, какие записи ему
                    принадлежали.
- ``origin_kind`` — откуда пришла запись (``task`` | ``epic``); отличается от
                    ``source`` (тот про происхождение идеи: native/legacy/…).
- ``origin_ref``  — slug сущности до увода: возврат восстанавливает прежний
                    slug, а не генерирует новый, поэтому ссылки в описаниях и
                    коммитах остаются рабочими.
- ``origin_payload`` — JSON с полями, которых нет в BacklogItem (ЦКП, описание,
                    epic/assignee, оценки). Без него возврат теряет содержимое:
                    сейчас единственный путь — cancel + ручной backlog add с
                    копипастом.

Все колонки nullable — существующие записи валидны без правки данных.

Revision ID: b1c4e7f20a91
Revises: e05f29093f6e
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1c4e7f20a91"
down_revision: Union[str, Sequence[str], None] = "e05f29093f6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table — SQLite не умеет ALTER ADD CONSTRAINT.
    with op.batch_alter_table("backlog_items") as batch:
        batch.add_column(sa.Column("parent_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("origin_kind", sa.String(20), nullable=True))
        batch.add_column(sa.Column("origin_ref", sa.String(100), nullable=True))
        batch.add_column(sa.Column("origin_payload", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_backlog_items_parent_id",
            "backlog_items", ["parent_id"], ["id"],
            ondelete="SET NULL",
        )
        # Возврат эпика пишет converted_kind='epic' — прежний CHECK знал только
        # task|project и ронял операцию.
        batch.drop_constraint("ck_backlog_items_converted_kind", type_="check")
        batch.create_check_constraint(
            "ck_backlog_items_converted_kind",
            "converted_kind IS NULL OR converted_kind IN ('task','project','epic')",
        )


def downgrade() -> None:
    with op.batch_alter_table("backlog_items") as batch:
        batch.drop_constraint("ck_backlog_items_converted_kind", type_="check")
        batch.create_check_constraint(
            "ck_backlog_items_converted_kind",
            "converted_kind IS NULL OR converted_kind IN ('task','project')",
        )
        batch.drop_constraint("fk_backlog_items_parent_id", type_="foreignkey")
        batch.drop_column("origin_payload")
        batch.drop_column("origin_ref")
        batch.drop_column("origin_kind")
        batch.drop_column("parent_id")
