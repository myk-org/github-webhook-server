Let me explore the repository thoroughly before writing the documentation.

Now let me check for GitHub App setup details and how tokens work:

Now let me check how GitHub tokens are required:

Now let me check the `.github-webhook-server.yaml` in repo root for more context:

Now let me check what Python version is required:

Let me check the docker-compose more carefully - the `build:` should be `image:`:

Now let me look at how the GitHub App and tokens are used in practice, and verify key configuration concepts:

Now I have enough information. Let me write the Getting Started guide.

# Getting Started

Get the webhook server running, connect it to a GitHub repository, and process your first webhook — all in under 10 minutes. Once complete, the server will automatically manage labels, check runs, and PR workflows for your repositories.

## Prerequisites

- **Python 3.13** installed (required by the project)
- [**uv**](https://docs.astral.sh/uv/) package manager installed
- A **GitHub personal access token** with `repo` and `admin:repo_hook` permissions
- A publicly accessible URL (or a tunnel like [smee.io](https://smee.io)) where GitHub can deliver webhooks

## Quick start

```bash
git clone https://github.com/myk-org/github-webhook-server.git
cd github-webhook-server
uv sync

export WEBHOOK_SERVER_DATA_DIR=/tmp/webhook-data
mkdir -p "$WEBHOOK_SERVER_DATA_DIR"
```

Create a minimal `config.yaml` in your data directory:

```yaml
github-tokens:
  - ghp_yourGitHubPersonalAccessToken

webhook-ip: https://your-domain.com/webhook_server

repositories:
  my-repo:
    name: my-org/my-repo
```

Start the server:

```bash
WEBHOOK_SERVER_DATA_DIR=/tmp/webhook-data uv run entrypoint.py
```

The server starts on `http://0.0.0.0:5000` by default. GitHub webhooks are received at `/webhook_server`.

## Step-by-step setup

### 1. Generate a GitHub token

1. Go to **Settings → Developer settings → Personal access tokens → Tokens (classic)** on GitHub.
2. Create a token with these scopes: `repo`, `admin:repo_hook`, `read:org`.
3. Copy the token — you'll need it for `config.yaml`.

> **Tip:** You can configure multiple tokens for automatic failover when one hits GitHub's rate limit. List them under `github-tokens`.

### 2. Get a webhook URL

GitHub needs a public URL to deliver events to your server. Choose one of these options:

| Method | Best for | URL format |
|--------|----------|------------|
| Public server / reverse proxy | Production | `https://your-domain.com/webhook_server` |
| [smee.io](https://smee.io) | Local development | `https://smee.io/your-channel` |
| ngrok or similar tunnel | Testing | `https://abc123.ngrok.io/webhook_server` |

Set this URL as the `webhook-ip` value in your config.

### 3. Create your configuration file

Create `config.yaml` in your data directory (`WEBHOOK_SERVER_DATA_DIR`). Here's a working example with common options:

```yaml
github-tokens:
  - ghp_yourGitHubToken1
  - ghp_yourGitHubToken2

webhook-ip: https://your-domain.com/webhook_server

default-status-checks:
  - "WIP"
  - "can-be-merged"

repositories:
  my-repo:
    name: my-org/my-repo
    verified-job: true
    pre-commit: true
    protected-branches:
      main: []
```

Key fields:

- **`github-tokens`** — one or more GitHub personal access tokens (the server picks the one with the highest remaining rate limit)
- **`webhook-ip`** — the full public URL where GitHub delivers webhook events (must include `/webhook_server` path unless using smee.io)
- **`repositories`** — each entry maps an identifier to a repository with `name` in `org/repo` format

> **Note:** On startup, the server automatically creates webhooks on each configured repository. You do not need to set up webhooks manually in GitHub.

### 4. Set the data directory

The server reads `config.yaml` from the path specified by `WEBHOOK_SERVER_DATA_DIR`. The default is `/home/podman/data` (used inside the Docker container). For local development, point it to your own directory:

```bash
export WEBHOOK_SERVER_DATA_DIR=/path/to/your/data
```

Your data directory should contain:

```
/path/to/your/data/
├── config.yaml
└── webhook-server.private-key.pem   # Only needed if using a GitHub App
```

### 5. Start the server

```bash
WEBHOOK_SERVER_DATA_DIR=/path/to/your/data uv run entrypoint.py
```

You should see log output indicating:
- Tokens are validated and rate limits checked
- Repository settings are configured
- Webhooks are created on your repositories
- The server is listening on port 5000

### 6. Verify it works

Check the health endpoint:

```bash
curl http://localhost:5000/webhook_server/healthcheck
```

You should get:

```json
{"status": 200, "message": "Alive"}
```

Now open a pull request on your configured repository. The webhook server will:

1. Receive the `pull_request` event from GitHub
2. Add size labels (e.g., `size/S`, `size/M`) based on lines changed
3. Add a branch label (e.g., `branch/main`)
4. Create WIP and can-be-merged check runs
5. Post a welcome comment on the PR

## Adding per-repository configuration

Beyond the global `config.yaml`, you can add a `.github-webhook-server.yaml` file to any repository's root. Settings in this file override the global config for that specific repository.

```yaml
# .github-webhook-server.yaml (in your repo root)
verified-job: true
pre-commit: true
conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
```

This is useful when different repositories need different CI checks, label configurations, or merge policies without changing the central `config.yaml`.

See [Configuring Repositories](configuring-repositories.html) for the full list of per-repository options.

## Advanced Usage

### Using a GitHub App instead of personal tokens

For organizations managing many repositories, a GitHub App provides better rate limits and fine-grained permissions. To use one:

1. Create a GitHub App with the required permissions (repository administration, pull requests, checks, contents).
2. Install the app on your organization or repositories.
3. Download the private key and save it as `webhook-server.private-key.pem` in your data directory.
4. Add the app ID to your `config.yaml`:

```yaml
github-app-id: 123456
github-tokens:
  - ghp_yourTokenForFallback
```

> **Note:** GitHub tokens are still required alongside the App — they serve as fallback and are used for operations the App cannot perform.

### Running with Docker

For production deployments, use the container image:

```bash
mkdir -p ./webhook_server_data_dir
cp config.yaml ./webhook_server_data_dir/
```

```yaml
# docker-compose.yaml
services:
  github-webhook-server:
    container_name: github-webhook-server
    image: ghcr.io/myk-org/github-webhook-server:latest
    volumes:
      - "./webhook_server_data_dir:/home/podman/data:Z"
    environment:
      - WEBHOOK_SERVER_PORT=5000
    ports:
      - "5000:5000"
    privileged: true
    restart: unless-stopped
```

```bash
docker compose up -d
```

See [Deploying with Docker](deploying-with-docker.html) for the complete Docker Compose reference including health checks, environment variables, and persistent storage.

### Securing webhooks

Add a webhook secret to verify that incoming requests are genuinely from GitHub:

```yaml
# In config.yaml
webhook-secret: your-random-secret-string
```

The server will reject any webhook that doesn't include a valid HMAC signature matching this secret. The secret is automatically configured on your GitHub repositories when webhooks are created at startup.

### Filtering events per repository

By default, the server subscribes to all GitHub events. To listen only to specific events for a repository:

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    events:
      - push
      - pull_request
      - pull_request_review
      - issue_comment
      - check_run
      - status
```

### Enabling the log viewer

Set the `ENABLE_LOG_SERVER` environment variable to view webhook processing logs in your browser:

```bash
ENABLE_LOG_SERVER=true WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py
```

Then open `http://localhost:5000/logs` in your browser.

> **Warning:** The log viewer has no authentication. Only enable it on trusted networks.

See [Using the Log Viewer](using-the-log-viewer.html) for full details.

### Multi-token failover

List multiple tokens to automatically survive GitHub API rate limits:

```yaml
github-tokens:
  - ghp_primaryToken
  - ghp_secondaryToken
  - ghp_tertiaryToken
```

The server selects the token with the highest remaining rate limit on each startup and API initialization. You can also set per-repository tokens:

```yaml
repositories:
  high-traffic-repo:
    name: my-org/high-traffic-repo
    github-tokens:
      - ghp_dedicatedToken1
      - ghp_dedicatedToken2
```

## Troubleshooting

**Server fails with "Config file not found"**
- Ensure `WEBHOOK_SERVER_DATA_DIR` points to a directory containing `config.yaml`.
- The default path is `/home/podman/data` — override it for local development.

**Server fails with "does not have `repositories`"**
- Your `config.yaml` must have at least one entry under `repositories`.

**Webhooks not arriving**
- Verify `webhook-ip` is reachable from the internet (or from GitHub's network).
- Check that the URL includes the full path (e.g., `https://example.com/webhook_server`).
- For local development, confirm your smee.io or ngrok tunnel is active.

**Token errors or "rate limit set to 60"**
- A rate limit of 60 indicates an invalid or expired token. Regenerate it in GitHub settings.
- Make sure the token has `repo` and `admin:repo_hook` scopes.

**Port already in use**
- Change the port in `config.yaml`:

```yaml
port: 8080
```

## Next steps

- [Configuring Repositories](configuring-repositories.html) — full config.yaml and per-repo YAML reference
- [Managing Pull Requests](managing-pull-requests.html) — learn PR commands like `/retest`, `/cherry-pick`, and `/approve`
- [Setting Up CI Checks](setting-up-ci-checks.html) — configure tox, pre-commit, and custom check runs
- [Deploying with Docker](deploying-with-docker.html) — production container deployment guide
- [Environment Variables](environment-variables.html) — all supported environment variables

## Related Pages

- [Configuring Repositories](configuring-repositories.html)
- [Deploying with Docker](deploying-with-docker.html)
- [Environment Variables](environment-variables.html)
- [Managing Pull Requests](managing-pull-requests.html)
- [Setting Up CI Checks](setting-up-ci-checks.html)
