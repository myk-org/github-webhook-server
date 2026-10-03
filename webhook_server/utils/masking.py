"""Log masking, in one dependency-free place.

Every logger in the server - main webhook logs, the JSONL webhook log, config and
structured loggers alike - must honour the ``mask-sensitive-data`` setting. ``simple_logger``
defaults ``mask_sensitive`` to **False**, so a logger built without it is unmasked, and
its keyword filter only ever sees ``record.msg``: a secret passed as a lazy ``%``-argument
is written verbatim. Keeping the logic here means every call site gets the same behaviour
instead of each remembering to ask.

This module deliberately imports nothing from the project, so any module can use it without
an import cycle. Config values are passed in by the caller.

Two layers, because neither alone is sufficient:

* **exact match** on secret values the process holds - precise, no false positives, works
  anywhere in the line;
* **keyword patterns** - the backstop for secrets this process never held, such as a
  rotated token, an environment value, or a library echoing a request header.

The keyword layer is deliberately broad, so ordinary prose after a word like ``token`` or
``login`` can render as ``*****``. That is cosmetic; the server's own operational log lines
avoid those words and stay readable.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

# Keywords that introduce a secret in a log line. simple_logger composes these as
# ({pattern}\W+[^\s]+) - the pattern is a regex fragment and a single separator is enough.
# Kept broad on purpose: an over-eager mask hides a login name, an under-eager one prints
# a credential. push_handler logs a live PyPI credential as "--password <token>", so these
# must stay space-tolerant.
DEFAULT_MASKING_PATTERNS: list[str] = [
    # Passwords and secrets
    "--password",
    "password",
    "secret",
    # Tokens and API keys
    "token",
    "apikey",
    "api_key",
    "github_token",
    "GITHUB_TOKEN",
    "pypi",
    # Authentication credentials
    "login",
    "-u",
    "-p",
    "--username",
    "--creds",
    # Private keys and sensitive IDs
    "private_key",
    "private-key",
    "webhook_secret",
    "webhook-secret",
    "github-app-id",
    "username",
    # Slack webhooks (contain sensitive URLs)
    "slack-webhook-url",
    "slack_webhook_url",
    "webhook-url",
    "webhook_url",
]

# Values shorter than this would mask ordinary words rather than credentials.
_MIN_SECRET_LENGTH = 8


def apply_masking(
    text: str,
    mask_sensitive: bool,
    secrets: list[str] | None = None,
    patterns: list[str] | None = None,
) -> str:
    """Return ``text`` masked if masking is on, or exactly as written if it is off.

    The whole contract is here. With ``mask_sensitive=False`` the text is returned
    byte-for-byte unchanged - a repository that disables masking really does get raw logs.
    """
    if not mask_sensitive:
        return text

    redacted = _redact_known_secrets(text, secrets)
    for pattern in patterns if patterns is not None else DEFAULT_MASKING_PATTERNS:
        # [^\s]+ not [^\s+]+: excluding '+' stopped the match at a plus sign and left the
        # rest of a password in the clear. Masking further than necessary is the safe side.
        redacted = re.sub(rf"({pattern}\W+[^\s]+)", f"{pattern} {'*' * 5} ", redacted, flags=re.IGNORECASE)
    return redacted


def _redact_known_secrets(text: str, secrets: list[str] | None) -> str:
    """Replace every occurrence of a known secret value.

    Sorted longest-first so a secret that is a prefix of another cannot leak its tail.
    """
    if not secrets:
        return text
    escaped = sorted(
        {re.escape(secret) for secret in secrets if isinstance(secret, str) and secret.strip()},
        key=len,
        reverse=True,
    )
    if not escaped:
        return text
    return re.sub(f"(?:{'|'.join(escaped)})", "***REDACTED***", text)


class SecretRedactionFilter(logging.Filter):
    """Mask the fully formatted record, not just ``record.msg``.

    Formatting first is what makes lazy ``%``-arguments safe: ``record.getMessage()``
    produces the text that will actually be written, both mask layers then see it, and
    clearing ``args`` stops the formatter re-interpolating the untouched originals.
    """

    def __init__(
        self,
        patterns: list[str] | None = None,
        secrets: list[str] | None = None,
        mask_sensitive: bool = True,
    ) -> None:
        super().__init__()
        self._patterns = DEFAULT_MASKING_PATTERNS if patterns is None else patterns
        self._secrets = [secret for secret in (secrets or []) if isinstance(secret, str) and secret]
        self._mask_sensitive = mask_sensitive

    def refresh(self, secrets: list[str] | None, patterns: list[str] | None = None) -> None:
        """Update in place instead of stacking another filter on the same logger."""
        self._secrets = [secret for secret in (secrets or []) if isinstance(secret, str) and secret]
        if patterns is not None:
            self._patterns = patterns

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._mask_sensitive:
            return True
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed format string; fall back to the raw msg
            message = str(record.msg)
        record.msg = apply_masking(message, mask_sensitive=True, secrets=self._secrets, patterns=self._patterns)
        record.args = ()
        return True


def attach_masking(
    logger: logging.Logger,
    mask_sensitive: bool = True,
    secrets: list[str] | None = None,
    patterns: list[str] | None = None,
) -> logging.Logger:
    """Attach this module's filter to ``logger``, replacing any previous one.

    Idempotent: repeated calls replace rather than stack, so a logger built per call site
    does not accumulate filters.
    """
    # A logger is cached per log destination and shared by every repository, so a filter
    # already installed here is protecting other repositories too. Stripping it when one
    # repository disables masking would unmask all of them, so this only ever adds.
    # Repeated calls do not stack: an existing SecretRedactionFilter is refreshed in place.
    filters = getattr(logger, "filters", None)
    if isinstance(filters, list):
        for existing in filters:
            if isinstance(existing, SecretRedactionFilter):
                existing.refresh(secrets=secrets, patterns=patterns)
                return logger
    if mask_sensitive and hasattr(logger, "addFilter"):
        logger.addFilter(SecretRedactionFilter(patterns=patterns, secrets=secrets, mask_sensitive=True))
    return logger


def config_secret_values(config: Any) -> list[str]:
    """Collect secret values from a Config so they can be matched exactly.

    Best-effort by design: this gives precision for values we do know, and the keyword
    patterns cover anything missing. Values too short to be a credential are skipped so
    they cannot mask ordinary words.

    Cached per config file (path + mtime + size) because a logger is built on hot paths -
    re-reading and re-parsing the YAML for every log line setup is not free.
    """
    fingerprint = _config_fingerprint(config)
    cache_key = None
    if fingerprint is not None:
        cache_key = (config.config_path, fingerprint)
        cached = _SECRET_VALUES_CACHE.get(cache_key)
        if cached is not None:
            return cached

    values: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            values.append(value)

    def harvest(raw: Any) -> None:
        if isinstance(raw, list):
            for item in raw:
                add(item)
        else:
            add(raw)

    for key in ("github-tokens", "webhook-secret", "slack-webhook-url", "pypi.token", "docker.password"):
        try:
            harvest(config.get_value(value=key))
        except Exception:  # pragma: no cover - a missing or malformed key must not break logging
            continue

    # Per-repository credentials live under the "repositories" MAPPING (name -> config),
    # not a list. Reading a key that does not exist returned None and silently collected
    # nothing, which is how every repository-scoped token went un-masked.
    try:
        repositories = config.get_value(value="repositories")
    except Exception:  # pragma: no cover
        repositories = None

    for repo in repositories.values() if isinstance(repositories, dict) else []:
        if not isinstance(repo, dict):
            continue
        for key in ("github-tokens", "pypi.token", "docker.password"):
            harvest(repo.get(key))

    result = [value for value in values if len(value) >= _MIN_SECRET_LENGTH]
    if cache_key is not None:
        _SECRET_VALUES_CACHE[cache_key] = result
    return result


def _config_fingerprint(config: Any) -> tuple[int, int] | None:
    """Cheap identity for a config file so the cache invalidates when it changes.

    None when the file cannot be stat'd, and the caller then skips caching entirely: a key
    with no fingerprint cannot be invalidated, so it would serve stale secrets forever.
    """
    try:
        stat = os.stat(config.config_path)
    except (AttributeError, OSError, TypeError):
        return None
    return (stat.st_mtime_ns, stat.st_size)


_SECRET_VALUES_CACHE: dict[Any, list[str]] = {}
