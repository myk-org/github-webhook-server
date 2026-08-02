# Start Automating a Repository

You want one GitHub repository under automation quickly so new pull requests start getting webhook handling, labels, and review workflow help without manual setup in GitHub. This guide gets you from an empty data directory to a live webhook and a working health check in a few minutes.

## Prerequisites

- Python `3.14`
- `uv`
- A GitHub App ID
- The matching GitHub App private key in PEM format
- At least one GitHub token that can manage the target repository
- A repository where the GitHub App is installed
- A public URL GitHub can reach, or a `smee.io` channel for local testing

## Quick example

```bash
export WEBHOOK_SERVER_DATA_DIR="$HOME/webhook-server-data"
mkdir -p "$WEBHOOK_SERVER_DATA_DIR"

cat > "$WEBHOOK_SERVER_DATA_DIR/config.yaml" <<'YAML'
github-app-id: 123456
github-tokens:
  - ghp_your_token_here

webhook-ip: https://your-domain.example/webhook_server

repositories:
  your-repo:
    name: your-org/your-repo
YAML

cp /path/to/your/github-app-private-key.pem \
  "$WEBHOOK_SERVER_DATA_DIR/webhook-server.private-key.pem"

uv sync
WEBHOOK_SERVER_DATA_DIR="$WEBHOOK_SERVER_DATA_DIR" uv run entrypoint.py
```

```bash
curl http://127.0.0.1:500/webhook_server/healthcheck
```

```json
{"status":200,"message":"Alive"}
```

Once the server starts, it bootstraps the repository you listed: it creates or updates the webhook and prepares the built-in automation. The health check proves the process is running; the steps below confirm GitHub can actually reach it.

## Step-by-step

### 1. Create the data directory

```bash
export WEBHOOK_SERVER_DATA_DIR="$HOME/webhook-server-data"
mkdir -p "$WEBHOOK_SERVER_DATA_DIR"
```

Your minimal layout should look like this:

```text
$WEBHOOK_SERVER_DATA_DIR/
├── config.yaml
└── webhook-server.private-key.pem
```

> **Note:** If you do not set `WEBHOOK_SERVER_DATA_DIR`, the server uses `/home/podman/data`.

### 2. Add the smallest working `config.yaml`

```yaml
github-app-id: 123456
github-tokens:
  - ghp_your_token_here

webhook-ip: https://your-domain.example/webhook_server

repositories:
  your-repo:
    name: your-org/your-repo
```

Use these values carefully:

| Setting | What to put there |
|---|---|
| `github-app-id` | Your GitHub App ID |
| `github-tokens` | One or more GitHub tokens for repository administration |
| `webhook-ip` | The callback URL GitHub will call |
| `repositories.your-repo` | The short repository name, such as `your-repo` |
| `repositories.your-repo.name` | The full repository name, such as `your-org/your-repo` |

> **Warning:** For a normal public deployment, `webhook-ip` should include the full webhook path, for example `https://your-domain.example/webhook_server`.


> **Warning:** The key under `repositories` should be the short repository name, not `owner/repo`. In the example above, the key is `your-repo` and the full name belongs in `name`.


> **Warning:** GitHub cannot deliver webhooks to `localhost`. Use a public URL or the `smee.io` workflow in [Advanced Usage](#advanced-usage).


> **Tip:** You do not need repo-local overrides for the first run. See [Configure Repositories](configure-repositories.html) for global defaults, per-repo overrides, and self-service settings.

### 3. Add the GitHub App private key

```bash
cp /path/to/your/github-app-private-key.pem \
  "$WEBHOOK_SERVER_DATA_DIR/webhook-server.private-key.pem"
```

The filename matters. The server looks for that exact name in the data directory.

Install the GitHub App on `your-org/your-repo` before you start the server. You need both the private key and at least one GitHub token for a working setup.

### 4. Install dependencies and start the server

```bash
uv sync
WEBHOOK_SERVER_DATA_DIR="$WEBHOOK_SERVER_DATA_DIR" uv run entrypoint.py
```

By default, the server listens on `0.0.0.0:500`. On startup it creates or updates the repository webhook and prepares the repository for automation.

> **Tip:** If you only need one repository working quickly, omit optional keys like `events`, `pre-commit`, `tox`, `container`, and `pypi` until the webhook is live.

### 5. Verify the server is healthy

```bash
curl http://127.0.0.1:500/webhook_server/healthcheck
```

You should get:

```json
{"status":200,"message":"Alive"}
```

A healthy response means the web process is running and listening on the webhook route.

> **Note:** The health check does not prove that GitHub can reach your `webhook-ip`. It only proves the server is up locally.

### 6. Verify GitHub can deliver to the webhook

After the server is running, check the delivery from GitHub:

1. Open the repository in GitHub.
2. Open the webhook or GitHub App delivery view for that repository.
3. Send a ping or redeliver the most recent test event.
4. Confirm the delivery gets an HTTP `200`.

If you see `200`, the server accepted the event and queued it for processing.

> **Note:** A `200` delivery means the event was accepted for background processing. It does not guarantee every automation step succeeded. See [Webhook and Health API](webhook-and-health-api.html) for the response behavior.

### 7. Prove the repository is actually automated

The fastest end-to-end proof is a tiny pull request.

1. Open a small PR against `your-org/your-repo`.
2. Wait for the server to process the `pull_request` event.
3. Confirm you see automation, such as a welcome comment, labels, or check runs on the PR.

If that works, the repository is connected and ready for deeper configuration. See [Manage Pull Requests](manage-pull-requests.html) for what happens on new PRs, see [Set Up OWNERS and Review Rules](set-up-owners-and-reviewers.html) before rolling this out to a team, and see [Set Up Checks and Release Workflows](set-up-checks-and-release-workflows.html) to add CI and release automation.

## Advanced Usage

### Add a webhook secret

```yaml
github-app-id: 123456
github-tokens:
  - ghp_your_token_here

webhook-ip: https://your-domain.example/webhook_server
webhook-secret: replace-this-with-a-shared-secret

repositories:
  your-repo:
    name: your-org/your-repo
```

Add `webhook-secret` once the basic flow works. GitHub will sign deliveries, and the server will reject requests with the wrong signature.

> **Tip:** See [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html) for production hardening after your first repository is connected.

### Use more than one GitHub token

```yaml
github-tokens:
  - ghp_primary_token
  - ghp_backup_token
```

The server supports a token pool. This is useful if you want more rate-limit headroom or a backup token when one is exhausted.

### Test locally with `smee.io`

```yaml
webhook-ip: https://smee.io/your-channel
```

```bash
smee -u https://smee.io/your-channel -p 500 -P /webhook_server
WEBHOOK_SERVER_DATA_DIR="$WEBHOOK_SERVER_DATA_DIR" uv run entrypoint.py
```

This is the fastest way to test from a laptop when GitHub cannot reach your machine directly.

> **Warning:** For `smee.io`, use the channel URL as `webhook-ip`. Do not append `/webhook_server` to the `smee.io` URL, because the local forwarding command already adds that path.

### Change the bind address, port, or worker count

```yaml
ip-bind: 0.0.0.0
port: 8080
max-workers: 20
```

Restart the server after changing these values. If you change `webhook-ip`, restart too so the startup bootstrap can update the repository webhook.

See [Environment Variables](environment-variables.html) for startup toggles such as log viewer and MCP support.

### Limit the events the repository processes

```yaml
repositories:
  your-repo:
    name: your-org/your-repo
    events:
      - pull_request
      - issue_comment
      - push
```

If you omit `events`, the webhook is created with `*`. See [Supported GitHub Events](supported-github-events.html) to decide which events you actually want.

## Troubleshooting

- **The server says `config.yaml` was not found**
  - Make sure `WEBHOOK_SERVER_DATA_DIR` points to the directory that contains `config.yaml`.
  - Make sure the file is named exactly `config.yaml`.

- **The server starts, but repository automation never appears**
  - Check that the GitHub App is installed on the repository.
  - Check that the `repositories` key uses the short repo name and `name` uses the full `owner/repo` value.
  - Check that the private key file is named exactly `webhook-server.private-key.pem`.

- **GitHub deliveries fail even though the health check passes**
  - Make sure `webhook-ip` is publicly reachable.
  - Make sure a public deployment uses the full callback path ending in `/webhook_server`.
  - If you set `webhook-secret`, make sure GitHub is using the same shared secret.

- **GitHub shows `200`, but the PR still did not update**
  - The server accepts webhook events immediately and processes them in the background.
  - See [Debug with the Log Viewer](debug-with-the-log-viewer.html) to inspect delivery IDs, step failures, and log output.# Start Automating a Repository

You want one GitHub repository under automation quickly so new pull requests start getting webhook handling, labels, and review workflow help without manual setup in GitHub. This guide gets you from an empty data directory to a live webhook and a working health check in a few minutes.

## Prerequisites

- Python `3.14`
- `uv`
- A GitHub App ID
- The matching GitHub App private key in PEM format
- At least one GitHub token that can manage the target repository
- A repository where the GitHub App is installed
- A public URL GitHub can reach, or a `smee.io` channel for local testing

## Quick example

```bash
export WEBHOOK_SERVER_DATA_DIR="$HOME/webhook-server-data"
mkdir -p "$WEBHOOK_SERVER_DATA_DIR"

cat > "$WEBHOOK_SERVER_DATA_DIR/config.yaml" <<'YAML'
github-app-id: 123456
github-tokens:
  - ghp_your_token_here

webhook-ip: https://your-domain.example/webhook_server

repositories:
  your-repo:
    name: your-org/your-repo
YAML

cp /path/to/your/github-app-private-key.pem \
  "$WEBHOOK_SERVER_DATA_DIR/webhook-server.private-key.pem"

uv sync
WEBHOOK_SERVER_DATA_DIR="$WEBHOOK_SERVER_DATA_DIR" uv run entrypoint.py
```

```bash
curl http://127.0.0.1:5000/webhook_server/healthcheck
```

```json
{"status":200,"message":"Alive"}
```

Once the server starts, it bootstraps the repository you listed: it creates or updates the webhook and prepares the built-in automation. The health check proves the process is running; the steps below confirm GitHub can actually reach it.

## Step-by-step

### 1. Create the data directory

```bash
export WEBHOOK_SERVER_DATA_DIR="$HOME/webhook-server-data"
mkdir -p "$WEBHOOK_SERVER_DATA_DIR"
```

Your minimal layout should look like this:

```text
$WEBHOOK_SERVER_DATA_DIR/
├── config.yaml
└── webhook-server.private-key.pem
```

> **Note:** If you do not set `WEBHOOK_SERVER_DATA_DIR`, the server uses `/home/podman/data`.

### 2. Add the smallest working `config.yaml`

```yaml
github-app-id: 123456
github-tokens:
  - ghp_your_token_here

webhook-ip: https://your-domain.example/webhook_server

repositories:
  your-repo:
    name: your-org/your-repo
```

Use these values carefully:

| Setting | What to put there |
|---|---|
| `github-app-id` | Your GitHub App ID |
| `github-tokens` | One or more GitHub tokens for repository administration |
| `webhook-ip` | The callback URL GitHub will call |
| `repositories.your-repo` | The short repository name, such as `your-repo` |
| `repositories.your-repo.name` | The full repository name, such as `your-org/your-repo` |

> **Warning:** For a normal public deployment, `webhook-ip` should include the full webhook path, for example `https://your-domain.example/webhook_server`.


> **Warning:** The key under `repositories` should be the short repository name, not `owner/repo`. In the example above, the key is `your-repo` and the full name belongs in `name`.


> **Warning:** GitHub cannot deliver webhooks to `localhost`. Use a public URL or the `smee.io` workflow in [Advanced Usage](#advanced-usage).


> **Tip:** You do not need repo-local overrides for the first run. See [Configure Repositories](configure-repositories.html) for global defaults, per-repo overrides, and self-service settings.

### 3. Add the GitHub App private key

```bash
cp /path/to/your/github-app-private-key.pem \
  "$WEBHOOK_SERVER_DATA_DIR/webhook-server.private-key.pem"
```

The filename matters. The server looks for that exact name in the data directory.

Install the GitHub App on `your-org/your-repo` before you start the server. You need both the private key and at least one GitHub token for a working setup.

### 4. Install dependencies and start the server

```bash
uv sync
WEBHOOK_SERVER_DATA_DIR="$WEBHOOK_SERVER_DATA_DIR" uv run entrypoint.py
```

By default, the server listens on `0.0.0.0:5000`. On startup it creates or updates the repository webhook and prepares the repository for automation.

> **Tip:** If you only need one repository working quickly, omit optional keys like `events`, `pre-commit`, `tox`, `container`, and `pypi` until the webhook is live.

### 5. Verify the server is healthy

```bash
curl http://127.0.0.1:5000/webhook_server/healthcheck
```

You should get:

```json
{"status":200,"message":"Alive"}
```

A healthy response means the web process is running and listening on the webhook route.

> **Note:** The health check does not prove that GitHub can reach your `webhook-ip`. It only proves the server is up locally.

### 6. Verify GitHub can deliver to the webhook

After the server is running, check the delivery from GitHub:

1. Open the repository in GitHub.
2. Open the webhook or GitHub App delivery view for that repository.
3. Send a ping or redeliver the most recent test event.
4. Confirm the delivery gets an HTTP `200`.

If you see `200`, the server accepted the event and queued it for processing.

> **Note:** A `200` delivery means the event was accepted for background processing. It does not guarantee every automation step succeeded. See [Webhook and Health API](webhook-and-health-api.html) for the response behavior.

### 7. Prove the repository is actually automated

The fastest end-to-end proof is a tiny pull request.

1. Open a small PR against `your-org/your-repo`.
2. Wait for the server to process the `pull_request` event.
3. Confirm you see automation, such as a welcome comment, labels, or check runs on the PR.

If that works, the repository is connected and ready for deeper configuration. See [Manage Pull Requests](manage-pull-requests.html) for what happens on new PRs, see [Set Up OWNERS and Review Rules](set-up-owners-and-reviewers.html) before rolling this out to a team, and see [Set Up Checks and Release Workflows](set-up-checks-and-release-workflows.html) to add CI and release automation.

## Advanced Usage

### Add a webhook secret

```yaml
github-app-id: 123456
github-tokens:
  - ghp_your_token_here

webhook-ip: https://your-domain.example/webhook_server
webhook-secret: replace-this-with-a-shared-secret

repositories:
  your-repo:
    name: your-org/your-repo
```

Add `webhook-secret` once the basic flow works. GitHub will sign deliveries, and the server will reject requests with the wrong signature.

> **Tip:** See [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html) for production hardening after your first repository is connected.

### Use more than one GitHub token

```yaml
github-tokens:
  - ghp_primary_token
  - ghp_backup_token
```

The server supports a token pool. This is useful if you want more rate-limit headroom or a backup token when one is exhausted.

### Test locally with `smee.io`

```yaml
webhook-ip: https://smee.io/your-channel
```

```bash
smee -u https://smee.io/your-channel -p 5000 -P /webhook_server
WEBHOOK_SERVER_DATA_DIR="$WEBHOOK_SERVER_DATA_DIR" uv run entrypoint.py
```

This is the fastest way to test from a laptop when GitHub cannot reach your machine directly.

> **Warning:** For `smee.io`, use the channel URL as `webhook-ip`. Do not append `/webhook_server` to the `smee.io` URL, because the local forwarding command already adds that path.

### Change the bind address, port, or worker count

```yaml
ip-bind: 0.0.0.0
port: 8080
max-workers: 20
```

Restart the server after changing these values. If you change `webhook-ip`, restart too so the startup bootstrap can update the repository webhook.

See [Environment Variables](environment-variables.html) for startup toggles such as log viewer and MCP support.

### Limit the events the repository processes

```yaml
repositories:
  your-repo:
    name: your-org/your-repo
    events:
      - pull_request
      - issue_comment
      - push
```

If you omit `events`, the webhook is created with `*`. See [Supported GitHub Events](supported-github-events.html) to decide which events you actually want.

## Troubleshooting

- **The server says `config.yaml` was not found**
  - Make sure `WEBHOOK_SERVER_DATA_DIR` points to the directory that contains `config.yaml`.
  - Make sure the file is named exactly `config.yaml`.

- **The server starts, but repository automation never appears**
  - Check that the GitHub App is installed on the repository.
  - Check that the `repositories` key uses the short repo name and `name` uses the full `owner/repo` value.
  - Check that the private key file is named exactly `webhook-server.private-key.pem`.

- **GitHub deliveries fail even though the health check passes**
  - Make sure `webhook-ip` is publicly reachable.
  - Make sure a public deployment uses the full callback path ending in `/webhook_server`.
  - If you set `webhook-secret`, make sure GitHub is using the same shared secret.

- **GitHub shows `200`, but the PR still did not update**
  - The server accepts webhook events immediately and processes them in the background.
  - See [Debug with the Log Viewer](debug-with-the-log-viewer.html) to inspect delivery IDs, step failures, and log output.

## Related Pages

- [Configure Repositories](configure-repositories.html)
- [Set Up OWNERS and Review Rules](set-up-owners-and-reviewers.html)
- [Manage Pull Requests](manage-pull-requests.html)
- [Set Up Checks and Release Workflows](set-up-checks-and-release-workflows.html)
- [Secure Webhooks and Pull Requests](secure-webhooks-and-pull-requests.html)
