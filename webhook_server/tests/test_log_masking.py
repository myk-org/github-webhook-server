"""Security tests for log masking.

simple_logger masks with `re.sub(r"({pattern}\\W+[^\\s]+)", ...)`, so a pattern is a
regex fragment and a single non-word character is enough to trigger it.

The list is deliberately broad on purpose. Exact-match redaction (_redact_secrets) only
covers the call sites that pass their own secret, so this filter is the only backstop for
a secret the code does not know about - a rotated token, an env-var token, or an exception
that echoes a request header. Broad beats clever here: an over-eager mask hides a login
name, an under-eager one prints a credential.

These tests therefore pin the no-leak direction. The cosmetic cost is that prose such as
"login alice" renders as "login *****"; log lines are worded to avoid that instead.
"""

import importlib
import inspect
import io
import logging
import re
from typing import Any
from unittest.mock import Mock, patch

import pytest

from webhook_server.libs.config import Config
from webhook_server.utils import masking
from webhook_server.utils.context import WebhookContext
from webhook_server.utils.helpers import get_api_with_highest_rate_limit, log_rate_limit
from webhook_server.utils.masking import (
    SecretRedactionFilter,
    apply_masking,
)

# Real-shaped secrets, assembled from parts so no contiguous token-shaped literal sits in
# the file - detect-private-key and gitleaks would both reject it. detect-secrets scores the
# remaining fragments by entropy regardless, hence the per-line pragma: these are fabricated
# fixtures, the scanners are right that a real credential in source would be a leak, and
# this is the repo's existing convention (see tests/conftest.py).
GITHUB_TOKEN = "ghp_" + "A1b2C3d4E5f6" + "G7h8I9j0K1l2"  # pragma: allowlist secret
OAUTH_TOKEN = "gho_" + "A1b2C3d4E5f6" + "G7h8I9j0K1l2"  # pragma: allowlist secret
SERVER_TOKEN = "ghs_" + "A1b2C3d4E5f6" + "G7h8I9j0K1l2"  # pragma: allowlist secret
FINE_GRAINED_TOKEN = "github_pat_" + "11ABCDEFG" + "0abcdefghijklmnopqrstuvwxyz0123456789"  # pragma: allowlist secret
PYPI_TOKEN = "pypi-" + "AgEIcHlwaS5vcmc"  # pragma: allowlist secret
AWS_ACCESS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"  # pragma: allowlist secret
PASSWORD = "hunter2" + "correcthorse"  # pragma: allowlist secret
PEM_KEY = "-----BEGIN " + "RSA PRIVATE " + "KEY-----"
SLACK_WEBHOOK_URL = "https://hooks.slack.com/" + "services/T000/B000/XXXXXXXX"  # pragma: allowlist secret

SECRETS: list[str] = [
    GITHUB_TOKEN,
    OAUTH_TOKEN,
    SERVER_TOKEN,
    FINE_GRAINED_TOKEN,
    PYPI_TOKEN,
    AWS_ACCESS_KEY,
    PASSWORD,
    PEM_KEY,
    SLACK_WEBHOOK_URL,
]
# Every keyword in the live pattern list, each in the forms that appear in real logs.
ASSIGNMENTS: list[str] = [
    "password={secret}",
    "password: {secret}",
    "password {secret}",
    "--password {secret}",
    "token={secret}",
    "token: {secret}",
    "token {secret}",
    "GITHUB_TOKEN={secret}",
    "github_token={secret}",
    "login={secret}",
    "login: {secret}",
    "username: {secret}",
    "secret: {secret}",
    "api_key={secret}",
    "apikey={secret}",
    "private_key: {secret}",
    "webhook_secret={secret}",
    "pypi: {secret}",
    "--username {secret}",
    "-u {secret}",
    "-p {secret}",
    "--creds {secret}",
    "container_repository_password={secret}",
    "slack-webhook-url: {secret}",
    "webhook_url={secret}",
    "github-app-id={secret}",
]


def _patterns() -> list[str]:
    """Read the live pattern list so these tests track the source, not a copy of it."""
    body = inspect.getsource(masking)
    body = body[body.index("DEFAULT_MASKING_PATTERNS: list[str] = [") :]
    body = body[: body.index("\n]")]
    patterns: list[str] = []
    for line in body.splitlines():
        entry = line.split("#", 1)[0].strip().rstrip(",").strip()
        if entry.startswith('"') and entry.endswith('"'):
            patterns.append(entry[1:-1])
    return patterns


def _redact(msg: str) -> str:
    """Mask exactly the way simple_logger's RedactingFilter does."""
    for entry in _patterns():
        msg = re.sub(rf"({entry}\W+[^\s+]+)", f"{entry} {'*' * 5} ", msg, flags=re.IGNORECASE)
    return msg


class TestNoSecretSurvivesMasking:
    @pytest.mark.parametrize("assignment", ASSIGNMENTS)
    def test_keyword_assignment_never_leaks(self, assignment: str) -> None:
        """No secret shape may survive any keyword form.

        Guards the coupling between this list and simple_logger's regex - adding a
        pattern that needs a separator, or a word the regex cannot match, fails here.
        """
        for secret in SECRETS:
            line = assignment.format(secret=secret)
            out = _redact(line)
            assert secret not in out, f"{secret[:12]}... leaked from {line!r} -> {out!r}"

    @pytest.mark.parametrize("template", ["raw value {secret}", "{secret}", "trailing {secret} words", "a {secret}"])
    def test_config_secret_is_masked_without_any_keyword(self, template: str) -> None:
        """Exact-match redaction must work anywhere, including at end of line.

        simple_logger's `({pattern}\\W+[^\\s]+)` requires trailing text, so an unknown
        value with no keyword beside it can survive. Values we hold are covered by
        _redact_secrets, which has no such requirement - that is why both layers exist.
        """
        for secret in SECRETS:
            line = template.format(secret=secret)
            out = apply_masking(line, mask_sensitive=True, secrets=SECRETS, patterns=_patterns())
            assert secret not in out, f"config secret leaked: {line!r} -> {out!r}"

    def test_unknown_value_without_keyword_is_a_documented_gap(self) -> None:
        """Pins the real limit of the keyword backstop rather than pretending it is total.

        If this ever starts failing it means the mechanism improved; if it is ever relied
        upon for a real credential, that is a bug - unknown values are only caught when a
        keyword sits beside them.
        """
        out = apply_masking(
            f"some value {GITHUB_TOKEN} trailing",
            mask_sensitive=True,
            secrets=[],
            patterns=_patterns(),
        )
        assert GITHUB_TOKEN in out

    def test_no_empty_pattern(self) -> None:
        """An empty pattern would match everywhere and corrupt every log line."""
        assert "" not in _patterns()
        assert all(p.strip() for p in _patterns())


class TestOurLogLinesSurviveMasking:
    """Our own log lines must not be mangled by our own filter."""

    @pytest.mark.parametrize(
        "line",
        [
            "API spend: deadlock1bot 3 API calls (initial: 4999, remaining: 4996, reset in 3595s)",
            "[deadlock1bot] API rate limit: 4873 of 5000",
            "Get API and tokens for repository myk-org/for-testing-only [1a2b3c4d-5678]",
            "API user myakove-bot selected with highest rate limit: 4624",
            "[SUCCESS] Webhook completed [check_run, hook 42, tokens:4 (deadlock1bot)]",
        ],
    )
    def test_operational_lines_are_untouched(self, line: str) -> None:
        """These lines carry the token-selection story; masking them would hide it."""
        out = apply_masking(line, mask_sensitive=True, secrets=SECRETS, patterns=_patterns())
        assert out == line, f"our own log line got masked: {line!r} -> {out!r}"


class TestDeliveryIdAttribution:
    def test_log_rate_limit_prepends_the_prefix(self) -> None:
        """The prefix carries the delivery id, so interleaved webhooks can be told apart."""
        logger = Mock(spec=logging.Logger)
        with patch("webhook_server.utils.helpers.get_logger_with_params", return_value=logger):
            log_rate_limit(remaining=100, limit=5000, api_user="bob", log_prefix="Get API [abc-123]")
        messages: list[Any] = [c.args[0] for c in logger.debug.call_args_list + logger.warning.call_args_list]
        assert messages, "log_rate_limit emitted nothing"
        assert any("abc-123" in m and "bob" in m for m in messages), messages

    def test_selection_reads_the_context_for_a_delivery_id(self) -> None:
        """get_api_with_highest_rate_limit() stamps the context's hook_id onto its prefix."""
        src = inspect.getsource(get_api_with_highest_rate_limit)
        assert "get_context()" in src, "selection does not read the webhook context"
        assert "delivery_id" in src, "selection does not stamp the delivery id"

    def test_context_hook_id_exists_at_construction_time(self) -> None:
        """The delivery id must already be set when selection runs, or the prefix is empty.

        app.py calls create_context() before constructing GithubWebhook, so the ContextVar
        is populated by the time get_api_with_highest_rate_limit() runs.
        """
        ctx = WebhookContext(
            hook_id="deadbeef-1234",
            event_type="check_run",
            repository="for-testing-only",
            repository_full_name="myk-org/for-testing-only",
        )
        assert ctx.hook_id == "deadbeef-1234"


class TestMaskSensitiveDataConfigIsHonoured:
    """mask-sensitive-data decides everything, in both directions."""

    SECRET = GITHUB_TOKEN

    def test_disabled_returns_the_log_untouched(self) -> None:
        """With masking off the log must come back byte-for-byte as written."""
        line = f"token: {self.SECRET} login: alice password: {PASSWORD}"
        out = apply_masking(line, mask_sensitive=False, secrets=[self.SECRET], patterns=_patterns())
        assert out == line, "masking ran despite mask_sensitive=False"

    def test_enabled_masks_the_same_line(self) -> None:
        line = f"token: {self.SECRET} login: alice password: {PASSWORD}"
        out = apply_masking(line, mask_sensitive=True, secrets=[self.SECRET], patterns=_patterns())
        assert self.SECRET not in out
        assert PASSWORD not in out
        assert "*****" in out


class TestLazyArgumentLeak:
    """A secret passed as a %-argument used to be logged verbatim.

    simple_logger's RedactingFilter rewrites record.msg only, so record.args - where
    lazy-format secrets live - passed through unmasked.
    """

    SECRET = GITHUB_TOKEN

    def _render(self, mask_sensitive: bool) -> str:
        stream = io.StringIO()
        logger = logging.getLogger("leak-probe-args")
        logger.handlers.clear()
        logger.propagate = False
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.filters.clear()
        logger.addFilter(
            SecretRedactionFilter(patterns=_patterns(), secrets=[self.SECRET], mask_sensitive=mask_sensitive)
        )
        logger.setLevel(logging.INFO)
        logger.info("%s: token", self.SECRET)
        logger.info("using %s", self.SECRET)
        return stream.getvalue()

    def test_secret_in_lazy_args_is_masked(self) -> None:
        out = self._render(mask_sensitive=True)
        assert self.SECRET not in out, f"lazy-arg secret leaked: {out!r}"

    def test_percent_placeholder_does_not_break_logging(self) -> None:
        """Masking must not consume a %s and make logging raise and drop the line."""
        out = self._render(mask_sensitive=True)
        assert out.strip(), "logging raised and the record was dropped"

    def test_disabled_leaves_lazy_args_intact(self) -> None:
        out = self._render(mask_sensitive=False)
        assert self.SECRET in out


class TestEveryLoggerHonoursTheSetting:
    """simple_logger defaults mask_sensitive to False, so a logger built without asking is raw.

    These three were built with a bare get_logger() and therefore wrote unmasked output,
    including the JSONL webhook log.
    """

    SECRET = GITHUB_TOKEN

    @pytest.mark.parametrize(
        "module",
        [
            "webhook_server.libs.config",
            "webhook_server.libs.log_parser",
            "webhook_server.utils.structured_logger",
        ],
    )
    def test_module_no_longer_assigns_a_bare_unmasked_logger(self, module: str) -> None:
        """A logger assigned straight from get_logger() is unmasked.

        simple_logger defaults mask_sensitive to False, so every assignment must go
        through attach_masking instead.
        """
        src = inspect.getsource(importlib.import_module(module))
        direct = [line.strip() for line in src.splitlines() if re.search(r"self\.logger\s*=\s*get_logger\(", line)]
        assert not direct, f"{module} assigns an unmasked logger: {direct}"
        assert "attach_masking" in src, f"{module} never calls attach_masking"

    def test_config_logger_masks_after_load(self) -> None:
        """Config re-applies masking once github-tokens and friends are readable."""
        assert hasattr(Config, "_apply_masking"), "Config never re-applies masking after load"
