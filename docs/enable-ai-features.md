# config.yaml
repositories:
  my-repository:
    name: my-org/my-repository
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
    ai-features:
      ai-provider: "claude"
      ai-model: "sonnet"
      conventional-title:
        enabled: true
        mode: suggest
      resolve-cherry-pick-conflicts-with-ai:
        enabled: true
    test-oracle:
      server-url: "http://localhost:800"
      ai-provider: "claude"
      ai-model: "sonnet"
      triggers:
        - approved
```

```yaml
# docker-compose.yaml
services:
  github-webhook-server:
    environment:
      - ANTHROPIC_API_KEY=sk-ant-xxx
```

Save the config, then restart only if you changed environment variables or mounts. On the next PR event, the title check can show an AI suggestion, the next conflicted cherry-pick can attempt AI resolution, and users allowed to run comment commands can trigger `/test-oracle` on demand.

## Step-by-Step

1. Put shared defaults and repo rules in the right place

```yaml
ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"

test-oracle:
  server-url: "http://localhost:800"
  ai-provider: "claude"
  ai-model: "sonnet"

repositories:
  my-repository:
    name: my-org/my-repository
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
```

Use top-level `ai-features` and `test-oracle` when several repos should share the same defaults. Keep `conventional-title` on each repo that should enforce it, and use repo-level or `.github-webhook-server.yaml` values when one repo needs different AI behavior.

Both `ai-features` and `test-oracle` need `ai-provider` and `ai-model`. `test-oracle` also needs `server-url`.

See [Configuration Reference](configuration-reference.html) for every supported key.

2. Add provider credentials and check that the sidecar is healthy

```yaml
services:
  github-webhook-server:
    environment:
      - ANTHROPIC_API_KEY=sk-ant-xxx
      # - GEMINI_API_KEY=xxx
      # - CURSOR_API_KEY=xxx
      # - ACPX_AGENTS=cursor
```

```bash
curl -f http://localhost:500/webhook_server/healthcheck
curl -f http://localhost:910/health
```

Use the credential that matches your `ai-provider` value. The first command checks the webhook server, and the second checks the local AI sidecar; replace `910` if you set `SIDECAR_PORT`.

| Provider | What to add |
| --- | --- |
| `claude` | `ANTHROPIC_API_KEY` |
| `gemini` | `GEMINI_API_KEY` |
| `cursor` | `CURSOR_API_KEY` |

> **Tip:** Add `ACPX_AGENTS=cursor` when you want Cursor model discovery. The example deployment also supports `docker exec -it github-webhook-server agent` if you prefer Cursor interactive login instead of `CURSOR_API_KEY`.

See [Environment Variables](environment-variables.html) for the full sidecar and credential list, and [Webhook and Health API](webhook-and-health-api.html) for the main server health endpoint.

3. Enable conventional title suggestions or auto-fixes

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
    ai-features:
      ai-provider: "claude"
      ai-model: "sonnet"
      conventional-title:
        enabled: true
        mode: suggest
        timeout-minutes: 10
```

| Mode | Result |
| --- | --- |
| `suggest` | A failed `conventional-title` check includes an `AI-Suggested Title` block. |
| `fix` | The server edits the PR title and turns the check green if the suggestion validates. |

Open a PR with a non-matching title, or retitle an existing PR, to test the feature. If the repo does not have a `conventional-title` rule, this AI feature stays idle even if `ai-features` is present.

> **Tip:** Start with `suggest` so humans can review the wording before you move to `fix`.

4. Enable AI cherry-pick conflict resolution

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    ai-features:
      ai-provider: "claude"
      ai-model: "sonnet"
      resolve-cherry-pick-conflicts-with-ai:
        enabled: true
        timeout-minutes: 10
```

When the server performs a cherry-pick and Git reports a real `CONFLICT`, it can ask the sidecar to resolve the files and finish the cherry-pick automatically. If that succeeds, the new PR gets the `ai-resolved-conflicts` label and both PRs receive comments telling the assignee to verify the result manually.

> **Warning:** AI-resolved cherry-picks are never auto-verified, even if `auto-verify-cherry-picked-prs` is `true`.

If AI cannot resolve the conflict, or the cherry-pick failed for a non-conflict reason, the server falls back to the normal manual cherry-pick instructions.

5. Enable test-oracle recommendations

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    test-oracle:
      server-url: "http://localhost:800"
      ai-provider: "claude"
      ai-model: "sonnet"
      test-patterns:
        - "tests/**/*.py"
      triggers:
        - approved
        # - pr-opened
        # - pr-synchronized
```

This block is separate from `ai-features`. Conventional-title suggestions and cherry-pick AI use the local sidecar, but `test-oracle` calls the external service at `server-url`.

| Trigger | When it runs |
| --- | --- |
| `approved` | When an approver uses `/approve` in a PR comment or a submitted review |
| `pr-opened` | When a PR is opened |
| `pr-synchronized` | When new commits are pushed to a PR |

`/test-oracle` always works on demand when the feature is configured, regardless of the trigger list.

> **Note:** The automatic `approved` trigger means the project's `/approve` command, not a plain GitHub review approval.


> **Tip:** The webhook server checks `GET /health` on the oracle before each analysis, so `/test-oracle` is the fastest end-to-end setup check.

6. Verify the result on a real PR

1. Open a new PR and check its welcome comment; enabled AI features are listed there with the provider and model in use.
2. Give the PR a non-conventional title and inspect the `conventional-title` check for `AI-Suggested Title` or `AI Auto-Fix Applied`.
3. From an account allowed to run comment commands, comment `/test-oracle`. If you enabled automatic triggers, also try `/approve` or push a new commit to exercise `approved` or `pr-synchronized`.
4. On the next cherry-pick that produces a merge conflict, confirm that the created PR gets `ai-resolved-conflicts` and a manual verification comment.

## Advanced Usage

```yaml
ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"
  conventional-title:
    enabled: true
    mode: suggest
    timeout-minutes: 5
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
    timeout-minutes: 20
```

Raise `timeout-minutes` when repos are large or conflicts are complicated. Title suggestions are usually quick, while cherry-pick resolution often benefits from a longer timeout.

```yaml
services:
  github-webhook-server:
    environment:
      - CURSOR_API_KEY=xxx
      - ACPX_AGENTS=cursor
      # - VERTEX_CLAUDE_1M=true
      # - SIDECAR_PORT=920
    volumes:
      - $HOME/.config/gcloud:/home/podman/.config/gcloud:ro
```

Use `ACPX_AGENTS=cursor` when you want Cursor model discovery. Add `VERTEX_CLAUDE_1M=true` plus a Google Cloud credential mount when you want Vertex-hosted 1M Claude models, and set `SIDECAR_PORT` if `910` conflicts with another service.

> **Tip:** `/test-oracle` can still run on draft PRs, and it does not depend on the automatic trigger list. If you reopen a PR and want fresh recommendations, run `/test-oracle` manually.

## Troubleshooting

- **No AI suggestion appears in the title check.** Make sure the repo still has `conventional-title:` configured and `ai-features.conventional-title.enabled: true`. If `fix` mode does not rename the PR, open the failed check and look for `AI Auto-Fix Failed` or `AI Auto-Fix Skipped`.
- **The sidecar never becomes healthy.** If startup logs show `[sidecar] ERROR: sidecar failed to become healthy within 15s`, verify your provider credentials, any Google Cloud mount for Vertex, and that nothing else is using `SIDECAR_PORT`. If the sidecar is unavailable, local AI features will not run.
- **The oracle says it is not responding.** Make sure `server-url` is reachable from the webhook server and that `GET /health` returns `200`. Health-check failures are posted back to the PR; later analyze failures are logged on the server instead of commented back.
- **Cherry-picks still fall back to manual steps.** AI resolution only runs for real merge conflicts. Other cherry-pick errors still produce the normal manual instructions, and even successful AI resolutions must be reviewed by a human before merge.# Enable AI Features

Turn on AI help when you want pull requests to spend less maintainer time on bad titles, conflicted cherry-picks, and test selection. With the right settings, the server can suggest or fix PR titles, try to resolve cherry-pick conflicts, and ask a separate oracle service for test recommendations.

## Prerequisites

- A running `github-webhook-server` deployment
- Access to `config.yaml` or a repository-local `.github-webhook-server.yaml`
- The provided container image, or another runtime that includes the bundled AI sidecar and supported AI CLIs
- One supported provider credential for local AI features: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or `CURSOR_API_KEY`
- A reachable `pr-test-oracle` service if you want test recommendations

## Quick Example

```yaml
# config.yaml
repositories:
  my-repository:
    name: my-org/my-repository
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
    ai-features:
      ai-provider: "claude"
      ai-model: "sonnet"
      conventional-title:
        enabled: true
        mode: suggest
      resolve-cherry-pick-conflicts-with-ai:
        enabled: true
    test-oracle:
      server-url: "http://localhost:8000"
      ai-provider: "claude"
      ai-model: "sonnet"
      triggers:
        - approved
```

```yaml
# docker-compose.yaml
services:
  github-webhook-server:
    environment:
      - ANTHROPIC_API_KEY=sk-ant-xxx
```

Save the config, then restart only if you changed environment variables or mounts. On the next PR event, the title check can show an AI suggestion, the next conflicted cherry-pick can attempt AI resolution, and users allowed to run comment commands can trigger `/test-oracle` on demand.

## Step-by-Step

1. Put shared defaults and repo rules in the right place

```yaml
ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"

test-oracle:
  server-url: "http://localhost:8000"
  ai-provider: "claude"
  ai-model: "sonnet"

repositories:
  my-repository:
    name: my-org/my-repository
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
```

Use top-level `ai-features` and `test-oracle` when several repos should share the same defaults. Keep `conventional-title` on each repo that should enforce it, and use repo-level or `.github-webhook-server.yaml` values when one repo needs different AI behavior.

Both `ai-features` and `test-oracle` need `ai-provider` and `ai-model`. `test-oracle` also needs `server-url`.

See [Configuration Reference](configuration-reference.html) for every supported key.

2. Add provider credentials and check that the sidecar is healthy

```yaml
services:
  github-webhook-server:
    environment:
      - ANTHROPIC_API_KEY=sk-ant-xxx
      # - GEMINI_API_KEY=xxx
      # - CURSOR_API_KEY=xxx
      # - ACPX_AGENTS=cursor
```

```bash
curl -f http://localhost:5000/webhook_server/healthcheck
curl -f http://localhost:9100/health
```

Use the credential that matches your `ai-provider` value. The first command checks the webhook server, and the second checks the local AI sidecar; replace `9100` if you set `SIDECAR_PORT`.

| Provider | What to add |
| --- | --- |
| `claude` | `ANTHROPIC_API_KEY` |
| `gemini` | `GEMINI_API_KEY` |
| `cursor` | `CURSOR_API_KEY` |

> **Tip:** Add `ACPX_AGENTS=cursor` when you want Cursor model discovery. The example deployment also supports `docker exec -it github-webhook-server agent` if you prefer Cursor interactive login instead of `CURSOR_API_KEY`.

See [Environment Variables](environment-variables.html) for the full sidecar and credential list, and [Webhook and Health API](webhook-and-health-api.html) for the main server health endpoint.

3. Enable conventional title suggestions or auto-fixes

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
    ai-features:
      ai-provider: "claude"
      ai-model: "sonnet"
      conventional-title:
        enabled: true
        mode: suggest
        timeout-minutes: 10
```

| Mode | Result |
| --- | --- |
| `suggest` | A failed `conventional-title` check includes an `AI-Suggested Title` block. |
| `fix` | The server edits the PR title and turns the check green if the suggestion validates. |

Open a PR with a non-matching title, or retitle an existing PR, to test the feature. If the repo does not have a `conventional-title` rule, this AI feature stays idle even if `ai-features` is present.

> **Tip:** Start with `suggest` so humans can review the wording before you move to `fix`.

4. Enable AI cherry-pick conflict resolution

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    ai-features:
      ai-provider: "claude"
      ai-model: "sonnet"
      resolve-cherry-pick-conflicts-with-ai:
        enabled: true
        timeout-minutes: 10
```

When the server performs a cherry-pick and Git reports a real `CONFLICT`, it can ask the sidecar to resolve the files and finish the cherry-pick automatically. If that succeeds, the new PR gets the `ai-resolved-conflicts` label and both PRs receive comments telling the assignee to verify the result manually.

> **Warning:** AI-resolved cherry-picks are never auto-verified, even if `auto-verify-cherry-picked-prs` is `true`.

If AI cannot resolve the conflict, or the cherry-pick failed for a non-conflict reason, the server falls back to the normal manual cherry-pick instructions.

5. Enable test-oracle recommendations

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    test-oracle:
      server-url: "http://localhost:8000"
      ai-provider: "claude"
      ai-model: "sonnet"
      test-patterns:
        - "tests/**/*.py"
      triggers:
        - approved
        # - pr-opened
        # - pr-synchronized
```

This block is separate from `ai-features`. Conventional-title suggestions and cherry-pick AI use the local sidecar, but `test-oracle` calls the external service at `server-url`.

| Trigger | When it runs |
| --- | --- |
| `approved` | When an approver uses `/approve` in a PR comment or a submitted review |
| `pr-opened` | When a PR is opened |
| `pr-synchronized` | When new commits are pushed to a PR |

`/test-oracle` always works on demand when the feature is configured, regardless of the trigger list.

> **Note:** The automatic `approved` trigger means the project's `/approve` command, not a plain GitHub review approval.


> **Tip:** The webhook server checks `GET /health` on the oracle before each analysis, so `/test-oracle` is the fastest end-to-end setup check.

6. Verify the result on a real PR

1. Open a new PR and check its welcome comment; enabled AI features are listed there with the provider and model in use.
2. Give the PR a non-conventional title and inspect the `conventional-title` check for `AI-Suggested Title` or `AI Auto-Fix Applied`.
3. From an account allowed to run comment commands, comment `/test-oracle`. If you enabled automatic triggers, also try `/approve` or push a new commit to exercise `approved` or `pr-synchronized`.
4. On the next cherry-pick that produces a merge conflict, confirm that the created PR gets `ai-resolved-conflicts` and a manual verification comment.

## Advanced Usage

```yaml
ai-features:
  ai-provider: "claude"
  ai-model: "sonnet"
  conventional-title:
    enabled: true
    mode: suggest
    timeout-minutes: 5
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
    timeout-minutes: 20
```

Raise `timeout-minutes` when repos are large or conflicts are complicated. Title suggestions are usually quick, while cherry-pick resolution often benefits from a longer timeout.

```yaml
services:
  github-webhook-server:
    environment:
      - CURSOR_API_KEY=xxx
      - ACPX_AGENTS=cursor
      # - VERTEX_CLAUDE_1M=true
      # - SIDECAR_PORT=9200
    volumes:
      - $HOME/.config/gcloud:/home/podman/.config/gcloud:ro
```

Use `ACPX_AGENTS=cursor` when you want Cursor model discovery. Add `VERTEX_CLAUDE_1M=true` plus a Google Cloud credential mount when you want Vertex-hosted 1M Claude models, and set `SIDECAR_PORT` if `9100` conflicts with another service.

> **Tip:** `/test-oracle` can still run on draft PRs, and it does not depend on the automatic trigger list. If you reopen a PR and want fresh recommendations, run `/test-oracle` manually.

## Troubleshooting

- **No AI suggestion appears in the title check.** Make sure the repo still has `conventional-title:` configured and `ai-features.conventional-title.enabled: true`. If `fix` mode does not rename the PR, open the failed check and look for `AI Auto-Fix Failed` or `AI Auto-Fix Skipped`.
- **The sidecar never becomes healthy.** If startup logs show `[sidecar] ERROR: sidecar failed to become healthy within 15s`, verify your provider credentials, any Google Cloud mount for Vertex, and that nothing else is using `SIDECAR_PORT`. If the sidecar is unavailable, local AI features will not run.
- **The oracle says it is not responding.** Make sure `server-url` is reachable from the webhook server and that `GET /health` returns `200`. Health-check failures are posted back to the PR; later analyze failures are logged on the server instead of commented back.
- **Cherry-picks still fall back to manual steps.** AI resolution only runs for real merge conflicts. Other cherry-pick errors still produce the normal manual instructions, and even successful AI resolutions must be reviewed by a human before merge.

## Related Pages

- [Configuration Reference](configuration-reference.html)
- [Environment Variables](environment-variables.html)
- [Webhook and Health API](webhook-and-health-api.html)
- [Run Pull Request Commands](run-pull-request-commands.html)
- [Manage Pull Requests](manage-pull-requests.html)
