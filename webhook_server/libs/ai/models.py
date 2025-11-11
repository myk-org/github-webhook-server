"""Pydantic models for AI-powered workflow automation.

Provides type-safe data structures for AI feature responses including:
- Command intent detection (NLP)
- Test failure analysis
- Smart reviewer suggestions
- AI configuration management
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FailureCategory(str, Enum):  # noqa: UP042
    """Test failure categorization."""

    FLAKY = "FLAKY"  # Intermittent failure, likely infrastructure
    REAL = "REAL"  # Genuine bug in code
    INFRASTRUCTURE = "INFRASTRUCTURE"  # Environment/setup issue
    UNKNOWN = "UNKNOWN"  # Cannot determine category


class CommandType(str, Enum):  # noqa: UP042
    """Supported webhook command types."""

    LGTM = "lgtm"  # Looks good to me
    RETEST = "retest"  # Re-run tests
    CHERRY_PICK = "cherry-pick"  # Cherry-pick to branch
    VERIFIED = "verified"  # Mark as verified
    HOLD = "hold"  # Hold PR from merging
    UNHOLD = "unhold"  # Remove hold
    UNKNOWN = "unknown"  # Unrecognized command


class CommandIntent(BaseModel):
    """Detected command from natural language comment.

    Represents an AI-detected user intent from PR comments.
    Example: "Looks good to me" → CommandIntent(command=LGTM, confidence=0.95)
    """

    command: CommandType = Field(description="Detected command type")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score (0.0-1.0)")
    original_text: str = Field(description="Original comment text that triggered detection")
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Additional command parameters (e.g., test_name for retest)"
    )
    reasoning: str | None = Field(default=None, description="AI explanation of why this command was detected")

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        """Ensure confidence is between 0 and 1."""
        if not 0.0 <= v <= 1.0:
            raise ValueError("Confidence must be between 0.0 and 1.0")
        return v

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "command": self.command.value,
            "confidence": self.confidence,
            "original_text": self.original_text,
            "parameters": self.parameters,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommandIntent":
        """Create from dictionary."""
        return cls(
            command=CommandType(data["command"]),
            confidence=data["confidence"],
            original_text=data["original_text"],
            parameters=data.get("parameters", {}),
            reasoning=data.get("reasoning"),
        )


class RemediationSuggestion(BaseModel):
    """Suggested fix for a test failure.

    Provides actionable remediation steps for developers.
    """

    action: str = Field(description="Recommended action (e.g., 'Fix race condition', 'Update test data')")
    description: str = Field(description="Detailed explanation of the fix")
    priority: int = Field(ge=1, le=5, description="Priority level (1=highest, 5=lowest)")
    file_path: str | None = Field(default=None, description="File to modify (if applicable)")
    line_number: int | None = Field(default=None, description="Line number in file (if applicable)")
    code_snippet: str | None = Field(default=None, description="Example code fix (if applicable)")

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: int) -> int:
        """Ensure priority is between 1 and 5."""
        if not 1 <= v <= 5:
            raise ValueError("Priority must be between 1 and 5")
        return v

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "action": self.action,
            "description": self.description,
            "priority": self.priority,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "code_snippet": self.code_snippet,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RemediationSuggestion":
        """Create from dictionary."""
        return cls(
            action=data["action"],
            description=data["description"],
            priority=data["priority"],
            file_path=data.get("file_path"),
            line_number=data.get("line_number"),
            code_snippet=data.get("code_snippet"),
        )


class FailureAnalysis(BaseModel):
    """Analysis of test failure with categorization and remediation.

    Represents AI analysis of a test failure including:
    - Failure category (FLAKY, REAL, INFRASTRUCTURE)
    - Root cause analysis
    - Suggested remediation steps
    """

    category: FailureCategory = Field(description="Failure category")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in categorization (0.0-1.0)")
    root_cause: str = Field(description="Analysis of root cause")
    affected_tests: list[str] = Field(default_factory=list, description="List of affected test names")
    remediations: list[RemediationSuggestion] = Field(
        default_factory=list, description="Suggested fixes, ordered by priority"
    )
    should_retry: bool = Field(default=False, description="Whether to automatically retry (for FLAKY)")
    error_pattern: str | None = Field(
        default=None, description="Detected error pattern (e.g., 'timeout', 'connection_refused')"
    )
    framework: str | None = Field(default=None, description="Test framework (pytest, tox, pre-commit)")

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        """Ensure confidence is between 0 and 1."""
        if not 0.0 <= v <= 1.0:
            raise ValueError("Confidence must be between 0.0 and 1.0")
        return v

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "category": self.category.value,
            "confidence": self.confidence,
            "root_cause": self.root_cause,
            "affected_tests": self.affected_tests,
            "remediations": [r.to_dict() for r in self.remediations],
            "should_retry": self.should_retry,
            "error_pattern": self.error_pattern,
            "framework": self.framework,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FailureAnalysis":
        """Create from dictionary."""
        return cls(
            category=FailureCategory(data["category"]),
            confidence=data["confidence"],
            root_cause=data["root_cause"],
            affected_tests=data.get("affected_tests", []),
            remediations=[RemediationSuggestion.from_dict(r) for r in data.get("remediations", [])],
            should_retry=data.get("should_retry", False),
            error_pattern=data.get("error_pattern"),
            framework=data.get("framework"),
        )


class ReviewerSuggestion(BaseModel):
    """Smart reviewer recommendation with expertise analysis.

    Represents an AI-recommended reviewer based on:
    - File expertise (recent commits to changed files)
    - Current workload
    - Availability
    """

    username: str = Field(description="GitHub username")
    score: float = Field(ge=0.0, le=1.0, description="Overall recommendation score (0.0-1.0)")
    expertise_score: float = Field(ge=0.0, le=1.0, description="Expertise with changed files (0.0-1.0)")
    workload_score: float = Field(
        ge=0.0, le=1.0, description="Availability based on current PRs (0.0-1.0, higher=more available)"
    )
    recent_commits: int = Field(ge=0, description="Number of recent commits to changed files")
    open_prs_count: int = Field(ge=0, description="Number of currently assigned PRs")
    reasoning: str = Field(description="Explanation of why this reviewer is suggested")
    relevant_files: list[str] = Field(default_factory=list, description="Files the reviewer has expertise in")

    @field_validator("score", "expertise_score", "workload_score")
    @classmethod
    def validate_scores(cls, v: float) -> float:
        """Ensure scores are between 0 and 1."""
        if not 0.0 <= v <= 1.0:
            raise ValueError("Scores must be between 0.0 and 1.0")
        return v

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "username": self.username,
            "score": self.score,
            "expertise_score": self.expertise_score,
            "workload_score": self.workload_score,
            "recent_commits": self.recent_commits,
            "open_prs_count": self.open_prs_count,
            "reasoning": self.reasoning,
            "relevant_files": self.relevant_files,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewerSuggestion":
        """Create from dictionary."""
        return cls(
            username=data["username"],
            score=data["score"],
            expertise_score=data["expertise_score"],
            workload_score=data["workload_score"],
            recent_commits=data["recent_commits"],
            open_prs_count=data["open_prs_count"],
            reasoning=data["reasoning"],
            relevant_files=data.get("relevant_files", []),
        )


class AIConfig(BaseModel):
    """AI features configuration.

    Parsed from config.yaml or .github-webhook-server.yaml.
    Provides type-safe access to AI feature settings.
    """

    enabled: bool = Field(default=False, description="Master toggle for AI features")
    provider: str = Field(default="gemini", description="AI provider (currently only gemini supported)")
    model: str = Field(default="gemini-2.0-flash-latest", description="AI model to use")
    temperature: float = Field(default=0.3, ge=0.0, le=2.0, description="Model temperature (0.0-2.0)")
    max_tokens: int = Field(default=2000, gt=0, description="Maximum tokens in response")
    api_key_env: str = Field(default="GEMINI_API_KEY", description="Environment variable containing API key")
    nlp_commands_enabled: bool = Field(default=False, description="Natural language command detection enabled")
    test_analysis_enabled: bool = Field(default=False, description="Test failure analysis enabled")
    smart_reviewers_enabled: bool = Field(default=False, description="Smart reviewer suggestions enabled")

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        """Ensure temperature is between 0 and 2."""
        if not 0.0 <= v <= 2.0:
            raise ValueError("Temperature must be between 0.0 and 2.0")
        return v

    @classmethod
    def from_config_dict(cls, config: dict[str, Any]) -> "AIConfig":
        """Create from config.yaml ai-features section.

        Args:
            config: Dictionary from config.yaml ai-features section

        Returns:
            AIConfig instance with parsed settings
        """
        gemini_config = config.get("gemini", {})
        features = config.get("features", {})

        return cls(
            enabled=config.get("enabled", False),
            provider=config.get("provider", "gemini"),
            model=gemini_config.get("model", "gemini-2.0-flash-latest"),
            temperature=gemini_config.get("temperature", 0.3),
            max_tokens=gemini_config.get("max-tokens", 2000),
            api_key_env=gemini_config.get("api-key-env", "GEMINI_API_KEY"),
            nlp_commands_enabled=features.get("nlp-commands", {}).get("enabled", False),
            test_analysis_enabled=features.get("test-analysis", {}).get("enabled", False),
            smart_reviewers_enabled=features.get("smart-reviewers", {}).get("enabled", False),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "gemini": {
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "api_key_env": self.api_key_env,
            },
            "features": {
                "nlp_commands": {"enabled": self.nlp_commands_enabled},
                "test_analysis": {"enabled": self.test_analysis_enabled},
                "smart_reviewers": {"enabled": self.smart_reviewers_enabled},
            },
        }


class AIAnalysisResult(BaseModel):
    """Generic AI analysis result wrapper.

    Provides common metadata for all AI analysis operations.
    """

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="When analysis was performed")
    feature: str = Field(description="Feature that generated this result (nlp-commands, test-analysis, etc.)")
    repository: str = Field(description="Repository being analyzed (owner/repo)")
    pr_number: int | None = Field(default=None, description="Pull request number (if applicable)")
    model_used: str = Field(description="AI model used for analysis")
    prompt_tokens: int = Field(ge=0, description="Tokens used in prompt")
    completion_tokens: int = Field(ge=0, description="Tokens used in completion")
    cost_usd: float = Field(ge=0.0, description="Estimated cost in USD")
    success: bool = Field(description="Whether analysis completed successfully")
    error_message: str | None = Field(default=None, description="Error message if analysis failed")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "feature": self.feature,
            "repository": self.repository,
            "pr_number": self.pr_number,
            "model_used": self.model_used,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
            "success": self.success,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AIAnalysisResult":
        """Create from dictionary."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            feature=data["feature"],
            repository=data["repository"],
            pr_number=data.get("pr_number"),
            model_used=data["model_used"],
            prompt_tokens=data["prompt_tokens"],
            completion_tokens=data["completion_tokens"],
            cost_usd=data["cost_usd"],
            success=data["success"],
            error_message=data.get("error_message"),
        )
