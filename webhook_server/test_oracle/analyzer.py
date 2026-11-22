"""Core PR Test Oracle orchestration: fetch PR -> map tests -> call AI -> post recommendations."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shlex
import shutil
import tempfile
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from github.GithubException import GithubException
from pydantic import ValidationError

from webhook_server.libs.ai_cli import call_ai
from webhook_server.test_oracle.models import AnalyzeResult, TestMapping, TestRecommendation
from webhook_server.test_oracle.test_mapper import TestMapper, prioritize_candidate_tests
from webhook_server.utils.github_retry import github_api_call
from webhook_server.utils.helpers import run_command

if TYPE_CHECKING:
    from github.File import File
    from github.PullRequest import PullRequest

    from webhook_server.libs.github_api import GithubWebhook

# "approved" refers to the /approve command trigger, not GitHub's review approval state.
DEFAULT_TRIGGERS: list[str] = ["approved"]
DEFAULT_TIMEOUT_MINUTES: int = 10
MAX_DIFF_CHARS: int = 100_000
PROMPT_FILENAME: str = "TESTS_ORACLE_PROMPT.md"
_MAX_CUSTOM_PROMPT_CHARS: int = 16_000
_MAX_CUSTOM_PROMPT_BYTES: int = _MAX_CUSTOM_PROMPT_CHARS * 4
_MAX_PROMPT_CANDIDATES_PER_FILE: int = 50
_GIT_INIT_TIMEOUT_SECONDS: int = 30
_GIT_NETWORK_TIMEOUT_SECONDS: int = 120
_MANUAL_FAILURE_COMMENT: str = "Test Oracle analysis failed; check server logs for details."

_LANGUAGE_MAP: dict[str, str] = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "jsx": "javascript",
    "tsx": "typescript",
    "go": "go",
    "java": "java",
    "rb": "ruby",
    "rs": "rust",
    "cs": "csharp",
    "php": "php",
    "sh": "bash",
    "bash": "bash",
}


def _detect_language(file_path: str) -> str:
    """Detect programming language from file extension for markdown code fences."""
    ext = file_path.rsplit(".", 1)[-1] if "." in file_path else ""
    return _LANGUAGE_MAP.get(ext, "")


def _sanitize_error(text: str, secret: str | list[str]) -> str:
    """Redact secret token(s) from error strings before logging."""
    if not text or not secret:
        return text
    secrets = [secret] if isinstance(secret, str) else secret
    sanitized = text
    for item in secrets:
        if item:
            sanitized = sanitized.replace(item, "***REDACTED***")
    return sanitized


def _build_pr_diff(pr_files: list[File], max_chars: int = MAX_DIFF_CHARS) -> str:
    """Build a unified diff string from PyGithub PR files with a total character cap."""
    sections: list[str] = []
    total_chars = 0

    for pr_file in pr_files:
        header = (
            f"diff --git a/{pr_file.filename} b/{pr_file.filename}\n"
            f"--- a/{pr_file.filename}\n"
            f"+++ b/{pr_file.filename}\n"
        )
        body = f"{pr_file.patch}\n" if pr_file.patch else f"[no patch available; status={pr_file.status}]\n"
        section = f"{header}{body}"

        if total_chars + len(section) > max_chars:
            remaining = max_chars - total_chars
            if remaining > 0:
                sections.append(section[:remaining])
            sections.append(f"\n... [diff truncated at {max_chars} chars]\n")
            break

        sections.append(section)
        total_chars += len(section)

    return "".join(sections)


def _truncate_custom_prompt(text: str, max_chars: int = _MAX_CUSTOM_PROMPT_CHARS) -> str:
    """Strip and cap custom prompt text at max_chars with a truncation notice."""
    stripped = text.strip()
    if len(stripped) > max_chars:
        return f"{stripped[:max_chars]}\n... [custom prompt truncated at {max_chars} chars]"
    return stripped


async def _resolve_custom_prompt(
    github_webhook: GithubWebhook,
    pull_request: PullRequest,
    raw_prompt: str | None,
    logger: logging.Logger,
    log_prefix: str,
) -> str:
    """Resolve custom prompt: config raw-prompt > base-branch TESTS_ORACLE_PROMPT.md > empty."""
    if raw_prompt and raw_prompt.strip():
        return _truncate_custom_prompt(raw_prompt)

    try:
        base_ref: str = await github_api_call(
            lambda: pull_request.base.ref,
            logger=logger,
            log_prefix=log_prefix,
        )
        content_obj = await github_api_call(
            github_webhook.repository.get_contents,
            PROMPT_FILENAME,
            ref=base_ref,
            logger=logger,
            log_prefix=log_prefix,
        )
    except GithubException as ex:
        if ex.status == 404:
            return ""
        logger.warning(f"{log_prefix} Failed to fetch {PROMPT_FILENAME} from base branch: {ex}")
        return ""
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.warning(f"{log_prefix} Failed to fetch {PROMPT_FILENAME} from base branch: {ex}")
        return ""

    if isinstance(content_obj, list):
        logger.warning(f"{log_prefix} {PROMPT_FILENAME} on base branch '{base_ref}' is a directory; ignoring")
        return ""

    try:
        file_size: int = await github_api_call(
            lambda: content_obj.size,
            logger=logger,
            log_prefix=log_prefix,
        )
        if isinstance(file_size, int) and file_size > _MAX_CUSTOM_PROMPT_BYTES:
            logger.warning(
                f"{log_prefix} {PROMPT_FILENAME} on base branch '{base_ref}' is too large "
                f"({file_size} bytes, max {_MAX_CUSTOM_PROMPT_BYTES}); ignoring"
            )
            return ""

        raw_bytes: bytes = await github_api_call(
            lambda: content_obj.decoded_content,
            logger=logger,
            log_prefix=log_prefix,
        )
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.warning(f"{log_prefix} Failed to read {PROMPT_FILENAME} from base branch '{base_ref}': {ex}")
        return ""

    return _truncate_custom_prompt(raw_bytes.decode("utf-8", errors="replace"))


def _build_ai_prompt(
    pr_diff: str,
    test_mappings: list[TestMapping],
    test_contents: dict[str, str],
    custom_prompt: str = "",
) -> str:
    """Build the AI prompt combining PR diff, static mappings, test contents, and custom instructions."""
    parts: list[str] = [
        "As an expert software testing engineer, analyze all modified files "
        "in this PR and create a targeted test execution plan.\n\n"
        "IMPORTANT: The repository may be a source code project with separate tests, "
        "OR it may be a test suite repository where the changed files ARE the tests. "
        "Adapt your analysis accordingly:\n"
        "- If changed files are source code: recommend which tests verify the changes.\n"
        "- If changed files are themselves tests: recommend running those changed tests, "
        "plus any other tests that share fixtures, utilities, or base classes with them.\n"
        "- If changed files are test utilities/fixtures/conftest: recommend running all "
        "tests that depend on or import from the changed utilities.\n",
        "## PR Diff\n",
        "IMPORTANT: Read the actual code changes below carefully. Do NOT "
        "base your recommendations on file names alone. Understand WHAT "
        "changed semantically — new functions, modified logic, changed "
        "signatures, altered control flow, updated configurations.\n",
        pr_diff,
        "\n",
        "## Pre-computed Test Mappings\n",
        "Static analysis has identified potential test file matches based on "
        "naming conventions and directory structure. These are CANDIDATES only — "
        "you must verify each one by reading the test file contents below and "
        "confirming it actually tests the changed code paths.\n",
    ]

    for mapping in test_mappings:
        parts.append(f"\n### {mapping.source_file}")
        parts.append(f"Mapping reason: {mapping.mapping_reason}")
        if mapping.candidate_tests:
            shown = mapping.candidate_tests[:_MAX_PROMPT_CANDIDATES_PER_FILE]
            parts.extend(f"  - {test}" for test in shown)
            omitted = len(mapping.candidate_tests) - len(shown)
            if omitted > 0:
                parts.append(f"  - ... (+{omitted} more)")
        else:
            parts.append("  (no direct mapping found)")
    parts.append("\n")

    if test_contents:
        parts.append("## Test File Contents\n")
        parts.append(
            "CRITICAL: Read each test file below thoroughly. Understand what "
            "each test function/class actually verifies. Only recommend a test "
            "if you can explain the specific connection between the PR changes "
            "and what the test validates.\n"
        )
        for file_path, content in test_contents.items():
            lang = _detect_language(file_path)
            parts.append(f"\n### {file_path}\n```{lang}\n{content}\n```\n")

    parts.append("## Analysis Instructions\n")
    parts.append(
        """For each changed file in the PR, determine:
1. If the changed file IS a test: recommend running it (the test itself was modified and needs execution)
2. If the changed file is a test utility, fixture, or conftest: recommend running ALL tests that import or depend on it
3. If the changed file is source code: recommend which tests DIRECTLY verify the changed code paths
4. Which other tests could BREAK due to downstream dependencies (imports, shared state, API contracts)
5. Whether any changes are purely cosmetic (formatting, comments, whitespace) and need NO testing

Be SELECTIVE — only recommend tests with a clear, explainable connection to the changes.
Do NOT recommend tests just because they are in the same module or have a similar name.
A test must actually exercise code paths affected by this PR.

For each recommended test, provide:
- test_file: path to the test file
- test_name: specific test class/function if applicable, or "(all)" if the entire file should run
- reason: a SPECIFIC explanation of how the PR changes affect what
  this test verifies (not generic statements like "tests the module")
- priority: "critical" (directly verifies changed code) or
  "standard" (regression safety for dependent code)
- confidence: "high" (certain), "medium" (likely), or
  "low" (possibly affected)

Your response must be ONLY a valid JSON array. No text before or after. No markdown code blocks.

Example:
[
  {
    "test_file": "tests/test_auth.py",
    "test_name": "TestAuth::test_login_flow",
    "reason": "PR modifies hash_password() in auth.py; this test calls login() which invokes it",
    "priority": "critical",
    "confidence": "high"
  }
]
"""
    )

    if custom_prompt:
        parts.append("## Additional Instructions\n")
        parts.append(custom_prompt)
        parts.append("\n")

    return "\n".join(parts)


def _parse_items(
    data: list[Any],
    logger: logging.Logger | None = None,
    log_prefix: str = "",
) -> list[TestRecommendation]:
    """Validate raw JSON list items into TestRecommendation objects, skipping malformed entries."""
    results: list[TestRecommendation] = []
    for item in data:
        if not isinstance(item, dict):
            if logger:
                logger.warning(f"{log_prefix} Skipping non-object recommendation item: {item!r}")
            continue
        try:
            results.append(TestRecommendation.model_validate(item))
        except (ValidationError, TypeError, ValueError):
            if logger:
                logger.warning(f"{log_prefix} Skipping malformed recommendation item: {item!r}")
    return results


def _parse_ai_response(
    raw_text: str,
    logger: logging.Logger | None = None,
    log_prefix: str = "",
) -> list[TestRecommendation] | None:
    """Extract and parse a JSON array of TestRecommendation objects from AI output.

    Returns:
        List of recommendations (possibly empty when AI explicitly returned ``[]``),
        or ``None`` when no valid JSON array could be parsed.
    """
    text = raw_text.strip()
    if not text:
        if logger:
            logger.warning(f"{log_prefix} AI returned empty response; no recommendations parsed")
        return None

    # 1. Direct JSON array
    try:
        data = json.loads(text)
        if isinstance(data, list):
            if not data:
                return []
            parsed = _parse_items(data, logger=logger, log_prefix=log_prefix)
            if parsed:
                return parsed
            if logger:
                logger.warning(f"{log_prefix} AI returned non-empty JSON array with no valid recommendation items")
            return None
    except json.JSONDecodeError:
        pass

    found_empty_array = False

    # 2. Fenced code blocks
    for block in re.findall(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            if not data:
                found_empty_array = True
                continue
            parsed = _parse_items(data, logger=logger, log_prefix=log_prefix)
            if parsed:
                return parsed

    # 3. Scan '[' offsets with JSONDecoder.raw_decode to handle surrounding prose
    decoder = json.JSONDecoder()
    pos = 0
    while (start := text.find("[", pos)) != -1:
        try:
            data, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            pos = start + 1
            continue
        if isinstance(data, list):
            if not data:
                found_empty_array = True
            else:
                parsed = _parse_items(data, logger=logger, log_prefix=log_prefix)
                if parsed:
                    return parsed
        pos = start + 1

    if found_empty_array:
        return []

    if logger:
        logger.warning(f"{log_prefix} Failed to parse AI response as JSON array")
    return None


def _format_pr_comment(
    recommendations: list[TestRecommendation],
    ai_provider: str,
    ai_model: str,
) -> str:
    """Format test recommendations as a GitHub PR markdown comment."""
    parts: list[str] = [
        "## Tests to Run\n",
        "The following tests should be run to verify this PR:\n",
    ]

    critical = [r for r in recommendations if r.priority == "critical"]
    standard = [r for r in recommendations if r.priority == "standard"]

    for section_title, section_recs in (
        ("### Critical (directly affected)", critical),
        ("### Standard (regression safety)", standard),
    ):
        if not section_recs:
            continue
        parts.append(section_title)
        for rec in section_recs:
            test_ref = f"`{rec.test_file}"
            if rec.test_name and rec.test_name != "(all)":
                test_ref += f"::{rec.test_name}"
            test_ref += "`"
            confidence = rec.confidence.capitalize()
            parts.append(f"- [ ] {test_ref} — {rec.reason} ({confidence} confidence)")
        parts.append("")

    if not recommendations:
        parts.append("No tests need to run for this PR. The changes do not appear to affect any testable code paths.\n")

    unique_files = len({r.test_file for r in recommendations})
    parts.append("### Summary")
    parts.append(f"- **{unique_files} test files** recommended ({len(critical)} critical, {len(standard)} standard)")
    parts.append(f"- AI Provider: {ai_provider} ({ai_model})")

    return "\n".join(parts)


def _map_and_load_context(
    repo_path: str,
    changed_files: list[str],
    test_patterns: list[str] | None,
    logger: logging.Logger,
    log_prefix: str,
) -> tuple[list[TestMapping], dict[str, str]]:
    """Run static test mapping and read prioritized candidate test files."""
    mapper = TestMapper(
        repo_path=repo_path,
        test_patterns=test_patterns,
        logger=logger,
        log_prefix=log_prefix,
    )
    test_mappings = mapper.map_changed_files(changed_files)
    all_candidates = prioritize_candidate_tests(test_mappings)
    test_contents = mapper.get_test_file_contents(all_candidates)
    return test_mappings, test_contents


async def _checkout_pr_head(
    repo_path: str,
    clone_url: str,
    github_token: str,
    pr_number: int,
    log_prefix: str,
    mask_sensitive: bool,
    logger: logging.Logger,
) -> bool:
    """Shallow-fetch refs/pull/{pr_number}/head into an isolated temp directory without leaking credentials."""
    quoted_repo = shlex.quote(repo_path)
    redact_list: list[str] = []
    fetch_env: dict[str, str] = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    if github_token:
        parsed = urlparse(clone_url)
        if not parsed.scheme or not parsed.netloc:
            logger.error(f"{log_prefix} Invalid clone URL for PR #{pr_number}: missing scheme or host")
            return False
        origin_base = f"{parsed.scheme}://{parsed.netloc}/"
        auth_b64 = base64.b64encode(f"x-access-token:{github_token}".encode()).decode("ascii")
        redact_list = [github_token, auth_b64]
        fetch_env.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"http.{origin_base}.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth_b64}",
        })

    redact: list[str] | None = redact_list or None

    rc, _, err = await run_command(
        command=f"git init {quoted_repo}",
        log_prefix=log_prefix,
        redact_secrets=redact,
        mask_sensitive=mask_sensitive,
        timeout=_GIT_INIT_TIMEOUT_SECONDS,
    )
    if not rc:
        logger.error(f"{log_prefix} Failed to initialize temp git repo: {_sanitize_error(err, redact_list)}")
        return False

    rc, _, err = await run_command(
        command=f"git -C {quoted_repo} fetch --depth=1 {shlex.quote(clone_url)} refs/pull/{pr_number}/head",
        log_prefix=log_prefix,
        redact_secrets=redact,
        mask_sensitive=mask_sensitive,
        timeout=_GIT_NETWORK_TIMEOUT_SECONDS,
        env=fetch_env,
    )
    if not rc:
        logger.error(f"{log_prefix} Failed to fetch PR #{pr_number} head: {_sanitize_error(err, redact_list)}")
        return False

    rc, _, err = await run_command(
        command=f"git -C {quoted_repo} checkout FETCH_HEAD",
        log_prefix=log_prefix,
        redact_secrets=redact,
        mask_sensitive=mask_sensitive,
        timeout=_GIT_NETWORK_TIMEOUT_SECONDS,
    )
    if not rc:
        logger.error(
            f"{log_prefix} Failed to checkout FETCH_HEAD for PR #{pr_number}: {_sanitize_error(err, redact_list)}"
        )
        return False

    return True


async def _post_failure_comment(
    pull_request: PullRequest,
    logger: logging.Logger,
    log_prefix: str,
) -> None:
    """Post a concise failure notice for manual /test-oracle commands without masking errors."""
    try:
        await github_api_call(
            pull_request.create_issue_comment,
            _MANUAL_FAILURE_COMMENT,
            logger=logger,
            log_prefix=log_prefix,
        )
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.warning(f"{log_prefix} Failed to post Test Oracle failure comment: {ex}")


async def _post_pr_recommendations(
    pull_request: PullRequest,
    pr_files: list[File],
    recommendations: list[TestRecommendation],
    comment_body: str,
    logger: logging.Logger,
    log_prefix: str,
) -> bool:
    """Post recommendations as a file-level review comment, falling back to an issue comment."""
    if recommendations:
        target_file = next((f.filename for f in pr_files if f.status != "removed" and f.patch), None)
        if target_file is not None:
            head_sha: str = await github_api_call(
                lambda: pull_request.head.sha,
                logger=logger,
                log_prefix=log_prefix,
            )
            try:
                await github_api_call(
                    pull_request.create_review_comment,
                    body=comment_body,
                    commit=head_sha,
                    path=target_file,
                    subject_type="file",
                    logger=logger,
                    log_prefix=log_prefix,
                )
                return True
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.warning(
                    f"{log_prefix} Failed to post review comment on {target_file} ({ex}); falling back to issue comment"
                )
        else:
            logger.warning(
                f"{log_prefix} No non-removed changed file with patch available for review comment; "
                "falling back to issue comment"
            )

    await github_api_call(
        pull_request.create_issue_comment,
        comment_body,
        logger=logger,
        log_prefix=log_prefix,
    )
    return False


async def call_test_oracle(
    github_webhook: GithubWebhook,
    pull_request: PullRequest,
    trigger: str | None = None,
) -> None:
    """Analyze a PR for test recommendations and post results on the PR.

    Args:
        github_webhook: The GithubWebhook instance with config, token, and repository.
        pull_request: The PyGithub PullRequest object.
        trigger: The event trigger (e.g., "approved", "pr-opened", "pr-synchronized").
                 "approved" means the /approve command, not GitHub review state.
                 None means command-triggered (/test-oracle) and always runs if configured.
    """
    config: dict[str, Any] | None = github_webhook.config.get_value("test-oracle")
    if not config:
        return

    logger = github_webhook.logger
    log_prefix = github_webhook.log_prefix

    if trigger is not None:
        triggers: list[str] = config.get("triggers", DEFAULT_TRIGGERS)
        if trigger not in triggers:
            logger.debug(f"{log_prefix} Test oracle trigger '{trigger}' not in configured triggers {triggers}")
            return

    ai_provider: str | None = config.get("ai-provider")
    ai_model: str | None = config.get("ai-model")
    if not ai_provider or not ai_model:
        logger.error(f"{log_prefix} Test oracle requires both 'ai-provider' and 'ai-model' in config")
        return

    repo_path: str | None = None
    try:
        pr_number: int = await github_api_call(
            lambda: pull_request.number,
            logger=logger,
            log_prefix=log_prefix,
        )
        pr_files: list[File] = await github_api_call(
            lambda: list(pull_request.get_files()),
            logger=logger,
            log_prefix=log_prefix,
        )
        if not pr_files:
            logger.info(f"{log_prefix} PR #{pr_number} has no changed files; skipping Test Oracle")
            return

        changed_files = [pr_file.filename for pr_file in pr_files]
        pr_diff = _build_pr_diff(pr_files)

        clone_url: str = await github_api_call(
            lambda: github_webhook.repository.clone_url,
            logger=logger,
            log_prefix=log_prefix,
        )

        # Isolated checkout: call_test_oracle runs in a background task, so never touch github_webhook.clone_repo_dir
        repo_path = await asyncio.to_thread(tempfile.mkdtemp, prefix="pr-test-oracle-")
        checked_out = await _checkout_pr_head(
            repo_path=repo_path,
            clone_url=clone_url,
            github_token=github_webhook.token,
            pr_number=pr_number,
            log_prefix=log_prefix,
            mask_sensitive=github_webhook.mask_sensitive,
            logger=logger,
        )
        if not checked_out:
            if trigger is None:
                await _post_failure_comment(pull_request=pull_request, logger=logger, log_prefix=log_prefix)
            return

        test_patterns: list[str] | None = config.get("test-patterns")
        raw_prompt: str | None = config.get("raw-prompt")
        test_mappings, test_contents = await asyncio.to_thread(
            _map_and_load_context,
            repo_path,
            changed_files,
            test_patterns,
            logger,
            log_prefix,
        )
        custom_prompt = await _resolve_custom_prompt(
            github_webhook=github_webhook,
            pull_request=pull_request,
            raw_prompt=raw_prompt,
            logger=logger,
            log_prefix=log_prefix,
        )

        prompt = _build_ai_prompt(
            pr_diff=pr_diff,
            test_mappings=test_mappings,
            test_contents=test_contents,
            custom_prompt=custom_prompt,
        )

        timeout_minutes: int = config.get("timeout-minutes", DEFAULT_TIMEOUT_MINUTES)
        logger.info(
            f"{log_prefix} Running Test Oracle for PR #{pr_number} "
            f"({len(changed_files)} changed files, {len(test_contents)} candidate test files)"
        )

        # tools=[] keeps the AI prompt-only so untrusted PR diffs cannot trigger tool execution
        ai_result = await call_ai(
            prompt=prompt,
            ai_provider=ai_provider,
            ai_model=ai_model,
            cwd=repo_path,
            timeout_minutes=timeout_minutes,
            tools=[],
        )
        if not ai_result.success:
            logger.error(f"{log_prefix} Test Oracle AI call failed: {ai_result.error}")
            if trigger is None:
                await _post_failure_comment(pull_request=pull_request, logger=logger, log_prefix=log_prefix)
            return

        recommendations = _parse_ai_response(ai_result.text, logger=logger, log_prefix=log_prefix)
        if recommendations is None:
            logger.error(f"{log_prefix} Test Oracle AI returned an unparseable response for PR #{pr_number}")
            if trigger is None:
                await _post_failure_comment(pull_request=pull_request, logger=logger, log_prefix=log_prefix)
            return

        critical_count = sum(1 for r in recommendations if r.priority == "critical")
        standard_count = sum(1 for r in recommendations if r.priority == "standard")
        unique_files = len({r.test_file for r in recommendations})
        summary = f"{unique_files} test files recommended ({critical_count} critical, {standard_count} standard)"

        comment_body = _format_pr_comment(
            recommendations=recommendations,
            ai_provider=ai_provider,
            ai_model=ai_model,
        )
        review_posted = await _post_pr_recommendations(
            pull_request=pull_request,
            pr_files=pr_files,
            recommendations=recommendations,
            comment_body=comment_body,
            logger=logger,
            log_prefix=log_prefix,
        )

        result = AnalyzeResult(
            ai_provider=ai_provider,
            ai_model=ai_model,
            recommendations=recommendations,
            summary=summary,
            review_posted=review_posted,
        )
        logger.info(
            f"{log_prefix} Test Oracle analysis complete for PR #{pr_number}: "
            f"{result.summary} (review_posted={result.review_posted})"
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(f"{log_prefix} Test Oracle analysis failed unexpectedly")
        if trigger is None:
            await _post_failure_comment(pull_request=pull_request, logger=logger, log_prefix=log_prefix)
    finally:
        if repo_path is not None:
            await asyncio.to_thread(shutil.rmtree, repo_path, True)
