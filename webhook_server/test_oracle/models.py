"""Pydantic v2 models for the built-in PR Test Oracle."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TestMapping(BaseModel):
    """Mapping of a changed source file to candidate test files."""

    source_file: str = Field(description="Changed source file path")
    candidate_tests: list[str] = Field(
        default_factory=list,
        description="Related test file paths",
    )
    mapping_reason: str = Field(
        default="",
        description="How the mapping was determined",
    )


class TestRecommendation(BaseModel):
    """A single AI test recommendation."""

    test_file: str = Field(description="Path to the test file")
    test_name: str = Field(
        default="(all)",
        description="Specific test name/class, or '(all)' for the entire file",
    )
    reason: str = Field(description="Why this test should run")
    priority: Literal["critical", "standard"] = Field(description="Test priority")
    confidence: Literal["high", "medium", "low"] = Field(description="Confidence level")

    @field_validator("test_name", mode="before")
    @classmethod
    def coerce_empty_test_name(cls, v: object) -> object:
        """Convert None or whitespace-only test_name to '(all)'."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return "(all)"
        return v


class AnalyzeResult(BaseModel):
    """Result of analyzing a PR for test recommendations."""

    ai_provider: str = Field(default="", description="AI provider used")
    ai_model: str = Field(default="", description="AI model used")
    recommendations: list[TestRecommendation] = Field(
        default_factory=list,
        description="Test recommendations",
    )
    summary: str = Field(default="", description="Human-readable summary")
    review_posted: bool = Field(
        default=False,
        description="Whether a file-level PR review comment was posted",
    )
