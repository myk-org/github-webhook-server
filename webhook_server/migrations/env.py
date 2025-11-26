"""
Alembic migration environment for GitHub Webhook Server metrics database.

This module configures Alembic to:
- Use async PostgreSQL via asyncpg
- Load database configuration from webhook_server/libs/config.py
- Support both online (with database connection) and offline (SQL script) migrations
- Integrate with project logging infrastructure

Key integration points:
- Database config loaded from config.yaml (metrics-database section)
- Uses DatabaseManager connection settings
- Async migration support for PostgreSQL

Architecture guarantees:
- Config is loaded from environment or default path - fail-fast if missing
- All SQLAlchemy models are imported for autogenerate support
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from simple_logger.logger import get_logger
from sqlalchemy import pool
from sqlalchemy.engine import URL, Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from webhook_server.libs.config import Config
from webhook_server.libs.models import Base

# Alembic Config object provides access to alembic.ini values
config = context.config

# Interpret the config file for Python logging
# This line sets up loggers basically
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Get simple logger for Alembic (avoid Config dependency for migration-only commands)
logger = get_logger(name="alembic.migrations", level="INFO")


def _configure_from_config() -> None:
    """
    Load database configuration and set Alembic options.

    This helper extracts the "load config + build URL + set Alembic options" logic
    for easier testing and better separation of concerns.

    Raises:
        FileNotFoundError: Config file not found
        KeyError: Missing required database configuration key
        ValueError: Database configuration section missing

    Architecture guarantees:
    - Config is loaded from environment or default path - fail-fast if missing
    - Required keys: username, password, database
    - Optional keys: host (default: localhost), port (default: 5432)
    """
    webhook_config = Config()
    db_config = webhook_config.root_data.get("metrics-database")

    if not db_config:
        raise ValueError(
            "Database configuration missing. Add 'metrics-database' section to config.yaml. "
            "See examples/config.yaml for reference."
        )

    # Construct PostgreSQL asyncpg URL using SQLAlchemy URL builder
    # This safely handles special characters in credentials and database name
    db_url = URL.create(
        drivername="postgresql+asyncpg",
        username=db_config["username"],
        password=db_config["password"],
        host=db_config.get("host", "localhost"),
        port=db_config.get("port", 5432),
        database=db_config["database"],
    )

    # Set database URL in Alembic config (overrides alembic.ini if set)
    # URL.create() returns a URL object, convert to string for Alembic
    config.set_main_option("sqlalchemy.url", str(db_url))

    # Set version_locations dynamically based on data directory
    # This replaces the hardcoded path in alembic.ini to support non-container deployments
    # version_locations is where Alembic stores migration version files
    versions_path = os.path.join(webhook_config.data_dir, "migrations", "versions")
    config.set_main_option("version_locations", versions_path)

    logger.info(
        f"Loaded database configuration: {db_config['username']}@"
        f"{db_config.get('host', 'localhost')}:{db_config.get('port', 5432)}"
        f"/{db_config['database']}"
    )
    logger.info(f"Migration versions directory: {versions_path}")


# Load database configuration from config.yaml
try:
    _configure_from_config()
except FileNotFoundError:
    logger.exception("Config file not found. Ensure config.yaml exists in WEBHOOK_SERVER_DATA_DIR.")
    raise
except KeyError:
    # logger.exception automatically logs the traceback and exception details
    # No need to interpolate the exception object
    logger.exception("Missing required key in metrics-database config")
    raise
except Exception:
    logger.exception("Failed to load database configuration")
    raise

# Set target metadata for autogenerate - enables schema comparison
# All models in models.py are automatically registered with Base.metadata when Base is imported
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    Useful for generating SQL scripts without database connectivity.

    Example:
        alembic upgrade head --sql > migration.sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,  # Detect column type changes
        compare_server_default=True,  # Detect default value changes
    )

    logger.info("Running migrations in offline mode (SQL script generation)")

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """
    Execute migrations with given database connection.

    Args:
        connection: SQLAlchemy connection to use for migrations

    This is called by run_migrations_online() and runs the actual
    migration operations against the database.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,  # Detect column type changes
        compare_server_default=True,  # Detect default value changes
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations using async engine.

    Creates async engine from configuration and runs migrations
    in async context. This is required for asyncpg (async PostgreSQL driver).

    The async engine is created from alembic.ini config with
    database URL loaded from config.yaml.
    """
    # Create async engine configuration
    configuration = config.get_section(config.config_ini_section, {})

    # Override with our database URL from config.yaml
    configuration["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")

    # Async engine configuration for asyncpg
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # No connection pooling in migrations
    )

    logger.info("Running migrations in online mode (async PostgreSQL)")

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.

    In this scenario we create an async Engine and associate a connection
    with the context. This is the normal mode for running migrations.

    Uses asyncpg for async PostgreSQL connectivity.

    Note on asyncio.run() usage:
        This function is called by the Alembic CLI, which runs in a synchronous context.
        Using asyncio.run() is safe here since no event loop is running.

        IMPORTANT: If run_migrations_online() is ever reused from an async context
        (e.g., from within a running FastAPI application), you MUST use an alternate
        entrypoint that directly awaits run_async_migrations() instead of wrapping
        it in asyncio.run(). Calling asyncio.run() from within an already-running
        event loop will raise RuntimeError.

        Example alternate async entrypoint:
            async def run_migrations_online_async() -> None:
                await run_async_migrations()

    Example:
        alembic upgrade head
        alembic downgrade -1
    """
    asyncio.run(run_async_migrations())


# Determine migration mode and execute
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
