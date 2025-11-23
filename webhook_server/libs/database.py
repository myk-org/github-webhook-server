"""
Async database connection management for PostgreSQL and Redis.

Provides connection pooling, health checks, and graceful error handling
for metrics storage and caching infrastructure.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg
import redis.asyncio as redis_async

from webhook_server.libs.config import Config
from webhook_server.utils.helpers import get_logger_with_params


class DatabaseManager:
    """
    Async PostgreSQL connection manager using asyncpg.

    Provides connection pooling, query execution, and health monitoring
    for metrics database operations.

    Architecture guarantees:
    - config is ALWAYS provided (required parameter) - no defensive checks needed
    - logger is ALWAYS provided (required parameter) - no defensive checks needed
    - pool starts as None (lazy initialization) - defensive check acceptable

    Example:
        async with DatabaseManager(config, logger) as db_manager:
            result = await db_manager.fetch("SELECT * FROM metrics WHERE id = $1", metric_id)
    """

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        """
        Initialize PostgreSQL connection manager.

        Args:
            config: Configuration object containing database settings
            logger: Logger instance for connection lifecycle events

        Raises:
            ValueError: If required database configuration is missing
        """
        self.config = config
        self.logger = logger
        self.pool: asyncpg.Pool[asyncpg.Record] | None = None  # Lazy initialization

        # Load database configuration - fail-fast if missing required fields
        db_config = self.config.root_data.get("metrics-database")
        if not db_config:
            raise ValueError(
                "Database configuration missing. Add 'metrics-database' section to config.yaml. "
                "See examples/config.yaml for reference."
            )

        self.host: str = db_config.get("host", "localhost")
        self.port: int = db_config.get("port", 5432)
        self.database: str = db_config.get("database", "")
        self.username: str = db_config.get("username", "")
        self.password: str = db_config.get("password", "")
        self.pool_size: int = db_config.get("pool-size", 20)

        # Validate required fields - fail-fast
        if not self.database:
            raise ValueError("Database name ('database') is required in metrics-database configuration")
        if not self.username:
            raise ValueError("Database username ('username') is required in metrics-database configuration")
        if not self.password:
            raise ValueError("Database password ('password') is required in metrics-database configuration")

    async def connect(self) -> None:
        """
        Create connection pool to PostgreSQL database.

        Establishes connection pool with configured parameters and validates connectivity.

        Raises:
            asyncpg.PostgresError: If connection fails
            ValueError: If pool already exists
        """
        if self.pool is not None:
            raise ValueError("Database pool already exists. Call disconnect() first.")

        self.logger.info(
            f"Connecting to PostgreSQL database: {self.username}@{self.host}:{self.port}/{self.database} "
            f"(pool_size={self.pool_size})"
        )

        try:
            self.pool = await asyncpg.create_pool(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.username,
                password=self.password,
                min_size=1,
                max_size=self.pool_size,
                command_timeout=60,  # 60 seconds for query execution
            )
            self.logger.info("PostgreSQL connection pool created successfully")
        except Exception:
            self.logger.exception("Failed to connect to PostgreSQL database")
            raise

    async def disconnect(self) -> None:
        """
        Close connection pool gracefully.

        Waits for active connections to finish and closes pool.
        Safe to call multiple times (idempotent).
        """
        if self.pool is not None:  # Legitimate check - lazy initialization
            self.logger.info("Closing PostgreSQL connection pool")
            try:
                await self.pool.close()
                self.logger.info("PostgreSQL connection pool closed successfully")
            except Exception:
                self.logger.exception("Error closing PostgreSQL connection pool")
            finally:
                self.pool = None

    async def execute(self, query: str, *args: Any) -> str:
        """
        Execute a SQL query that doesn't return data (INSERT, UPDATE, DELETE).

        Args:
            query: SQL query with $1, $2, ... placeholders
            *args: Query parameters

        Returns:
            Result status string (e.g., "INSERT 0 1", "UPDATE 5", "DELETE 3")

        Raises:
            ValueError: If connection pool not initialized
            asyncpg.PostgresError: If query execution fails

        Example:
            await db.execute("INSERT INTO metrics (name, value) VALUES ($1, $2)", "cpu", 85.5)
        """
        if self.pool is None:  # Legitimate check - lazy initialization
            raise ValueError("Database pool not initialized. Call connect() first.")

        try:
            async with self.pool.acquire() as connection:
                result = await connection.execute(query, *args)
                self.logger.debug(f"Query executed successfully: {result}")
                return result
        except Exception:
            self.logger.exception(f"Failed to execute query: {query}")
            raise

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        """
        Execute a SQL query and fetch all results (SELECT).

        Args:
            query: SQL query with $1, $2, ... placeholders
            *args: Query parameters

        Returns:
            List of records (each record behaves like dict and tuple)

        Raises:
            ValueError: If connection pool not initialized
            asyncpg.PostgresError: If query execution fails

        Example:
            rows = await db.fetch("SELECT * FROM metrics WHERE timestamp > $1", start_time)
            for row in rows:
                print(row["name"], row["value"])
        """
        if self.pool is None:  # Legitimate check - lazy initialization
            raise ValueError("Database pool not initialized. Call connect() first.")

        try:
            async with self.pool.acquire() as connection:
                results = await connection.fetch(query, *args)
                self.logger.debug(f"Query returned {len(results)} rows")
                return results
        except Exception:
            self.logger.exception(f"Failed to fetch query results: {query}")
            raise

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        """
        Execute a SQL query and fetch single result row (SELECT).

        Args:
            query: SQL query with $1, $2, ... placeholders
            *args: Query parameters

        Returns:
            Single record or None if no results

        Raises:
            ValueError: If connection pool not initialized
            asyncpg.PostgresError: If query execution fails

        Example:
            row = await db.fetchrow("SELECT * FROM metrics WHERE id = $1", metric_id)
            if row:
                print(row["name"], row["value"])
        """
        if self.pool is None:  # Legitimate check - lazy initialization
            raise ValueError("Database pool not initialized. Call connect() first.")

        try:
            async with self.pool.acquire() as connection:
                result = await connection.fetchrow(query, *args)
                if result:
                    self.logger.debug("Query returned 1 row")
                else:
                    self.logger.debug("Query returned no rows")
                return result
        except Exception:
            self.logger.exception(f"Failed to fetch single row: {query}")
            raise

    async def health_check(self) -> bool:
        """
        Check database connectivity and responsiveness.

        Returns:
            True if database is healthy, False otherwise

        Example:
            if await db.health_check():
                print("Database is healthy")
        """
        try:
            if self.pool is None:  # Legitimate check - lazy initialization
                self.logger.warning("Database pool not initialized")
                return False

            async with self.pool.acquire() as connection:
                await connection.fetchval("SELECT 1")
                self.logger.debug("Database health check: OK")
                return True
        except Exception:
            self.logger.exception("Database health check failed")
            return False

    async def __aenter__(self) -> DatabaseManager:
        """Context manager entry - initialize connection pool."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit - cleanup connection pool."""
        await self.disconnect()


class RedisManager:
    """
    Async Redis connection manager using redis-py.

    Provides caching operations with TTL support and health monitoring
    for metrics caching infrastructure.

    Architecture guarantees:
    - config is ALWAYS provided (required parameter) - no defensive checks needed
    - logger is ALWAYS provided (required parameter) - no defensive checks needed
    - client starts as None (lazy initialization) - defensive check acceptable

    Example:
        async with RedisManager(config, logger) as redis_manager:
            await redis_manager.set("key", "value", ttl=300)
            result = await redis_manager.get("key")
    """

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        """
        Initialize Redis connection manager.

        Args:
            config: Configuration object containing Redis settings
            logger: Logger instance for connection lifecycle events
        """
        self.config = config
        self.logger = logger
        self.client: redis_async.Redis | None = None  # Lazy initialization

        # Load Redis configuration (with defaults for optional deployment)
        redis_config = self.config.root_data.get("metrics-redis", {})
        self.host: str = redis_config.get("host", "localhost")
        self.port: int = redis_config.get("port", 6379)
        self.password: str | None = redis_config.get("password")
        self.default_ttl: int = redis_config.get("cache-ttl", 300)

        # No validation - all Redis settings are optional with sensible defaults

    async def connect(self) -> None:
        """
        Create connection to Redis server.

        Establishes connection with configured parameters and validates connectivity.

        Raises:
            redis.RedisError: If connection fails
            ValueError: If client already exists
        """
        if self.client is not None:
            raise ValueError("Redis client already exists. Call disconnect() first.")

        self.logger.info(f"Connecting to Redis: {self.host}:{self.port} (default_ttl={self.default_ttl}s)")

        try:
            self.client = redis_async.Redis(
                host=self.host,
                port=self.port,
                password=self.password,
                decode_responses=True,  # Return strings instead of bytes
                socket_connect_timeout=5,  # 5 seconds connection timeout
                socket_timeout=5,  # 5 seconds command timeout
            )

            # Test connection
            await self.client.ping()
            self.logger.info("Redis connection established successfully")
        except Exception:
            self.logger.exception("Failed to connect to Redis")
            # Cleanup on failure
            if self.client:
                await self.client.aclose()
                self.client = None
            raise

    async def disconnect(self) -> None:
        """
        Close Redis connection gracefully.

        Safe to call multiple times (idempotent).
        """
        if self.client is not None:  # Legitimate check - lazy initialization
            self.logger.info("Closing Redis connection")
            try:
                await self.client.aclose()
                self.logger.info("Redis connection closed successfully")
            except Exception:
                self.logger.exception("Error closing Redis connection")
            finally:
                self.client = None

    async def get(self, key: str) -> str | None:
        """
        Get value from Redis cache.

        Args:
            key: Cache key

        Returns:
            Cached value as string, or None if key doesn't exist

        Raises:
            ValueError: If Redis client not initialized
            redis.RedisError: If operation fails

        Example:
            value = await redis.get("metrics:cpu:avg")
        """
        if self.client is None:  # Legitimate check - lazy initialization
            raise ValueError("Redis client not initialized. Call connect() first.")

        try:
            result = await self.client.get(key)
            if result:
                self.logger.debug(f"Cache hit: {key}")
            else:
                self.logger.debug(f"Cache miss: {key}")
            return result
        except Exception:
            self.logger.exception(f"Failed to get key from Redis: {key}")
            raise

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        """
        Set value in Redis cache with optional TTL.

        Args:
            key: Cache key
            value: Value to cache (must be string or serializable)
            ttl: Time-to-live in seconds (uses default_ttl if None)

        Returns:
            True if successful

        Raises:
            ValueError: If Redis client not initialized
            redis.RedisError: If operation fails

        Example:
            await redis.set("metrics:cpu:avg", "85.5", ttl=600)
        """
        if self.client is None:  # Legitimate check - lazy initialization
            raise ValueError("Redis client not initialized. Call connect() first.")

        try:
            _ttl = ttl if ttl is not None else self.default_ttl
            await self.client.set(key, value, ex=_ttl)
            self.logger.debug(f"Cache set: {key} (ttl={_ttl}s)")
            return True
        except Exception:
            self.logger.exception(f"Failed to set key in Redis: {key}")
            raise

    async def delete(self, key: str) -> bool:
        """
        Delete key from Redis cache.

        Args:
            key: Cache key to delete

        Returns:
            True if key was deleted, False if key didn't exist

        Raises:
            ValueError: If Redis client not initialized
            redis.RedisError: If operation fails

        Example:
            deleted = await redis.delete("metrics:cpu:avg")
        """
        if self.client is None:  # Legitimate check - lazy initialization
            raise ValueError("Redis client not initialized. Call connect() first.")

        try:
            deleted_count = await self.client.delete(key)
            if deleted_count > 0:
                self.logger.debug(f"Cache deleted: {key}")
                return True
            else:
                self.logger.debug(f"Cache key not found: {key}")
                return False
        except Exception:
            self.logger.exception(f"Failed to delete key from Redis: {key}")
            raise

    async def health_check(self) -> bool:
        """
        Check Redis connectivity and responsiveness.

        Returns:
            True if Redis is healthy, False otherwise

        Example:
            if await redis.health_check():
                print("Redis is healthy")
        """
        try:
            if self.client is None:  # Legitimate check - lazy initialization
                self.logger.warning("Redis client not initialized")
                return False

            await self.client.ping()
            self.logger.debug("Redis health check: OK")
            return True
        except Exception:
            self.logger.exception("Redis health check failed")
            return False

    async def __aenter__(self) -> RedisManager:
        """Context manager entry - initialize connection."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit - cleanup connection."""
        await self.disconnect()


def get_database_manager(repository_name: str = "") -> DatabaseManager:
    """
    Factory function to create DatabaseManager with proper logging.

    Args:
        repository_name: Repository name for logger context (optional)

    Returns:
        Configured DatabaseManager instance

    Raises:
        ImportError: If asyncpg not installed
        ValueError: If database configuration missing

    Example:
        db_manager = get_database_manager()
        async with db_manager as db:
            results = await db.fetch("SELECT * FROM metrics")
    """
    config = Config(repository=repository_name)
    logger = get_logger_with_params(repository_name=repository_name)
    return DatabaseManager(config=config, logger=logger)


def get_redis_manager(repository_name: str = "") -> RedisManager:
    """
    Factory function to create RedisManager with proper logging.

    Args:
        repository_name: Repository name for logger context (optional)

    Returns:
        Configured RedisManager instance

    Raises:
        ImportError: If redis not installed

    Example:
        redis_manager = get_redis_manager()
        async with redis_manager as redis:
            await redis.set("key", "value", ttl=300)
    """
    config = Config(repository=repository_name)
    logger = get_logger_with_params(repository_name=repository_name)
    return RedisManager(config=config, logger=logger)
