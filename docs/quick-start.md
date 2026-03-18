# Quick Start

This guide gets `github-webhook-server` running with one repository. You will create a data directory, add a minimal `config.yaml`, place the GitHub App private key where the server expects it, start the app, and verify that it is alive.

## Before You Start

You need:
- Python `3.13`
- `uv`
- A GitHub App ID
- The matching GitHub App private key in PEM format
- At least one GitHub token the server can use for API calls
- A repository where that GitHub App is installed

> **Warning:** The server uses both `github-tokens` and GitHub App auth. The token pool is used for regular GitHub API calls, and `github-app-id` plus `webhook-server.private-key.pem` are used to authenticate as the app installation.

## 1. Create a Data Directory

The server loads `config.yaml` from `WEBHOOK_SERVER_DATA_DIR`. If you do not set that variable, it defaults to `/home/podman/data`.

```bash
export WEBHOOK_SERVER_DATA_DIR=/path/to/data
mkdir -p "$WEBHOOK_SERVER_DATA_DIR"
```

Your directory should look like this:

```text
/path/to/data/
├── config.yaml
└── webhook-server.private-key.pem
```

## 2. Create a Minimal `config.yaml`

A minimal working config needs:
- `github-app-id`
- `github-tokens`
- `webhook-ip`
- At least one repository under `repositories`

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/myk-org/github-webhook-server/refs/heads/main/webhook_server/config/schema.yaml

github-app-id: 123456
github-tokens:
  - token1

webhook-ip: https://your-domain.com/webhook_server

repositories:
  test-repo:
    name: org/test-repo
```

Replace `123456`, `token1`, `https://your-domain.com/webhook_server`, and `org/test-repo` with your real values.

What each part means:
- `github-app-id` is your GitHub App ID.
- `github-tokens` is the token pool the server will choose from at startup.
- `webhook-ip` is the public URL GitHub should call.
- `repositories` is the list of repositories the server should manage.
- `test-repo` is the short repository name.
- `name` is the full `owner/repo` name.

> **Warning:** The key under `repositories` should be the short repository name, such as `test-repo`, not the full `owner/repo`. The full name belongs in the nested `name` field.

> **Warning:** `webhook-ip` should be the full webhook URL. In a normal deployment that means including `/webhook_server`, for example `https://your-domain.com/webhook_server`.

> **Warning:** `localhost` is fine for the health check, but GitHub cannot deliver webhooks to `localhost`. Use a real public URL or a `smee.io` channel URL for webhook delivery.

> **Note:** If you omit `events`, the server creates the webhook with `*`, which subscribes it to all events.

> **Note:** You can list more than one token in `github-tokens`. The server checks them and selects the one with the highest remaining rate limit.

If you want GitHub to sign webhook deliveries, add a shared secret:

```yaml
webhook-secret: test-webhook-secret
```

> **Tip:** You do not need a repo-local `.github-webhook-server.yaml` file for a minimal setup. The global `config.yaml` is enough to get started.

## 3. Add the GitHub App Private Key

Save the GitHub App private key as:

`$WEBHOOK_SERVER_DATA_DIR/webhook-server.private-key.pem`

The filename matters. The server loads that exact file from the data directory when it creates the GitHub App installation client.

> **Warning:** The private key is not a replacement for `github-tokens`. You need both.

> **Warning:** The matching GitHub App must be installed on every repository you add, or the server will not be able to fetch the repository installation.

## 4. Install Dependencies and Start the Server

Install the project dependencies:

```bash
uv sync
```

Start the server:

```bash
WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py
```

By default, the server starts on `0.0.0.0:5000` with `10` workers. You can override that in `config.yaml` with:
- `ip-bind`
- `port`
- `max-workers`

> **Note:** On startup, the server applies repository settings, resets in-progress check runs to queued, and creates or updates GitHub webhooks for every repository in `config.yaml`.

> **Tip:** Validate the file before starting the server with `uv run webhook_server/tests/test_schema_validator.py "$WEBHOOK_SERVER_DATA_DIR/config.yaml"`.

## 5. Verify the Health Endpoint

Once the server is running, check the health endpoint:

```bash
curl http://127.0.0.1:5000/webhook_server/healthcheck
```

You should get:

```json
{"status":200,"message":"Alive"}
```

If you changed `port` in `config.yaml`, use that port instead of `5000`.

This is the same endpoint the container health check uses.

> **Note:** A healthy response means the web server is up. It does not confirm that GitHub can reach your public `webhook-ip` yet.

At this point, the process is running and listening for webhook traffic on `/webhook_server`. If GitHub can reach the URL you set in `webhook-ip`, the server is ready to receive events.
