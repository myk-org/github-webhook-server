# Installation

To run the `github-webhook-server` and start automating your repository workflows, you need to prepare the runtime environment and provide GitHub credentials. This guide walks you through setting up Python, configuring the required data directory, and launching the server locally or in production.

## Prerequisites
- **Python 3.13**: Required by the server application.
- **uv**: The Python package manager used to sync dependencies and run the server.
- **GitHub App**: A registered GitHub App with its App ID and a generated Private Key.
- **Node.js** (Optional): Only required if you plan to use AI features powered by the local sidecar.

## Quick Example
Start the development server with local data:

```bash
# Clone the repository and install dependencies
git clone https://github.com/myakove/github-webhook-server.git
cd github-webhook-server
uv sync

# Set up the data directory
export WEBHOOK_SERVER_DATA_DIR=/tmp/webhook-server-data
mkdir -p "$WEBHOOK_SERVER_DATA_DIR"
touch "$WEBHOOK_SERVER_DATA_DIR/config.yaml"

# Run the server
WEBHOOK_SERVER_DATA_DIR=/tmp/webhook-server-data uv run entrypoint.py
```

## Step-by-Step Installation

### 1. Set Up the Environment
Install `uv` if you haven't already, then use it to sync the Python environment. The tool will automatically prepare your virtual environment and dependencies.

```bash
uv sync
source .venv/bin/activate
```

### 2. Create the Data Directory
The server requires a single, persistent data directory to store its configuration, rotating logs, and cryptographic keys.

```bash
export WEBHOOK_SERVER_DATA_DIR=/var/lib/webhook-server
mkdir -p "$WEBHOOK_SERVER_DATA_DIR"
```

> **Note:** If `WEBHOOK_SERVER_DATA_DIR` is not set, the server looks for `/home/podman/data` by default.

### 3. Provide GitHub Credentials
The server needs access to a GitHub App to process webhooks and manipulate your repositories.

1. Create a GitHub App in your organization or user account.
2. Generate a new Private Key for the App.
3. Save the key inside your data directory, for example as `webhook-server.private-key.pem`.

Your data directory should now contain at least these two files before you fully configure the app:
```text
/var/lib/webhook-server/
├── config.yaml
└── webhook-server.private-key.pem
```

See [Repository Bootstrap and GitHub App](repository-bootstrap-and-github-app.html) for detailed steps on setting up App permissions.

### 4. Start the Server
To launch the server, run the entrypoint script using `uv`. Always ensure the data directory variable is exported or passed inline.

```bash
WEBHOOK_SERVER_DATA_DIR=/var/lib/webhook-server uv run entrypoint.py
```

## Advanced Usage

### Enabling AI Features with the Node.js Sidecar
If you want to use the AI test oracle or auto-fix capabilities, you must run the local Node.js sidecar alongside the Python server.

```bash
# Build the Node.js sidecar
cd sidecar-helper
npm install
npm run build
cd ..

# Start the server using the shell wrapper
WEBHOOK_SERVER_DATA_DIR=/var/lib/webhook-server ./entrypoint.sh
```

The `./entrypoint.sh` wrapper automatically manages the lifecycle of both the Node.js process and the Python server, ensuring they start and stop together. See [AI Features and Test Oracle](ai-features-and-test-oracle.html) to learn how to trigger these features.

### Containerized Deployment
For production usage, the server is commonly run as a container using Podman or Docker instead of a local Python environment.

When deploying as a container, the standard practice is to mount your local data directory into the container's default `/home/podman/data` path. This removes the need to manually set the `WEBHOOK_SERVER_DATA_DIR` variable on the host.

See [Docker and Container Deployment](docker-deployment.html) for Compose configurations, volume mounts, and network setups.

## Troubleshooting

- **Server crashes with "Config file /home/podman/data/config.yaml not found"**: The server could not locate your configuration. Verify that your `WEBHOOK_SERVER_DATA_DIR` variable points to an existing directory containing your `config.yaml` file.
- **Missing Sidecar warning**: If you see `WARNING: sidecar-helper/dist/server.js not found` on startup, the Node.js bridge hasn't been built. You can safely ignore this if you aren't using AI features, and standard webhook processing will continue to work normally.
- **Port already in use or configuration ignored**: The server reads listener settings like `port`, `ip-bind`, and `max-workers` strictly from `config.yaml`. Check your YAML file rather than relying on environment variables.

## Related Pages

- [Quick Start](quick-start.html)
- [Docker and Container Deployment](docker-deployment.html)
- [Configuration Reference](configuration-reference.html)
