# Configuration Model

This guide explains how to configure the GitHub Webhook Server using the global configuration file and repository-specific overrides. By understanding the configuration precedence, you can set smart defaults for all your projects while customizing workflows for individual repositories.

## Prerequisites
* A running instance of the GitHub Webhook Server.
* Access to edit the global `config.yaml` on the server.
* Write permissions to the repositories you want to configure locally.

## Quick Example

The server reads configuration values in a specific order of precedence. This allows you to define global defaults, override them for a specific repository on the server side, and further customize them within the repository itself.

Here is how you might configure PyPI token access across different levels:

**1. Global Default (`config.yaml`)**
```yaml
pypi:
  token: "global-fallback-token"

repositories:
  "my-org/core-lib":
    pypi:
      token: "core-lib-specific-token"
```

**2. Repository Override (`.github-webhook-server.yaml` inside `my-org/core-lib`)**
```yaml
pypi:
  token: "developer-override-token"
```

When a webhook triggers for `my-org/core-lib`, the server evaluates the config and uses `"developer-override-token"` because the in-repository configuration wins.

## Precedence Order

When the server processes a webhook, it resolves configuration keys using the following priority (highest to lowest):

1. **Repository Local File (`.github-webhook-server.yaml`)**
   Configuration committed directly into the root of the target repository. This allows developers to self-serve configuration changes without needing access to the webhook server.

2. **Repository Server Config (`config.yaml` > `repositories`)**
   Configuration defined on the webhook server for a specific repository. Useful for injecting secrets or enforcing server-enforced overrides that repository developers shouldn't modify.

3. **Global Server Config (`config.yaml` root level)**
   The fallback configuration for all repositories managed by this server instance. Ideal for setting default labels, AI providers, and organization-wide rules.

## Step-by-Step Configuration

### Step 1: Set Global Defaults

Edit the `config.yaml` file located in your `WEBHOOK_SERVER_DATA_DIR` to set baseline behaviors for all repositories.

```yaml
labels:
  enabled-labels:
    - size
    - approved
    - verified

pull_requests:
  minimum-lgtm: 2
```

### Step 2: Configure Server-Side Overrides

To customize rules for a specific repository without committing a file to that repository, add a block under the `repositories` key in your `config.yaml`.

```yaml
repositories:
  "my-org/frontend-app":
    pull_requests:
      minimum-lgtm: 1  # Override the global default of 2
```

### Step 3: Enable Developer Self-Service

For settings that developers should control (like Docker registries or specific CI behaviors), create a `.github-webhook-server.yaml` file in the root of the target repository.

```yaml
docker:
  username: "frontend-deployer"
  registry: "ghcr.io"
```

> **Tip:** You do not need to duplicate settings. If you only specify `docker.username` in the local repository file, the server will fall back to the global `config.yaml` for any missing keys.

## Advanced Usage

### Injecting Secrets

You should never commit secrets to `.github-webhook-server.yaml`. Instead, define sensitive data in the server's global `config.yaml` using repository-specific blocks.

Since the server evaluates `repositories` block settings after local files (but before global defaults), you can securely manage tokens on the server:

```yaml
# config.yaml (Server-side)
repositories:
  "my-org/secure-service":
    pypi:
      token: "pypi-secret-token"
    github_tokens:
      - "ghp_secret_token_1"
```

Because the server handles the webhook, the local repository can still dictate *how* things are built via `.github-webhook-server.yaml`, but the authentication tokens remain securely isolated on the server.

### Dot Notation Evaluation

When the server fetches configuration values, it uses dot notation to merge and evaluate nested dictionaries. For example, if a handler requests `docker.username`, the server will check:
1. Local `.github-webhook-server.yaml` for `docker: { username: ... }`
2. Server `config.yaml` under `repositories: "org/repo": { docker: { username: ... } }`
3. Server `config.yaml` root for `docker: { username: ... }`

The first match found in this chain is returned immediately.

## Troubleshooting

* **Configuration ignored:** Ensure your in-repository file is named exactly `.github-webhook-server.yaml` and is placed in the root directory.
* **Typographical errors:** The server silently falls back to lower-priority configurations if a key is misspelled. Double-check your YAML indentation and key names.
* **Invalid YAML syntax:** If the repository's `.github-webhook-server.yaml` contains invalid YAML syntax, the webhook processing will log an error and fall back to the server-side configuration defaults. See the [Log Viewer Guide](log-viewer-guide.html) to inspect processing errors.

## Related Pages

- [Configuration Reference](configuration-reference.html)
- [Repository Overrides](repository-overrides.html)
- [Architecture and Event Flow](architecture-and-event-flow.html)
