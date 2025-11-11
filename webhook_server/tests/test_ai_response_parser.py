"""Tests for AI response parser with valid and invalid responses."""

import json

import pytest

from webhook_server.libs.ai.models import CommandType, FailureCategory
from webhook_server.libs.ai.response_parser import (
    ResponseParseError,
    parse_command_intent,
    parse_reviewer_suggestions,
    parse_test_analysis,
    safe_parse_command_intent,
    safe_parse_reviewer_suggestions,
    safe_parse_test_analysis,
    validate_response_format,
)


class TestValidateResponseFormat:
    """Test JSON extraction and validation."""

    def test_pure_json(self) -> None:
        """Test parsing pure JSON response."""
        response = '{"command": "lgtm", "confidence": 0.9}'
        result = validate_response_format(response)
        assert result["command"] == "lgtm"
        assert result["confidence"] == 0.9

    def test_json_in_markdown_code_block(self) -> None:
        """Test extracting JSON from markdown code block."""
        response = """Here's the analysis:
```json
{
    "command": "retest",
    "confidence": 0.85
}
```
That's what I found."""

        result = validate_response_format(response)
        assert result["command"] == "retest"
        assert result["confidence"] == 0.85

    def test_json_in_code_block_without_language(self) -> None:
        """Test extracting JSON from code block without language tag."""
        response = """```
{"command": "verified"}
```"""

        result = validate_response_format(response)
        assert result["command"] == "verified"

    def test_json_embedded_in_text(self) -> None:
        """Test extracting JSON object from surrounding text."""
        response = 'The result is: {"command": "hold", "confidence": 0.95} based on analysis.'

        result = validate_response_format(response)
        assert result["command"] == "hold"

    def test_empty_response(self) -> None:
        """Test handling empty response."""
        with pytest.raises(ResponseParseError, match="Empty response"):
            validate_response_format("")

    def test_invalid_json(self) -> None:
        """Test handling invalid JSON."""
        with pytest.raises(ResponseParseError, match="Invalid JSON"):
            validate_response_format("{invalid json}")


class TestParseCommandIntent:
    """Test command intent parsing with various formats."""

    def test_valid_lgtm_command(self) -> None:
        """Test parsing valid LGTM command."""
        response = json.dumps({
            "command": "lgtm",
            "confidence": 0.95,
            "original_text": "Looks good to me!",
            "parameters": {},
            "reasoning": "User expressed approval",
        })

        intent = parse_command_intent(response)

        assert intent.command == CommandType.LGTM
        assert intent.confidence == 0.95
        assert intent.original_text == "Looks good to me!"
        assert intent.reasoning == "User expressed approval"

    def test_valid_retest_command_with_parameters(self) -> None:
        """Test parsing retest command with parameters."""
        response = json.dumps({
            "command": "retest",
            "confidence": 0.88,
            "original_text": "Please rerun pytest",
            "parameters": {"test_name": "pytest"},
            "reasoning": "User requested specific test",
        })

        intent = parse_command_intent(response)

        assert intent.command == CommandType.RETEST
        assert intent.parameters == {"test_name": "pytest"}

    def test_unknown_command_fallback(self) -> None:
        """Test handling unknown command type."""
        response = json.dumps({"command": "invalid_command", "confidence": 0.5, "original_text": "Some text"})

        intent = parse_command_intent(response)

        assert intent.command == CommandType.UNKNOWN

    def test_missing_required_field(self) -> None:
        """Test error when required field is missing."""
        response = json.dumps({
            "command": "lgtm",
            # Missing confidence
            "original_text": "LGTM",
        })

        with pytest.raises(ResponseParseError, match="Missing 'confidence'"):
            parse_command_intent(response)

    def test_invalid_confidence_value(self) -> None:
        """Test error when confidence is out of range."""
        response = json.dumps({
            "command": "lgtm",
            "confidence": 1.5,  # > 1.0
            "original_text": "LGTM",
        })

        with pytest.raises(ResponseParseError, match="Invalid command intent"):
            parse_command_intent(response)

    def test_command_in_markdown_block(self) -> None:
        """Test parsing command from markdown code block."""
        response = """```json
{
    "command": "cherry-pick",
    "confidence": 0.92,
    "original_text": "Cherry pick to v1.0"
}
```"""

        intent = parse_command_intent(response)

        assert intent.command == CommandType.CHERRY_PICK


class TestParseTestAnalysis:
    """Test failure analysis parsing with various formats."""

    def test_valid_flaky_analysis(self) -> None:
        """Test parsing flaky test analysis."""
        response = json.dumps({
            "category": "FLAKY",
            "confidence": 0.85,
            "root_cause": "Network timeout in external API call",
            "affected_tests": ["test_api_integration"],
            "remediations": [
                {
                    "action": "Add retry logic",
                    "description": "Implement exponential backoff",
                    "priority": 1,
                    "file_path": "test_api.py",
                    "line_number": 42,
                }
            ],
            "should_retry": True,
            "error_pattern": "timeout",
            "framework": "pytest",
        })

        analysis = parse_test_analysis(response)

        assert analysis.category == FailureCategory.FLAKY
        assert analysis.confidence == 0.85
        assert analysis.should_retry is True
        assert len(analysis.remediations) == 1
        assert analysis.remediations[0].priority == 1

    def test_valid_real_failure(self) -> None:
        """Test parsing real bug failure."""
        response = json.dumps({
            "category": "REAL",
            "confidence": 0.95,
            "root_cause": "Null pointer exception in auth module",
            "affected_tests": ["test_login", "test_logout"],
            "remediations": [
                {
                    "action": "Add null check",
                    "description": "Check user object before access",
                    "priority": 1,
                    "file_path": "auth.py",
                    "line_number": 100,
                    "code_snippet": "if user is not None:",
                }
            ],
            "should_retry": False,
        })

        analysis = parse_test_analysis(response)

        assert analysis.category == FailureCategory.REAL
        assert analysis.should_retry is False
        assert len(analysis.affected_tests) == 2

    def test_missing_category_field(self) -> None:
        """Test error when category field is missing."""
        response = json.dumps({
            # Missing category
            "confidence": 0.8,
            "root_cause": "Some error",
        })

        with pytest.raises(ResponseParseError, match="Missing 'category'"):
            parse_test_analysis(response)

    def test_unknown_category_fallback(self) -> None:
        """Test handling unknown category."""
        response = json.dumps({"category": "INVALID_CATEGORY", "confidence": 0.7, "root_cause": "Unknown error"})

        analysis = parse_test_analysis(response)

        assert analysis.category == FailureCategory.UNKNOWN

    def test_invalid_remediation_priority(self) -> None:
        """Test error when remediation priority is invalid."""
        response = json.dumps({
            "category": "REAL",
            "confidence": 0.9,
            "root_cause": "Bug",
            "remediations": [
                {
                    "action": "Fix",
                    "description": "Fix the bug",
                    "priority": 10,  # > 5
                }
            ],
        })

        with pytest.raises(ResponseParseError, match="Invalid test analysis"):
            parse_test_analysis(response)


class TestParseReviewerSuggestions:
    """Test reviewer suggestions parsing."""

    def test_valid_suggestions_with_suggestions_key(self) -> None:
        """Test parsing valid reviewer suggestions with 'suggestions' key."""
        response = json.dumps({
            "suggestions": [
                {
                    "username": "developer1",
                    "score": 0.85,
                    "expertise_score": 0.9,
                    "workload_score": 0.8,
                    "recent_commits": 15,
                    "open_prs_count": 2,
                    "reasoning": "Expert in auth code",
                    "relevant_files": ["auth.py", "users.py"],
                },
                {
                    "username": "developer2",
                    "score": 0.75,
                    "expertise_score": 0.7,
                    "workload_score": 0.8,
                    "recent_commits": 10,
                    "open_prs_count": 1,
                    "reasoning": "Recent contributor",
                    "relevant_files": ["api.py"],
                },
            ]
        })

        suggestions = parse_reviewer_suggestions(response)

        assert len(suggestions) == 2
        assert suggestions[0].username == "developer1"
        assert suggestions[0].score == 0.85
        assert suggestions[1].username == "developer2"

    def test_valid_suggestions_direct_list(self) -> None:
        """Test parsing reviewer suggestions as direct list."""
        response = json.dumps([
            {
                "username": "expert_dev",
                "score": 0.95,
                "expertise_score": 1.0,
                "workload_score": 0.9,
                "recent_commits": 25,
                "open_prs_count": 1,
                "reasoning": "Primary maintainer",
                "relevant_files": ["core.py"],
            }
        ])

        suggestions = parse_reviewer_suggestions(response)

        assert len(suggestions) == 1
        assert suggestions[0].username == "expert_dev"
        assert suggestions[0].expertise_score == 1.0

    def test_missing_suggestions_field(self) -> None:
        """Test error when suggestions field is missing."""
        response = json.dumps({"other_field": "value"})

        with pytest.raises(ResponseParseError, match="Missing 'suggestions'"):
            parse_reviewer_suggestions(response)

    def test_invalid_score_value(self) -> None:
        """Test error when score is out of range."""
        response = json.dumps({
            "suggestions": [
                {
                    "username": "dev",
                    "score": 1.5,  # > 1.0
                    "expertise_score": 0.8,
                    "workload_score": 0.7,
                    "recent_commits": 5,
                    "open_prs_count": 1,
                    "reasoning": "Test",
                }
            ]
        })

        with pytest.raises(ResponseParseError, match="Invalid reviewer suggestion"):
            parse_reviewer_suggestions(response)


class TestSafeParsers:
    """Test safe parser functions that return None/empty on error."""

    def test_safe_parse_command_intent_success(self) -> None:
        """Test safe parser returns valid result."""
        response = json.dumps({"command": "lgtm", "confidence": 0.9, "original_text": "LGTM"})

        intent = safe_parse_command_intent(response)

        assert intent is not None
        assert intent.command == CommandType.LGTM

    def test_safe_parse_command_intent_failure(self) -> None:
        """Test safe parser returns None on error."""
        response = "invalid json"

        intent = safe_parse_command_intent(response)

        assert intent is None

    def test_safe_parse_test_analysis_success(self) -> None:
        """Test safe parser returns valid result."""
        response = json.dumps({"category": "FLAKY", "confidence": 0.8, "root_cause": "Test"})

        analysis = safe_parse_test_analysis(response)

        assert analysis is not None
        assert analysis.category == FailureCategory.FLAKY

    def test_safe_parse_test_analysis_failure(self) -> None:
        """Test safe parser returns None on error."""
        response = "{}"

        analysis = safe_parse_test_analysis(response)

        assert analysis is None

    def test_safe_parse_reviewer_suggestions_success(self) -> None:
        """Test safe parser returns valid result."""
        response = json.dumps({
            "suggestions": [
                {
                    "username": "dev",
                    "score": 0.8,
                    "expertise_score": 0.9,
                    "workload_score": 0.7,
                    "recent_commits": 10,
                    "open_prs_count": 2,
                    "reasoning": "Test",
                }
            ]
        })

        suggestions = safe_parse_reviewer_suggestions(response)

        assert len(suggestions) == 1

    def test_safe_parse_reviewer_suggestions_failure(self) -> None:
        """Test safe parser returns empty list on error."""
        response = "invalid"

        suggestions = safe_parse_reviewer_suggestions(response)

        assert suggestions == []
