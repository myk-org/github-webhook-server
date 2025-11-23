"""Tests for database connection managers."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

import pytest


def create_async_pool_mock(connection: AsyncMock) -> Mock:
    """Create a properly mocked async pool with async context manager."""

    @asynccontextmanager
    async def mock_acquire():
        yield connection

    pool = Mock()
    pool.acquire = mock_acquire
    pool.close = AsyncMock()
    return pool


class TestDatabaseManager:
    """Test suite for DatabaseManager class."""

    @pytest.fixture
    def mock_config(self) -> Mock:
        """Create a mock Config object."""
        mock = Mock()
        mock.root_data = {
            "metrics-database": {
                "host": "localhost",
                "port": 5432,
                "database": "test_db",
                "username": "test_user",
                "password": "test_pass",  # pragma: allowlist secret
                "pool-size": 10,
            }
        }
        return mock

    @pytest.fixture
    def mock_logger(self) -> Mock:
        """Create a mock logger."""
        return Mock()

    def test_database_manager_init(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager initialization."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)

        assert manager.host == "localhost"
        assert manager.port == 5432
        assert manager.database == "test_db"
        assert manager.username == "test_user"
        assert manager.password == "test_pass"  # pragma: allowlist secret
        assert manager.pool_size == 10
        assert manager.pool is None

    def test_database_manager_init_missing_config(
        self,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager initialization with missing config."""
        from webhook_server.libs.database import DatabaseManager

        mock_config = Mock()
        mock_config.root_data = {}

        with pytest.raises(ValueError, match="Database configuration missing"):
            DatabaseManager(mock_config, mock_logger)

    def test_database_manager_init_missing_database(
        self,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager initialization with missing database name."""
        from webhook_server.libs.database import DatabaseManager

        mock_config = Mock()
        mock_config.root_data = {
            "metrics-database": {
                "host": "localhost",
                "port": 5432,
                "username": "test_user",
                "password": "test_pass",  # pragma: allowlist secret
            }
        }

        with pytest.raises(ValueError, match="Database name"):
            DatabaseManager(mock_config, mock_logger)

    def test_database_manager_init_missing_username(
        self,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager initialization with missing username."""
        from webhook_server.libs.database import DatabaseManager

        mock_config = Mock()
        mock_config.root_data = {
            "metrics-database": {
                "host": "localhost",
                "port": 5432,
                "database": "test_db",
                "password": "test_pass",  # pragma: allowlist secret
            }
        }

        with pytest.raises(ValueError, match="username"):
            DatabaseManager(mock_config, mock_logger)

    def test_database_manager_init_missing_password(
        self,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager initialization with missing password."""
        from webhook_server.libs.database import DatabaseManager

        mock_config = Mock()
        mock_config.root_data = {
            "metrics-database": {
                "host": "localhost",
                "port": 5432,
                "database": "test_db",
                "username": "test_user",
            }
        }

        with pytest.raises(ValueError, match="password"):
            DatabaseManager(mock_config, mock_logger)

    @pytest.mark.asyncio
    async def test_database_manager_connect(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager connect."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)

        with patch("webhook_server.libs.database.asyncpg.create_pool", new=AsyncMock()) as mock_create_pool:
            mock_pool = Mock()
            mock_pool.close = AsyncMock()
            mock_create_pool.return_value = mock_pool

            await manager.connect()

            assert manager.pool is mock_pool
            mock_create_pool.assert_called_once()

    @pytest.mark.asyncio
    async def test_database_manager_connect_already_connected(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager connect when already connected."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)
        manager.pool = Mock()

        with pytest.raises(ValueError, match="Database pool already exists"):
            await manager.connect()

    @pytest.mark.asyncio
    async def test_database_manager_connect_failure(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager connect failure."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)

        with patch("webhook_server.libs.database.asyncpg.create_pool") as mock_create_pool:
            mock_create_pool.side_effect = Exception("Connection failed")

            with pytest.raises(Exception, match="Connection failed"):
                await manager.connect()

    @pytest.mark.asyncio
    async def test_database_manager_disconnect(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager disconnect."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)
        mock_pool = AsyncMock()
        manager.pool = mock_pool

        await manager.disconnect()

        mock_pool.close.assert_called_once()
        assert manager.pool is None

    @pytest.mark.asyncio
    async def test_database_manager_disconnect_no_pool(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager disconnect when no pool exists."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)

        # Should not raise
        await manager.disconnect()
        assert manager.pool is None

    @pytest.mark.asyncio
    async def test_database_manager_disconnect_error(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager disconnect with error."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)
        mock_pool = AsyncMock()
        mock_pool.close.side_effect = Exception("Close failed")
        manager.pool = mock_pool

        # Should not raise, but log error
        await manager.disconnect()
        assert manager.pool is None

    @pytest.mark.asyncio
    async def test_database_manager_execute(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager execute."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)
        mock_connection = AsyncMock()
        mock_connection.execute.return_value = "INSERT 0 1"
        manager.pool = create_async_pool_mock(mock_connection)

        result = await manager.execute("INSERT INTO test VALUES ($1)", "value")

        assert result == "INSERT 0 1"
        mock_connection.execute.assert_called_once_with("INSERT INTO test VALUES ($1)", "value")

    @pytest.mark.asyncio
    async def test_database_manager_execute_no_pool(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager execute without pool."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)

        with pytest.raises(ValueError, match="Database pool not initialized"):
            await manager.execute("INSERT INTO test VALUES ($1)", "value")

    @pytest.mark.asyncio
    async def test_database_manager_execute_failure(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager execute failure."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)
        mock_connection = AsyncMock()
        mock_connection.execute.side_effect = Exception("Execute failed")
        manager.pool = create_async_pool_mock(mock_connection)

        with pytest.raises(Exception, match="Execute failed"):
            await manager.execute("INSERT INTO test VALUES ($1)", "value")

    @pytest.mark.asyncio
    async def test_database_manager_fetch(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager fetch."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)
        mock_connection = AsyncMock()
        mock_records = [{"id": 1, "name": "test"}]
        mock_connection.fetch.return_value = mock_records
        manager.pool = create_async_pool_mock(mock_connection)

        result = await manager.fetch("SELECT * FROM test WHERE id = $1", 1)

        assert result == mock_records
        mock_connection.fetch.assert_called_once_with("SELECT * FROM test WHERE id = $1", 1)

    @pytest.mark.asyncio
    async def test_database_manager_fetch_no_pool(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager fetch without pool."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)

        with pytest.raises(ValueError, match="Database pool not initialized"):
            await manager.fetch("SELECT * FROM test")

    @pytest.mark.asyncio
    async def test_database_manager_fetch_failure(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager fetch failure."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)
        mock_connection = AsyncMock()
        mock_connection.fetch.side_effect = Exception("Fetch failed")
        manager.pool = create_async_pool_mock(mock_connection)

        with pytest.raises(Exception, match="Fetch failed"):
            await manager.fetch("SELECT * FROM test")

    @pytest.mark.asyncio
    async def test_database_manager_fetchrow(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager fetchrow."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)
        mock_connection = AsyncMock()
        mock_record = {"id": 1, "name": "test"}
        mock_connection.fetchrow.return_value = mock_record
        manager.pool = create_async_pool_mock(mock_connection)

        result = await manager.fetchrow("SELECT * FROM test WHERE id = $1", 1)

        assert result == mock_record
        mock_connection.fetchrow.assert_called_once_with("SELECT * FROM test WHERE id = $1", 1)

    @pytest.mark.asyncio
    async def test_database_manager_fetchrow_no_result(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager fetchrow with no result."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)
        mock_connection = AsyncMock()
        mock_connection.fetchrow.return_value = None
        manager.pool = create_async_pool_mock(mock_connection)

        result = await manager.fetchrow("SELECT * FROM test WHERE id = $1", 999)

        assert result is None

    @pytest.mark.asyncio
    async def test_database_manager_fetchrow_no_pool(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager fetchrow without pool."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)

        with pytest.raises(ValueError, match="Database pool not initialized"):
            await manager.fetchrow("SELECT * FROM test WHERE id = $1", 1)

    @pytest.mark.asyncio
    async def test_database_manager_fetchrow_failure(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager fetchrow failure."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)
        mock_connection = AsyncMock()
        mock_connection.fetchrow.side_effect = Exception("Fetchrow failed")
        manager.pool = create_async_pool_mock(mock_connection)

        with pytest.raises(Exception, match="Fetchrow failed"):
            await manager.fetchrow("SELECT * FROM test WHERE id = $1", 1)

    @pytest.mark.asyncio
    async def test_database_manager_health_check_success(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager health_check success."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)
        mock_connection = AsyncMock()
        mock_connection.fetchval.return_value = 1
        manager.pool = create_async_pool_mock(mock_connection)

        result = await manager.health_check()

        assert result is True
        mock_connection.fetchval.assert_called_once_with("SELECT 1")

    @pytest.mark.asyncio
    async def test_database_manager_health_check_no_pool(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager health_check without pool."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)

        result = await manager.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_database_manager_health_check_failure(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager health_check failure."""
        from webhook_server.libs.database import DatabaseManager

        manager = DatabaseManager(mock_config, mock_logger)
        mock_connection = AsyncMock()
        mock_connection.fetchval.side_effect = Exception("Health check failed")
        manager.pool = create_async_pool_mock(mock_connection)

        result = await manager.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_database_manager_context_manager(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test DatabaseManager as context manager."""
        from webhook_server.libs.database import DatabaseManager

        with patch("webhook_server.libs.database.asyncpg.create_pool", new=AsyncMock()) as mock_create_pool:
            mock_pool = Mock()
            mock_pool.close = AsyncMock()
            mock_create_pool.return_value = mock_pool

            async with DatabaseManager(mock_config, mock_logger) as manager:
                assert manager.pool is mock_pool

            # Pool should be closed after context exit
            mock_pool.close.assert_called_once()


class TestRedisManager:
    """Test suite for RedisManager class."""

    @pytest.fixture
    def mock_config(self) -> Mock:
        """Create a mock Config object."""
        mock = Mock()
        mock.root_data = {
            "metrics-redis": {
                "host": "localhost",
                "port": 6379,
                "password": None,
                "cache-ttl": 300,
            }
        }
        return mock

    @pytest.fixture
    def mock_logger(self) -> Mock:
        """Create a mock logger."""
        return Mock()

    def test_redis_manager_init(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager initialization."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)

        assert manager.host == "localhost"
        assert manager.port == 6379
        assert manager.password is None
        assert manager.default_ttl == 300
        assert manager.client is None

    def test_redis_manager_init_no_config(
        self,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager initialization without config."""
        from webhook_server.libs.database import RedisManager

        mock_config = Mock()
        mock_config.root_data = {}

        manager = RedisManager(mock_config, mock_logger)

        # Should use defaults
        assert manager.host == "localhost"
        assert manager.port == 6379
        assert manager.password is None
        assert manager.default_ttl == 300

    @pytest.mark.asyncio
    async def test_redis_manager_connect(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager connect."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)

        with patch("webhook_server.libs.database.redis_async.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_redis_class.return_value = mock_client

            await manager.connect()

            assert manager.client is mock_client
            mock_client.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_manager_connect_already_connected(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager connect when already connected."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        manager.client = Mock()

        with pytest.raises(ValueError, match="Redis client already exists"):
            await manager.connect()

    @pytest.mark.asyncio
    async def test_redis_manager_connect_failure(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager connect failure."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)

        with patch("webhook_server.libs.database.redis_async.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_client.ping.side_effect = Exception("Connection failed")
            mock_redis_class.return_value = mock_client

            with pytest.raises(Exception, match="Connection failed"):
                await manager.connect()

            # Client should be cleaned up
            mock_client.aclose.assert_called_once()
            assert manager.client is None

    @pytest.mark.asyncio
    async def test_redis_manager_disconnect(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager disconnect."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        manager.client = mock_client

        await manager.disconnect()

        mock_client.aclose.assert_called_once()
        assert manager.client is None

    @pytest.mark.asyncio
    async def test_redis_manager_disconnect_no_client(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager disconnect when no client exists."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)

        # Should not raise
        await manager.disconnect()
        assert manager.client is None

    @pytest.mark.asyncio
    async def test_redis_manager_disconnect_error(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager disconnect with error."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        mock_client.aclose.side_effect = Exception("Close failed")
        manager.client = mock_client

        # Should not raise, but log error
        await manager.disconnect()
        assert manager.client is None

    @pytest.mark.asyncio
    async def test_redis_manager_get_success(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager get success."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        mock_client.get.return_value = "cached_value"
        manager.client = mock_client

        result = await manager.get("test_key")

        assert result == "cached_value"
        mock_client.get.assert_called_once_with("test_key")

    @pytest.mark.asyncio
    async def test_redis_manager_get_miss(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager get cache miss."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        mock_client.get.return_value = None
        manager.client = mock_client

        result = await manager.get("test_key")

        assert result is None

    @pytest.mark.asyncio
    async def test_redis_manager_get_no_client(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager get without client."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)

        with pytest.raises(ValueError, match="Redis client not initialized"):
            await manager.get("test_key")

    @pytest.mark.asyncio
    async def test_redis_manager_get_failure(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager get failure."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Get failed")
        manager.client = mock_client

        with pytest.raises(Exception, match="Get failed"):
            await manager.get("test_key")

    @pytest.mark.asyncio
    async def test_redis_manager_set_with_ttl(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager set with custom TTL."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        manager.client = mock_client

        result = await manager.set("test_key", "test_value", ttl=600)

        assert result is True
        mock_client.set.assert_called_once_with("test_key", "test_value", ex=600)

    @pytest.mark.asyncio
    async def test_redis_manager_set_default_ttl(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager set with default TTL."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        manager.client = mock_client

        result = await manager.set("test_key", "test_value")

        assert result is True
        mock_client.set.assert_called_once_with("test_key", "test_value", ex=300)

    @pytest.mark.asyncio
    async def test_redis_manager_set_no_client(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager set without client."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)

        with pytest.raises(ValueError, match="Redis client not initialized"):
            await manager.set("test_key", "test_value")

    @pytest.mark.asyncio
    async def test_redis_manager_set_failure(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager set failure."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        mock_client.set.side_effect = Exception("Set failed")
        manager.client = mock_client

        with pytest.raises(Exception, match="Set failed"):
            await manager.set("test_key", "test_value")

    @pytest.mark.asyncio
    async def test_redis_manager_delete_success(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager delete success."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        mock_client.delete.return_value = 1
        manager.client = mock_client

        result = await manager.delete("test_key")

        assert result is True
        mock_client.delete.assert_called_once_with("test_key")

    @pytest.mark.asyncio
    async def test_redis_manager_delete_not_found(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager delete when key not found."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        mock_client.delete.return_value = 0
        manager.client = mock_client

        result = await manager.delete("test_key")

        assert result is False

    @pytest.mark.asyncio
    async def test_redis_manager_delete_no_client(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager delete without client."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)

        with pytest.raises(ValueError, match="Redis client not initialized"):
            await manager.delete("test_key")

    @pytest.mark.asyncio
    async def test_redis_manager_delete_failure(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager delete failure."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        mock_client.delete.side_effect = Exception("Delete failed")
        manager.client = mock_client

        with pytest.raises(Exception, match="Delete failed"):
            await manager.delete("test_key")

    @pytest.mark.asyncio
    async def test_redis_manager_health_check_success(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager health_check success."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        manager.client = mock_client

        result = await manager.health_check()

        assert result is True
        mock_client.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_manager_health_check_no_client(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager health_check without client."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)

        result = await manager.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_redis_manager_health_check_failure(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager health_check failure."""
        from webhook_server.libs.database import RedisManager

        manager = RedisManager(mock_config, mock_logger)
        mock_client = AsyncMock()
        mock_client.ping.side_effect = Exception("Ping failed")
        manager.client = mock_client

        result = await manager.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_redis_manager_context_manager(
        self,
        mock_config: Mock,
        mock_logger: Mock,
    ) -> None:
        """Test RedisManager as context manager."""
        from webhook_server.libs.database import RedisManager

        with patch("webhook_server.libs.database.redis_async.Redis") as mock_redis_class:
            mock_client = AsyncMock()
            mock_redis_class.return_value = mock_client

            async with RedisManager(mock_config, mock_logger) as manager:
                assert manager.client is mock_client

            # Client should be closed after context exit
            mock_client.aclose.assert_called_once()


class TestFactoryFunctions:
    """Test suite for factory functions."""

    def test_get_database_manager(self) -> None:
        """Test get_database_manager factory function."""
        from webhook_server.libs.database import get_database_manager

        with patch("webhook_server.libs.database.Config") as mock_config_class:
            with patch("webhook_server.libs.database.get_logger_with_params") as mock_logger_func:
                mock_config = Mock()
                mock_config.root_data = {
                    "metrics-database": {
                        "host": "localhost",
                        "port": 5432,
                        "database": "test_db",
                        "username": "test_user",
                        "password": "test_pass",  # pragma: allowlist secret
                    }
                }
                mock_config_class.return_value = mock_config
                mock_logger = Mock()
                mock_logger_func.return_value = mock_logger

                manager = get_database_manager("test/repo")

                mock_config_class.assert_called_once_with(repository="test/repo")
                mock_logger_func.assert_called_once_with(repository_name="test/repo")
                assert manager.config is mock_config
                assert manager.logger is mock_logger

    def test_get_redis_manager(self) -> None:
        """Test get_redis_manager factory function."""
        from webhook_server.libs.database import get_redis_manager

        with patch("webhook_server.libs.database.Config") as mock_config_class:
            with patch("webhook_server.libs.database.get_logger_with_params") as mock_logger_func:
                mock_config = Mock()
                mock_config.root_data = {}
                mock_config_class.return_value = mock_config
                mock_logger = Mock()
                mock_logger_func.return_value = mock_logger

                manager = get_redis_manager("test/repo")

                mock_config_class.assert_called_once_with(repository="test/repo")
                mock_logger_func.assert_called_once_with(repository_name="test/repo")
                assert manager.config is mock_config
                assert manager.logger is mock_logger
