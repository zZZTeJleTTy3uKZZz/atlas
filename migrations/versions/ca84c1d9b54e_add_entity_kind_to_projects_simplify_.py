"""add entity_kind to projects + simplify statuses

Revision ID: ca84c1d9b54e
Revises: 237c08c450f6
Create Date: 2026-04-29 17:26:39.044459

W45-39: Entity model refactor.

Что меняется:
1. Добавляется колонка ``projects.entity_kind`` (VARCHAR(20), NOT NULL,
   default 'project') с CHECK constraint
   ``entity_kind IN ('project','idea','inbox')``.
2. Backfill: проекты с `type=inbox` (project_types.slug='inbox') получают
   `entity_kind='inbox'`. Все остальные → 'project' (через server_default).
3. Backfill статусов: legacy-значения, которые мы убираем из канонической
   палитры, конвертятся:
     - status=`idea`         → entity_kind=idea, status=active
       (сама запись остаётся `Project`-like, но переезжает в idea-bucket)
     - status=`research`/`maintained`/`planned`/`graduating` → status=active
     - status=`dormant`/`frozen`                              → status=paused
4. Канонические статусы после миграции: `active`, `paused`, `archived`,
   `cancelled` (новый), `experiment`. Старые-неиспользуемые ENUM-значения
   на этом уровне не дропаем (статус — внешняя таблица, ничего не ломается).
   Новый статус `cancelled` добавляется в seeds (миграция данных не трогает
   project_statuses-таблицу).

NOTE: SQLite не поддерживает ALTER COLUMN с CHECK через ALTER TABLE напрямую.
Для CHECK constraint используем batch_alter_table (alembic-ом включается
recreate-table mode).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ca84c1d9b54e"
down_revision: Union[str, Sequence[str], None] = "237c08c450f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


VALID_ENTITY_KINDS = ("project", "idea", "inbox")


def upgrade() -> None:
    """Upgrade schema: add entity_kind column with CHECK + backfill."""
    bind = op.get_bind()

    # 1. Добавить колонку с server_default='project' (так все existing rows
    #    получат 'project' автоматически).
    with op.batch_alter_table("projects", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "entity_kind",
                sa.String(length=20),
                server_default=sa.text("'project'"),
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_projects_entity_kind",
            f"entity_kind IN {VALID_ENTITY_KINDS}",
        )

    # 2. Backfill: type=inbox → entity_kind=inbox.
    inbox_type_row = bind.execute(
        sa.text("SELECT id FROM project_types WHERE slug = 'inbox'")
    ).first()
    if inbox_type_row is not None:
        inbox_type_id = inbox_type_row[0]
        bind.execute(
            sa.text(
                "UPDATE projects SET entity_kind = 'inbox' "
                "WHERE type_id = :tid"
            ),
            {"tid": inbox_type_id},
        )

    # 3. Backfill: status=idea → entity_kind=idea, status=active.
    #    (А статус 'idea' — пока оставляем в project_statuses, чтобы не ломать
    #    другие FK; зачищать лишние статусы будем отдельной миграцией seeds.)
    idea_status_row = bind.execute(
        sa.text("SELECT id FROM project_statuses WHERE slug = 'idea'")
    ).first()
    active_status_row = bind.execute(
        sa.text("SELECT id FROM project_statuses WHERE slug = 'active'")
    ).first()
    if idea_status_row is not None and active_status_row is not None:
        idea_status_id = idea_status_row[0]
        active_status_id = active_status_row[0]
        bind.execute(
            sa.text(
                "UPDATE projects "
                "SET entity_kind = 'idea', status_id = :active_id "
                "WHERE status_id = :idea_id"
            ),
            {"active_id": active_status_id, "idea_id": idea_status_id},
        )

    # 4. Конверсия статусов research/maintained/planned/graduating → active,
    #    dormant/frozen → paused.
    _convert_statuses(
        bind,
        {
            "research": "active",
            "maintained": "active",
            "planned": "active",
            "graduating": "active",
            "dormant": "paused",
            "frozen": "paused",
        },
    )

    # 5. Добавить новый статус 'cancelled' в project_statuses, если его нет.
    cancelled_exists = bind.execute(
        sa.text("SELECT 1 FROM project_statuses WHERE slug = 'cancelled'")
    ).first()
    if cancelled_exists is None:
        # Найти max(order_idx)+1 для нового статуса.
        max_order_row = bind.execute(
            sa.text("SELECT COALESCE(MAX(order_idx), 0) FROM project_statuses")
        ).first()
        next_order = int(max_order_row[0] if max_order_row else 0) + 1
        # Сгенерировать UUID на стороне python (проверено: совместимо с моделью).
        import uuid as _uuid
        from datetime import datetime as _dt

        bind.execute(
            sa.text(
                "INSERT INTO project_statuses "
                "(id, slug, name, order_idx, description, created_at) "
                "VALUES (:id, 'cancelled', 'Отменено', :ord, "
                "'Решено не делать; идея/проект закрыт без архивирования истории', "
                ":created)"
            ),
            {
                "id": str(_uuid.uuid4()),
                "ord": next_order,
                "created": _dt.utcnow(),
            },
        )


def downgrade() -> None:
    """Downgrade schema: убрать entity_kind, восстановить status=idea."""
    bind = op.get_bind()

    # 1. Откатить idea-конверсию: entity_kind=idea → status=idea (если статус
    #    'idea' существует в БД).
    idea_status_row = bind.execute(
        sa.text("SELECT id FROM project_statuses WHERE slug = 'idea'")
    ).first()
    if idea_status_row is not None:
        idea_status_id = idea_status_row[0]
        bind.execute(
            sa.text(
                "UPDATE projects SET status_id = :idea_id "
                "WHERE entity_kind = 'idea'"
            ),
            {"idea_id": idea_status_id},
        )

    # 2. Удалить статус 'cancelled' (только если ни один проект его не
    #    использует).
    cancelled_row = bind.execute(
        sa.text("SELECT id FROM project_statuses WHERE slug = 'cancelled'")
    ).first()
    if cancelled_row is not None:
        cancelled_id = cancelled_row[0]
        used = bind.execute(
            sa.text(
                "SELECT 1 FROM projects WHERE status_id = :sid LIMIT 1"
            ),
            {"sid": cancelled_id},
        ).first()
        if used is None:
            bind.execute(
                sa.text("DELETE FROM project_statuses WHERE id = :sid"),
                {"sid": cancelled_id},
            )

    # 3. Удалить колонку entity_kind + CHECK constraint.
    with op.batch_alter_table("projects", recreate="auto") as batch_op:
        batch_op.drop_constraint("ck_projects_entity_kind", type_="check")
        batch_op.drop_column("entity_kind")


def _convert_statuses(bind, mapping: dict[str, str]) -> None:
    """Конвертация status_id для всех проектов с заданными legacy-статусами."""
    for old_slug, new_slug in mapping.items():
        old_row = bind.execute(
            sa.text("SELECT id FROM project_statuses WHERE slug = :s"),
            {"s": old_slug},
        ).first()
        new_row = bind.execute(
            sa.text("SELECT id FROM project_statuses WHERE slug = :s"),
            {"s": new_slug},
        ).first()
        if old_row is None or new_row is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE projects SET status_id = :new_id "
                "WHERE status_id = :old_id"
            ),
            {"new_id": new_row[0], "old_id": old_row[0]},
        )
