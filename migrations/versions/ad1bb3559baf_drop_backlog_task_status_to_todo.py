"""drop backlog task status to todo

Revision ID: ad1bb3559baf
Revises: 5b893ab8883c
Create Date: 2026-06-30 13:43:00.768301

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'ad1bb3559baf'
down_revision: Union[str, Sequence[str], None] = '5b893ab8883c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Статус `backlog` убран из задач → переносим существующие в `todo`.

    Планирование теперь = единственный статус `todo` (default новой задачи).
    Уровень «идея/сырьё» (бывший backlog) переехал в отдельный пул `atlas backlog`
    (таблица backlog_items, отдельной миграцией).

    CHECK ``ck_tasks_status`` на существующих БД оставляем пермиссивным (всё ещё
    перечисляет 'backlog') — приложение его не пишет (VALID_STATUSES/валидация уже
    убрали backlog), а table-recreate ``tasks`` (много FK на participants/sprints/
    epics) — лишний риск ради косметики. Авторитет — модель (models.py): свежие БД
    (create_all/тесты) получают CHECK уже без backlog.
    """
    op.execute("UPDATE tasks SET status = 'todo' WHERE status = 'backlog'")


def downgrade() -> None:
    """Необратимо по смыслу: бывшие backlog-задачи неотличимы от todo (todo не
    использовался). No-op — откат вернёт лишь схему, не семантику."""
    pass
