"""AI response parser for converting Gemini responses to Pydantic models.

Handles parsing and validation of AI responses for:
- Command intent detection (NLP)
- Test failure analysis
- Smart reviewer suggestions

Provides robust error handling for malformed responses.
"""

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from webhook_server.libs.ai.models import (
    CommandIntent,
    CommandType,
    FailureAnalysis,
    FailureCategory,
    RemediationSuggestion,
    ReviewerSuggestion,
)

logger = logging.getLogger(__name__)


class ResponseParseError(Exception):
    """Raised when AI response cannot be parsed."""

    pass


def validate_response_format(response_text: str) -> dict[str, Any]:
    """Validate and extract JSON from AI response.

    Handles various response formats:
    - Pure JSON
    - JSON in markdown code blocks (```json ... ```)
    - JSON with surrounding text

    Args:
        response_text: Raw AI response text

    Returns:
        Parsed JSON as dictionary

    Raises:
        ResponseParseError: If response cannot be parsed as JSON
    """
    if not response_text or not response_text.strip():
        raise ResponseParseError("Empty response from AI")

    # Try to extract JSON from markdown code blocks
    json_block_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
    matches = re.findall(json_block_pattern, response_text, re.DOTALL)

    if matches:
        # Use the first JSON block found
        response_text = matches[0].strip()

    # Try to find JSON object or array in text (handle cases where JSON is embedded in prose)
    if not response_text.startswith("{") and not response_text.startswith("["):
        # Look for JSON object or array pattern
        json_object_pattern = r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
        json_array_pattern = r"\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]"

        # Try object first, then array
        match = re.search(json_object_pattern, response_text, re.DOTALL)
        if not match:
            match = re.search(json_array_pattern, response_text, re.DOTALL)

        if match:
            response_text = match.group(0)

    try:
        return json.loads(response_text)
    except json.JSONDecodeError as ex:
        logger.error(f"Failed to parse JSON from AI response: {ex}")
        logger.debug(f"Response text: {response_text[:500]}")
        raise ResponseParseError(f"Invalid JSON in AI response: {ex}") from ex


def parse_command_intent(response_text: str) -> CommandIntent:
    """Parse command intent detection response.

    Expected JSON format:
    {
        "command": "lgtm",
        "confidence": 0.95,
        "original_text": "Looks good to me!",
        "parameters": {},
        "reasoning": "User expressed approval"
    }

    Args:
        response_text: Raw AI response

    Returns:
        CommandIntent model

    Raises:
        ResponseParseError: If response is invalid
    """
    try:
        data = validate_response_format(response_text)

        # Validate required fields
        if "command" not in data:
            raise ResponseParseError("Missing 'command' field in response")
        if "confidence" not in data:
            raise ResponseParseError("Missing 'confidence' field in response")
        if "original_text" not in data:
            raise ResponseParseError("Missing 'original_text' field in response")

        # Map command string to enum (case-insensitive)
        command_str = data["command"].lower()
        try:
            command = CommandType(command_str)
        except ValueError:
            logger.warning(f"Unknown command type: {data['command']}, using UNKNOWN")
            command = CommandType.UNKNOWN

        # Create and validate with Pydantic
        return CommandIntent(
            command=command,
            confidence=float(data["confidence"]),
            original_text=data["original_text"],
            parameters=data.get("parameters", {}),
            reasoning=data.get("reasoning"),
        )

    except ValidationError as ex:
        logger.error(f"Pydantic validation failed for CommandIntent: {ex}")
        raise ResponseParseError(f"Invalid command intent format: {ex}") from ex
    except (KeyError, TypeError, ValueError) as ex:
        logger.error(f"Failed to parse command intent: {ex}")
        raise ResponseParseError(f"Invalid command intent response: {ex}") from ex


def parse_test_analysis(response_text: str) -> FailureAnalysis:
    """Parse test failure analysis response.

    Expected JSON format:
    {
        "category": "FLAKY",
        "confidence": 0.85,
        "root_cause": "Network timeout...",
        "affected_tests": ["test_api"],
        "remediations": [
            {
                "action": "Add retry logic",
                "description": "...",
                "priority": 1,
                "file_path": "test.py",
                "line_number": 42,
                "code_snippet": "..."
            }
        ],
        "should_retry": true,
        "error_pattern": "timeout",
        "framework": "pytest"
    }

    Args:
        response_text: Raw AI response

    Returns:
        FailureAnalysis model

    Raises:
        ResponseParseError: If response is invalid
    """
    try:
        data = validate_response_format(response_text)

        # Validate required fields
        if "category" not in data:
            raise ResponseParseError("Missing 'category' field in response")
        if "confidence" not in data:
            raise ResponseParseError("Missing 'confidence' field in response")
        if "root_cause" not in data:
            raise ResponseParseError("Missing 'root_cause' field in response")

        # Map category string to enum
        category_str = data["category"].upper()
        try:
            category = FailureCategory(category_str)
        except ValueError:
            logger.warning(f"Unknown category: {data['category']}, using UNKNOWN")
            category = FailureCategory.UNKNOWN

        # Parse remediations if present
        remediations = []
        for rem_data in data.get("remediations", []):
            remediations.append(
                RemediationSuggestion(
                    action=rem_data["action"],
                    description=rem_data["description"],
                    priority=int(rem_data["priority"]),
                    file_path=rem_data.get("file_path"),
                    line_number=rem_data.get("line_number"),
                    code_snippet=rem_data.get("code_snippet"),
                )
            )

        # Create and validate with Pydantic
        return FailureAnalysis(
            category=category,
            confidence=float(data["confidence"]),
            root_cause=data["root_cause"],
            affected_tests=data.get("affected_tests", []),
            remediations=remediations,
            should_retry=data.get("should_retry", False),
            error_pattern=data.get("error_pattern"),
            framework=data.get("framework"),
        )

    except ValidationError as ex:
        logger.error(f"Pydantic validation failed for FailureAnalysis: {ex}")
        raise ResponseParseError(f"Invalid test analysis format: {ex}") from ex
    except (KeyError, TypeError, ValueError) as ex:
        logger.error(f"Failed to parse test analysis: {ex}")
        raise ResponseParseError(f"Invalid test analysis response: {ex}") from ex


def parse_reviewer_suggestions(response_text: str) -> list[ReviewerSuggestion]:
    """Parse reviewer suggestions response.

    Expected JSON format:
    {
        "suggestions": [
            {
                "username": "developer1",
                "score": 0.85,
                "expertise_score": 0.9,
                "workload_score": 0.8,
                "recent_commits": 15,
                "open_prs_count": 2,
                "reasoning": "Expert in auth code",
                "relevant_files": ["auth.py"]
            }
        ]
    }

    Args:
        response_text: Raw AI response

    Returns:
        List of ReviewerSuggestion models

    Raises:
        ResponseParseError: If response is invalid
    """
    try:
        data = validate_response_format(response_text)

        # Handle both "suggestions" key and direct list
        if isinstance(data, list):
            suggestions_data = data
        elif "suggestions" in data:
            suggestions_data = data["suggestions"]
        else:
            raise ResponseParseError("Missing 'suggestions' field in response")

        if not isinstance(suggestions_data, list):
            raise ResponseParseError("'suggestions' must be a list")

        # Parse each suggestion
        suggestions = []
        for sugg_data in suggestions_data:
            suggestions.append(
                ReviewerSuggestion(
                    username=sugg_data["username"],
                    score=float(sugg_data["score"]),
                    expertise_score=float(sugg_data["expertise_score"]),
                    workload_score=float(sugg_data["workload_score"]),
                    recent_commits=int(sugg_data["recent_commits"]),
                    open_prs_count=int(sugg_data["open_prs_count"]),
                    reasoning=sugg_data["reasoning"],
                    relevant_files=sugg_data.get("relevant_files", []),
                )
            )

        return suggestions

    except ValidationError as ex:
        logger.error(f"Pydantic validation failed for ReviewerSuggestion: {ex}")
        raise ResponseParseError(f"Invalid reviewer suggestion format: {ex}") from ex
    except (KeyError, TypeError, ValueError) as ex:
        logger.error(f"Failed to parse reviewer suggestions: {ex}")
        raise ResponseParseError(f"Invalid reviewer suggestions response: {ex}") from ex


def safe_parse_command_intent(
    response_text: str,
    fallback_command: CommandType = CommandType.UNKNOWN,
) -> CommandIntent | None:
    """Safely parse command intent with fallback.

    Returns None instead of raising exceptions for better error handling
    in production webhook flows.

    Args:
        response_text: Raw AI response
        fallback_command: Command to use if parsing fails (reserved for future use)

    Returns:
        CommandIntent or None if parsing fails
    """
    del fallback_command  # Reserved for future use
    try:
        return parse_command_intent(response_text)
    except ResponseParseError:
        logger.exception("Failed to parse command intent, returning None")
        return None


def safe_parse_test_analysis(
    response_text: str,
) -> FailureAnalysis | None:
    """Safely parse test analysis with fallback.

    Returns None instead of raising exceptions for better error handling
    in production webhook flows.

    Args:
        response_text: Raw AI response

    Returns:
        FailureAnalysis or None if parsing fails
    """
    try:
        return parse_test_analysis(response_text)
    except ResponseParseError:
        logger.exception("Failed to parse test analysis, returning None")
        return None


def safe_parse_reviewer_suggestions(
    response_text: str,
) -> list[ReviewerSuggestion]:
    """Safely parse reviewer suggestions with fallback.

    Returns empty list instead of raising exceptions for better error
    handling in production webhook flows.

    Args:
        response_text: Raw AI response

    Returns:
        List of ReviewerSuggestion or empty list if parsing fails
    """
    try:
        return parse_reviewer_suggestions(response_text)
    except ResponseParseError:
        logger.exception("Failed to parse reviewer suggestions, returning empty list")
        return []
