"""Built-in PR Test Oracle package."""

from __future__ import annotations

from webhook_server.test_oracle.analyzer import DEFAULT_TRIGGERS, call_test_oracle
from webhook_server.test_oracle.models import AnalyzeResult, TestMapping, TestRecommendation
from webhook_server.test_oracle.test_mapper import TestMapper

__all__ = [
    "AnalyzeResult",
    "DEFAULT_TRIGGERS",
    "TestMapper",
    "TestMapping",
    "TestRecommendation",
    "call_test_oracle",
]
