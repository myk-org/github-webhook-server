# Enable AI Features

Turn on AI help when you want pull requests to spend less maintainer time on bad titles, conflicted cherry-picks, and test selection. With the right settings, the server can suggest or fix PR titles, try to resolve cherry-pick conflicts, and generate test recommendations for pull requests.

## Prerequisites

- A running `github-webhook-server` deployment
- Access to `config.yaml` or a repository-local `.github-webhook-server.yaml`
- The provided container image, or another runtime that includes the bundled AI sidecar (no external AI CLIs required)
- A credential for your chosen `pi-sidecar` provider (for example, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, Google Cloud Vertex ADC, or the credential expected by another native or custom sidecar provider)

## Quick Example

```yaml
# config.yaml
repositories:
  my-repository:
    name: my-org/my-repository
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
    ai-features:
      ai-provider: "anthropic"
      ai-model: "claude-sonnet-4-6"
      conventional-title:
        enabled: true
        mode: suggest
      resolve-cherry-pick-conflicts-with-ai:
        enabled: true
    test-oracle:
      ai-provider: "anthropic"
      ai-model: "claude-sonnet-4-6"
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
  ai-provider: "anthropic"
  ai-model: "claude-sonnet-4-6"

test-oracle:
  ai-provider: "anthropic"
  ai-model: "claude-sonnet-4-6"

repositories:
  my-repository:
    name: my-org/my-repository
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
```

Use top-level `ai-features` and `test-oracle` when several repos should share the same defaults. Keep `conventional-title` on each repo that should enforce it, and use repo-level or `.github-webhook-server.yaml` values when one repo needs different AI behavior.

Both `ai-features` and `test-oracle` need `ai-provider` and `ai-model`. `ai-provider` accepts any non-empty native or custom provider identifier supported by the bundled `pi-sidecar` — including `anthropic` (native Anthropic API), `claude` (alias for `google-vertex-claude` on Google Cloud Vertex AI), `gemini` (alias for `google`), `google-vertex`, `openai`, or a sidecar-configured custom provider. Set `ai-model` to a real model ID accepted by that provider (for example, `claude-sonnet-4-6`, not shorthand names).

See [Configuration Reference](configuration-reference.html) for every supported key.

2. Add provider credentials and check that the sidecar is healthy

```yaml
services:
  github-webhook-server:
    environment:
      - ANTHROPIC_API_KEY=sk-ant-xxx
      # - GEMINI_API_KEY=xxx
      # - GOOGLE_CLOUD_PROJECT=my-gcp-project
      # - VERTEX_CLAUDE_1M=true
    # volumes:
    #   - $HOME/.config/gcloud:/home/podman/.config/gcloud:ro
```

```bash
curl -f http://localhost:5000/webhook_server/healthcheck
curl -f http://localhost:9100/health
```

Use the credential that matches your `ai-provider` value. The first command checks the webhook server, and the second checks the local AI sidecar; replace `9100` if you set `SIDECAR_PORT`.

| Provider | What to add |
| --- | --- |
| `anthropic` | `ANTHROPIC_API_KEY` (native Anthropic API) |
| `claude` (alias for `google-vertex-claude`) | Google Cloud Vertex ADC mount + `GOOGLE_CLOUD_PROJECT` (optional `VERTEX_CLAUDE_1M=true` for 1M context models) |
| `gemini` (alias for `google`) | `GEMINI_API_KEY` (native Google AI) |
| Other `pi-sidecar` native or custom providers | The credential, environment variable, or sidecar config required by that provider (for example, `OPENAI_API_KEY` or a custom provider's config) |

See [Environment Variables](environment-variables.html) for the full sidecar and credential list, and [Webhook and Health API](webhook-and-health-api.html) for the main server health endpoint.

3. Enable conventional title suggestions or auto-fixes

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    conventional-title: "feat,fix,build,chore,ci,docs,style,refactor,perf,test,revert"
    ai-features:
      ai-provider: "anthropic"
      ai-model: "claude-sonnet-4-6"
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
      ai-provider: "anthropic"
      ai-model: "claude-sonnet-4-6"
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
      ai-provider: "anthropic"
      ai-model: "claude-sonnet-4-6"
      test-patterns:
        - "tests/**/*.py"
      triggers:
        - approved
        # - pr-opened
        # - pr-synchronized
      timeout-minutes: 10
```

This block is configured separately from `ai-features`, and runs built-in using the local AI sidecar — no external service is needed. The oracle combines static test mapping with AI analysis and posts file-level review comments on the PR. You can tune the timeout with `timeout-minutes`, or append custom instructions to the built-in analysis prompt using `raw-prompt` or a `TESTS_ORACLE_PROMPT.md` file at the repository root. For security, `TESTS_ORACLE_PROMPT.md` is read from the repository's **base branch** (not the PR head), and `raw-prompt` overrides `TESTS_ORACLE_PROMPT.md` when both are set.

| Trigger | When it runs |
| --- | --- |
| `approved` | When an approver uses `/approve` in a PR comment or a submitted review |
| `pr-opened` | When a PR is opened |
| `pr-synchronized` | When new commits are pushed to a PR |

`/test-oracle` always works on demand when the feature is configured, regardless of the trigger list.

> **Note:** The automatic `approved` trigger means the project's `/approve` command, not a plain GitHub review approval.

6. Verify the result on a real PR

1. Open a new PR and check its welcome comment; enabled AI features are listed there with the provider and model in use.
2. Give the PR a non-conventional title and inspect the `conventional-title` check for `AI-Suggested Title` or `AI Auto-Fix Applied`.
3. From an account allowed to run comment commands, comment `/test-oracle`. If you enabled automatic triggers, also try `/approve` or push a new commit to exercise `approved` or `pr-synchronized`.
4. On the next cherry-pick that produces a merge conflict, confirm that the created PR gets `ai-resolved-conflicts` and a manual verification comment.

## Advanced Usage

```yaml
ai-features:
  ai-provider: "anthropic"
  ai-model: "claude-sonnet-4-6"
  conventional-title:
    enabled: true
    mode: suggest
    timeout-minutes: 5
  resolve-cherry-pick-conflicts-with-ai:
    enabled: true
    timeout-minutes: 20
```

Raise `timeout-minutes` when repos are large or conflicts are complicated. Title suggestions are usually quick, while cherry-pick resolution often benefits from a longer timeout.

To use Claude models on Google Cloud Vertex AI, set `ai-provider` to `"claude"` (alias for `"google-vertex-claude"`) and provide Vertex ADC credentials plus `GOOGLE_CLOUD_PROJECT`:

```yaml
# config.yaml
ai-features:
  ai-provider: "claude" # or "google-vertex-claude"
  ai-model: "claude-sonnet-4-6" # or "claude-opus-4-6[1m]" with VERTEX_CLAUDE_1M=true
```

```yaml
# docker-compose.yaml
services:
  github-webhook-server:
    environment:
      - GOOGLE_CLOUD_PROJECT=my-gcp-project
      - VERTEX_CLAUDE_1M=true
      # - GEMINI_API_KEY=xxx
      # - SIDECAR_PORT=9200
    volumes:
      - $HOME/.config/gcloud:/home/podman/.config/gcloud:ro
```

Set `GOOGLE_CLOUD_PROJECT` and mount your Google Cloud ADC directory for Vertex Claude models, add `VERTEX_CLAUDE_1M=true` if you use 1M context models, set `GEMINI_API_KEY` for `gemini` (`google`) models, and set `SIDECAR_PORT` if `9100` conflicts with another service.

> **Tip:** `/test-oracle` can still run on draft PRs, and it does not depend on the automatic trigger list. If you reopen a PR and want fresh recommendations, run `/test-oracle` manually.

## Troubleshooting

- **No AI suggestion appears in the title check.** Make sure the repo still has `conventional-title:` configured and `ai-features.conventional-title.enabled: true`. If `fix` mode does not rename the PR, open the failed check and look for `AI Auto-Fix Failed` or `AI Auto-Fix Skipped`.
- **The sidecar never becomes healthy.** If startup logs show `[sidecar] ERROR: sidecar failed to become healthy within 15s`, verify your provider credentials, any Google Cloud mount for Vertex, and that nothing else is using `SIDECAR_PORT`. If the sidecar is unavailable, local AI features will not run.
- **Test-oracle posts no recommendations.** Verify that the local AI sidecar is healthy (`curl -f http://localhost:9100/health`, using your `SIDECAR_PORT` if customized), confirm the provider credentials match `test-oracle.ai-provider`, and inspect the webhook server logs for analysis or timeout errors. Raise `test-oracle.timeout-minutes` if large PRs time out.
- **Cherry-picks still fall back to manual steps.** AI resolution only runs for real merge conflicts. Other cherry-pick errors still produce the normal manual instructions, and even successful AI resolutions must be reviewed by a human before merge.

## Related Pages

- [Configuration Reference](configuration-reference.html)
- [Environment Variables](environment-variables.html)
- [Webhook and Health API](webhook-and-health-api.html)
- [Run Pull Request Commands](run-pull-request-commands.html)
- [Manage Pull Requests](manage-pull-requests.html)
