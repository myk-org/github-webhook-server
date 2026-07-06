Let me start by reading the knowledge graph and understanding the repository structure.

Now let me explore the AI-related source files:

Now let me look at the runner handler for how AI features are actually used in cherry-picks and conventional titles:

Now let me look at how the welcome message displays AI features and understand the `auto-verify-cherry-picked-prs` interaction:

Now let me check the SIDECAR_PORT environment variable usage and the issue comment handler for `/test-oracle`:

Now let me check how the review handler triggers test oracle on approval:

Let me also check the cherry-pick flow in the runner handler to understand where AI conflict resolution fits:

Now let me look at what environment variables control sidecar/AI provider credentials:

Now let me look at the per-repo `.github-webhook-server.yaml` to understand how AI settings can be configured at repo level:

Now let me look at what the tool server does:

Let me check how Cursor interactive login works, since it was mentioned in docker-compose:

Let me also check what happens when test oracle posts its results (i.e., how the results are communicated to the user):

Now I have enough information to write the documentation. Let me compile everything:

# Enabling AI Features

Set up AI-powered automation for your webhook server to get conventional commit title suggestions on failing PR checks, automatically resolve cherry-pick merge conflicts, and analyze PRs for test coverage recommendations.

## Prerequisites

- A running webhook server instance (see [Getting Started](quickstart.html))
- **Docker deployment** (recommended): The container image ships with the AI sidecar pre-built
- An API key for at least one supported AI provider:

| Provider | Environment Variable | Notes |
|----------|---------------------|-------|
| Claude | `ANTHROPIC_API_KEY` | Direct API access |
| Claude (Vertex AI) | Google Cloud credentials + `VERTEX_CLAUDE_1M=true` | For 1M context window models |
| Gemini | `GEMINI_API_KEY` | Google Gemini CLI |
| Cursor | `CURSOR_API_KEY` or interactive login | API key or `docker exec -it github-webhook-server agent` |

## Quick Example

Add AI features to your `config.yaml` to start using AI-powered conventional title suggestions and cherry-pick conflict resolution:

```yaml
ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"
  conventional-title:
    enabled: true
    mode: suggest
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
```

Then pass your API key as an environment variable when starting the server:

```yaml
# docker-compose.yaml
environment:
  - ANTHROPIC_API_KEY=sk-ant-xxx
```

That's it — PRs that fail conventional title validation now show AI-suggested titles, and cherry-picks with merge conflicts are automatically resolved.

## Step 1: Configure the AI Provider

The `ai-features` block in `config.yaml` sets the AI provider and model used by the conventional title and cherry-pick conflict resolution features. Add it at the top level for all repositories, or inside a specific repository to override:

```yaml
# Global (applies to all repositories)
ai-features:
  ai-provider: "claude"       # claude | gemini | cursor
  ai-model: "sonnet"          # Model identifier (e.g., sonnet, gemini-2.5-pro)
```

```yaml
# Per-repository override
repositories:
  my-repo:
    name: my-org/my-repo
    ai-features:
      ai-provider: "gemini"
      ai-model: "gemini-2.5-pro"
      conventional-title:
        enabled: true
        mode: fix
```

> **Note:** The `ai-provider` and `ai-model` fields are required whenever `ai-features` is present. The `conventional-title` and `resolve-cherry-pick-conflicts-with-ai` sub-keys are optional.

## Step 2: Set Up API Credentials

Pass the appropriate environment variable for your chosen provider. In Docker Compose:

```yaml
services:
  github-webhook-server:
    environment:
      # Pick one (or more if using different providers per repo):
      - ANTHROPIC_API_KEY=sk-ant-xxx         # Claude
      - GEMINI_API_KEY=xxx                    # Gemini
      - CURSOR_API_KEY=xxx                    # Cursor (API key method)
      # Optional: Enable Cursor model discovery
      # - ACPX_AGENTS=cursor
      # Optional: Enable Claude 1M context window via Vertex AI
      # - VERTEX_CLAUDE_1M=true
```

For Vertex AI (Claude via Google Cloud), mount your credentials into the container:

```yaml
volumes:
  - $HOME/.config/gcloud:/home/podman/.config/gcloud:ro
```

> **Note:** For Cursor interactive login (instead of API key), exec into the running container: `docker exec -it github-webhook-server agent`


> **Warning:** Never commit API keys to your repository. Use environment variables or a secrets manager. See [Environment Variables](environment-variables.html) for all available settings.

## Step 3: Enable Conventional Title Suggestions

When your repository enforces conventional commit PR titles, AI can suggest or auto-fix titles that fail validation. This requires both `conventional-title` under `ai-features` **and** the `conventional-title` setting on the repository. See [Setting Up CI Checks](setting-up-ci-checks.html) for configuring conventional commit validation.

```yaml
ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"
  conventional-title:
    enabled: true
    mode: suggest            # Show suggestion in check run output
    timeout-minutes: 10      # Optional (default: 10)

repositories:
  my-repo:
    name: my-org/my-repo
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
```

There are two modes:

| Mode | Behavior |
|------|----------|
| `suggest` | When the PR title fails validation, the check run output includes an AI-suggested title. The author copies it manually. |
| `fix` | The PR title is automatically updated to the AI suggestion. The check run re-evaluates and passes. |

In **suggest** mode, the check run output includes a section like:

```
### AI-Suggested Title

> feat(auth): add OAuth2 login support
```

In **fix** mode, the PR title is updated silently and a success message confirms the change.

> **Tip:** Start with `suggest` mode to review AI suggestions before trusting `fix` to auto-update titles.

## Step 4: Enable AI Cherry-Pick Conflict Resolution

When a PR is merged and cherry-picked to another branch, merge conflicts sometimes occur. With this feature enabled, the AI automatically resolves conflicts — preserving the intent of the original commit on the target branch.

```yaml
ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
    timeout-minutes: 10      # Optional (default: 10)
```

When a cherry-pick encounters a `CONFLICT`:

1. The AI inspects the original commit, its diff, and the conflicted files
2. It edits the conflicted files to resolve the merge
3. The resolved files are staged and the cherry-pick is finalized
4. The cherry-pick PR is created with an `ai-resolved-conflicts` label
5. A comment is posted on both the original PR and the cherry-pick PR requesting manual review

If AI resolution fails, the server falls back to posting manual cherry-pick instructions (the same behavior as when AI is disabled).

> **Warning:** AI-resolved cherry-picks are **never auto-verified and never auto-merged**, even when `auto-verify-cherry-picked-prs` is `true`. The `ai-resolved-conflicts` label ensures a human reviews the changes. See [Cherry-Picking and Branch Protection](cherry-picking-and-branching.html) for more on cherry-pick workflows.

## Step 5: Set Up the PR Test Oracle

The Test Oracle is a separate feature from `ai-features` — it has its own configuration block. It integrates with the [pr-test-oracle](https://github.com/myk-org/pr-test-oracle) server to analyze PR diffs and recommend which tests to run.

```yaml
test-oracle:
  server-url: "http://localhost:8000"    # URL of your pr-test-oracle instance
  ai-provider: "claude"                  # claude | gemini | cursor
  ai-model: "sonnet"
  test-patterns:                         # Optional — oracle has defaults
    - "tests/**/*.py"
  triggers:                              # Optional (default: [approved])
    - approved                           # Run when /approve command is used
    # - pr-opened                        # Run when a new PR is opened
    # - pr-synchronized                  # Run when new commits are pushed
```

The Test Oracle can be configured globally or per repository:

```yaml
repositories:
  my-repo:
    name: my-org/my-repo
    test-oracle:
      server-url: "http://localhost:8000"
      ai-provider: "claude"
      ai-model: "sonnet"
      triggers:
        - approved
        - pr-opened
```

### Test Oracle Triggers

| Trigger | When It Fires |
|---------|---------------|
| `approved` | When a maintainer uses the `/approve` command on a PR |
| `pr-opened` | When a new PR is opened |
| `pr-synchronized` | When new commits are pushed to an existing PR |

> **Tip:** The `/test-oracle` comment command works anytime on any PR, regardless of configured triggers. Triggers only control *automatic* analysis.

### Deploying the Test Oracle Server

The Test Oracle requires a running instance of [pr-test-oracle](https://github.com/myk-org/pr-test-oracle). Follow its setup instructions, then point `server-url` to your instance. The webhook server performs a health check before each analysis request and posts a comment if the oracle is unreachable.

## Verifying the Setup

After configuring AI features, verify they're working:

1. **Check sidecar health** — The container health check includes the sidecar:
   ```
   curl -f http://localhost:9100/health
   ```

2. **Open a PR with a bad title** — If you have conventional title enforcement enabled with AI in `suggest` or `fix` mode, the check run output should include an AI suggestion or auto-fix.

3. **Trigger a cherry-pick with conflicts** — Merge a PR with cherry-pick targets where you know conflicts exist. The AI should attempt resolution and the resulting cherry-pick PR should carry the `ai-resolved-conflicts` label.

4. **Run `/test-oracle`** — Comment `/test-oracle` on any PR to trigger an on-demand analysis.

## Advanced Usage

### Combining Features

All three AI features are independent and can be enabled in any combination:

```yaml
ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"
  conventional-title:
    enabled: true
    mode: fix
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
    timeout-minutes: 15

test-oracle:
  server-url: "http://oracle.internal:8000"
  ai-provider: "gemini"
  ai-model: "gemini-2.5-pro"
  triggers:
    - approved
    - pr-opened
    - pr-synchronized
```

> **Note:** The Test Oracle can use a different AI provider and model than `ai-features`. Each is configured independently.

### Using Different Providers Per Repository

Override the global AI configuration for specific repositories:

```yaml
ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"
  conventional-title:
    enabled: true
    mode: suggest

repositories:
  critical-repo:
    name: my-org/critical-repo
    ai-features:
      ai-provider: "claude"
      ai-model: "claude-opus-4-6-1m"
      conventional-title:
        enabled: true
        mode: fix
        timeout-minutes: 15
      resolve-cherry-pick-conflicts-with-ai:
        enabled: true
        timeout-minutes: 20
```

### Adjusting Timeouts

Both conventional title and cherry-pick resolution support `timeout-minutes` (default: 10). Increase this for large repositories or complex conflicts:

```yaml
ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"
  conventional-title:
    enabled: true
    mode: suggest
    timeout-minutes: 5       # Title suggestions are quick
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
    timeout-minutes: 20      # Conflict resolution may take longer
```

### Sidecar Port Configuration

The AI sidecar runs on port 9100 by default. Change it with the `SIDECAR_PORT` environment variable:

```yaml
environment:
  - SIDECAR_PORT=9200
```

The container health check automatically uses the configured port.

### Welcome Message Integration

When AI features are configured, the PR welcome comment includes an **AI Features** section summarizing what's active:

- **Conventional Title**: Mode and provider/model
- **Cherry-Pick Conflict Resolution**: Whether enabled and provider/model
- **Test Oracle**: Configured triggers and the `/test-oracle` command availability

This helps PR authors understand what AI automation is in play. See [Managing Pull Requests](managing-pull-requests.html) for more on welcome messages.

## Troubleshooting

**Sidecar health check fails on startup:**
The entrypoint script waits up to 15 seconds for the sidecar to become healthy. If it fails, you'll see `ERROR: sidecar failed to become healthy within 15s — AI features will not work`. Check that:
- The AI provider API key environment variable is set correctly
- The `SIDECAR_PORT` isn't conflicting with another service
- Container logs show the sidecar started without errors

**AI title suggestion returns nothing:**
- Verify `conventional-title` has `enabled: true` under `ai-features`
- Ensure the repository also has `conventional-title` configured with allowed commit types
- Check server logs for timeout or API errors

**Cherry-pick AI resolution falls back to manual:**
- Look for log messages containing "AI conflict resolution failed" for details
- The AI only attempts resolution for actual `CONFLICT` markers — other cherry-pick failures skip AI
- If the sidecar is unavailable, it returns immediately with a fallback

**Test Oracle says "server is not responding":**
- Verify the `server-url` is reachable from the webhook server container
- The oracle server must respond to `GET /health` within 5 seconds
- Check that the [pr-test-oracle](https://github.com/myk-org/pr-test-oracle) service is running

**AI-resolved cherry-pick won't auto-merge:**
This is by design. Cherry-picks with the `ai-resolved-conflicts` label are never auto-merged or auto-verified, regardless of other settings. A human must review and manually verify the PR.

## Related Pages

- [Cherry-Picking and Branch Protection](cherry-picking-and-branching.html)
- [Setting Up CI Checks](setting-up-ci-checks.html)
- [Environment Variables](environment-variables.html)
- [Configuring Repositories](configuring-repositories.html)
- [Managing Pull Requests](managing-pull-requests.html)
