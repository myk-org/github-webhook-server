"""Prompt templates for AI-powered workflow automation.

This module provides versioned, reusable prompt templates for various AI features.
Each template is a function that takes context variables and returns a formatted prompt.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

TEMPLATE_VERSION = "1.0.0"


@dataclass
class PromptTemplate:
    """Base class for prompt templates with version tracking."""

    name: str
    version: str = TEMPLATE_VERSION
    created_at: str = datetime.now().isoformat()


def nlp_command_detection(
    comment_body: str,
    pr_number: int,
    pr_title: str,
    available_commands: list[str],
) -> str:
    """Generate prompt for natural language command detection.

    Args:
        comment_body: The comment text to analyze
        pr_number: Pull request number
        pr_title: Pull request title
        available_commands: List of available command names

    Returns:
        Formatted prompt for Gemini
    """
    return f"""You are analyzing a GitHub pull request comment to detect commands and user intent.

**Context:**
- Pull Request: #{pr_number} - {pr_title}
- Comment: {comment_body}

**Available Commands:**
{chr(10).join(f"- {cmd}" for cmd in available_commands)}

**Your Task:**
Extract any commands or intentions from the comment. Users may use:
1. Exact command syntax (e.g., "/lgtm", "/approve")
2. Natural language (e.g., "looks good to me", "please rerun tests")
3. Typos (e.g., "/aprove" instead of "/approve")

**Examples:**
- "Looks good to me" → lgtm
- "LGTM!" → lgtm
- "Please approve this" → approve
- "Can you rerun the tox tests?" → retest (test_name: tox)
- "Cherry pick this to stable-v1" → cherry_pick (branch: stable-v1)
- "/aprove" → approve (typo correction)

**Response Format:**
Return a list of detected commands with their arguments. For each command:
- command: The canonical command name
- arguments: Dictionary of arguments (if any)
- confidence: Confidence score 0.0-1.0
- original_text: The text fragment that triggered this detection

If no commands detected, return empty list.

**Important:**
- Be permissive with natural language variations
- Correct common typos
- High confidence (>0.8) for exact matches
- Medium confidence (0.5-0.8) for natural language
- Low confidence (<0.5) for ambiguous cases
"""


def test_failure_analysis(
    test_output: str,
    check_name: str,
    pr_number: int,
    changed_files: list[str],
) -> str:
    """Generate prompt for test failure analysis.

    Args:
        test_output: The test failure output/logs
        check_name: Name of the check that failed (tox, pre-commit, etc.)
        pr_number: Pull request number
        changed_files: List of files changed in the PR

    Returns:
        Formatted prompt for Gemini
    """
    files_str = "\n".join(f"- {f}" for f in changed_files[:20])  # Limit to first 20
    if len(changed_files) > 20:
        files_str += f"\n... and {len(changed_files) - 20} more files"

    return f"""You are analyzing a test failure to categorize it and suggest remediation.

**Context:**
- Pull Request: #{pr_number}
- Check: {check_name}
- Changed Files:
{files_str}

**Test Output:**
```
{test_output[:5000]}  # Limit output size
```

**Your Task:**
Analyze this test failure and provide:

1. **Category** (choose one):
   - FLAKY: Random/intermittent failure (network timeout, race condition, timing issue)
   - REAL: Actual bug or logic error in the code
   - INFRASTRUCTURE: CI/runner issue (dependency problem, resource exhaustion, configuration)

2. **Root Cause**: Brief explanation of what caused the failure

3. **Confidence**: How confident you are in this categorization (0.0-1.0)

4. **Remediation**: Specific suggestions to fix the issue

5. **Auto-Retry**: Should this be automatically retried? (true/false)

**Analysis Guidelines:**
- Network timeouts → FLAKY
- Assertion failures → REAL
- Import errors → INFRASTRUCTURE (if dependency-related) or REAL (if code issue)
- Race conditions → FLAKY
- Out of memory → INFRASTRUCTURE
- API rate limits → FLAKY

**Response Format:**
Provide structured analysis with all fields above.
"""


def reviewer_recommendation(
    pr_title: str,
    pr_description: str,
    changed_files: list[str],
    owners_list: list[dict[str, Any]],
    current_reviewers: list[str],
) -> str:
    """Generate prompt for smart reviewer recommendations.

    Args:
        pr_title: Pull request title
        pr_description: Pull request description
        changed_files: List of changed files
        owners_list: List of potential reviewers with metadata
        current_reviewers: Already assigned reviewers

    Returns:
        Formatted prompt for Gemini
    """
    files_str = "\n".join(f"- {f}" for f in changed_files[:30])
    if len(changed_files) > 30:
        files_str += f"\n... and {len(changed_files) - 30} more files"

    owners_str = "\n".join(
        f"- {owner['login']}: workload={owner.get('workload', 'unknown')}, "
        f"expertise={owner.get('expertise', 'unknown')}"
        for owner in owners_list[:10]
    )

    return f"""You are suggesting the best reviewers for a GitHub pull request.

**Pull Request:**
- Title: {pr_title}
- Description: {pr_description[:500]}

**Changed Files:**
{files_str}

**Available Reviewers:**
{owners_str}

**Current Reviewers:**
{", ".join(current_reviewers) if current_reviewers else "None assigned yet"}

**Your Task:**
Recommend the top 3 reviewers who should review this PR, considering:
1. **Expertise**: Who has worked on these files recently?
2. **Workload**: Who has capacity (lower current review load)?
3. **Relevance**: Does the PR topic match their domain?

**Response Format:**
For each recommended reviewer:
- login: GitHub username
- priority: 1 (highest) to 3 (lowest)
- reason: Brief explanation (1 sentence) why they're a good fit
- confidence: Confidence score 0.0-1.0

**Guidelines:**
- Avoid overloading reviewers with high workload
- Prioritize recent contributors to modified files
- Consider the PR's complexity and topic
- Don't recommend already-assigned reviewers
"""


def cherry_pick_suggestion(
    pr_title: str,
    pr_labels: list[str],
    pr_description: str,
    merged_to_branch: str,
    available_branches: list[str],
    recent_cherry_picks: list[dict[str, str]],
) -> str:
    """Generate prompt for cherry-pick recommendations.

    Args:
        pr_title: Pull request title
        pr_labels: PR labels (bug, enhancement, etc.)
        pr_description: Pull request description
        merged_to_branch: Branch where PR was merged
        available_branches: List of branches that could receive cherry-picks
        recent_cherry_picks: Recent cherry-pick history for context

    Returns:
        Formatted prompt for Gemini
    """
    labels_str = ", ".join(pr_labels) if pr_labels else "None"
    branches_str = "\n".join(f"- {b}" for b in available_branches)

    recent_str = "None"
    if recent_cherry_picks:
        recent_str = "\n".join(
            f"- PR #{cp.get('pr_number', 'N/A')}: {cp.get('from_branch', '?')} → {cp.get('to_branches', [])}"
            for cp in recent_cherry_picks[:5]
        )

    return f"""You are recommending which branches should receive a cherry-pick (backport) of a merged PR.

**Pull Request:**
- Title: {pr_title}
- Labels: {labels_str}
- Description: {pr_description[:500]}
- Merged to: {merged_to_branch}

**Available Target Branches:**
{branches_str}

**Recent Cherry-Pick Patterns:**
{recent_str}

**Your Task:**
Determine which branches should receive this change as a cherry-pick, considering:

1. **Severity**: Is this a critical bug fix or just an enhancement?
   - Bug fixes (especially security) → backport to stable branches
   - New features → usually only in main/master
   - Breaking changes → usually NOT backported

2. **Branch Policy**:
   - stable-v1, stable-v2 → Only critical fixes
   - release-* → Bug fixes and important improvements
   - main/master → Everything (but cherry-picks go FROM here)

3. **Labels**:
   - "bug", "security", "critical" → Strong cherry-pick candidates
   - "enhancement", "feature" → Weaker cherry-pick candidates
   - "breaking-change" → Usually NOT cherry-picked

**Response Format:**
For each recommended target branch:
- branch: Branch name
- priority: high/medium/low
- reason: Brief explanation (1 sentence)
- conflict_risk: low/medium/high (estimate merge conflict likelihood)

If no cherry-pick needed, return empty list with explanation.
"""


# Template registry for version tracking and introspection
TEMPLATES = {
    "nlp_command_detection": PromptTemplate(
        name="nlp_command_detection",
        version=TEMPLATE_VERSION,
    ),
    "test_failure_analysis": PromptTemplate(
        name="test_failure_analysis",
        version=TEMPLATE_VERSION,
    ),
    "reviewer_recommendation": PromptTemplate(
        name="reviewer_recommendation",
        version=TEMPLATE_VERSION,
    ),
    "cherry_pick_suggestion": PromptTemplate(
        name="cherry_pick_suggestion",
        version=TEMPLATE_VERSION,
    ),
}


def get_template_version(template_name: str) -> str:
    """Get version of a specific template.

    Args:
        template_name: Name of the template

    Returns:
        Version string

    Raises:
        KeyError: If template not found
    """
    return TEMPLATES[template_name].version


def list_templates() -> list[str]:
    """List all available template names.

    Returns:
        List of template names
    """
    return list(TEMPLATES.keys())
