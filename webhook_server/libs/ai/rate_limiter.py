"""Rate limiting for AI API calls using token bucket algorithm.

Provides per-repository and global rate limiting with graceful degradation.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    enabled: bool = True


@dataclass
class TokenBucket:
    """Token bucket for rate limiting.

    Implements the token bucket algorithm where tokens are added at a fixed rate
    and consumed for each request. If no tokens available, request is rate limited.
    """

    capacity: int
    refill_rate: float  # Tokens per second
    tokens: float = field(init=False)
    last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        """Initialize bucket with full capacity."""
        self.tokens = float(self.capacity)
        self.last_refill = time.time()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill

        # Add tokens based on elapsed time and refill rate
        new_tokens = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + new_tokens)
        self.last_refill = now

    def consume(self, tokens: int = 1) -> bool:
        """Attempt to consume tokens from the bucket.

        Args:
            tokens: Number of tokens to consume

        Returns:
            True if tokens were available and consumed, False otherwise
        """
        self._refill()

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def get_available_tokens(self) -> int:
        """Get current number of available tokens.

        Returns:
            Number of tokens currently available
        """
        self._refill()
        return int(self.tokens)

    def time_until_available(self, tokens: int = 1) -> float:
        """Calculate time until requested tokens will be available.

        Args:
            tokens: Number of tokens needed

        Returns:
            Seconds until tokens will be available (0 if already available)
        """
        self._refill()

        if self.tokens >= tokens:
            return 0.0

        tokens_needed = tokens - self.tokens
        return tokens_needed / self.refill_rate


class RateLimiter:
    """Rate limiter for AI API calls with per-repo and global limits."""

    def __init__(
        self,
        global_config: RateLimitConfig | None = None,
        logger: logging.Logger | None = None,
    ):
        """Initialize rate limiter.

        Args:
            global_config: Global rate limit configuration
            logger: Logger instance
        """
        self.logger = logger or logging.getLogger(__name__)
        self.global_config = global_config or RateLimitConfig()

        # Global rate limit buckets
        self._global_minute_bucket = TokenBucket(
            capacity=self.global_config.requests_per_minute,
            refill_rate=self.global_config.requests_per_minute / 60.0,  # Per second
        )
        self._global_hour_bucket = TokenBucket(
            capacity=self.global_config.requests_per_hour,
            refill_rate=self.global_config.requests_per_hour / 3600.0,  # Per second
        )

        # Per-repository rate limit buckets
        self._repo_buckets: dict[str, dict[str, TokenBucket]] = {}

        # Statistics
        self._total_requests = 0
        self._rate_limited_requests = 0

        # Lock for thread-safe bucket access
        self._lock = asyncio.Lock()

    def configure_repo_limits(
        self,
        repository: str,
        requests_per_minute: int,
        requests_per_hour: int,
    ) -> None:
        """Configure rate limits for a specific repository.

        Args:
            repository: Repository name (org/repo format)
            requests_per_minute: Requests allowed per minute
            requests_per_hour: Requests allowed per hour
        """
        self._repo_buckets[repository] = {
            "minute": TokenBucket(
                capacity=requests_per_minute,
                refill_rate=requests_per_minute / 60.0,
            ),
            "hour": TokenBucket(
                capacity=requests_per_hour,
                refill_rate=requests_per_hour / 3600.0,
            ),
        }
        self.logger.debug(
            f"Configured rate limits for {repository}: {requests_per_minute}/min, {requests_per_hour}/hour"
        )

    async def check_rate_limit(
        self,
        repository: str | None = None,
        tokens: int = 1,
    ) -> tuple[bool, str | None]:
        """Check if request is within rate limits.

        Args:
            repository: Repository name for per-repo limits (optional)
            tokens: Number of tokens to consume (default: 1)

        Returns:
            Tuple of (allowed, reason):
            - allowed: True if request allowed, False if rate limited
            - reason: Reason for rate limiting (if applicable)
        """
        if not self.global_config.enabled:
            return True, None

        async with self._lock:
            self._total_requests += 1

            # Check global limits first
            if not self._global_minute_bucket.consume(tokens):
                self._rate_limited_requests += 1
                wait_time = self._global_minute_bucket.time_until_available(tokens)
                self.logger.warning(
                    f"Global rate limit exceeded (minute): {tokens} tokens needed, available in {wait_time:.1f}s"
                )
                return False, f"Global rate limit exceeded (requests/minute). Retry in {wait_time:.1f}s"

            if not self._global_hour_bucket.consume(tokens):
                self._rate_limited_requests += 1
                wait_time = self._global_hour_bucket.time_until_available(tokens)
                self.logger.warning(
                    f"Global rate limit exceeded (hour): {tokens} tokens needed, available in {wait_time:.1f}s"
                )
                return False, f"Global rate limit exceeded (requests/hour). Retry in {wait_time:.1f}s"

            # Check per-repo limits if configured
            if repository and repository in self._repo_buckets:
                repo_buckets = self._repo_buckets[repository]

                if not repo_buckets["minute"].consume(tokens):
                    self._rate_limited_requests += 1
                    wait_time = repo_buckets["minute"].time_until_available(tokens)
                    self.logger.warning(
                        f"Repository {repository} rate limit exceeded (minute): "
                        f"{tokens} tokens needed, available in {wait_time:.1f}s"
                    )
                    return (
                        False,
                        f"Repository rate limit exceeded (requests/minute). Retry in {wait_time:.1f}s",
                    )

                if not repo_buckets["hour"].consume(tokens):
                    self._rate_limited_requests += 1
                    wait_time = repo_buckets["hour"].time_until_available(tokens)
                    self.logger.warning(
                        f"Repository {repository} rate limit exceeded (hour): "
                        f"{tokens} tokens needed, available in {wait_time:.1f}s"
                    )
                    return False, f"Repository rate limit exceeded (requests/hour). Retry in {wait_time:.1f}s"

            # All limits passed
            self.logger.debug(f"Rate limit check passed for {repository or 'global'}: {tokens} token(s) consumed")
            return True, None

    async def wait_for_capacity(
        self,
        repository: str | None = None,
        tokens: int = 1,
        max_wait: float = 60.0,
    ) -> bool:
        """Wait until rate limit capacity is available.

        Args:
            repository: Repository name for per-repo limits (optional)
            tokens: Number of tokens needed
            max_wait: Maximum time to wait in seconds

        Returns:
            True if capacity became available, False if max_wait exceeded
        """
        start_time = time.time()

        while True:
            allowed, reason = await self.check_rate_limit(repository, tokens)
            if allowed:
                return True

            elapsed = time.time() - start_time
            if elapsed >= max_wait:
                self.logger.warning(
                    f"Rate limit wait timeout after {elapsed:.1f}s for {repository or 'global'}: {reason}"
                )
                return False

            # Wait a short time before retrying
            await asyncio.sleep(1.0)

    def get_stats(self, repository: str | None = None) -> dict[str, Any]:
        """Get rate limiting statistics.

        Args:
            repository: Get stats for specific repository (optional)

        Returns:
            Dictionary with rate limiting statistics
        """
        stats = {
            "total_requests": self._total_requests,
            "rate_limited_requests": self._rate_limited_requests,
            "rate_limit_percentage": (
                (self._rate_limited_requests / self._total_requests * 100) if self._total_requests > 0 else 0.0
            ),
            "global_limits": {
                "minute": {
                    "capacity": self.global_config.requests_per_minute,
                    "available": self._global_minute_bucket.get_available_tokens(),
                },
                "hour": {
                    "capacity": self.global_config.requests_per_hour,
                    "available": self._global_hour_bucket.get_available_tokens(),
                },
            },
        }

        if repository and repository in self._repo_buckets:
            repo_buckets = self._repo_buckets[repository]
            stats["repository_limits"] = {
                "repository": repository,
                "minute": {
                    "capacity": repo_buckets["minute"].capacity,
                    "available": repo_buckets["minute"].get_available_tokens(),
                },
                "hour": {
                    "capacity": repo_buckets["hour"].capacity,
                    "available": repo_buckets["hour"].get_available_tokens(),
                },
            }

        return stats

    def reset_stats(self) -> None:
        """Reset rate limiting statistics."""
        self._total_requests = 0
        self._rate_limited_requests = 0
        self.logger.debug("Rate limiting statistics reset")
