# Webhook and Health API

To configure your GitHub repository or organization to communicate with your deployment, you need to point it to the webhook endpoint and verify the deployment is running using the health check endpoint.

## Prerequisites
* A running instance of the GitHub Webhook Server.
* A GitHub repository or organization where you have admin permissions.
* A webhook secret configured in your server deployment (usually via `WEBHOOK_SECRET` environment variable).

## Quick Example

To check if your server is healthy, make a GET request to the healthcheck endpoint:

```bash
curl https://your-server.com/webhook_server/healthcheck
```
```json
{
  "status": "OK"
}
```

## Step-by-Step

### Step 1: Verify the Server is Healthy
Before configuring GitHub, ensure your server is reachable and healthy.
The `/webhook_server/healthcheck` endpoint responds to HTTP GET requests.

```bash
# Check the health endpoint
curl -s https://your-server.com/webhook_server/healthcheck
```
If the server is running correctly, it returns an HTTP 200 OK with the JSON response `{"status": "OK"}`.

### Step 2: Configure the Webhook in GitHub
1. In your GitHub repository or organization, go to **Settings** > **Webhooks** > **Add webhook**.
2. Set the **Payload URL** to your server's webhook endpoint: `https://your-server.com/webhook_server`.
3. Set the **Content type** to `application/json`.
4. Set the **Secret** to match the webhook secret configured on your server.
5. Select the events you want to trigger the webhook (e.g., Pull Requests, Issues, Push).
6. Ensure **Active** is checked and click **Add webhook**.

> **Note:** The server expects the payload to be delivered as JSON. Form-encoded payloads are not supported.

### Step 3: Understand Webhook Processing
The `/webhook_server` endpoint receives POST requests from GitHub.

When a webhook is received, the server:
1. Validates the signature using your configured secret.
2. Immediately responds with an HTTP 202 Accepted.
3. Processes the event asynchronously in the background.

This immediate-response design ensures the server never times out the GitHub webhook delivery, even for long-running tasks.

## Advanced Usage

### Payload Validation
GitHub signs webhook payloads using a Hash-based Message Authentication Code (HMAC) with SHA-256.

The server requires the `X-Hub-Signature-256` header to be present and valid. If the signature is missing or incorrect, the server will reject the request with an HTTP 400 Bad Request or HTTP 403 Forbidden.

> **Warning:** Never disable webhook signature validation in production. It prevents unauthorized sources from triggering actions on your server.

### Supported Events
The server ignores events it doesn't know how to handle. If an unsupported event type is sent, the server will still return HTTP 202 Accepted, but will log that the event was ignored.

## Troubleshooting

### Webhook Delivery Failures in GitHub
If GitHub shows a red warning icon next to your webhook delivery:
* **HTTP 401/403 Error:** Verify the Secret in GitHub exactly matches the secret configured on your server.
* **Timeout:** Ensure your server is reachable from the public internet. The server responds immediately (HTTP 202), so timeouts indicate a network or routing issue before the request reaches the application.

### Healthcheck Fails
If `/webhook_server/healthcheck` returns an error or fails to connect:
* Check your reverse proxy or load balancer configuration.
* Verify the application is actually running and listening on the expected port.

## Related Pages

- [Architecture and Event Flow](architecture-and-event-flow.html)
- [Supported GitHub Events](supported-github-events.html)
- [Troubleshooting](troubleshooting.html)
