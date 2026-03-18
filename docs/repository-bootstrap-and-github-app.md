# Repository Bootstrap and GitHub App

When `github-webhook-server` starts, it does a one-time bootstrap pass before it begins serving webhook traffic. That startup pass is what makes each configured repository "ready" on GitHub: it creates the server's built-in labels, applies repository defaults, writes branch protection for the branches you list, repairs stale built-in check runs, and makes sure the repository webhook points back to this server.

This is a startup-only workflow. If you change bootstrap-related settings such as `webhook-ip`, `branch-protection`, or `protected-branches`, restart the server so the bootstrap runs again.

From `entrypoint.py`:

```python
if __name__ == "__main__":
    # Run Podman cleanup before starting the application
    run_podman_cleanup()

    result = asyncio.run(repository_and_webhook_settings(webhook_secret=_webhook_secret))

    uvicorn.run(
        "webhook_server.app:FASTAPI_APP",
        host=_ip_bind,
        port=int(_port),
        workers=int(_max_workers),
        reload=False,
    )
```

From `webhook_server/utils/github_repository_and_webhook_settings.py`:

```python
async def repository_and_webhook_settings(webhook_secret: str | None = None) -> None:
    config = Config(logger=LOGGER)
    apis_dict: dict[str, dict[str, Any]] = {}

    apis: list[Future[tuple[str, github.Github | None, str]]] = []
    with ThreadPoolExecutor() as executor:
        for repo, _ in config.root_data["repositories"].items():
            apis.append(
                executor.submit(
                    get_repository_api,
                    **{"repository": repo},
                )
            )

        for result in as_completed(apis):
            repository, github_api, api_user = result.result()
            apis_dict[repository] = {"api": github_api, "user": api_user}

    LOGGER.debug(f"Repositories APIs: {apis_dict}")

    await set_repositories_settings(config=config, apis_dict=apis_dict)
    set_all_in_progress_check_runs_to_queued(repo_config=config, apis_dict=apis_dict)
    create_webhook(config=config, apis_dict=apis_dict, secret=webhook_secret)
```

> **Note:** The startup bootstrap uses the central `config.yaml` in the server data directory. The repo-local `.github-webhook-server.yaml` file is loaded later during normal webhook processing, not during this bootstrap pass.

## What You Need

The server reads its main configuration from `WEBHOOK_SERVER_DATA_DIR`, which defaults to `/home/podman/data`.

From `webhook_server/libs/config.py`:

```python
self.data_dir: str = os.environ.get("WEBHOOK_SERVER_DATA_DIR", "/home/podman/data")
self.config_path: str = os.path.join(self.data_dir, "config.yaml")
```

That same directory also needs the GitHub App private key file named `webhook-server.private-key.pem`.

The container example makes that explicit:

```yaml
volumes:
  - "./webhook_server_data_dir:/home/podman/data:Z" # Should include config.yaml and webhook-server.private-key.pem
```

At minimum, you should have:

- a `config.yaml` with `github-app-id`, `webhook-ip`, and `repositories`
- at least one valid `github-token` for each repository you want to bootstrap
- the GitHub App private key at `webhook-server.private-key.pem`
- the GitHub App installed on the repositories where you want App-backed check runs to work

A trimmed example from `examples/config.yaml`:

```yaml
github-app-id: 123456 # GitHub app id
github-tokens:
  - <GITHIB TOKEN1>
  - <GITHIB TOKEN2>

webhook-ip: <HTTP://IP OR URL:PORT/webhook_server> # Full URL with path (e.g., https://your-domain.com/webhook_server or https://smee.io/your-channel)

default-status-checks:
  - "WIP"
  - "dpulls"
  - "can-be-merged"

branch-protection:
  strict: True
  require_code_owner_reviews: True
  dismiss_stale_reviews: False
  required_approving_review_count: 1
  required_linear_history: True
  required_conversation_resolution: True

repositories:
  my-repository:
    name: my-org/my-repository
    events:
      - push
      - pull_request
      - pull_request_review
      - pull_request_review_thread
      - issue_comment
      - check_run
      - status
    tox:
      main: all
      dev: testenv1,testenv2
    pre-commit: true
    protected-branches:
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
      release: true
```

> **Warning:** `webhook-ip` must be the full callback URL, including `/webhook_server`. The bootstrap code uses this value exactly as written when it creates the GitHub webhook.

## How Authentication Is Used

Startup bootstrap uses two different GitHub auth paths.

- `github-tokens` are used for normal repository administration: reading the repo, creating labels, editing repository settings, applying branch protection, and creating repository webhooks.
- The configured GitHub App is used when the server needs an installation-scoped API that can create check runs. That matters during startup because stale built-in checks are repaired through the App.

The token side is selected per repository, choosing the token with the highest remaining rate limit:

```python
config = Config(repository=repository, logger=LOGGER)
github_api, _, api_user = get_api_with_highest_rate_limit(config=config, repository_name=repository)
```

The GitHub App side is created from `github-app-id` plus `webhook-server.private-key.pem`:

```python
with open(os.path.join(config_.data_dir, "webhook-server.private-key.pem")) as fd:
    private_key = fd.read()

github_app_id: int = config_.root_data["github-app-id"]
auth: AppAuth = Auth.AppAuth(app_id=github_app_id, private_key=private_key)
app_instance: GithubIntegration = GithubIntegration(auth=auth)
owner, repo = repository_name.split("/")

return app_instance.get_repo_installation(owner=owner, repo=repo).get_github_for_installation()
```

> **Note:** If you configure repository-specific `github-tokens`, those override the global token list because bootstrap resolves tokens through repository-aware config lookups.

## What Bootstrap Changes

### Labels and Repository Defaults

For each configured repository, bootstrap first makes sure the static label set exists and has the expected colors. If a label is missing, it is created. If it exists with a different color, it is updated.

From `webhook_server/utils/github_repository_settings.py`:

```python
def set_repository_labels(repository: Repository, api_user: str) -> str:
    LOGGER.info(f"[API user {api_user}] - Set repository {repository.name} labels")
    repository_labels: dict[str, dict[str, Any]] = {}
    for label in repository.get_labels():
        repository_labels[label.name.lower()] = {
            "object": label,
            "color": label.color,
        }

    for label_name, label_color in STATIC_LABELS_DICT.items():
        label_lower: str = label_name.lower()
        if label_lower in repository_labels:
            repo_label: Label = repository_labels[label_lower]["object"]
            if repository_labels[label_lower]["color"] == label_color:
                continue
            else:
                LOGGER.debug(f"{repository.name}: Edit repository label {label_name} with color {label_color}")
                repo_label.edit(name=repo_label.name, color=label_color)
        else:
            LOGGER.debug(f"{repository.name}: Add repository label {label_name} with color {label_color}")
            repository.create_label(name=label_name, color=label_color)

    return f"[API user {api_user}] - {repository}: Setting repository labels is done"
```

The static startup label set comes from `STATIC_LABELS_DICT`:

```python
STATIC_LABELS_DICT: dict[str, str] = {
    **USER_LABELS_DICT,
    CHERRY_PICKED_LABEL: "1D76DB",
    AI_RESOLVED_CONFLICTS_LABEL: "FFA500",
    f"{SIZE_LABEL_PREFIX}L": "F5621C",
    f"{SIZE_LABEL_PREFIX}M": "F09C74",
    f"{SIZE_LABEL_PREFIX}S": "0E8A16",
    f"{SIZE_LABEL_PREFIX}XL": "D93F0B",
    f"{SIZE_LABEL_PREFIX}XS": "ededed",
    f"{SIZE_LABEL_PREFIX}XXL": "B60205",
    NEEDS_REBASE_LABEL_STR: "B60205",
    CAN_BE_MERGED_STR: "0E8A17",
    HAS_CONFLICTS_LABEL_STR: "B60205",
}
```

In practice, that means startup ensures labels such as:

- `hold`, `verified`, `wip`, `lgtm`, `approve`, `automerge`
- `CherryPicked`, `ai-resolved-conflicts`
- `needs-rebase`, `has-conflicts`, `can-be-merged`
- `size/XS`, `size/S`, `size/M`, `size/L`, `size/XL`, `size/XXL`

After labels, bootstrap applies repository-wide defaults:

```python
def set_repository_settings(repository: Repository, api_user: str) -> None:
    LOGGER.info(f"[API user {api_user}] - Set repository {repository.name} settings")
    repository.edit(delete_branch_on_merge=True, allow_auto_merge=True, allow_update_branch=True)

    if repository.private:
        LOGGER.warning(f"{repository.name}: Repository is private, skipping setting security settings")
        return

    LOGGER.info(f"[API user {api_user}] - Set repository {repository.name} security settings")
    repository._requester.requestJsonAndCheck(
        "PATCH",
        f"{repository.url}/code-scanning/default-setup",
        input={"state": "not-configured"},
    )

    repository._requester.requestJsonAndCheck(
        "PATCH",
        repository.url,
        input={
            "security_and_analysis": {
                "secret_scanning": {"status": "enabled"},
                "secret_scanning_push_protection": {"status": "enabled"},
            }
        },
    )
```

So, on public repositories, startup also:

- enables delete-branch-on-merge
- enables auto-merge
- enables update branch
- enables secret scanning
- enables secret scanning push protection
- sets code scanning default setup to `not-configured`

> **Note:** Private repositories still get label reconciliation and repository-level merge defaults, but the startup path skips the public-repo security setup and does not apply branch protection for them.

> **Tip:** Startup only pre-creates the static labels. Dynamic labels such as `approved-*`, `lgtm-*`, `commented-*`, `changes-requested-*`, `cherry-pick-*`, and `branch-*` show up later when real webhook events need them.

### Branch Protection and Required Checks

Only branches listed under `protected-branches` are touched. Bootstrap reads the top-level `branch-protection` block, then lets `repositories.<name>.branch-protection` override it for that repository.

If you do not override anything, the default protection settings are:

- `strict: true`
- `require_code_owner_reviews: false`
- `dismiss_stale_reviews: true`
- `required_approving_review_count: 0`
- `required_linear_history: true`
- `required_conversation_resolution: true`

When branch protection is applied, the required status checks are passed directly into `branch.edit_protection(...)`:

```python
def set_branch_protection(
    branch: Branch,
    repository: Repository,
    required_status_checks: list[str],
    strict: bool,
    require_code_owner_reviews: bool,
    dismiss_stale_reviews: bool,
    required_approving_review_count: int,
    required_linear_history: bool,
    required_conversation_resolution: bool,
    api_user: str,
) -> bool:
    LOGGER.info(
        f"[API user {api_user}] - Set branch {branch} setting for {repository.name}. "
        f"enabled checks: {required_status_checks}"
    )
    branch.edit_protection(
        strict=strict,
        required_conversation_resolution=required_conversation_resolution,
        contexts=required_status_checks,
        require_code_owner_reviews=require_code_owner_reviews,
        dismiss_stale_reviews=dismiss_stale_reviews,
        required_approving_review_count=required_approving_review_count,
        required_linear_history=required_linear_history,
        users_bypass_pull_request_allowances=[api_user],
        teams_bypass_pull_request_allowances=[api_user],
        apps_bypass_pull_request_allowances=[api_user],
    )

    return True
```

The required check list is generated from your repository configuration. From `get_required_status_checks(...)`:

```python
def get_required_status_checks(
    repo: Repository,
    data: dict[str, Any],
    default_status_checks: list[str],
    exclude_status_checks: list[str],
) -> list[str]:
    if data.get("tox"):
        default_status_checks.append("tox")

    if data.get("verified-job", True):
        default_status_checks.append("verified")

    if data.get("container"):
        default_status_checks.append(BUILD_CONTAINER_STR)

    if data.get("pypi"):
        default_status_checks.append(PYTHON_MODULE_INSTALL_STR)

    if data.get("pre-commit"):
        default_status_checks.append(PRE_COMMIT_STR)

    if data.get(CONVENTIONAL_TITLE_STR):
        default_status_checks.append(CONVENTIONAL_TITLE_STR)

    try:
        repo.get_contents(".pre-commit-config.yaml")
        default_status_checks.append("pre-commit.ci - pr")
    except UnknownObjectException:
        pass

    # Deduplicate status checks while preserving order
    seen: set[str] = set()
    deduplicated: list[str] = []
    for status_check in default_status_checks:
        if status_check not in seen:
            seen.add(status_check)
            deduplicated.append(status_check)

    # Remove excluded status checks
    for status_check in exclude_status_checks:
        while status_check in deduplicated:
            deduplicated.remove(status_check)

    return deduplicated
```

Here is how that plays out:

- Start with `default-status-checks`
- Always add `can-be-merged`
- Add `tox` if `tox` is configured
- Add `verified` unless `verified-job: false`
- Add `build-container` if `container` is configured
- Add `python-module-install` if `pypi` is configured
- Add `pre-commit` if `pre-commit: true`
- Add `conventional-title` if `conventional-title` is configured
- Add `pre-commit.ci - pr` automatically when `.pre-commit-config.yaml` exists in the repository
- Deduplicate the list
- Remove anything listed in `exclude-runs`

> **Warning:** `include-runs` is not additive. If you set `include-runs` for a branch, that list replaces the automatically generated check list for that branch. Use `exclude-runs` when you want "the generated list, minus a few checks."

> **Tip:** The server always adds `can-be-merged` before deduplicating. You do not need to add it twice.

One important limitation to remember: startup branch protection is built from the built-in checks above and your explicit `include-runs`. Runtime-only features such as `custom-check-runs` are handled later during webhook processing, not automatically added by this startup branch-protection pass.

### GitHub App Check-State Repair

After repository settings are written, bootstrap scans open pull requests and repairs built-in checks that were left stuck in `in_progress`. This is where the GitHub App matters most at startup.

The built-in check names are:

```python
BUILTIN_CHECK_NAMES: frozenset[str] = frozenset({
    TOX_STR,
    PRE_COMMIT_STR,
    BUILD_CONTAINER_STR,
    PYTHON_MODULE_INSTALL_STR,
    CONVENTIONAL_TITLE_STR,
    CAN_BE_MERGED_STR,
})
```

And the reset logic is:

```python
def set_repository_check_runs_to_queued(
    config_: Config,
    data: dict[str, Any],
    github_api: Github,
    check_runs: frozenset[str],
    api_user: str,
) -> tuple[bool, str, Callable[..., Any]]:
    def _set_checkrun_queued(_api: Repository, _pull_request: PullRequest) -> None:
        last_commit: Commit | None = None
        for commit in _pull_request.get_commits():
            last_commit = commit
        if last_commit is None:
            LOGGER.error(f"[API user {api_user}] - {repository}: [PR:{_pull_request.number}] No commits found")
            return
        for check_run in last_commit.get_check_runs():
            if check_run.name in check_runs and check_run.status == IN_PROGRESS_STR:
                LOGGER.warning(
                    f"[API user {api_user}] - {repository}: [PR:{_pull_request.number}] "
                    f"{check_run.name} status is {IN_PROGRESS_STR}, "
                    f"Setting check run {check_run.name} to {QUEUED_STR}"
                )
                _api.create_check_run(name=check_run.name, head_sha=last_commit.sha, status=QUEUED_STR)
```

What this means for you:

- it looks only at open PRs
- it inspects the last commit on each PR
- it only repairs built-in check runs
- if one of those checks is still `in_progress` when the server starts, bootstrap creates a new check run with the same name and sets it to `queued`

This is especially useful after restarts or crashes, because it prevents built-in checks like `tox` or `can-be-merged` from being left permanently stuck in the GitHub UI.

> **Note:** Custom checks are not part of this startup repair pass. The App-backed reset only targets the built-in check names listed above.

> **Warning:** If the GitHub App is not installed on a repository, the startup repair step cannot create replacement check runs for that repository.

### Webhook Creation and Reconciliation

The last bootstrap step makes sure every configured repository has a webhook pointing back to this server.

From `webhook_server/utils/webhook.py`:

```python
config_: dict[str, str] = {"url": webhook_ip, "content_type": "json"}

if secret:
    config_["secret"] = secret

events: list[str] = data.get("events", ["*"])

try:
    hooks: list[Hook] = list(repo.get_hooks())
except Exception as ex:
    return (
        False,
        f"[API user {api_user}] - Could not list webhook for {full_repository_name}, check token permissions: {ex}",
        LOGGER.error,
    )

for _hook in hooks:
    if webhook_ip in _hook.config["url"]:
        secret_presence_mismatch = bool(_hook.config.get("secret")) != bool(secret)
        if secret_presence_mismatch:
            LOGGER.info(f"[API user {api_user}] - {full_repository_name}: Deleting old webhook")
            _hook.delete()

        else:
            # Check if events need updating
            hook_events = sorted(set(_hook.events))
            config_events = sorted(set(events))
            if hook_events != config_events:
                LOGGER.info(
                    f"[API user {api_user}] - {full_repository_name}: "
                    f"Updating webhook events: {hook_events} -> {config_events}"
                )
                _hook.edit(name="web", config=config_, events=config_events, active=True)
                return (
                    True,
                    f"[API user {api_user}] - {full_repository_name}: "
                    f"Hook updated with new events - {_hook.config['url']}",
                    LOGGER.info,
                )

            return (
                True,
                f"[API user {api_user}] - {full_repository_name}: Hook already exists - {_hook.config['url']}",
                LOGGER.info,
            )

LOGGER.info(
    f"[API user {api_user}] - Creating webhook: {config_['url']} for {full_repository_name} with events: {events}"
)
repo.create_hook(name="web", config=config_, events=events, active=True)
```

That gives you a simple startup contract:

- if no matching hook exists, bootstrap creates one
- if a matching hook exists with the same event list, bootstrap leaves it alone
- if the event list changed, bootstrap updates the existing hook
- if you added or removed a webhook secret, bootstrap deletes the old matching hook and recreates it
- if `events` is omitted for a repository, bootstrap subscribes that webhook to `*`

If you also set `webhook-secret`, the same secret is used later to validate incoming requests:

```python
webhook_secret = root_config.get("webhook-secret")

if webhook_secret:
    signature_header = request.headers.get("x-hub-signature-256")
    verify_signature(payload_body=payload_body, secret_token=webhook_secret, signature_header=signature_header)
```

That gives you one secret on both sides:

- bootstrap attaches it to the GitHub repository webhook
- request handling verifies `x-hub-signature-256` with that same value

## What To Expect After a Restart

After a successful restart, the practical results should be easy to spot in GitHub:

- missing static labels appear, and existing static label colors are corrected
- repository defaults like delete-branch-on-merge and auto-merge are enabled
- listed protected branches are updated with the configured protection rules and required checks
- built-in open-PR checks that were stuck in `in_progress` are reset to `queued`
- the repository webhook points to your configured `webhook-ip` and listens to the configured events

If one of those things does not happen, the first things to verify are:

- the repo exists in `config.yaml`
- the selected GitHub token has enough repository/admin access to edit the repo
- the GitHub App is installed on that repo
- `webhook-server.private-key.pem` and `github-app-id` match the installed App
- `webhook-ip` is the full callback URL, including `/webhook_server`
