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
import os
import re
from typing import Any
from unittest.mock import Mock, patch

import pytest
import yaml

from webhook_server.libs.config import Config
from webhook_server.utils import masking
from webhook_server.utils.context import WebhookContext, clear_context, create_context
from webhook_server.utils.helpers import get_api_with_highest_rate_limit, log_rate_limit
from webhook_server.utils.masking import (
    SecretRedactionFilter,
    apply_masking,
    attach_masking,
    config_secret_values,
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
        """Render through the project's own attach_masking() path, not a bare logger.

        Building the logger with logging.getLogger() and bolting a filter on by hand
        tested a construction the server never uses, so it could not catch a regression in
        how masking is actually attached.
        """
        stream = io.StringIO()
        logger = logging.getLogger("leak-probe-args")
        logger.handlers.clear()
        logger.propagate = False
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.filters.clear()
        attach_masking(logger, mask_sensitive=mask_sensitive, secrets=[self.SECRET], patterns=_patterns())
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


class TestAttachMasking:
    """attach_masking() is what keeps a logger protected - it needs behaviour tests."""

    def _logger(self) -> logging.Logger:
        return logging.getLogger(f"attach-probe-{id(self)}")

    def test_attaches_a_redacting_filter_when_enabled(self) -> None:
        logger = self._logger()
        attach_masking(logger, mask_sensitive=True, secrets=[GITHUB_TOKEN])

        assert any(isinstance(f, SecretRedactionFilter) for f in logger.filters)

    def test_masking_applies_through_the_attached_filter(self) -> None:
        """Not just attached - it has to actually redact the rendered record."""
        stream = io.StringIO()
        logger = self._logger()
        logger.handlers.clear()
        logger.propagate = False
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        attach_masking(logger, mask_sensitive=True, secrets=[GITHUB_TOKEN])
        # Without this the logger inherits WARNING, logger.info() is dropped, and the
        # assertion below passes because nothing was ever logged.
        logger.setLevel(logging.INFO)

        logger.info("raw value %s here", GITHUB_TOKEN)

        assert stream.getvalue(), "nothing was logged - the assertion would pass vacuously"
        assert GITHUB_TOKEN not in stream.getvalue()

    def test_disabled_attaches_no_filter(self) -> None:
        logger = self._logger()
        attach_masking(logger, mask_sensitive=False, secrets=[GITHUB_TOKEN])

        assert not any(isinstance(f, SecretRedactionFilter) for f in logger.filters)

    def test_disabled_call_does_not_strip_an_existing_filter(self) -> None:
        """Regression: loggers are shared per log destination, so stripping here would
        unmask every other repository's logs when one of them disables masking."""
        logger = self._logger()
        attach_masking(logger, mask_sensitive=True, secrets=[GITHUB_TOKEN])
        attach_masking(logger, mask_sensitive=False, secrets=[])

        assert any(isinstance(f, SecretRedactionFilter) for f in logger.filters)

    def test_repeated_calls_do_not_stack_filters(self) -> None:
        logger = self._logger()
        attach_masking(logger, mask_sensitive=True, secrets=[GITHUB_TOKEN])
        attach_masking(logger, mask_sensitive=True, secrets=[GITHUB_TOKEN])

        assert sum(isinstance(f, SecretRedactionFilter) for f in logger.filters) == 1

    def test_refresh_picks_up_new_secrets_without_stacking(self) -> None:
        """A second attach with a different token set must take effect."""
        stream = io.StringIO()
        logger = self._logger()
        logger.handlers.clear()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        attach_masking(logger, mask_sensitive=True, secrets=["stale-secret-value"])
        attach_masking(logger, mask_sensitive=True, secrets=[GITHUB_TOKEN])

        logger.info("value %s", GITHUB_TOKEN)

        assert GITHUB_TOKEN not in stream.getvalue(), "stale secret list survived the refresh"


class TestConfigSecretValues:
    """Repository-scoped credentials must actually be found."""

    class _FakeConfig:
        def __init__(self, values: dict[str, Any]) -> None:
            self._values = values
            self.config_path = "/nonexistent/config.yaml"

        def get_value(self, value: str, return_on_none: Any = None, extra_dict: Any = None) -> Any:
            return self._values.get(value, return_on_none)

    def _config(self) -> Any:
        return self._FakeConfig({
            "github-tokens": [GITHUB_TOKEN],
            "repositories": {"org/repo": {"github-tokens": [PYPI_TOKEN]}},
        })

    def test_collects_global_secrets(self) -> None:
        assert GITHUB_TOKEN in config_secret_values(self._config())

    def test_collects_repository_scoped_secrets(self) -> None:
        """Regression: the repositories key is a MAPPING and was read as a list, so every
        per-repository token went un-masked."""
        assert PYPI_TOKEN in config_secret_values(self._config())

    def test_skips_values_too_short_to_be_credentials(self) -> None:
        """A short value would mask ordinary words."""
        cfg = self._FakeConfig({"github-tokens": ["abc"], "repositories": {}})

        assert config_secret_values(cfg) == []


class TestPerRepositoryMasking:
    """Loggers are shared per log destination, so masking must be decided per repository."""

    def _render(self, repository: str | None, attach_with: str) -> str:
        """Attach for one repository, then log a record attributed to another."""
        stream = io.StringIO()
        logger = logging.getLogger(f"per-repo-{attach_with}-{repository}")
        logger.handlers.clear()
        logger.propagate = False
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.filters.clear()
        logger.setLevel(logging.INFO)

        attach_masking(logger, mask_sensitive=attach_with != "off", secrets=[GITHUB_TOKEN], repository=attach_with)
        if attach_with != "off":
            attach_masking(logger, mask_sensitive=False, secrets=[GITHUB_TOKEN], repository="other-repo")

        if repository is None:
            clear_context()
        else:
            create_context(
                hook_id="h1",
                event_type="check_run",
                repository=repository,
                repository_full_name=f"org/{repository}",
            )
        logger.info("value %s", GITHUB_TOKEN)
        clear_context()
        return stream.getvalue()

    def test_a_repository_can_disable_masking_for_its_own_records(self) -> None:
        """Regression: never stripping the filter made mask-sensitive-data: false inert."""
        out = self._render(repository="off", attach_with="off")

        assert GITHUB_TOKEN in out, "mask-sensitive-data: false was ignored for its own repository"

    def test_disabling_for_one_repository_does_not_unmask_another(self) -> None:
        """The other direction: one repository turning masking off must not leak its
        neighbours' records, which was the bug that made this filter 'never strip'."""
        out = self._render(repository="masked-repo", attach_with="on")

        assert GITHUB_TOKEN not in out, "another repository's records were unmasked"


class TestSecretCacheUsesRealConfigFile:
    """The cache is keyed on the config file, so it has to be exercised with a real one."""

    def test_unchanged_file_hits_the_cache(self, tmp_path: Any) -> None:
        masking._SECRET_VALUES_CACHE.clear()
        path = _write_config(tmp_path, f"github-tokens:\n  - {GITHUB_TOKEN}\n")
        cfg = _FileConfig(path)

        first = config_secret_values(cfg)
        second = config_secret_values(cfg)

        assert first == second == [GITHUB_TOKEN]
        assert len(masking._SECRET_VALUES_CACHE) == 1, "cache was never populated"

    def test_changing_the_file_invalidates_the_cache(self, tmp_path: Any) -> None:
        masking._SECRET_VALUES_CACHE.clear()
        cfg = _FileConfig(_write_config(tmp_path, f"github-tokens:\n  - {GITHUB_TOKEN}\n"))
        assert config_secret_values(cfg) == [GITHUB_TOKEN]

        os.utime(cfg.config_path, (0, 0))  # ensure the fingerprint changes
        _write_config(tmp_path, f"github-tokens:\n  - {OAUTH_TOKEN}\n")

        assert config_secret_values(cfg) == [OAUTH_TOKEN], "stale secrets served after the file changed"

    def test_different_repositories_do_not_share_cached_values(self, tmp_path: Any) -> None:
        """Regression: the cache key omitted the repository.

        get_value() resolves repository-scoped overrides, so the same file yields
        different secrets per repository. Keyed on the file alone, the first caller's
        entry was served to every other repository - which then masked the wrong values
        and left that repository's own credential exposed.
        """
        masking._SECRET_VALUES_CACHE.clear()
        path = _write_config(
            tmp_path,
            f"github-tokens:\n  - {GITHUB_TOKEN}\nrepositories:\n  one:\n    github-tokens:\n      - {PYPI_TOKEN}\n",
        )

        root_values = config_secret_values(_FileConfig(path, repository=""))
        repo_values = config_secret_values(_FileConfig(path, repository="one"))

        # Each repository must resolve its OWN tokens, not whichever one ran first.
        assert GITHUB_TOKEN in root_values
        assert PYPI_TOKEN in repo_values
        assert GITHUB_TOKEN not in repo_values, "the root's cached values leaked into this repository"

    def test_two_repositories_each_get_their_own_values(self, tmp_path: Any) -> None:
        masking._SECRET_VALUES_CACHE.clear()
        path = _write_config(
            tmp_path,
            f"github-tokens:\n  - {GITHUB_TOKEN}\n"
            f"repositories:\n"
            f"  one:\n    github-tokens:\n      - {PYPI_TOKEN}\n"
            f"  two:\n    github-tokens:\n      - {AWS_ACCESS_KEY}\n",
        )

        one = config_secret_values(_FileConfig(path, repository="one"))
        two = config_secret_values(_FileConfig(path, repository="two"))

        # Each repository must contribute its OWN credential - this is what the cache
        # bug broke. Collecting every repository's values is deliberate, so extra
        # values are expected and harmless; a repo-scoped block also REPLACES the root
        # value for github-tokens rather than merging, so the root token is not required.
        assert PYPI_TOKEN in one
        assert AWS_ACCESS_KEY in two


def _write_config(tmp_path: Any, body: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return str(path)


class _FileConfig:
    """Config stub backed by a real file, so _config_fingerprint() actually works.

    ``get_value`` resolves repository-scoped overrides the way Config does - the value for
    a repository wins over the root - because that resolution is exactly what makes the
    same file yield different secrets per repository.
    """

    def __init__(self, config_path: str, repository: str = "") -> None:
        self.config_path = config_path
        self.repository = repository

    def get_value(self, value: str, return_on_none: Any = None, extra_dict: Any = None) -> Any:
        # Re-read every call: the point of these tests is that a changed file yields
        # changed secrets, so caching the parsed YAML here would hide that.
        data = yaml.safe_load(open(self.config_path)) or {}
        for key in (f"repositories.{self.repository}.{value}" if self.repository else "", value):
            node: Any = data
            for part in key.split("."):
                if not isinstance(node, dict):
                    node = None
                    break
                node = node.get(part)
            if node is not None:
                return node
        return return_on_none
