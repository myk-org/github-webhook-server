# Installation

`github-webhook-server` is configured around a small server data directory plus GitHub credentials. A working install needs a Python `3.13.x` interpreter, `uv`, `git`, a reachable webhook URL, and GitHub credentials that can manage the repositories you configure.

## Runtime requirements

The project pins Python exactly:

```45:45:pyproject.toml
requires-python = "==3.13.*"
```

Install these tools for a normal source install:

- `uv`
- `git`

Install these only if you use the matching features:

- `podman` for `docker:` login and repository `container:` build/push automation
- `gh` for automated cherry-pick PR creation
- `claude`, `gemini`, or `cursor` CLI if you enable `ai-features` or `test-oracle`
- Node.js and `npm` if you want to install the Gemini CLI locally

> **Note:** The built-in tox, pre-commit, and twine flows are launched through `uv` and `uvx`, so you do not need to install those tools globally.

## Python and `uv` setup

Once Python `3.13.x` and `uv` are available, install the project from the repository root:

```bash
uv sync
```

Start the server with a data directory of your choice:

```bash
WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py
```

The bind address, port, worker count, and webhook secret are read from `config.yaml`:

```13:16:entrypoint.py
_ip_bind = _root_config.get("ip-bind", "0.0.0.0")
_port = _root_config.get("port", 5000)
_max_workers = _root_config.get("max-workers", 10)
_webhook_secret = _root_config.get("webhook-secret")
```

> **Tip:** Put listener settings such as `ip-bind`, `port`, `max-workers`, and `webhook-secret` in `config.yaml`. The important environment variable for startup is `WEBHOOK_SERVER_DATA_DIR`.

## Prepare the data directory and config

The server always looks for `config.yaml` inside the data directory. If `WEBHOOK_SERVER_DATA_DIR` is not set, it defaults to `/home/podman/data`:

```20:33:webhook_server/libs/config.py
self.data_dir: str = os.environ.get("WEBHOOK_SERVER_DATA_DIR", "/home/podman/data")
self.config_path: str = os.path.join(self.data_dir, "config.yaml")
self.repository = repository
self.exists()
self.repositories_exists()

...

if not os.path.isfile(self.config_path):
    raise FileNotFoundError(f"Config file {self.config_path} not found")

...

if not self.root_data.get("repositories"):
    raise ValueError(f"Config {self.config_path} does not have `repositories`")
```

The GitHub App private key is also expected in the same directory, with this exact filename:

```413:418:webhook_server/utils/github_repository_settings.py
with open(os.path.join(config_.data_dir, "webhook-server.private-key.pem")) as fd:
    private_key = fd.read()

github_app_id: int = config_.root_data["github-app-id"]
auth: AppAuth = Auth.AppAuth(app_id=github_app_id, private_key=private_key)
```

Create a directory like this before first start:

```text
/path/to/data/
  config.yaml
  webhook-server.private-key.pem
  logs/
```

You only need to create `config.yaml` and `webhook-server.private-key.pem` yourself. The server creates the log directory and structured log files automatically:

```74:91:webhook_server/utils/structured_logger.py
self.log_dir = Path(self.config.data_dir) / "logs"

# Create log directory if it doesn't exist
self.log_dir.mkdir(parents=True, exist_ok=True)

...

date_str = date.strftime("%Y-%m-%d")
return self.log_dir / f"webhooks_{date_str}.json"
```

Relative log filenames are stored under `<data-dir>/logs`:

```141:147:webhook_server/utils/helpers.py
if log_file_name and not log_file_name.startswith("/"):
    log_file_path = os.path.join(config.data_dir, "logs")

    if not os.path.isdir(log_file_path):
        os.makedirs(log_file_path, exist_ok=True)
    return os.path.join(log_file_path, log_file_name)
```

Typical generated contents are:

- `logs/webhook-server.log`
- `logs/webhooks_YYYY-MM-DD.json`
- `logs/mcp_server.log` if MCP is enabled
- `logs/logs_server.log` if the log viewer is enabled
- `log-colors.json` in the data directory root when repository colors are first assigned

If you run the container image, mount your host data directory to `/home/podman/data`:

```5:6:examples/docker-compose.yaml
volumes:
  - "./webhook_server_data_dir:/home/podman/data:Z" # Should include config.yaml and webhook-server.private-key.pem
```

### GitHub credentials

A working install needs both of these:

- `github-app-id` in `config.yaml`, plus the matching private key in `webhook-server.private-key.pem`
- one or more GitHub tokens in `github-tokens`

From the shipped example config:

```12:17:examples/config.yaml
github-app-id: 123456 # GitHub app id
github-tokens:
  - <GITHIB TOKEN1>
  - <GITHIB TOKEN2>

webhook-ip: <HTTP://IP OR URL:PORT/webhook_server> # Full URL with path (e.g., https://your-domain.com/webhook_server or https://smee.io/your-channel)
```

Replace those placeholder values with your real credentials.

The `repositories` section uses the short repository name as the map key, and the full `owner/repo` string inside `name`:

```139:142:examples/config.yaml
repositories:
  my-repository:
    name: my-org/my-repository
    log-level: DEBUG # Override global log-level for repository
```

That means:

- the map key (`my-repository`) should match GitHub’s `repository.name`
- the `name` field must be the full `owner/repo`
- at least one repository entry is required

The server builds a client for every configured token and selects the one with the highest remaining rate limit:

```455:518:webhook_server/utils/helpers.py
apis_and_tokens: list[tuple[github.Github, str]] = []
tokens = config.get_value(value="github-tokens") or []

for _token in tokens:
    apis_and_tokens.append((github.Github(auth=github.Auth.Token(_token)), _token))

# ... choose the token with the highest remaining rate limit ...

if not _api_user or not api or not token:
    raise NoApiTokenError("Failed to get API with highest rate limit")
```

> **Warning:** A GitHub token alone is not enough. The server also reads `github-app-id` and `webhook-server.private-key.pem`, then requests the repository installation from GitHub. Make sure the GitHub App is installed on every repository listed in `config.yaml`.

> **Note:** `webhook-secret` is optional in code, but strongly recommended in any real deployment. If you set it, the server verifies GitHub’s webhook signature before queueing work.

> **Tip:** `webhook-ip` must be the full external URL GitHub can reach, including the `/webhook_server` path. For local testing, the example config explicitly allows a relay URL such as `https://smee.io/your-channel`.

Startup is active, not passive. Before serving requests, the application syncs repository settings and creates or updates webhooks for every configured repository:

```43:45:webhook_server/utils/github_repository_and_webhook_settings.py
await set_repositories_settings(config=config, apis_dict=apis_dict)
set_all_in_progress_check_runs_to_queued(repo_config=config, apis_dict=apis_dict)
create_webhook(config=config, apis_dict=apis_dict, secret=webhook_secret)
```

> **Warning:** Use credentials with enough permission to manage repository settings, branch protection, labels, hooks, and pull-request workflows. Read-only credentials are not enough for this server.

## Start and verify

Before first start, validate the config file:

```bash
uv run webhook_server/tests/test_schema_validator.py /path/to/data/config.yaml
```

Then start the server:

```bash
WEBHOOK_SERVER_DATA_DIR=/path/to/data uv run entrypoint.py
```

Verify that the health endpoint responds:

```bash
curl http://127.0.0.1:5000/webhook_server/healthcheck
```

A healthy server responds on `/webhook_server/healthcheck`, and if your credentials and `webhook-ip` are correct, startup will also sync repository settings and webhook configuration.

> **Warning:** If you enable `ENABLE_LOG_SERVER=true`, treat `/logs` as a trusted-network-only interface. It is intended for internal use, not public internet exposure.
