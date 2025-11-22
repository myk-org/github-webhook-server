"""Cost tracking for AI API usage with aggregation and reporting.

Tracks token usage and calculates costs for Google Gemini API calls.
Provides daily/monthly aggregation for budget monitoring.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass
class UsageRecord:
    """Record of a single AI API usage."""

    timestamp: datetime
    feature: str  # nlp-commands, test-analysis, smart-reviewers
    repository: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str
    success: bool
    error_message: str | None = None

    @property
    def cost_usd(self) -> float:
        """Calculate cost for this usage.

        Gemini 2.0 Flash pricing:
        - Input: $0.075 per 1M tokens
        - Output: $0.30 per 1M tokens

        Returns:
            Cost in USD
        """
        input_cost = (self.prompt_tokens / 1_000_000) * 0.075
        output_cost = (self.completion_tokens / 1_000_000) * 0.30
        return input_cost + output_cost


@dataclass
class AggregatedUsage:
    """Aggregated usage statistics for a time period."""

    period: str  # 'daily', 'monthly', 'total'
    start_date: datetime
    end_date: datetime
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    by_feature: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_repository: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_requests == 0:
            return 0.0
        return (self.successful_requests / self.total_requests) * 100

    @property
    def average_cost_per_request(self) -> float:
        """Calculate average cost per request."""
        if self.total_requests == 0:
            return 0.0
        return self.total_cost_usd / self.total_requests


class CostTracker:
    """Track and aggregate AI API usage costs."""

    def __init__(self, logger: logging.Logger | None = None):
        """Initialize cost tracker.

        Args:
            logger: Logger instance (optional)
        """
        self.logger = logger or logging.getLogger(__name__)
        self._usage_records: list[UsageRecord] = []

    def record_usage(
        self,
        feature: str,
        repository: str,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
        success: bool = True,
        error_message: str | None = None,
    ) -> UsageRecord:
        """Record a single AI API usage.

        Args:
            feature: AI feature name (nlp-commands, test-analysis, etc.)
            repository: Repository name (org/repo format)
            prompt_tokens: Number of input tokens
            completion_tokens: Number of output tokens
            model: Model name used
            success: Whether the request succeeded
            error_message: Error message if failed

        Returns:
            UsageRecord created
        """
        total_tokens = prompt_tokens + completion_tokens

        record = UsageRecord(
            timestamp=datetime.now(),
            feature=feature,
            repository=repository,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=model,
            success=success,
            error_message=error_message,
        )

        self._usage_records.append(record)

        self.logger.debug(
            f"Recorded AI usage: {feature} for {repository} - "
            f"{prompt_tokens} prompt + {completion_tokens} completion tokens = "
            f"${record.cost_usd:.6f}"
        )

        return record

    def get_usage_today(self) -> AggregatedUsage:
        """Get aggregated usage for today.

        Returns:
            AggregatedUsage for today
        """
        now = datetime.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.get_usage_for_period(start_of_day, now, period="daily")

    def get_usage_this_month(self) -> AggregatedUsage:
        """Get aggregated usage for current month.

        Returns:
            AggregatedUsage for current month
        """
        now = datetime.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self.get_usage_for_period(start_of_month, now, period="monthly")

    def get_usage_for_period(
        self,
        start_date: datetime,
        end_date: datetime,
        period: str = "custom",
    ) -> AggregatedUsage:
        """Get aggregated usage for a specific time period.

        Args:
            start_date: Start of period
            end_date: End of period
            period: Period label ('daily', 'monthly', 'custom')

        Returns:
            AggregatedUsage for the period
        """
        # Filter records within period
        period_records = [r for r in self._usage_records if start_date <= r.timestamp <= end_date]

        if not period_records:
            return AggregatedUsage(
                period=period,
                start_date=start_date,
                end_date=end_date,
            )

        # Aggregate totals
        total_requests = len(period_records)
        successful_requests = sum(1 for r in period_records if r.success)
        failed_requests = total_requests - successful_requests
        total_prompt_tokens = sum(r.prompt_tokens for r in period_records)
        total_completion_tokens = sum(r.completion_tokens for r in period_records)
        total_tokens = sum(r.total_tokens for r in period_records)
        total_cost_usd = sum(r.cost_usd for r in period_records)

        # Aggregate by feature
        by_feature: dict[str, dict[str, Any]] = {}
        for record in period_records:
            if record.feature not in by_feature:
                by_feature[record.feature] = {
                    "requests": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                }
            by_feature[record.feature]["requests"] += 1
            by_feature[record.feature]["prompt_tokens"] += record.prompt_tokens
            by_feature[record.feature]["completion_tokens"] += record.completion_tokens
            by_feature[record.feature]["total_tokens"] += record.total_tokens
            by_feature[record.feature]["cost_usd"] += record.cost_usd

        # Aggregate by repository
        by_repository: dict[str, dict[str, Any]] = {}
        for record in period_records:
            if record.repository not in by_repository:
                by_repository[record.repository] = {
                    "requests": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                }
            by_repository[record.repository]["requests"] += 1
            by_repository[record.repository]["prompt_tokens"] += record.prompt_tokens
            by_repository[record.repository]["completion_tokens"] += record.completion_tokens
            by_repository[record.repository]["total_tokens"] += record.total_tokens
            by_repository[record.repository]["cost_usd"] += record.cost_usd

        return AggregatedUsage(
            period=period,
            start_date=start_date,
            end_date=end_date,
            total_requests=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            total_prompt_tokens=total_prompt_tokens,
            total_completion_tokens=total_completion_tokens,
            total_tokens=total_tokens,
            total_cost_usd=total_cost_usd,
            by_feature=by_feature,
            by_repository=by_repository,
        )

    def get_total_usage(self) -> AggregatedUsage:
        """Get aggregated usage for all time.

        Returns:
            AggregatedUsage for all records
        """
        if not self._usage_records:
            now = datetime.now()
            return AggregatedUsage(
                period="total",
                start_date=now,
                end_date=now,
            )

        start_date = min(r.timestamp for r in self._usage_records)
        end_date = max(r.timestamp for r in self._usage_records)

        return self.get_usage_for_period(start_date, end_date, period="total")

    def get_cost_summary(self) -> dict[str, Any]:
        """Get comprehensive cost summary.

        Returns:
            Dictionary with today, this month, and total usage
        """
        today = self.get_usage_today()
        this_month = self.get_usage_this_month()
        total = self.get_total_usage()

        return {
            "today": {
                "requests": today.total_requests,
                "tokens": today.total_tokens,
                "cost_usd": round(today.total_cost_usd, 6),
                "success_rate": round(today.success_rate, 2),
            },
            "this_month": {
                "requests": this_month.total_requests,
                "tokens": this_month.total_tokens,
                "cost_usd": round(this_month.total_cost_usd, 6),
                "success_rate": round(this_month.success_rate, 2),
            },
            "total": {
                "requests": total.total_requests,
                "tokens": total.total_tokens,
                "cost_usd": round(total.total_cost_usd, 6),
                "success_rate": round(total.success_rate, 2),
            },
            "by_feature": total.by_feature,
            "by_repository": total.by_repository,
        }

    def check_budget_alert(
        self,
        daily_budget_usd: float | None = None,
        monthly_budget_usd: float | None = None,
    ) -> dict[str, Any]:
        """Check if usage exceeds budget thresholds.

        Args:
            daily_budget_usd: Daily budget limit in USD
            monthly_budget_usd: Monthly budget limit in USD

        Returns:
            Dictionary with alert status and details
        """
        today = self.get_usage_today()
        this_month = self.get_usage_this_month()

        alerts: dict[str, Any] = {
            "has_alerts": False,
            "daily_alert": None,
            "monthly_alert": None,
        }

        if daily_budget_usd is not None:
            daily_usage = today.total_cost_usd
            daily_percentage = (daily_usage / daily_budget_usd) * 100 if daily_budget_usd > 0 else 0

            if daily_usage >= daily_budget_usd:
                alerts["has_alerts"] = True
                alerts["daily_alert"] = {
                    "status": "exceeded",
                    "budget": daily_budget_usd,
                    "usage": round(daily_usage, 6),
                    "percentage": round(daily_percentage, 2),
                }
            elif daily_percentage >= 80:
                alerts["has_alerts"] = True
                alerts["daily_alert"] = {
                    "status": "warning",
                    "budget": daily_budget_usd,
                    "usage": round(daily_usage, 6),
                    "percentage": round(daily_percentage, 2),
                }

        if monthly_budget_usd is not None:
            monthly_usage = this_month.total_cost_usd
            monthly_percentage = (monthly_usage / monthly_budget_usd) * 100 if monthly_budget_usd > 0 else 0

            if monthly_usage >= monthly_budget_usd:
                alerts["has_alerts"] = True
                alerts["monthly_alert"] = {
                    "status": "exceeded",
                    "budget": monthly_budget_usd,
                    "usage": round(monthly_usage, 6),
                    "percentage": round(monthly_percentage, 2),
                }
            elif monthly_percentage >= 80:
                alerts["has_alerts"] = True
                alerts["monthly_alert"] = {
                    "status": "warning",
                    "budget": monthly_budget_usd,
                    "usage": round(monthly_usage, 6),
                    "percentage": round(monthly_percentage, 2),
                }

        return alerts

    def clear_old_records(self, days_to_keep: int = 90) -> int:
        """Clear usage records older than specified days.

        Args:
            days_to_keep: Number of days to retain records

        Returns:
            Number of records deleted
        """
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        original_count = len(self._usage_records)

        self._usage_records = [r for r in self._usage_records if r.timestamp >= cutoff_date]

        deleted_count = original_count - len(self._usage_records)

        if deleted_count > 0:
            self.logger.info(f"Cleared {deleted_count} usage records older than {days_to_keep} days")

        return deleted_count
