# Configuration Model

`github-webhook-server` has three potential configuration layers:

1. The root of the server's `config.yaml`
2. The matching `repositories.<repo>` entry inside `config.yaml`
3. An optional `.github-webhook-server.yaml` in the repository itself

Not every setting participates in all three layers, but when a repository-scoped setting does, the server resolves it from most specific to least specific: repository-local file first, then the repo entry in `config.yaml`, then the root of `config.yaml`.

```132:153:webhook_server/libs/config.py
def get_value(self, value: str, return_on_none: Any = None, extra_dict: dict[str, Any] | None = None) -> Any:
    """
    Get value from config

    Supports dot notation for nested values (e.g., "docker.username", "pypi.token")

    Order of getting value:
        1. Local repository file (.github-webhook-server.yaml)
        2. Repository level global config file (config.yaml)
        3. Root level global config file (config.yaml)
    """
    if extra_dict:
        result = self._get_nested_value(value, extra_dict)
        if result is not None:
            return result

    for scope in (self.repository_data, self.root_data):
        result = self._get_nested_value(value, scope)
        if result is not None:
            return result

    return return_on_none
```

Think of the model like this: root `config.yaml` provides shared defaults, `repositories.<repo>` provides server-side exceptions for one repository, and `.github-webhook-server.yaml` lets a repository carry some of its own runtime behavior in version control.

## Where `config.yaml` Lives

By default, the server reads `config.yaml` from `/home/podman/data/config.yaml`. Set `WEBHOOK_SERVER_DATA_DIR` if you want a different base directory. The Docker example mounts `./webhook_server_data_dir` into `/home/podman/data`, which is why that path is the default.

> **Warning:** `config.yaml` is required, and `repositories:` must exist and be non-empty. Missing file or missing `repositories:` is a hard error.

## Server-Managed `config.yaml`

Both the global defaults and the per-repository overrides live in the same file. Root keys apply to every repository unless a repo-specific entry overrides them.

```3:190:examples/config.yaml
log-level: INFO # Set global log level, change take effect immediately without server restart
log-file: webhook-server.log # Set global log file, change take effect immediately without server restart

github-app-id: 123456 # GitHub app id
github-tokens:
  - <GITHIB TOKEN1>
  - <GITHIB TOKEN2>

webhook-ip: <HTTP://IP OR URL:PORT/webhook_server>

# ...

repositories:
  my-repository:
    name: my-org/my-repository
    log-level: DEBUG # Override global log-level for repository
    log-file: my-repository.log # Override global log-file for repository
    events:
      - push
      - pull_request
      - pull_request_review
      - pull_request_review_thread
      - issue_comment
      - check_run
      - status

    # ...

    github-tokens: # override GitHub tokens per repository
      - <GITHUB TOKEN1>
      - <GITHUB TOKEN2>
```

Use the root of `config.yaml` for shared or server-level values such as `github-app-id`, global `github-tokens`, `webhook-ip`, global `labels`, and other defaults you want every repository to inherit.

Use `repositories.<repo>` for repo-specific settings that the server must know before it starts processing that repository. Common examples are `name`, `events`, and repo-specific `github-tokens`.

> **Note:** The key under `repositories:` is the short repository name, while `name:` stores the full `owner/repo`. In the example above, `my-repository` is the lookup key and `my-org/my-repository` is the actual GitHub repository. Because lookup is by short name, avoid configuring two different repos that share the same short name.

## Repository-Managed `.github-webhook-server.yaml`

Use `.github-webhook-server.yaml` when you want repository-owned behavior to live with the code and be reviewed in pull requests. This is a good fit for runtime settings such as `tox`, `pypi`, `container`, `pre-commit`, `conventional-title`, `ai-features`, `minimum-lgtm`, `create-issue-for-new-pr`, and label-related behavior.

```118:162:examples/.github-webhook-server.yaml
conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"

minimum-lgtm: 2

create-issue-for-new-pr: true # Create tracking issues for new PRs
cherry-pick-assign-to-pr-author: true # Assign cherry-pick PRs to the original PR author

# ...

ai-features:
  ai-provider: "claude" # claude | gemini | cursor
  ai-model: "claude-opus-4-6[1m]"
  conventional-title:
    enabled: true
    mode: suggest
    timeout-minutes: 10
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
    timeout-minutes: 10
```

If the file is missing, the server simply falls back to `config.yaml`. If the file exists but contains invalid YAML, loading it fails instead of being silently ignored.

The local file is not applied first thing at startup. The webhook runtime loads base config, selects the API token, and only then fetches `.github-webhook-server.yaml` and reapplies the supported repository settings.

```114:151:webhook_server/libs/github_api.py
# Get config without .github-webhook-server.yaml data
self._repo_data_from_config(repository_config={})
github_api, self.token, self.api_user = get_api_with_highest_rate_limit(
    config=self.config, repository_name=self.repository_name
)

# ...

# Once we have a repository, we can get the config from .github-webhook-server.yaml
local_repository_config = self.config.repository_local_data(
    github_api=github_api, repository_full_name=self.repository_full_name
)
# Call _repo_data_from_config() again to update self args from .github-webhook-server.yaml
self._repo_data_from_config(repository_config=local_repository_config)
```

> **Warning:** `.github-webhook-server.yaml` is best thought of as a runtime-behavior layer, not a full replacement for `config.yaml`. Keep administrative settings such as `events`, repo tokens, logging, branch protection, draft-command rules, `pr-size-thresholds`, and `test-oracle` in `config.yaml`.

> **Note:** The repository-local file is fetched through GitHub's contents API without an explicit `ref`, so the default-branch version is the one the server sees. A config change in a pull request does not become active until that file reaches the default branch.

## Merge Rules

The precedence chain is key-by-key, not file-by-file. In practice, that means:

- If a key is missing at the repository-local level, lookup continues to the repo entry in `config.yaml`, then to the root.
- If a higher-precedence key is present but set to YAML `null`, the server treats it as not set and keeps falling back.
- For most nested objects, the higher-precedence object replaces the lower-precedence object instead of being recursively merged.
- `labels` is the main special case: the server merges the top-level `labels` object, and then merges `labels.colors` again so you can override a few colors without redefining every color.

A concrete example is in `examples/config.yaml`: the root `labels.colors.hold` is `red`, while the repo-specific `labels.colors.hold` is `purple`. For that repository, the effective `hold` color becomes `purple`, but the other global label colors still apply. The same merge behavior is used when `labels` comes from `.github-webhook-server.yaml`.

> **Tip:** To inherit a lower-precedence value, omit the key entirely or set it to `null`.

> **Tip:** When you override structured settings such as `container`, `branch-protection`, or `test-oracle`, restate every field you still need. Do not assume a deep merge unless that setting is explicitly documented as merged.

## Recommended Placement

- Put server-wide defaults and startup-time settings in the root of `config.yaml`.
- Put repo-specific server settings in `repositories.<repo>` inside `config.yaml`.
- Put repository-owned runtime behavior in `.github-webhook-server.yaml` when you want config changes reviewed and versioned alongside the repository.

> **Tip:** For webhook-time repository behavior, changes are picked up on later webhook deliveries because the server re-reads `config.yaml` and re-fetches `.github-webhook-server.yaml` instead of keeping one permanently merged config in memory.
