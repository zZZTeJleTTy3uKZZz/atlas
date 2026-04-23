from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Импортируем наши модели, чтобы Base.metadata знал обо всех таблицах
from notion_task_cli.pm.db import DEFAULT_DB_PATH
from notion_task_cli.pm.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Переопределяем sqlalchemy.url. Приоритет:
# 1. env var NOTION_TASK_CLI_DB_URL (production / CI / tests override)
# 2. значение из alembic.ini (если кто-то явно прописал)
# 3. default — локальный ~/.cifro-pm/portfolio.db
import os
env_url = os.environ.get("NOTION_TASK_CLI_DB_URL")
ini_url = config.get_main_option("sqlalchemy.url") or ""
if env_url:
    config.set_main_option("sqlalchemy.url", env_url)
elif not ini_url or ini_url.startswith("driver://"):
    # placeholder 'driver://user:pass@localhost/dbname' оставленный alembic init
    DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.set_main_option("sqlalchemy.url", f"sqlite:///{DEFAULT_DB_PATH}")

# add your model's MetaData object here for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite не поддерживает нативный ALTER для FK-constraints,
            # поэтому Alembic использует batch-режим (пересоздание таблицы)
            render_as_batch=connection.dialect.name == "sqlite",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
