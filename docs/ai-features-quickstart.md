# AI Features - Quick Start Guide

## Prerequisites

1. **Get a Google Gemini API Key**
   - Visit: https://makersuite.google.com/app/apikey
   - Create a new API key
   - Copy the key (starts with `AIza...`)

## Setup Steps

### Step 1: Set Environment Variable

Add your Gemini API key to your environment:

```bash
# Option 1: Add to your shell profile (~/.bashrc or ~/.zshrc)
export GEMINI_API_KEY="your-api-key-here"  # pragma: allowlist secret

# Option 2: Add to systemd service file (if running as service)
Environment="GEMINI_API_KEY=your-api-key-here"  # pragma: allowlist secret

# Option 3: Add to docker-compose.yml (if using Docker)
environment:
  - GEMINI_API_KEY=your-api-key-here  # pragma: allowlist secret
```

### Step 2: Update Your config.yaml

Add this to your main config file (e.g., `/home/podman/data/config.yaml`):

```yaml
# Add at the root level (same level as repositories, log-level, etc.)
ai-features:
  enabled: true
  provider: gemini

  gemini:
    api-key-env: GEMINI_API_KEY
    model: gemini-2.0-flash-latest
    temperature: 0.3
    max-tokens: 2000

  features:
    nlp-commands:
      enabled: true

    test-analysis:
      enabled: true

    smart-reviewers:
      enabled: true
```

### Step 3: Restart Webhook Server

```bash
# If running as systemd service
sudo systemctl restart github-webhook-server

# If running with uv
uv run entrypoint.py

# If running with Docker
docker-compose restart webhook-server
```

### Step 4: Verify Configuration

Check the logs for AI feature initialization:

```bash
# View logs
journalctl -u github-webhook-server -f

# Or if using docker
docker-compose logs -f webhook-server
```

You should see:
- No errors about missing GEMINI_API_KEY
- AI features being initialized when processing webhooks

## Per-Repository Override

To enable/disable AI features for a specific repository, add `.github-webhook-server.yaml` to the repository:

```yaml
# .github-webhook-server.yaml in your repository
ai-features:
  enabled: true

  features:
    nlp-commands:
      enabled: true  # Enable natural language commands

    test-analysis:
      enabled: false  # Disable test analysis for this repo

    smart-reviewers:
      enabled: true
```

## Testing AI Features

### Test 1: Natural Language Commands

Instead of `/lgtm`, try commenting on a PR:
- "Looks good to me"
- "This looks great, approved!"
- "LGTM!"

The AI should detect the intent and apply the LGTM label.

### Test 2: Test Failure Analysis

When a test fails, check the check run output. The AI should:
- Categorize the failure (FLAKY, REAL, INFRASTRUCTURE)
- Provide root cause analysis
- Suggest remediation steps

### Test 3: Smart Reviewer Suggestions

When opening a new PR, the AI should post a comment with:
- Top 3 recommended reviewers
- Explanation of why each reviewer is suggested
- Workload and expertise considerations

## Cost Monitoring

The AI features use Google Gemini which has costs. Monitor your usage:

```bash
# Check logs for cost tracking
grep "AI usage" /path/to/logs/*.log
```

**Estimated costs:**
- **Gemini 2.0 Flash:** $0.075 per 1M input tokens, $0.30 per 1M output tokens
- **Typical PR:** ~5,000 tokens = **$0.0007** (less than 1 cent)
- **1,000 PRs/month:** ~**$0.70/month**

Very affordable! 🎉

## Troubleshooting

### Issue: "GEMINI_API_KEY not found"

**Solution:** Make sure the environment variable is set:
```bash
echo $GEMINI_API_KEY
# Should print your API key
```

### Issue: "AI features not working"

**Checklist:**
1. ✅ Is `ai-features.enabled: true` in config.yaml?
2. ✅ Is the specific feature enabled (e.g., `nlp-commands.enabled: true`)?
3. ✅ Is GEMINI_API_KEY environment variable set?
4. ✅ Did you restart the webhook server?
5. ✅ Check logs for errors

### Issue: "Rate limit exceeded"

**Solution:** Adjust rate limits in config:
```yaml
ai-features:
  # ... existing config ...

  rate-limiting:
    requests-per-minute: 30  # Lower if hitting limits
    requests-per-hour: 500
```

## Advanced Configuration

### Custom Gemini Model

```yaml
ai-features:
  gemini:
    model: gemini-1.5-pro  # Use a different model
    temperature: 0.1  # More deterministic
    max-tokens: 4000  # Longer responses
```

### Feature-Specific Settings

```yaml
ai-features:
  features:
    nlp-commands:
      enabled: true
      confidence-threshold: 0.8  # Require 80% confidence

    test-analysis:
      enabled: true
      auto-retry-flaky: true  # Auto-retry detected flaky tests
```

## Next Steps

- See `examples/ai-features-config.yaml` for more configuration examples
- Check the main README for full feature documentation
- Monitor costs and adjust settings as needed

## Need Help?

- Check logs: `journalctl -u github-webhook-server -f`
- Review configuration schema: `webhook_server/config/schema.yaml`
- Report issues: https://github.com/myakove/github-webhook-server/issues
