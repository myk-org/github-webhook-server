[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Container](https://img.shields.io/badge/Container-ghcr.io-red)](https://ghcr.io/myk-org/github-webhook-server)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)

# GitHub Webhook Server

A [FastAPI](https://fastapi.tiangolo.com)-based webhook server for automating GitHub repository management and pull request workflows.

[![Documentation](https://img.shields.io/badge/Documentation-blue?logo=readthedocs&logoColor=white)](https://myk-org.github.io/github-webhook-server/)

## Key Features

- **Pull Request Automation** — reviewer assignment, approval workflows, auto-merge, size labeling, and WIP/hold management
- **Cherry-Pick Workflows** — automated cherry-picks with AI-powered conflict resolution
- **Check Runs and Mergeability** — configurable status checks, verified labels, and merge-readiness evaluation
- **OWNERS-Based Permissions** — reviewer and approver assignment from OWNERS files with per-directory granularity
- **Container and PyPI Publishing** — automated container builds, tag-based releases, and PyPI publishing
- **Issue Comment Commands** — `/retest`, `/approve`, `/cherry-pick`, `/build-and-push-container`, and more
- **AI Features** — conventional commit title validation and suggestions via Claude, Gemini, or Cursor
- **Repository Bootstrap** — automatic label creation, branch protection, and webhook configuration on startup
- **Log Viewer** — real-time log streaming, webhook flow visualization, and structured log analysis
- **Multi-Token Support** — automatic GitHub token failover for rate limit resilience

## Getting Started

See the [documentation](https://myk-org.github.io/github-webhook-server/) for installation, configuration, and deployment guides.

```bash
# Quick start
uv sync
cp examples/config.yaml /path/to/data/config.yaml  # Edit with your settings
uv run entrypoint.py
```
