# config.yaml
webhook-secret: your-random-secret-string
verify-github-ips: true
mask-sensitive-data: true

repositories:
  my-repository:
    name: my-org/my-repository
    security-checks:
      mandatory: true
      committer-identity-check: true
      trusted-committers:
        - "pre-commit-ci[bot]"
```

This gives you the safest baseline for direct GitHub delivery: signed webhooks, GitHub source-IP filtering, masked logs, and blocking committer checks on pull requests. If you do not set `suspicious-paths`, the server already protects common sensitive locations such as `.claude/`, `.vscode/`, `.cursor/`, `.devcontainer/`, `.pi/`, `.github/workflows/`, and `.github/actions/`.

## Step-by-step

1. Set a webhook secret on the server and in GitHub.

```yaml
webhook-secret: your-random-secret-string
```

Use the same value in your GitHub webhook or GitHub App callback settings and in `config.yaml`. If the server manages webhooks for you, it includes that secret when it creates the hook.

> **Warning:** If `webhook-secret` is unset, the server skips signature verification.


> **Tip:** See [Webhook and Health API](webhook-and-health-api.html) for the exact webhook callback path.

2. Turn on the right source-IP allowlist for your delivery path.

```yaml
verify-github-ips: true
# verify-cloudflare-ips: true
```

| Delivery path | Setting | Use it when |
| --- | --- | --- |
| GitHub connects directly to the server | `verify-github-ips: true` | GitHub is the client your app sees |
| Cloudflare proxies the webhook | `verify-cloudflare-ips: true` | Cloudflare is the client your app sees |
| You intentionally accept both paths | Set both to `true` | Either GitHub or Cloudflare may deliver the request |

When either allowlist is enabled, the server loads the current CIDR ranges during startup. If you enable IP verification and no valid ranges can be loaded, the service fails closed instead of starting insecurely.

> **Warning:** The allowlist check uses the client IP the app actually sees. If another proxy or load balancer sits in front of the server, you may end up validating the proxy IP instead of GitHub or Cloudflare.

3. Enable repository-level security checks.

```yaml
repositories:
  my-repository:
    name: my-org/my-repository
    security-checks:
      mandatory: true
      suspicious-paths:
        - ".github/workflows/"
        - ".github/actions/"
      committer-identity-check: true
      trusted-committers:
        - "pre-commit-ci[bot]"
```

Use `suspicious-paths` for path prefixes that should trigger extra review, and keep `committer-identity-check: true` so the last committer must match the pull request author unless that committer is explicitly trusted.

| Setting | Result |
| --- | --- |
| `mandatory: true` | Security checks block merge readiness until they pass or are overridden by a maintainer |
| `mandatory: false` | Security checks still run, but they do not block merge readiness |

> **Note:** `trusted-committers` is best for expected automation identities. The server already trusts the GitHub App bot, GitHub `web-flow`, and GitHub API users tied to your configured `github-tokens`, so you usually only need to add extra bots or org-specific service accounts.

4. Keep security-relevant data masked in logs.

```yaml
mask-sensitive-data: true
```

Leave masking enabled for normal operation. You can override it per repository, but only use that for short-lived debugging on a trusted system.

> **Warning:** Turning masking off can expose tokens, passwords, webhook URLs, and other secrets in log files.

5. Keep optional internal endpoints on trusted networks only.

```yaml
environment:
  - ENABLE_LOG_SERVER=true
  - ENABLE_MCP_SERVER=true
```

Only the literal string `true` enables either feature. If you turn them on, keep them behind a VPN or internal network, or put a reverse proxy with authentication and TLS in front of them.

> **Warning:** These feature flags do not add authentication by themselves. The safest public deployment is to leave them disabled unless you actively need them.

## Advanced Usage

Use a repository-local override when maintainers should own the security policy for one repository:

```yaml
# .github-webhook-server.yaml
security-checks:
  mandatory: false
  suspicious-paths:
    - ".github/workflows/"
    - ".github/actions/"
  committer-identity-check: true
  trusted-committers:
    - "pre-commit-ci[bot]"
```

That is useful when one team needs a different rollout pace than the server-wide default. See [Configure Repositories](configure-repositories.html) for when to keep a setting in central `config.yaml` versus the repository-local file.

If a mandatory security check blocks an expected automation flow, a maintainer can temporarily bypass it and later force a re-check:

```text
/security-override
/security-override cancel
```

Use this sparingly. The override is intended for reviewed exceptions, not for normal day-to-day merging. If you need to define who counts as a maintainer, see [Set Up OWNERS and Review Rules](set-up-owners-and-reviewers.html).

If you want to start with visibility before enforcement, turn on the checks but make them advisory first:

```yaml
security-checks:
  mandatory: false
  committer-identity-check: true
```

That lets teams see failures without blocking merges, then switch to `mandatory: true` once the trusted-committer list and suspicious path prefixes are settled.

## Troubleshooting

- **GitHub gets `403` immediately**
  - Check that `webhook-secret` matches on both sides.
  - Make sure GitHub is sending the `x-hub-signature-256` header.
  - If you recently rotated the secret, update GitHub too.

- **The server will not start after enabling IP verification**
  - Confirm the host can reach GitHub and/or Cloudflare to fetch allowlists.
  - Restart after fixing outbound network access.

- **A legitimate bot fails the committer check**
  - Add that login to `trusted-committers`.
  - Keep `committer-identity-check: true` enabled so only expected bots are exempted.

- **A pull request keeps failing on sensitive file checks**
  - Review whether the changed paths really belong in `suspicious-paths`.
  - Narrow the prefixes if they are too broad, or use a maintainer override for a one-off reviewed exception.

- **Optional internal endpoints are still reachable too broadly**
  - Do not rely on the feature flags alone.
  - Keep them on trusted networks, or add reverse-proxy authentication and TLS in front of them.

> **Tip:** See [Configuration Reference](configuration-reference.html) for the full list of security-related keys and defaults, and [Environment Variables](environment-variables.html) for startup flags that affect exposure.

## Related Pages

- [Webhook and Health API](webhook-and-health-api.html)
- [Configure Repositories](configure-repositories.html)
- [Environment Variables](environment-variables.html)
- [Configuration Reference](configuration-reference.html)
- [Set Up OWNERS and Review Rules](set-up-owners-and-reviewers.html)
