[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Container](https://img.shields.io/badge/Container-quay.io-red)](https://quay.io/repository/myakove/github-webhook-server)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)

A comprehensive [FastAPI-based](https://fastapi.tiangolo.com) webhook server for automating GitHub repository management and pull request workflows.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Prerequisites](#prerequisites)
- [Examples](#examples)
- [Installation](#installation)
- [Configuration](#configuration)
- [Configuration Validation](#configuration-validation)
- [Deployment](#deployment)
- [Usage](#usage)
- [API Reference](#api-reference)
- [Log Viewer](#log-viewer)
- [User Commands](#user-commands)
- [OWNERS File Format](#owners-file-format)
- [Security](#security)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## Overview

GitHub Webhook Server is an enterprise-grade automation platform that streamlines GitHub repository management through intelligent webhook processing. It provides comprehensive pull request workflow automation, branch protection management, and seamless CI/CD integration.

### Architecture

```
GitHub Events → Webhook Server → Repository Management
                      ↓
              ┌─────────────────┐
              │  FastAPI Server │
              └─────────────────┘
                      ↓
              ┌─────────────────┐
              │ Webhook Handler │
              └─────────────────┘
                      ↓
    ┌─────────────────────────────────────┐
    │           Automation                │
    ├─────────────────────────────────────┤
    │ • Pull Request Management          │
    │ • Branch Protection                │
    │ • Container Building               │
    │ • PyPI Publishing                  │
    │ • Code Review Automation           │
    └─────────────────────────────────────┘
```

## Features

### 🔧 Repository Management

- **Automated repository setup** with branch protection rules
- **Label management** with automatic creation of missing labels
- **Webhook configuration** with automatic setup and validation
- **Multi-repository support** with centralized configuration

### 📋 Pull Request Automation

- **Intelligent reviewer assignment** based on OWNERS files
- **Automated labeling** including size calculation and status tracking
- **Configurable PR size labels** with custom names, thresholds, and colors
- **Merge readiness validation** with comprehensive checks
- **Issue tracking** with automatic creation and lifecycle management

### 🚀 CI/CD Integration

- **Container building and publishing** with multi-registry support
- **PyPI package publishing** for Python projects
- **Tox testing integration** with configurable test environments
- **Pre-commit hook validation** for code quality assurance

### 👥 User Commands

- **Interactive PR management** through comment-based commands
- **Cherry-pick automation** across multiple branches
- **Manual test triggering** for specific components
- **Review process automation** with approval workflows

### 🔒 Security & Compliance

- **IP allowlist validation** for GitHub and Cloudflare
- **Webhook signature verification** to prevent unauthorized access
- **Token rotation support** with automatic failover
- **SSL/TLS configuration** with customizable warning controls

## Prerequisites

- **Python 3.12+**
- **GitHub App** with appropriate permissions
- **GitHub Personal Access Tokens** with admin rights to repositories
- **Container runtime** (Podman/Docker) for containerized deployment
- **Network access** to GitHub API and webhook endpoints

### GitHub App Permissions

Your GitHub App requires the following permissions:

- **Repository permissions:**

  - `Contents`: Read & Write
  - `Issues`: Read & Write
  - `Pull requests`: Read & Write
  - `Checks`: Read & Write
  - `Metadata`: Read
  - `Administration`: Read & Write (for branch protection)

- **Organization permissions:**

  - `Members`: Read (for OWNERS validation)

- **Events:**
  - `Push`, `Pull request`, `Issue comment`, `Check run`, `Pull request review`

## Examples

The [`examples/`](examples/) directory contains comprehensive configuration examples to help you get started:

| File                                                                  | Description                                                      |
| --------------------------------------------------------------------- | ---------------------------------------------------------------- |
| [`config.yaml`](examples/config.yaml)                                 | Complete webhook server configuration with all available options |
| [`docker-compose.yaml`](examples/docker-compose.yaml)                 | Docker Compose deployment configuration                          |
| [`.github-webhook-server.yaml`](examples/.github-webhook-server.yaml) | Repository-specific configuration template                       |

These examples demonstrate:

- 🔧 **Server configuration** with security settings
- 🏗️ **Multi-repository setup** with different features per repo
- 🐳 **Container deployment** configurations
- 📝 **Repository-specific overrides** using `.github-webhook-server.yaml`

## Installation

### Using Pre-built Container (Recommended)

```bash
# Pull the latest stable release
podman pull quay.io/myakove/github-webhook-server:latest

# Or using Docker
docker pull quay.io/myakove/github-webhook-server:latest
```

### Building from Source

```bash
# Clone the repository
git clone https://github.com/myakove/github-webhook-server.git
cd github-webhook-server

# Build with Podman
podman build --format docker -t github-webhook-server .

# Or with Docker
docker build -t github-webhook-server .
```

### Local Development

```bash
# Install dependencies using uv (recommended)
uv sync

# Or using pip
pip install -e .

# Run the development server
uv run entrypoint.py
```

## Configuration

### Environment Variables

| Variable                  | Description                      | Default             | Required    |
| ------------------------- | -------------------------------- | ------------------- | ----------- |
| `WEBHOOK_SERVER_DATA_DIR` | Directory containing config.yaml | `/home/podman/data` | Yes         |
| `WEBHOOK_SERVER_IP_BIND`  | IP address to bind server        | `0.0.0.0`           | No          |
| `WEBHOOK_SERVER_PORT`     | Port to bind server              | `5000`              | No          |
| `MAX_WORKERS`             | Maximum number of workers        | `10`                | No          |
| `WEBHOOK_SECRET`          | GitHub webhook secret            | -                   | Recommended |
| `VERIFY_GITHUB_IPS`       | Verify GitHub IP addresses       | `false`             | No          |
| `VERIFY_CLOUDFLARE_IPS`   | Verify Cloudflare IP addresses   | `false`             | No          |

### Minimal Configuration

Create `config.yaml` in your data directory:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/myk-org/github-webhook-server/refs/heads/main/webhook_server/config/schema.yaml

github-app-id: 123456
webhook-ip: https://your-domain.com
github-tokens:
  - ghp_your_github_token

repositories:
  my-repository:
    name: my-org/my-repository
    protected-branches:
      main: []
```

### Advanced Configuration

```yaml
# Server Configuration
ip-bind: "0.0.0.0"
port: 5000
max-workers: 20
log-level: INFO
log-file: webhook-server.log

# Security Configuration
webhook-secret: "your-webhook-secret" # pragma: allowlist secret
verify-github-ips: true
verify-cloudflare-ips: true
disable-ssl-warnings: false

# Global Defaults
default-status-checks:
  - "WIP"
  - "can-be-merged"
  - "build"

auto-verified-and-merged-users:
  - "renovate[bot]"
  - "dependabot[bot]"

# Global PR Size Labels (optional)
pr-size-thresholds:
  Tiny:
    threshold: 10
    color: lightgray
  Small:
    threshold: 50
    color: green
  Medium:
    threshold: 150
    color: orange
  Large:
    threshold: 300
    color: red

# Threshold rules: PRs with changes ≥ threshold and < next-threshold get that label

# Docker Registry Access
docker:
  username: your-docker-username
  password: your-docker-password

# Repository Configuration
repositories:
  my-project:
    name: my-org/my-project
    log-level: DEBUG
    slack-webhook-url: https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK

    # CI/CD Features
    verified-job: true
    pre-commit: true

    # Testing Configuration
    tox:
      main: all
      develop: unit,integration
    tox-python-version: "3.12"

    # Container Configuration
    container:
      username: registry-user
      password: registry-password
      repository: quay.io/my-org/my-project
      tag: latest
      release: true
      build-args:
        - BUILD_VERSION=1.0.0
      args:
        - --no-cache

    # PyPI Publishing
    pypi:
      token: pypi-token

    # Pull Request Settings
    minimum-lgtm: 2
    conventional-title: "feat,fix,docs,refactor,test"
    can-be-merged-required-labels:
      - "approved"

    # Repository-specific PR Size Labels (see global example above; values override at repository level)
    pr-size-thresholds:
      Express:
        threshold: 25
        color: lightblue
      Standard:
        threshold: 100
        color: green

    # Branch Protection
    protected-branches:
      main:
        include-runs:
          - "test"
          - "build"
        exclude-runs:
          - "optional-check"
      develop: []

    # Automation
    set-auto-merge-prs:
      - main
    auto-verified-and-merged-users:
      - "trusted-bot[bot]"
```

### Configurable PR Size Labels

The webhook server supports configurable pull request size labels with custom names,
thresholds, and colors. This feature allows repository administrators to define
their own categorization system.

#### Configuration Options

```yaml
# Global configuration (applies to all repositories)
pr-size-thresholds:
  Tiny:
    threshold: 10     # Required: positive integer (lines changed)
    color: lightgray  # Optional: CSS3 color name, defaults to lightgray
  Small:
    threshold: 50
    color: green
  Medium:
    threshold: 150
    color: orange
  Large:
    threshold: 300
    color: red

# Repository-specific configuration (overrides global)
repositories:
  my-project:
    name: my-org/my-project
    pr-size-thresholds:
      Express:
        threshold: 25
        color: lightblue
      Standard:
        threshold: 100
        color: green
      Premium:
        threshold: 500
        color: orange
```

#### Configuration Rules

- **threshold**: Required positive integer representing total lines changed
  (additions + deletions)
- **color**: Optional CSS3 color name
  (e.g., `red`, `green`, `orange`, `lightblue`, `darkred`)
- **Label Names**: Any string (e.g., `Tiny`, `Express`, `Premium`, `Critical`)
- **Hierarchy**: Repository-level configuration overrides global configuration
- **Fallback**: If no custom configuration is provided, uses default static labels
  (XS, S, M, L, XL, XXL)

#### Supported Color Names

Any valid CSS3 color name is supported, including:

- Basic colors: `red`, `green`, `blue`, `orange`, `yellow`, `purple`
- Extended colors: `lightgray`, `darkred`, `lightblue`, `darkorange`
- Grayscale: `black`, `white`, `gray`, `lightgray`, `darkgray`

Invalid color names automatically fall back to `lightgray`.

#### Real-time Updates

Configuration changes take effect immediately without server restart. The webhook
server re-reads configuration for each incoming webhook event.

### Repository-Level Overrides

Create `.github-webhook-server.yaml` in your repository root to override or extend the global configuration for that specific repository. This file supports all repository-level configuration options.

**Simple Example:**

```yaml
# Basic repository-specific settings
minimum-lgtm: 1
can-be-merged-required-labels:
  - "ready-to-merge"
tox:
  main: all
  feature: unit
set-auto-merge-prs:
  - develop
pre-commit: true
conventional-title: "feat,fix,docs"

# Custom PR size labels for this repository
pr-size-thresholds:
  Quick:
    threshold: 20
    color: lightgreen
  Normal:
    threshold: 100
    color: green
  Complex:
    threshold: 300
    color: orange
```

For a comprehensive example showing all available options, see [`examples/.github-webhook-server.yaml`](examples/.github-webhook-server.yaml).

**Key Benefits:**

- 🎯 **Repository-specific settings** without modifying global config
- 🔧 **Per-project customization** of CI/CD behavior
- 📝 **Version-controlled configuration** alongside your code
- 🚀 **Zero-downtime updates** to repository settings

## Configuration Validation

### Schema Validation

The webhook server includes comprehensive configuration validation with JSON Schema support for IDE autocompletion and validation.

#### Validate Configuration Files

```bash
# Validate your configuration
uv run webhook_server/tests/test_schema_validator.py config.yaml

# Validate example configuration
uv run webhook_server/tests/test_schema_validator.py examples/config.yaml
```

#### Validation Features

- ✅ **Required fields validation** - Ensures all mandatory fields are present
- ✅ **Type checking** - Validates strings, integers, booleans, arrays, and objects
- ✅ **Enum validation** - Checks valid values for restricted fields
- ✅ **Structure validation** - Verifies complex object configurations
- ✅ **Cross-field validation** - Ensures configuration consistency

#### Running Configuration Tests

```bash
# Run all configuration schema tests
uv run pytest webhook_server/tests/test_config_schema.py -v

# Run specific validation test
uv run pytest webhook_server/tests/test_config_schema.py::TestConfigSchema::test_valid_full_config_loads -v
```

### Configuration Reference

#### Root Level Options

| Category     | Options                                                                                                             |
| ------------ | ------------------------------------------------------------------------------------------------------------------- |
| **Server**   | `ip-bind`, `port`, `max-workers`, `log-level`, `log-file`                                                           |
| **Security** | `webhook-secret`, `verify-github-ips`, `verify-cloudflare-ips`, `disable-ssl-warnings`                              |
| **GitHub**   | `github-app-id`, `github-tokens`, `webhook-ip`                                                                      |
| **Defaults** | `docker`, `default-status-checks`, `auto-verified-and-merged-users`, `branch-protection`, `create-issue-for-new-pr` |

#### Repository Level Options

| Category          | Options                                                                                          |
| ----------------- | ------------------------------------------------------------------------------------------------ |
| **Basic**         | `name`, `log-level`, `log-file`, `slack-webhook-url`, `events`                                   |
| **Features**      | `verified-job`, `pre-commit`, `pypi`, `tox`, `container`                                         |
| **Pull Requests** | `minimum-lgtm`, `conventional-title`, `can-be-merged-required-labels`, `create-issue-for-new-pr` |
| **Automation**    | `set-auto-merge-prs`, `auto-verified-and-merged-users`                                           |
| **Protection**    | `protected-branches`, `branch-protection`                                                        |

## Deployment

### Docker Compose (Recommended)

```yaml
version: "3.8"
services:
  github-webhook-server:
    image: quay.io/myakove/github-webhook-server:latest
    container_name: github-webhook-server
    ports:
      - "5000:5000"
    volumes:
      - "./webhook_server_data:/home/podman/data:Z"
    environment:
      - WEBHOOK_SERVER_DATA_DIR=/home/podman/data
      - WEBHOOK_SECRET=your-webhook-secret
      - VERIFY_GITHUB_IPS=1
      - VERIFY_CLOUDFLARE_IPS=1
    healthcheck:
      test:
        [
          "CMD",
          "curl",
          "--fail",
          "http://localhost:5000/webhook_server/healthcheck",
        ]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    restart: unless-stopped
    privileged: true # Required for container building
```

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: github-webhook-server
spec:
  replicas: 2
  selector:
    matchLabels:
      app: github-webhook-server
  template:
    metadata:
      labels:
        app: github-webhook-server
    spec:
      containers:
        - name: webhook-server
          image: quay.io/myakove/github-webhook-server:latest
          ports:
            - containerPort: 5000
          env:
            - name: WEBHOOK_SERVER_DATA_DIR
              value: "/data"
            - name: WEBHOOK_SECRET
              valueFrom:
                secretKeyRef:
                  name: webhook-secret
                  key: secret
          volumeMounts:
            - name: config-volume
              mountPath: /data
          livenessProbe:
            httpGet:
              path: /webhook_server/healthcheck
              port: 5000
            initialDelaySeconds: 30
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /webhook_server/healthcheck
              port: 5000
            initialDelaySeconds: 5
            periodSeconds: 10
      volumes:
        - name: config-volume
          configMap:
            name: webhook-config
---
apiVersion: v1
kind: Service
metadata:
  name: github-webhook-server-service
spec:
  selector:
    app: github-webhook-server
  ports:
    - protocol: TCP
      port: 80
      targetPort: 5000
  type: LoadBalancer
```

### Systemd Service

```ini
[Unit]
Description=GitHub Webhook Server
After=network.target

[Service]
Type=simple
User=webhook
Group=webhook
WorkingDirectory=/opt/github-webhook-server
Environment=WEBHOOK_SERVER_DATA_DIR=/opt/github-webhook-server/data
ExecStart=/usr/local/bin/uv run entrypoint.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Usage

### Starting the Server

```bash
# Using the container
podman run -d \
  --name github-webhook-server \
  -p 5000:5000 \
  -v ./data:/home/podman/data:Z \
  -e WEBHOOK_SECRET=your-secret \
  quay.io/myakove/github-webhook-server:latest

# From source
uv run entrypoint.py
```

### Webhook Setup

1. **Configure GitHub Webhook:**

   - Go to your repository settings
   - Navigate to Webhooks → Add webhook
   - Set Payload URL: `https://your-domain.com/webhook_server`
   - Content type: `application/json`
   - Secret: Your webhook secret
   - Events: Select individual events or "Send me everything"

2. **Required Events:**
   - Push
   - Pull requests
   - Issue comments
   - Check runs
   - Pull request reviews

## API Reference

### Health Check

```http
GET /webhook_server/healthcheck
```

**Response:**

```json
{
  "status": 200,
  "message": "Alive"
}
```

### Webhook Endpoint

```http
POST /webhook_server
```

**Headers:**

- `X-GitHub-Event`: Event type
- `X-GitHub-Delivery`: Unique delivery ID
- `X-Hub-Signature-256`: HMAC signature (if webhook secret configured)

**Response:**

```json
{
  "status": 200,
  "message": "Webhook queued for processing",
  "delivery_id": "12345678-1234-1234-1234-123456789012",
  "event_type": "pull_request"
}
```

## Log Viewer

The webhook server includes a comprehensive log viewer web interface for monitoring and analyzing webhook processing in real-time.

### ⚠️ Security Warning

**CRITICAL**: The log viewer endpoints (`/logs/*`) are **NOT PROTECTED** by authentication or authorization. They should **NEVER** be exposed outside your local network or trusted environment.

**Recommendations:**
- Deploy behind a reverse proxy with authentication (e.g., nginx with basic auth)
- Use firewall rules to restrict access to trusted IP ranges
- Consider the log viewer for internal debugging only
- Monitor access to log endpoints in your infrastructure

### Features

- 🔍 **Real-time log streaming** via WebSocket connections
- 📊 **Advanced filtering** by hook ID, PR number, repository, user, log level, and text search
- 🎨 **Dark/light theme support** with automatic preference saving
- 📈 **PR flow visualization** showing webhook processing stages
- 📥 **JSON export** functionality for log analysis
- 🎯 **Color-coded log levels** for quick visual identification

### Accessing the Log Viewer

**Web Interface:**
```
http://your-server:5000/logs
```

### API Endpoints

#### Get Historical Log Entries

```http
GET /logs/api/entries
```

**Query Parameters:**
- `hook_id` (string): Filter by GitHub delivery ID (x-github-delivery)
- `pr_number` (integer): Filter by pull request number
- `repository` (string): Filter by repository name (e.g., "org/repo")
- `event_type` (string): Filter by GitHub event type
- `github_user` (string): Filter by GitHub username
- `level` (string): Filter by log level (DEBUG, INFO, WARNING, ERROR)
- `start_time` (string): Start time filter (ISO 8601 format)
- `end_time` (string): End time filter (ISO 8601 format)
- `search` (string): Free text search in log messages
- `limit` (integer): Maximum entries to return (1-1000, default: 100)
- `offset` (integer): Pagination offset (default: 0)

**Example:**
```bash
curl "http://localhost:5000/logs/api/entries?pr_number=123&level=ERROR&limit=50"
```

**Response:**
```json
{
  "entries": [
    {
      "timestamp": "2025-01-30T10:30:00.123000",
      "level": "INFO",
      "logger_name": "GithubWebhook",
      "message": "Processing webhook for repository: my-org/my-repo",
      "hook_id": "abc123-def456",
      "event_type": "pull_request",
      "repository": "my-org/my-repo",
      "pr_number": 123,
      "github_user": "username"
    }
  ],
  "total": 1500,
  "filtered_total": 25,
  "limit": 50,
  "offset": 0
}
```

#### Export Logs

```http
GET /logs/api/export
```

**Query Parameters:** (Same as `/logs/api/entries` plus)
- `format` (string): Export format - only "json" is supported
- `limit` (integer): Maximum entries to export (max 50,000, default: 10,000)

**Example:**
```bash
curl "http://localhost:5000/logs/api/export?format=json&pr_number=123" -o logs.json
```

#### WebSocket Real-time Streaming

```
ws://your-server:5000/logs/ws
```

**Query Parameters:** (Same filtering options as API endpoints)

**Example WebSocket Connection:**
```javascript
const ws = new WebSocket('ws://localhost:5000/logs/ws?level=ERROR');
ws.onmessage = function(event) {
    const logEntry = JSON.parse(event.data);
    console.log('New error log:', logEntry);
};
```

#### PR Flow Visualization

```http
GET /logs/api/pr-flow/{identifier}
```

**Parameters:**
- `identifier`: Hook ID (e.g., "abc123") or PR number (e.g., "123")

**Example:**
```bash
curl "http://localhost:5000/logs/api/pr-flow/123"
```

**Response:**
```json
{
  "identifier": "123",
  "stages": [
    {
      "name": "Webhook Received",
      "timestamp": "2025-01-30T10:30:00.123000",
      "duration_ms": null
    },
    {
      "name": "Validation Complete",
      "timestamp": "2025-01-30T10:30:00.245000",
      "duration_ms": 122
    }
  ],
  "total_duration_ms": 2500,
  "success": true
}
```

### Log Level Color Coding

The web interface uses intuitive color coding for different log levels:

- 🟢 **INFO (Green)**: Successful operations and informational messages
- 🟡 **WARNING (Yellow)**: Warning messages that need attention
- 🔴 **ERROR (Red)**: Error messages requiring immediate action  
- ⚪ **DEBUG (Gray)**: Technical debug information

### Web Interface Features

#### Filtering Controls
- **Hook ID**: GitHub delivery ID for tracking specific webhook calls
- **PR Number**: Filter by pull request number
- **Repository**: Filter by repository name (org/repo format)
- **User**: Filter by GitHub username
- **Log Level**: Filter by severity level
- **Search**: Free text search across log messages

#### Real-time Features
- **Live Updates**: WebSocket connection for real-time log streaming
- **Auto-refresh**: Historical logs refresh when filters change
- **Connection Status**: Visual indicator for WebSocket connection status

#### Theme Support
- **Dark/Light Modes**: Toggle between themes with automatic preference saving
- **Responsive Design**: Works on desktop and mobile devices
- **Keyboard Shortcuts**: Quick access to common functions

### Usage Examples

#### Monitor Specific PR
```bash
# View all logs for PR #123
curl "http://localhost:5000/logs/api/entries?pr_number=123"
```

#### Track Webhook Processing
```bash
# Follow specific webhook delivery
curl "http://localhost:5000/logs/api/entries?hook_id=abc123-def456"
```

#### Debug Error Issues
```bash
# Export all error logs for analysis
curl "http://localhost:5000/logs/api/export?format=json&level=ERROR" -o errors.json
```

#### Monitor Repository Activity
```bash
# Watch real-time activity for specific repository
# Connect WebSocket to: ws://localhost:5000/logs/ws?repository=my-org/my-repo
```

### Security Considerations

1. **Network Isolation**: Deploy in isolated network segments
2. **Access Control**: Implement reverse proxy authentication
3. **Log Sanitization**: Logs may contain sensitive information
4. **Monitoring**: Monitor access to log viewer endpoints
5. **Data Retention**: Consider log rotation and retention policies

### Troubleshooting

#### WebSocket Connection Issues
- Check firewall rules for WebSocket traffic
- Verify server is accessible on specified port
- Ensure WebSocket upgrades are allowed by reverse proxy

#### Missing Log Data
- Verify log file permissions and paths
- Check if log directory exists and is writable
- Ensure log parser patterns match your log format

#### Performance Issues
- Reduce filter result sets for better performance
- Use pagination for large datasets
- Consider log file rotation to manage size

## User Commands

Users can interact with the webhook server through GitHub comments on pull requests and issues.

### Pull Request Commands

| Command             | Description                   | Example             |
| ------------------- | ----------------------------- | ------------------- |
| `/verified`         | Mark PR as verified           | `/verified`         |
| `/verified cancel`  | Remove verification           | `/verified cancel`  |
| `/hold`             | Block PR merging              | `/hold`             |
| `/hold cancel`      | Unblock PR merging            | `/hold cancel`      |
| `/wip`              | Mark as work in progress      | `/wip`              |
| `/wip cancel`       | Remove WIP status             | `/wip cancel`       |
| `/lgtm`             | Approve changes               | `/lgtm`             |
| `/approve`          | Approve PR                    | `/approve`          |
| `/assign-reviewers` | Assign OWNERS-based reviewers | `/assign-reviewers` |
| `/check-can-merge`  | Check merge readiness         | `/check-can-merge`  |

### Testing Commands

| Command                         | Description               | Example                         |
| ------------------------------- | ------------------------- | ------------------------------- |
| `/retest all`                   | Run all configured tests  | `/retest all`                   |
| `/retest tox`                   | Run tox tests             | `/retest tox`                   |
| `/retest build-container`       | Rebuild container         | `/retest build-container`       |
| `/retest python-module-install` | Test package installation | `/retest python-module-install` |
| `/retest pre-commit`            | Run pre-commit checks     | `/retest pre-commit`            |

### Container Commands

| Command                                           | Description              | Example                                             |
| ------------------------------------------------- | ------------------------ | --------------------------------------------------- |
| `/build-and-push-container`                       | Build and push container | `/build-and-push-container`                         |
| `/build-and-push-container --build-arg KEY=value` | Build with custom args   | `/build-and-push-container --build-arg VERSION=1.0` |

### Cherry-pick Commands

| Command                        | Description                      | Example                  |
| ------------------------------ | -------------------------------- | ------------------------ |
| `/cherry-pick branch`          | Cherry-pick to single branch     | `/cherry-pick develop`   |
| `/cherry-pick branch1 branch2` | Cherry-pick to multiple branches | `/cherry-pick v1.0 v2.0` |

### Label Commands

| Command           | Description  | Example       |
| ----------------- | ------------ | ------------- |
| `/<label>`        | Add label    | `/bug`        |
| `/<label> cancel` | Remove label | `/bug cancel` |

### Review & Approval

| Command             | Description                                                                 | Example             |
| ------------------- | ------------------------------------------------------------------------- | ------------------- |
| `/lgtm`             | Approve changes (looks good to me)                                         | `/lgtm`             |
| `/approve`          | Approve PR (approvers only)                                                 | `/approve`          |
| `/automerge`         | Enable automatic merging when all requirements are met (maintainers/approvers only) | `/automerge`         |
| `/assign-reviewers`  | Assign reviewers based on OWNERS file                                        | `/assign-reviewers`  |
| `/assign-reviewer`   | Assign specific reviewer                                                   | `/assign-reviewer @username` |
| `/check-can-merge`   | Checks if the pull request meets all merge requirements                        | `/check-can-merge`   |

### Testing & Validation

| Command             | Description                                                                 | Example             |
| ------------------- | ------------------------------------------------------------------------- | ------------------- |
| `/retest <test-name>` | Run specific tests like `tox` or `pre-commit`                              | `/retest <test-name>` |

## OWNERS File Format

The OWNERS file system provides fine-grained control over code review assignments.

### Root OWNERS File

```yaml
# Repository root OWNERS file
approvers:
  - senior-dev1
  - senior-dev2
  - team-lead
reviewers:
  - developer1
  - developer2
  - developer3
```

### Directory-specific OWNERS

```yaml
# Component-specific OWNERS (e.g., backend/OWNERS)
root-approvers: false # Don't require root approvers for this component
approvers:
  - backend-lead
  - senior-backend-dev
reviewers:
  - backend-dev1
  - backend-dev2
```

### OWNERS File Rules

- **Approvers**: Can approve pull requests for the component
- **Reviewers**: Can review but approval from approvers still required
- **root-approvers**: When `false`, root approvers are not required
- **Inheritance**: Subdirectories inherit parent OWNERS unless overridden

## Security

### IP Allowlist

Configure IP-based access control:

```yaml
verify-github-ips: true # Restrict to GitHub's IP ranges
verify-cloudflare-ips: true # Allow Cloudflare IPs (if using CF proxy)
```

### Webhook Security

```yaml
webhook-secret: "your-secure-secret" # HMAC-SHA256 signature verification # pragma: allowlist secret
```

### SSL/TLS Configuration

```yaml
disable-ssl-warnings:
  false # Keep SSL warnings in development
  # Set to true in production if needed
```

### Token Security

- Use fine-grained personal access tokens when possible
- Implement token rotation strategy
- Monitor token usage and rate limits
- Store tokens securely (environment variables, secrets management)

### Best Practices

1. **Network Security**: Deploy behind reverse proxy with TLS termination
2. **Container Security**: Run as non-privileged user when possible
3. **Secrets Management**: Use external secret management systems
4. **Monitoring**: Enable comprehensive logging and monitoring
5. **Updates**: Regularly update to latest stable version

## Monitoring

### Health Checks

The server provides built-in health monitoring:

```bash
curl http://localhost:5000/webhook_server/healthcheck
```

### Logging

Configure comprehensive logging:

```yaml
log-level: INFO # DEBUG, INFO, WARNING, ERROR
log-file: /path/to/webhook-server.log
```

### Metrics and Observability

- **Request/Response logging** with delivery IDs
- **Rate limit monitoring** with automatic token switching
- **Error tracking** with detailed stack traces
- **Performance metrics** for webhook processing times

### Monitoring Integration

Example Prometheus configuration:

```yaml
# Add to your monitoring stack
scrape_configs:
  - job_name: "github-webhook-server"
    static_configs:
      - targets: ["webhook-server:5000"]
    metrics_path: "/metrics" # If metrics endpoint added
```

## Troubleshooting

### Common Issues

#### Webhook Not Receiving Events

1. **Check webhook configuration** in GitHub repository settings
2. **Verify network connectivity** between GitHub and your server
3. **Check IP allowlist settings** if enabled
4. **Validate webhook secret** if configured

#### Repository Not Found Errors

1. **Verify repository name** in configuration matches GitHub
2. **Check token permissions** for repository access
3. **Confirm GitHub App installation** on target repositories

#### Rate Limiting Issues

1. **Monitor token usage** in logs
2. **Add additional tokens** to configuration
3. **Check token permissions** and validity

#### Container Build Failures

1. **Verify Podman/Docker availability** in container
2. **Check registry credentials** and permissions
3. **Review build logs** in check run output

### Debug Mode

Enable detailed logging:

```yaml
log-level: DEBUG
```

### Log Analysis

Key log patterns to monitor:

```bash
# Successful webhook processing
grep "Webhook processing completed successfully" webhook-server.log

# Rate limit warnings
grep "rate limit" webhook-server.log

# Failed authentications
grep "signatures didn't match" webhook-server.log

# Repository configuration errors
grep "Repository not found" webhook-server.log
```

### Support Channels

- **GitHub Issues**: [Create an issue](https://github.com/myakove/github-webhook-server/issues)
- **Documentation**: Check this README and inline code documentation
- **Configuration**: Use the schema validator for configuration issues

## Contributing

We welcome contributions! Please see our [contributing guidelines](CONTRIBUTING.md) for details.

### Development Setup

```bash
git clone https://github.com/myakove/github-webhook-server.git
cd github-webhook-server
uv sync
uv run pre-commit install
```

### Running Tests

```bash
# Run all tests
tox
```

### Code Quality

- **Linting**: `uv run ruff check`
- **Formatting**: `uv run ruff format`

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

---

**Note**: This webhook server is designed for public repositories. Private repository support may require additional configuration and testing.
