# Configuration Reference

`github-webhook-server` reads its main configuration from `config.yaml` in the server data directory. In code, that directory defaults to `/home/podman/data`, so the default config path is `/home/podman/data/config.yaml`. Relative log file names are resolved under `<data_dir>/logs/`.

The checked-in example file shows the top-level shape:

```3:21:examples/config.yaml
log-level: INFO # Set global log level, change take effect immediately without server restart
log-file: webhook-server.log # Set global log file, change take effect immediately without server restart
mcp-log-file: mcp_server.log # Set global MCP log file, change take effect immediately without server restart
logs-server-log-file: logs_server.log # Set global Logs Server log file, change take effect immediately without server restart
mask-sensitive-data: true # Mask sensitive data in logs (default: true). Set to false for debugging (NOT recommended in production)

# Server configuration
disable-ssl-warnings: true # Disable SSL warnings (useful in production to reduce log noise from SSL certificate issues)

github-app-id: 123456 # GitHub app id
github-tokens:
  - <GITHIB TOKEN1>
  - <GITHIB TOKEN2>

webhook-ip: <HTTP://IP OR URL:PORT/webhook_server> # Full URL with path (e.g., https://your-domain.com/webhook_server or https://smee.io/your-channel)

docker: # Used to pull images from docker.io
  username: <username>
  password: <password>
```

Repository-specific settings live under `repositories`:

```139:183:examples/config.yaml
repositories:
  my-repository:
    name: my-org/my-repository
    log-level: DEBUG # Override global log-level for repository
    log-file: my-repository.log # Override global log-file for repository
    mask-sensitive-data: false # Override global setting - disable masking for debugging this specific repo (NOT recommended in production)
    slack-webhook-url: <Slack webhook url> # Send notification to slack on several operations
    verified-job: true
    pypi:
      token: <PYPI TOKEN>

    events: # To listen to all events do not send events
      - push
      - pull_request
      - pull_request_review
      - pull_request_review_thread
      - issue_comment
      - check_run
      - status
    tox:
      main: all # Run all tests in tox.ini when pull request parent branch is main
      dev: testenv1,testenv2 # Run testenv1 and testenv2 tests in tox.ini when pull request parent branch is dev

    pre-commit: true # Run pre-commit check

    protected-branches:
      dev: []
      main: # set [] in order to set all defaults run included
        include-runs:
          - "pre-commit.ci - pr"
          - "WIP"
        exclude-runs:
          - "SonarCloud Code Analysis"
    container:
      username: <registry username>
      password: <registry_password>
      repository: <registry_repository_full_path>
      tag: <image_tag>
      release: true # Push image to registry on new release with release as the tag
      build-args: # build args to send to podman build command
        - my-build-arg1=1
        - my-build-arg2=2
      args: # args to send to podman build command
        - --format docker
```

> **Note:** In `repositories`, the map key is the short GitHub repository name, while `name` inside the block is the full `owner/repo`.

> **Note:** This page lists keys in `config.yaml` form. The sample `.github-webhook-server.yaml` uses the same repository-level shape without the surrounding `repositories.<repo>` wrapper.

> **Note:** Most repository settings replace the global value entirely. Two important exceptions are `branch-protection`, which is merged with global defaults, and `labels.colors`, where repository colors override only the keys you redefine.

> **Warning:** Use exact branch names for `tox` and `protected-branches`, and use string values such as `all` or `testenv1,testenv2` for `tox`. The current runner/setup code looks up branches by exact key and builds the tox command from a string value.

## Global settings

### Logging and diagnostics

- `log-level`: Global application log level. Allowed values are `INFO` and `DEBUG`.
- `log-file`: Main webhook server log file. Relative names are written under `<data_dir>/logs/`; absolute paths are used as-is.
- `mcp-log-file`: Separate log file for the optional MCP server. Default is `mcp_server.log`.
- `logs-server-log-file`: Separate log file for the optional log viewer / logs server. Default is `logs_server.log`.
- `mask-sensitive-data`: Enables log redaction. Default is `true`. When enabled, the logger masks secrets such as tokens, passwords, webhook secrets, Slack webhook URLs, and similar values.

> **Warning:** `labels.colors` and `pr-size-thresholds.*.color` expect CSS3 color names such as `green`, `orange`, `royalblue`, and `darkred`. The label code converts those names to hex internally; hex strings are not the documented input format.

### Server, webhook, and security

- `webhook-ip`: The public webhook URL that GitHub should call. Include the full path, for example `https://example.com/webhook_server`.
- `ip-bind`: The bind address for the FastAPI / uvicorn server. If omitted, startup defaults to `0.0.0.0`.
- `port`: The listening port. If omitted, startup defaults to `5000`.
- `max-workers`: Uvicorn worker count. If omitted, startup defaults to `10`.
- `webhook-secret`: Optional shared secret for GitHub webhook signature verification. When set, the server validates the incoming `x-hub-signature-256` header and uses the same secret when it creates GitHub webhooks.
- `verify-github-ips`: If `true`, only accept webhook requests from GitHub’s published webhook IP ranges.
- `verify-cloudflare-ips`: If `true`, also trust Cloudflare’s published IP ranges. This is useful when traffic reaches the server through Cloudflare.
- `disable-ssl-warnings`: If `true`, suppress `urllib3` SSL warnings during runtime.

> **Warning:** IP allowlist verification is fail-closed. If `verify-github-ips` and/or `verify-cloudflare-ips` are enabled but the allowlists cannot be loaded, the server aborts startup instead of accepting requests insecurely.

### GitHub authentication and shared defaults

- `github-app-id`: GitHub App ID used for app-scoped repository management. In practice this goes with a `webhook-server.private-key.pem` file in the data directory and an installed GitHub App.
- `github-tokens`: List of GitHub tokens used for normal API calls. The server checks all configured tokens and picks the one with the highest remaining rate limit.
- `docker.username`: Docker Hub username used for the startup `podman login` step.
- `docker.password`: Docker Hub password used for the startup `podman login` step.
- `default-status-checks`: Extra check or status context names that should always be part of the generated branch-protection rules. Use exact GitHub context names.
- `auto-verified-and-merged-users`: Global default list of users or bots whose PRs can be auto-verified and auto-merged when the other merge rules are satisfied.
- `auto-verify-cherry-picked-prs`: Global default for automatic verification of cherry-picked PRs. Default is `true`.
- `create-issue-for-new-pr`: Global default for creating a tracking issue when a new PR opens. Default is `true`.
- `cherry-pick-assign-to-pr-author`: Global default for assigning cherry-pick PRs to the original PR author. Default is `true`.
- `allow-commands-on-draft-prs`: Global default for user commands on draft PRs. Omit it to block commands on draft PRs. Set it to `[]` to allow all commands. Set it to a list such as `["build-and-push-container", "retest"]` to allow only those command names.

> **Tip:** Repository-level `github-tokens` replace the global token list for that repository. During webhook processing, the server also adds the GitHub users behind the active API tokens to the auto-verified user list.

### Labels and PR size

The sample config includes label and size settings like this:

```47:102:examples/config.yaml
labels:
  # Optional: List of label categories to enable
  # If not set, all labels are enabled. If set, only listed categories are enabled.
  # Note: reviewed-by labels (approved-*, lgtm-*, etc.) are always enabled and cannot be disabled
  enabled-labels:
    - verified
    - hold
    - wip
    - needs-rebase
    - has-conflicts
    - can-be-merged
    - size
    - branch
    - cherry-pick
    - automerge
  # Optional: Custom colors for labels (CSS3 color names)
  colors:
    hold: red
    verified: green
    wip: orange
    needs-rebase: darkred
    has-conflicts: red
    can-be-merged: limegreen
    automerge: green
    # Dynamic label prefixes
    approved-: green
    lgtm-: yellowgreen
    changes-requested-: orange
    commented-: gold
    cherry-pick-: coral
    branch-: royalblue

# Global PR size label configuration (optional)
# Define custom categories based on total lines changed (additions + deletions)
# threshold: positive integer or 'inf' for unbounded largest category
# color: CSS3 color name (e.g., red, green, blue, lightgray, darkorange)
# Infinity behavior: 'inf' ensures all PRs beyond largest finite threshold are captured
#                   Always sorted last, regardless of definition order
pr-size-thresholds:
  Tiny:
    threshold: 10 # PRs with 0-9 lines changed
    color: lightgray
  Small:
    threshold: 50 # PRs with 10-49 lines changed
    color: green
  Medium:
    threshold: 150 # PRs with 50-149 lines changed
    color: orange
  Large:
    threshold: 300 # PRs with 150-299 lines changed
    color: red
  Massive:
    threshold: inf # PRs with 300+ lines changed (unbounded largest category)
    color: darkred # 'inf' means no upper limit - catches all PRs above 300 lines
```

- `labels.enabled-labels`: List of label categories to allow. Valid categories are `verified`, `hold`, `wip`, `needs-rebase`, `has-conflicts`, `can-be-merged`, `size`, `branch`, `cherry-pick`, and `automerge`. If omitted, all configurable categories are enabled. If set to `[]`, all configurable categories are disabled. Review-state labels such as `approved-*`, `lgtm-*`, `changes-requested-*`, and `commented-*` are always enabled.
- `labels.colors`: Map of label names or dynamic label prefixes to CSS3 color names. Exact keys such as `hold` or `verified` affect one label. Prefix keys such as `approved-` or `branch-` affect any label that starts with that prefix.
- `pr-size-thresholds.<label>.threshold`: Threshold used to compute the PR size label. The handler sorts thresholds ascending and picks the first bucket where `total_changes < threshold`. Use `inf` for the open-ended largest bucket.
- `pr-size-thresholds.<label>.color`: CSS3 color name for that bucket.

If you do not configure `pr-size-thresholds`, the built-in buckets are:

```34:41:webhook_server/libs/handlers/labels_handler.py
STATIC_PR_SIZE_THRESHOLDS: tuple[tuple[int | float, str, str], ...] = (
    (20, "XS", "ededed"),
    (50, "S", "0E8A16"),
    (100, "M", "F09C74"),
    (300, "L", "F5621C"),
    (500, "XL", "D93F0B"),
    (float("inf"), "XXL", "B60205"),
)
```

### Branch protection

- `branch-protection.strict`: GitHub branch protection `strict` setting. Global default is `true`.
- `branch-protection.require_code_owner_reviews`: Require CODEOWNERS reviews. Global default is `false`.
- `branch-protection.dismiss_stale_reviews`: Dismiss stale reviews on new commits. Global default is `true`.
- `branch-protection.required_approving_review_count`: Required approval count. Global default is `0`.
- `branch-protection.required_linear_history`: Require linear history. Global default is `true`.
- `branch-protection.required_conversation_resolution`: Require resolved review conversations. Global default is `true`. The webhook processor also uses this flag when deciding whether resolved/unresolved review-thread events should affect mergeability.

### PR Test Oracle and AI features

The sample global config includes both `test-oracle` and `ai-features`:

```104:137:examples/config.yaml
branch-protection:
  strict: True
  require_code_owner_reviews: True
  dismiss_stale_reviews: False
  required_approving_review_count: 1
  required_linear_history: True
  required_conversation_resolution: True

# PR Test Oracle integration
# Analyzes PR diffs with AI and recommends which tests to run
# See: https://github.com/myk-org/pr-test-oracle
test-oracle:
  server-url: "http://localhost:8000"
  ai-provider: "claude" # claude | gemini | cursor
  ai-model: "claude-opus-4-6[1m]"
  test-patterns:
    - "tests/**/*.py"
  triggers: # Default: [approved]
    - approved # Run when /approve command is used
    # - pr-opened             # Run when PR is opened
    # - pr-synchronized       # Run when new commits pushed

# AI Features configuration
# Enables AI-powered enhancements (e.g., conventional title suggestions)
ai-features:
  ai-provider: "claude" # claude | gemini | cursor
  ai-model: "claude-opus-4-6[1m]"
  conventional-title:
    enabled: true
    mode: suggest  # suggest: show in checkrun | fix: auto-update PR title
    timeout-minutes: 10
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
    timeout-minutes: 10  # Timeout in minutes for AI CLI (default: 10)
```

- `test-oracle.server-url`: Base URL of the PR Test Oracle service.
- `test-oracle.ai-provider`: AI provider sent to the oracle. Allowed values are `claude`, `gemini`, and `cursor`.
- `test-oracle.ai-model`: AI model sent to the oracle.
- `test-oracle.test-patterns`: Optional test-file globs sent to the oracle as `test_patterns`.
- `test-oracle.triggers`: Automatic oracle triggers. Valid values are `approved`, `pr-opened`, and `pr-synchronized`. Default is `["approved"]`. Here, `approved` means the `/approve` comment command, not GitHub’s native review approval event.
- `ai-features.ai-provider`: AI CLI provider for built-in AI features.
- `ai-features.ai-model`: AI CLI model for built-in AI features.
- `ai-features.conventional-title.enabled`: Turn AI assistance for failed `conventional-title` checks on or off.
- `ai-features.conventional-title.mode`: `suggest` appends an AI-suggested PR title to the failed check output. `fix` auto-edits the PR title when the suggestion validates.
- `ai-features.conventional-title.timeout-minutes`: Timeout for the title-suggestion AI CLI call. Default is `10`.
- `ai-features.resolve-cherry-pick-conflicts-with-ai.enabled`: Allow AI conflict resolution during cherry-pick workflows.
- `ai-features.resolve-cherry-pick-conflicts-with-ai.timeout-minutes`: Timeout for the cherry-pick conflict resolution AI CLI call. Default is `10`.

> **Note:** `/test-oracle` always works when `test-oracle` is configured, even if the current event is not listed in `triggers`.

> **Note:** If the Test Oracle health check fails, the server posts a PR comment and skips analysis. If the AI CLI fails for `ai-features`, the server logs the problem and continues without blocking the rest of the webhook flow.

> **Warning:** AI-resolved cherry-picks are never auto-verified. They always require manual review after the conflict resolution step.

### Required top-level map

- `repositories`: Required top-level map of repository-specific settings. The config loader refuses a config file that does not contain `repositories`.

## Repository settings

The following keys are written under `repositories.<short-repo-name>` in `config.yaml`.

### Identity, logging, and webhook subscription

- `name`: Full repository name in `owner/repo` form. This is required.
- `log-level`: Repository-specific log level override.
- `log-file`: Repository-specific log file override.
- `mask-sensitive-data`: Repository-specific redaction override.
- `events`: GitHub webhook events to subscribe when the server creates or updates the repo webhook. If omitted, webhook registration uses `*`. The current processing path explicitly handles `push`, `pull_request`, `pull_request_review`, `issue_comment`, `check_run`, `status`, `pull_request_review_thread`, and `ping`.
- `github-tokens`: Repository-specific token list. This replaces the global `github-tokens` list for that repository.
- `slack-webhook-url`: Slack incoming webhook for notifications related to PyPI publishing and container pushes.

### Checks and CI behavior

- `verified-job`: Enable or disable the built-in `verified` check. Default is `true`. When disabled, the repo setup code does not add `verified` to generated required checks, and merge requirements do not include the verified step.
- `tox`: Map of exact base branch name to tox env selection. Use `all` to run the default tox config, or a comma-separated env list such as `testenv1,testenv2`.
- `tox-python-version`: Optional Python version passed as `uvx --python=<version> tox ...`.
- `pre-commit`: Enable or disable the built-in pre-commit check. Default is `true`.
- `conventional-title`: Comma-separated list of allowed Conventional Commit types for PR titles, such as `feat,fix,docs`. Use `*` to accept any type while still enforcing the format `<type>[optional scope]: <description>`. This validates the PR title, not commit messages.
- `custom-check-runs[].name`: Custom GitHub check-run name. It must be unique, use only safe characters, and not collide with built-in names such as `tox`, `pre-commit`, `build-container`, `python-module-install`, `conventional-title`, or `can-be-merged`.
- `custom-check-runs[].command`: Command run in the repository worktree. Environment-variable prefixes and multiline commands are supported, but the executable must exist on the server.
- `custom-check-runs[].mandatory`: Whether the custom check must pass for mergeability. Default is `true`. `false` checks still run; they just stop gating merges.
- `test-oracle.server-url`, `test-oracle.ai-provider`, `test-oracle.ai-model`, `test-oracle.test-patterns`, `test-oracle.triggers`: Same meanings as the global `test-oracle` keys. A repository-level object replaces the global object for that repository.
- `ai-features.ai-provider`, `ai-features.ai-model`, `ai-features.conventional-title.enabled`, `ai-features.conventional-title.mode`, `ai-features.conventional-title.timeout-minutes`, `ai-features.resolve-cherry-pick-conflicts-with-ai.enabled`, `ai-features.resolve-cherry-pick-conflicts-with-ai.timeout-minutes`: Same meanings as the global `ai-features` keys. A repository-level object replaces the global object for that repository.

> **Tip:** Custom check names become valid `/retest <name>` targets, and `/retest all` includes them.

### Branch protection and merge policy

- `protected-branches`: Map of exact branch names that should have branch protection configured by the startup repository-setup job.
- `protected-branches.<branch>: []`: Protect the branch and use the auto-generated required status-check list.
- `protected-branches.<branch>.include-runs`: Replace the auto-generated required status-check list with exactly these contexts.
- `protected-branches.<branch>.exclude-runs`: Remove these contexts from the auto-generated required list.
- `branch-protection.strict`, `branch-protection.require_code_owner_reviews`, `branch-protection.dismiss_stale_reviews`, `branch-protection.required_approving_review_count`, `branch-protection.required_linear_history`, `branch-protection.required_conversation_resolution`: Same meanings as the global branch-protection keys. Repository values are merged on top of the global defaults.

When the server auto-generates required checks for a protected branch, it starts from `default-status-checks`, always adds `can-be-merged`, and then adds built-in checks that are enabled for the repo. That includes `tox`, `verified`, `build-container`, `python-module-install`, `pre-commit`, `conventional-title`, and `pre-commit.ci - pr` when the repo contains a `.pre-commit-config.yaml`.

- `auto-verified-and-merged-users`: Repository-specific auto-verified user list. This replaces the global list for that repository.
- `auto-verify-cherry-picked-prs`: Repository override for automatic verification of cherry-picked PRs. Default is `true`.
- `set-auto-merge-prs`: List of base branch names that should be put into GitHub auto-merge. The server enables GitHub auto-merge with squash merge.
- `can-be-merged-required-labels`: Extra labels that must be present before the `can-be-merged` check can pass.
- `minimum-lgtm`: Minimum number of reviewer `/lgtm` labels required before the PR is considered mergeable. Default is `0`.
- `create-issue-for-new-pr`: Repository override for tracking-issue creation on new PRs.
- `cherry-pick-assign-to-pr-author`: Repository override for assigning cherry-pick PRs to the original PR author.
- `allow-commands-on-draft-prs`: Repository override for draft-PR comment commands. Omit it to block commands on draft PRs. Set it to `[]` to allow all commands. Set it to a list to allow only those raw command names, for example `build-and-push-container` or `retest`.

> **Warning:** For `protected-branches`, stick to `[]` or the object form with `include-runs` / `exclude-runs`. Those are the forms the branch-protection setup code actually uses.

> **Note:** `/test-oracle` is exempt from the normal draft-PR command restriction and can still be triggered when configured.

### Labels and PR size at repo level

- `labels.enabled-labels`: Repository-specific enabled-label list. If you set it, it replaces the global enabled-label list for that repository.
- `labels.colors`: Repository-specific color overrides. Repo color keys override the same keys from the global `labels.colors` map and inherit the rest.
- `pr-size-thresholds.<label>.threshold`: Repository-specific PR size threshold.
- `pr-size-thresholds.<label>.color`: Repository-specific PR size color. Repository `pr-size-thresholds` replace the global threshold map for that repository.

### Publishing, releases, and notifications

- `pypi.token`: Enable Python package publishing on tag pushes. On a tag push, the server builds an sdist with `uv build`, runs `twine check`, and uploads with `twine upload --skip-existing`. If publishing fails, the handler opens a GitHub issue.
- `container.username`: Registry username used for `podman push`.
- `container.password`: Registry password used for `podman push`.
- `container.repository`: Full image repository, for example `quay.io/org/repo` or `ghcr.io/org/pkg`.
- `container.tag`: Default merged-PR image tag for `main` and `master`. If omitted, the build code defaults to `latest`. PR builds use `pr-<number>`, and tag pushes use the Git tag itself.
- `container.release`: If `true`, a tag push builds and pushes a release image. If `false`, tag pushes do not push a release image.
- `container.build-args`: Extra `--build-arg` values passed to `podman build`.
- `container.args`: Raw extra arguments prepended to `podman build`, for example `--format docker` or `--platform=linux/amd64`.

The checked-in source handles release publishing only on tag pushes:

```34:52:webhook_server/libs/handlers/push_handler.py
tag = re.search(r"^refs/tags/(.+)$", self.hook_data["ref"])
if tag:
    tag_name = tag.group(1)
    self.logger.info(f"{self.log_prefix} Processing push for tag: {tag.group(1)}")
    self.logger.debug(f"{self.log_prefix} Tag: {tag_name}")
    if self.github_webhook.pypi:
        self.logger.info(f"{self.log_prefix} Processing upload to pypi for tag: {tag_name}")
        try:
            await self.upload_to_pypi(tag_name=tag_name)
        except Exception as ex:
            self.logger.exception(f"{self.log_prefix} PyPI upload failed")
            if self.ctx:
                self.ctx.fail_step("push_handler", ex, traceback.format_exc())
            return

    if self.github_webhook.build_and_push_container and self.github_webhook.container_release:
        self.logger.info(f"{self.log_prefix} Processing build and push container for tag: {tag_name}")
        try:
            await self.runner_handler.run_build_container(push=True, set_check=False, tag=tag_name)
```

> **Note:** `slack-webhook-url` is used for successful PyPI publish messages and for container push success/failure notifications. It is not a general-purpose notification switch for every webhook event.
