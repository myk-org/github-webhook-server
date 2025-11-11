"""Tests for AI Pydantic models validation and serialization."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from webhook_server.libs.ai.models import (
    AIAnalysisResult,
    AIConfig,
    CommandIntent,
    CommandType,
    FailureAnalysis,
    FailureCategory,
    RemediationSuggestion,
    ReviewerSuggestion,
)


class TestCommandIntent:
    """Test CommandIntent model validation and serialization."""

    def test_valid_command_intent(self) -> None:
        """Test creating valid CommandIntent."""
        intent = CommandIntent(
            command=CommandType.LGTM,
            confidence=0.95,
            original_text="Looks good to me!",
            parameters={},
            reasoning="User expressed approval",
        )

        assert intent.command == CommandType.LGTM
        assert intent.confidence == 0.95
        assert intent.original_text == "Looks good to me!"
        assert intent.reasoning == "User expressed approval"

    def test_confidence_validation(self) -> None:
        """Test confidence must be between 0 and 1."""
        # Valid confidence
        CommandIntent(
            command=CommandType.LGTM,
            confidence=0.5,
            original_text="test",
        )

        # Invalid confidence > 1
        with pytest.raises(ValidationError):
            CommandIntent(
                command=CommandType.LGTM,
                confidence=1.5,
                original_text="test",
            )

        # Invalid confidence < 0
        with pytest.raises(ValidationError):
            CommandIntent(
                command=CommandType.LGTM,
                confidence=-0.1,
                original_text="test",
            )

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        intent = CommandIntent(
            command=CommandType.RETEST,
            confidence=0.85,
            original_text="Please rerun the tests",
            parameters={"test_name": "pytest"},
            reasoning="User requested test rerun",
        )

        result = intent.to_dict()

        assert result["command"] == "retest"
        assert result["confidence"] == 0.85
        assert result["original_text"] == "Please rerun the tests"
        assert result["parameters"] == {"test_name": "pytest"}
        assert result["reasoning"] == "User requested test rerun"

    def test_from_dict(self) -> None:
        """Test deserialization from dictionary."""
        data = {
            "command": "lgtm",
            "confidence": 0.9,
            "original_text": "LGTM",
            "parameters": {},
            "reasoning": "Approval detected",
        }

        intent = CommandIntent.from_dict(data)

        assert intent.command == CommandType.LGTM
        assert intent.confidence == 0.9
        assert intent.original_text == "LGTM"


class TestRemediationSuggestion:
    """Test RemediationSuggestion model validation and serialization."""

    def test_valid_remediation(self) -> None:
        """Test creating valid RemediationSuggestion."""
        suggestion = RemediationSuggestion(
            action="Fix race condition",
            description="Add synchronization lock",
            priority=1,
            file_path="test_file.py",
            line_number=42,
            code_snippet="with lock:",
        )

        assert suggestion.action == "Fix race condition"
        assert suggestion.priority == 1
        assert suggestion.file_path == "test_file.py"
        assert suggestion.line_number == 42

    def test_priority_validation(self) -> None:
        """Test priority must be between 1 and 5."""
        # Valid priorities
        for priority in [1, 2, 3, 4, 5]:
            RemediationSuggestion(
                action="test",
                description="test",
                priority=priority,
            )

        # Invalid priority > 5
        with pytest.raises(ValidationError):
            RemediationSuggestion(
                action="test",
                description="test",
                priority=6,
            )

        # Invalid priority < 1
        with pytest.raises(ValidationError):
            RemediationSuggestion(
                action="test",
                description="test",
                priority=0,
            )

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        suggestion = RemediationSuggestion(
            action="Update test data",
            description="Refresh test fixtures",
            priority=3,
            file_path="tests/fixtures.py",
        )

        result = suggestion.to_dict()

        assert result["action"] == "Update test data"
        assert result["priority"] == 3
        assert result["file_path"] == "tests/fixtures.py"
        assert result["line_number"] is None

    def test_from_dict(self) -> None:
        """Test deserialization from dictionary."""
        data = {
            "action": "Fix timeout",
            "description": "Increase timeout value",
            "priority": 2,
            "file_path": None,
            "line_number": None,
            "code_snippet": None,
        }

        suggestion = RemediationSuggestion.from_dict(data)

        assert suggestion.action == "Fix timeout"
        assert suggestion.priority == 2
        assert suggestion.file_path is None


class TestFailureAnalysis:
    """Test FailureAnalysis model validation and serialization."""

    def test_valid_test_analysis(self) -> None:
        """Test creating valid FailureAnalysis."""
        remediation = RemediationSuggestion(
            action="Add retry logic",
            description="Implement exponential backoff",
            priority=1,
        )

        analysis = FailureAnalysis(
            category=FailureCategory.FLAKY,
            confidence=0.85,
            root_cause="Network timeout in external API call",
            affected_tests=["test_api_integration"],
            remediations=[remediation],
            should_retry=True,
            error_pattern="timeout",
            framework="pytest",
        )

        assert analysis.category == FailureCategory.FLAKY
        assert analysis.confidence == 0.85
        assert analysis.should_retry is True
        assert len(analysis.remediations) == 1
        assert analysis.framework == "pytest"

    def test_confidence_validation(self) -> None:
        """Test confidence must be between 0 and 1."""
        # Valid confidence
        FailureAnalysis(
            category=FailureCategory.REAL,
            confidence=0.7,
            root_cause="Bug in code",
        )

        # Invalid confidence
        with pytest.raises(ValidationError):
            FailureAnalysis(
                category=FailureCategory.REAL,
                confidence=1.2,
                root_cause="Bug in code",
            )

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        analysis = FailureAnalysis(
            category=FailureCategory.INFRASTRUCTURE,
            confidence=0.9,
            root_cause="Database connection failed",
            affected_tests=["test_db_query"],
            remediations=[],
            error_pattern="connection_refused",
        )

        result = analysis.to_dict()

        assert result["category"] == "INFRASTRUCTURE"
        assert result["confidence"] == 0.9
        assert result["root_cause"] == "Database connection failed"
        assert result["affected_tests"] == ["test_db_query"]
        assert result["remediations"] == []

    def test_from_dict(self) -> None:
        """Test deserialization from dictionary."""
        data = {
            "category": "REAL",
            "confidence": 0.95,
            "root_cause": "Null pointer exception",
            "affected_tests": ["test_feature"],
            "remediations": [
                {
                    "action": "Add null check",
                    "description": "Check for null before access",
                    "priority": 1,
                    "file_path": "src/feature.py",
                    "line_number": 100,
                    "code_snippet": "if obj is not None:",
                }
            ],
            "should_retry": False,
            "error_pattern": "null_pointer",
            "framework": "pytest",
        }

        analysis = FailureAnalysis.from_dict(data)

        assert analysis.category == FailureCategory.REAL
        assert analysis.confidence == 0.95
        assert len(analysis.remediations) == 1
        assert analysis.remediations[0].action == "Add null check"


class TestReviewerSuggestion:
    """Test ReviewerSuggestion model validation and serialization."""

    def test_valid_reviewer_suggestion(self) -> None:
        """Test creating valid ReviewerSuggestion."""
        suggestion = ReviewerSuggestion(
            username="developer1",
            score=0.85,
            expertise_score=0.9,
            workload_score=0.8,
            recent_commits=15,
            open_prs_count=2,
            reasoning="Expert in modified files with low workload",
            relevant_files=["src/auth.py", "src/users.py"],
        )

        assert suggestion.username == "developer1"
        assert suggestion.score == 0.85
        assert suggestion.expertise_score == 0.9
        assert suggestion.workload_score == 0.8
        assert suggestion.recent_commits == 15
        assert suggestion.open_prs_count == 2
        assert len(suggestion.relevant_files) == 2

    def test_score_validation(self) -> None:
        """Test all scores must be between 0 and 1."""
        # Valid scores
        ReviewerSuggestion(
            username="dev",
            score=0.5,
            expertise_score=0.6,
            workload_score=0.7,
            recent_commits=5,
            open_prs_count=1,
            reasoning="test",
        )

        # Invalid overall score
        with pytest.raises(ValidationError):
            ReviewerSuggestion(
                username="dev",
                score=1.5,
                expertise_score=0.6,
                workload_score=0.7,
                recent_commits=5,
                open_prs_count=1,
                reasoning="test",
            )

        # Invalid expertise score
        with pytest.raises(ValidationError):
            ReviewerSuggestion(
                username="dev",
                score=0.5,
                expertise_score=-0.1,
                workload_score=0.7,
                recent_commits=5,
                open_prs_count=1,
                reasoning="test",
            )

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        suggestion = ReviewerSuggestion(
            username="reviewer2",
            score=0.75,
            expertise_score=0.8,
            workload_score=0.7,
            recent_commits=10,
            open_prs_count=3,
            reasoning="Good expertise, moderate workload",
            relevant_files=["api.py"],
        )

        result = suggestion.to_dict()

        assert result["username"] == "reviewer2"
        assert result["score"] == 0.75
        assert result["relevant_files"] == ["api.py"]

    def test_from_dict(self) -> None:
        """Test deserialization from dictionary."""
        data = {
            "username": "expert_dev",
            "score": 0.95,
            "expertise_score": 1.0,
            "workload_score": 0.9,
            "recent_commits": 25,
            "open_prs_count": 1,
            "reasoning": "Primary maintainer of affected files",
            "relevant_files": ["core.py", "utils.py"],
        }

        suggestion = ReviewerSuggestion.from_dict(data)

        assert suggestion.username == "expert_dev"
        assert suggestion.score == 0.95
        assert suggestion.expertise_score == 1.0


class TestAIConfig:
    """Test AIConfig model validation and serialization."""

    def test_default_config(self) -> None:
        """Test creating AIConfig with defaults."""
        config = AIConfig()

        assert config.enabled is False
        assert config.provider == "gemini"
        assert config.model == "gemini-2.0-flash-latest"
        assert config.temperature == 0.3
        assert config.max_tokens == 2000
        assert config.nlp_commands_enabled is False
        assert config.test_analysis_enabled is False
        assert config.smart_reviewers_enabled is False

    def test_temperature_validation(self) -> None:
        """Test temperature must be between 0 and 2."""
        # Valid temperatures
        AIConfig(temperature=0.0)
        AIConfig(temperature=1.0)
        AIConfig(temperature=2.0)

        # Invalid temperature > 2
        with pytest.raises(ValidationError):
            AIConfig(temperature=2.5)

        # Invalid temperature < 0
        with pytest.raises(ValidationError):
            AIConfig(temperature=-0.1)

    def test_from_config_dict(self) -> None:
        """Test creating AIConfig from config.yaml structure."""
        config_data = {
            "enabled": True,
            "provider": "gemini",
            "gemini": {
                "model": "gemini-2.0-flash-latest",
                "temperature": 0.5,
                "max-tokens": 3000,
                "api-key-env": "MY_GEMINI_KEY",
            },
            "features": {
                "nlp-commands": {"enabled": True},
                "test-analysis": {"enabled": False},
                "smart-reviewers": {"enabled": True},
            },
        }

        config = AIConfig.from_config_dict(config_data)

        assert config.enabled is True
        assert config.model == "gemini-2.0-flash-latest"
        assert config.temperature == 0.5
        assert config.max_tokens == 3000
        assert config.api_key_env == "MY_GEMINI_KEY"  # pragma: allowlist secret
        assert config.nlp_commands_enabled is True
        assert config.test_analysis_enabled is False
        assert config.smart_reviewers_enabled is True

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        config = AIConfig(
            enabled=True,
            temperature=0.7,
            nlp_commands_enabled=True,
        )

        result = config.to_dict()

        assert result["enabled"] is True
        assert result["gemini"]["temperature"] == 0.7
        assert result["features"]["nlp_commands"]["enabled"] is True


class TestAIAnalysisResult:
    """Test AIAnalysisResult model validation and serialization."""

    def test_valid_analysis_result(self) -> None:
        """Test creating valid AIAnalysisResult."""
        result = AIAnalysisResult(
            feature="test-analysis",
            repository="owner/repo",
            pr_number=123,
            model_used="gemini-2.0-flash-latest",
            prompt_tokens=1500,
            completion_tokens=500,
            cost_usd=0.0015,
            success=True,
        )

        assert result.feature == "test-analysis"
        assert result.repository == "owner/repo"
        assert result.pr_number == 123
        assert result.model_used == "gemini-2.0-flash-latest"
        assert result.prompt_tokens == 1500
        assert result.completion_tokens == 500
        assert result.cost_usd == 0.0015
        assert result.success is True
        assert result.error_message is None

    def test_failed_analysis_result(self) -> None:
        """Test creating failed analysis result."""
        result = AIAnalysisResult(
            feature="nlp-commands",
            repository="owner/repo",
            model_used="gemini-2.0-flash-latest",
            prompt_tokens=100,
            completion_tokens=0,
            cost_usd=0.0,
            success=False,
            error_message="Rate limit exceeded",
        )

        assert result.success is False
        assert result.error_message == "Rate limit exceeded"
        assert result.completion_tokens == 0

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        timestamp = datetime.now(UTC)
        result = AIAnalysisResult(
            timestamp=timestamp,
            feature="smart-reviewers",
            repository="org/project",
            pr_number=456,
            model_used="gemini-2.0-flash-latest",
            prompt_tokens=2000,
            completion_tokens=800,
            cost_usd=0.003,
            success=True,
        )

        data = result.to_dict()

        assert data["timestamp"] == timestamp.isoformat()
        assert data["feature"] == "smart-reviewers"
        assert data["pr_number"] == 456
        assert data["success"] is True

    def test_from_dict(self) -> None:
        """Test deserialization from dictionary."""
        timestamp = datetime.now(UTC)
        data = {
            "timestamp": timestamp.isoformat(),
            "feature": "test-analysis",
            "repository": "user/repo",
            "pr_number": 789,
            "model_used": "gemini-2.0-flash-latest",
            "prompt_tokens": 1000,
            "completion_tokens": 400,
            "cost_usd": 0.002,
            "success": True,
            "error_message": None,
        }

        result = AIAnalysisResult.from_dict(data)

        assert result.feature == "test-analysis"
        assert result.pr_number == 789
        assert result.prompt_tokens == 1000
        assert result.success is True
