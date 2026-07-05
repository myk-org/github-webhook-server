Let me start by reading the exclusions file and pages manifest, then explore the codebase thoroughly.

Now let me check the examples/config.yaml for the full context of how slack-webhook-url sits in the config structure:

Now let me look at the schema definition more carefully for the `slack-webhook-url` field and check if it can be set globally:

Now let me check if slack-webhook-url is also defined at the global level or only at the repository level:

So `slack-webhook-url` is defined only within repository config (`additionalProperties` under `repositories`), not at global level. Let me confirm by checking the schema structure:

Let me check if there's a global-level `slack-webhook-url` in the schema:

Good. The `slack-webhook-url` field is only available at the per-repository level. Let me also look at how the per-repo config in `.github-webhook-server.yaml` can set it:

Now let me check what specific events trigger Slack notifications - the runner_handler container push section more broadly:

Let me also look at the test for notification_utils to understand error scenarios:

Now let me check how `mask-sensitive-data` handles slack URLs:

Now let me check if there's a `.github-webhook-server.yaml` schema definition that includes `slack-webhook-url`:

Now I have a thorough understanding of the Slack notification feature. Let me write the documentation.

# Setting Up Slack Notifications

Get notified in Slack when your PyPI packages are published and container images are pushed (or fail to push). This guide walks you through connecting a Slack incoming webhook to your repositories.

## Prerequisites

- A running github-webhook-server instance with at least one repository configured
- A Slack workspace where you can create incoming webhooks
- PyPI publishing or container builds configured for the repository (notifications only fire for these events)

## Quick Example

Add `slack-webhook-url` to your repository configuration in either `config.yaml` or `.github-webhook-server.yaml`:

```yaml
slack-webhook-url: https://slack-webhook-url/replace-with-your-webhook-url
```

That's it. Once configured, you'll receive Slack messages when:

- A Python package version is published to PyPI
- A container image is successfully pushed to a registry
- A container image push fails

## Step-by-Step Setup

### 1. Create a Slack Incoming Webhook

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps) and create a new app (or use an existing one).
2. Under **Incoming Webhooks**, toggle the feature on.
3. Click **Add New Webhook to Workspace** and select the channel where you want notifications.
4. Copy the webhook URL. It looks like:
   ```
   https://slack-webhook-url/replace-with-your-webhook-url
   ```

### 2. Add the Webhook URL to Your Repository

You have two options for where to place the configuration.

**Option A: Server-side in `config.yaml`**

Add `slack-webhook-url` under the repository entry:

```yaml
repositories:
  my-repo:
    name: my-org/my-repository
    slack-webhook-url: https://slack-webhook-url/replace-with-your-webhook-url
    pypi:
      token: <PYPI TOKEN>
    container:
      username: my-user
      password: my-password
      repository: quay.io/my-org/my-repo
      tag: latest
      release: true
```

**Option B: In-repository `.github-webhook-server.yaml`**

Place a `.github-webhook-server.yaml` file in the root of your GitHub repository:

```yaml
slack-webhook-url: https://slack-webhook-url/replace-with-your-webhook-url
```

> **Tip:** Values in `.github-webhook-server.yaml` take precedence over `config.yaml`. Use the in-repository file when different teams manage their own notification channels.

### 3. Verify It Works

Push a tag to a repository that has PyPI publishing configured. For example:

```bash
git tag v1.0.0
git push origin v1.0.0
```

If the PyPI upload succeeds, you'll see a message in your Slack channel like:

```
my-org/my-repository Version v1.0.0 published to PYPI.
```

## What Triggers Notifications

Slack notifications are not a general-purpose event stream — they fire only for specific release-related operations:

| Event | Notification sent? | Message content |
|---|---|---|
| PyPI package published successfully | ✅ Yes | `<repo> Version <tag> published to PYPI.` |
| Container image pushed successfully | ✅ Yes | `<repo> New container for <image:tag> published.` |
| Container image push failed | ✅ Yes | `<repo> Failed to build and push <image:tag>.` |
| PR opened/closed/merged | ❌ No | — |
| Check runs (tox, pre-commit) | ❌ No | — |
| Cherry-picks | ❌ No | — |
| Label changes | ❌ No | — |

> **Note:** If `slack-webhook-url` is not set for a repository, notifications are silently skipped. No errors are logged.

## Advanced Usage

### Different Webhook URLs Per Repository

Each repository can have its own Slack webhook URL pointing to a different channel. There is no global `slack-webhook-url` setting — you configure it per repository:

```yaml
repositories:
  frontend:
    name: my-org/frontend
    slack-webhook-url: https://slack-webhook-url/replace-with-your-webhook-url
    container:
      # ...

  backend:
    name: my-org/backend
    slack-webhook-url: https://slack-webhook-url/replace-with-your-webhook-url
    pypi:
      token: <TOKEN>
```

### Sensitive Data Masking

The webhook URL is treated as sensitive data. When `mask-sensitive-data` is enabled (the default), Slack webhook URLs are automatically redacted in log output. This prevents accidental exposure of the URL in logs.

> **Warning:** Do not set `mask-sensitive-data: false` in production. Your Slack webhook URL will appear in plaintext in logs. See [Configuration Reference](configuration-reference.html) for details on the masking behavior.

### Combining with Container and PyPI Workflows

Slack notifications work alongside container builds and PyPI publishing — not independently. You must configure at least one of these for the repository to receive any notifications:

- **PyPI publishing** requires a `pypi.token` in your config. See [Setting Up CI Checks](setting-up-ci-checks.html) for details.
- **Container push on release** requires a `container` block with `release: true`. See [Setting Up CI Checks](setting-up-ci-checks.html) for container build configuration.

A minimal config that enables both container push notifications and PyPI publish notifications:

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    slack-webhook-url: https://slack-webhook-url/replace-with-your-webhook-url
    pypi:
      token: <PYPI TOKEN>
    container:
      username: my-user
      password: my-password
      repository: quay.io/my-org/my-repo
      tag: latest
      release: true
```

## Troubleshooting

**No notifications appearing in Slack**

- Confirm `slack-webhook-url` is set at the repository level, not at the root of `config.yaml`. It is a per-repository setting only.
- Verify the event is one that triggers notifications (PyPI publish or container push). PR merges and check runs do not send Slack messages.
- Check that the triggering workflow completed — a PyPI upload that fails before finishing does not send a notification.

**Error: "Request to slack returned an error 401"**

The webhook URL is invalid or has been revoked. Generate a new incoming webhook in your Slack app settings and update the configuration.

**Error: "Request to slack returned an error 404"**

The webhook URL endpoint no longer exists. This typically happens when the Slack app or the webhook has been deleted. Recreate it in [Slack's API dashboard](https://api.slack.com/apps).

**Connection timeout errors**

The server uses a 10-second timeout for Slack webhook requests. If your network is slow or Slack is experiencing an outage, the notification will fail but the underlying operation (PyPI publish, container push) will still complete normally.

> **Tip:** Slack is a notification layer, not the source of truth. Your actual release state lives in GitHub, your container registry, and PyPI. A failed Slack notification does not mean the release failed.

## Related Pages

- [Configuring Repositories](configuring-repositories.html)
- [Setting Up CI Checks](setting-up-ci-checks.html)
- [Publishing Packages to PyPI](publishing-to-pypi.html)
- [Configuration Reference](configuration-reference.html)
- [Configuration Recipes](config-recipes.html)
