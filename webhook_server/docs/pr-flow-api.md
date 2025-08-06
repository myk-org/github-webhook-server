# PR Flow API Documentation

## Overview

The PR Flow API (`/logs/api/pr-flow/{hook_id}`) provides comprehensive pull-request workflow
visualization data for process analysis and debugging.
It tracks the complete lifecycle of PR processing workflows from webhook receipt through completion.

## Primary Use Cases

- **Workflow Visualization**: View complete PR processing workflow from start to finish
- **Debugging**: Investigate stuck or failed PR automation workflows
- **Performance Analysis**: Identify bottlenecks and optimize workflow performance
- **Monitoring**: Track automated reviewer assignment and approval processes
- **Build Tracking**: Monitor container builds, tests, and deployment status for PRs
- **Analytics**: Generate PR workflow analytics and performance reports
- **Issue Investigation**: Debug webhook delivery and processing issues

## Security Model

The PR Flow API endpoints (`/logs/api/pr-flow/*`) are **unauthenticated by design** for simplicity and ease of integration.
They rely on network-level security controls and should only be deployed in trusted environments.

### Security Considerations

- **No Built-in Authentication**: The API does not implement authentication or authorization mechanisms
- **Network-Level Security Required**: Security must be implemented at the network infrastructure level
- **Trusted Networks Only**: Deploy behind VPN, on internal networks, or with proper network isolation

### Recommended Security Controls

- **VPN Access**: Deploy behind corporate VPN for remote access
- **Internal Networks**: Restrict access to internal/private networks only
- **Reverse Proxy Authentication**: Use nginx, Apache, or cloud load balancer authentication
- **Firewall Rules**: Implement IP allowlists and port restrictions
- **Network Segmentation**: Isolate webhook server in dedicated network segments

### Security Expectations for API Consumers

API consumers should ensure:

- Access is restricted to authorized personnel only
- Network traffic is encrypted in transit (HTTPS)
- Logs containing sensitive data are handled according to data governance policies
- Integration systems implement proper access controls and audit logging

## Parameters

**Method**: `GET`  
**Response Content-Type**: `application/json`

### hook_id (required)

- **Type**: String
- **Description**: GitHub webhook delivery ID that initiated the PR workflow
- **Example**: `"f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f"`
- **Source**: Found in GitHub webhook logs or the `X-GitHub-Delivery` header
- **Purpose**: Links all related workflow steps and events together

## Response Structure

### Complete Example Response

```json
{
  "hook_id": "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f",
  "pr_metadata": {
    "pr_number": 42,
    "repository": "myakove/github-webhook-server",
    "title": "Add memory optimization for log processing",
    "author": "contributor123",
    "state": "open",
    "created_at": "2024-01-15T10:00:00Z",
    "updated_at": "2024-01-15T14:30:00Z"
  },
  "workflow_stages": [
    {
      "stage": "webhook_received",
      "timestamp": "2024-01-15T10:00:00.123456",
      "status": "completed",
      "duration_ms": 45,
      "details": {
        "event_type": "pull_request",
        "action": "opened",
        "delivery_id": "f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f"
      }
    },
    {
      "stage": "pr_analysis",
      "timestamp": "2024-01-15T10:00:01.234567",
      "status": "completed",
      "duration_ms": 1200,
      "details": {
        "size_classification": "large",
        "files_changed": 15,
        "lines_added": 450,
        "lines_deleted": 120,
        "complexity_score": 7.5
      }
    },
    {
      "stage": "reviewer_assignment",
      "timestamp": "2024-01-15T10:00:02.345678",
      "status": "completed",
      "duration_ms": 800,
      "details": {
        "owners_file_processed": true,
        "reviewers_assigned": ["senior-dev", "team-lead"],
        "auto_assignment_rules": ["code_owners", "expertise_matching"]
      }
    },
    {
      "stage": "container_build",
      "timestamp": "2024-01-15T10:05:00.456789",
      "status": "in_progress",
      "duration_ms": null,
      "details": {
        "build_type": "docker",
        "registry": "quay.io",
        "build_context": "containerfiles/Dockerfile"
      }
    }
  ],
  "performance_metrics": {
    "total_processing_time_ms": 2045,
    "stages_completed": 3,
    "stages_pending": 1,
    "stages_failed": 0,
    "workflow_health": "healthy",
    "bottlenecks_detected": []
  },
  "integration_status": {
    "github_api_calls": 15,
    "rate_limit_remaining": 4985,
    "external_service_calls": {
      "slack": {"status": "success", "notifications_sent": 2},
      "jira": {"status": "pending", "tickets_created": 0}
    }
  }
}
```

## Workflow Stages

### Common Workflow Stages

- **webhook_received**: Initial webhook processing and validation
- **pr_analysis**: PR size, complexity, and impact analysis
- **reviewer_assignment**: Automatic reviewer assignment based on OWNERS files
- **label_assignment**: Automatic labeling based on changed files and PR content
- **container_build**: Docker/Podman container building and publishing
- **test_execution**: Automated test suite execution
- **security_scan**: Security vulnerability scanning
- **compliance_check**: Policy and compliance validation
- **notification_dispatch**: Slack, email, or other notification delivery
- **deployment_trigger**: Automated deployment initiation

### Stage Status Values

- **pending**: Stage is queued but not yet started
- **in_progress**: Stage is currently executing
- **completed**: Stage finished successfully
- **failed**: Stage encountered an error
- **skipped**: Stage was bypassed due to conditions or configuration
- **timeout**: Stage exceeded maximum execution time

## Analysis Scenarios

### Debugging Examples

- **Reviewer Assignment Issues**: Debug why PR reviewer assignment failed or took too long
- **Performance Bottlenecks**: Identify which stage is causing PR processing delays
- **Build Failures**: Monitor container build success rates and failure patterns
- **Assignment Accuracy**: Analyze reviewer assignment accuracy and OWNERS file effectiveness
- **Integration Health**: Track external service integration health (Slack, JIRA, etc.)
- **SLA Monitoring**: Generate PR processing performance reports and SLA monitoring

## Error Conditions

### HTTP Status Codes

- **400 Bad Request**: Invalid hook_id format (not a valid UUID or delivery ID)
- **404 Not Found**:
  - No PR workflow found for the specified hook_id
  - Hook_id exists but no PR-related events in the workflow
- **500 Internal Server Error**:
  - Log parsing errors or corrupted workflow data
  - Internal errors during workflow data aggregation

## AI Agent Usage Examples

### Common Queries

```text
"Analyze PR workflow for delivery abc123 to debug why reviewer assignment failed"
```

```text
"Get PR flow data for hook xyz789 to identify container build bottlenecks"
```

```text
"Show me the complete workflow timeline for delivery def456 to optimize performance"
```

```text
"Debug why PR notifications aren't being sent using hook ghi789 flow data"
```

```text
"Generate workflow analysis report for hook jkl012 to identify process improvements"
```

## Performance Considerations

### Response Times

- Response time depends on the complexity and duration of the workflow
- Large workflows with many stages may take 1-3 seconds to aggregate
- Data is computed on-demand with no caching for real-time accuracy
- Hook IDs from workflows older than 30 days may have limited data availability

### Data Sources

- **Structured Logs**: Parsing from webhook-server.log
- **GitHub API**: Response caching and metadata
- **External Services**: Integration logs (Slack, JIRA, etc.)
- **Performance Data**: Timing data from internal instrumentation

## Integration Examples

### Using with Log Viewer

The PR Flow API integrates seamlessly with the log viewer system to provide:

- Real-time workflow status updates
- Interactive timeline visualization
- Performance metric dashboards
- Bottleneck identification and alerts

### Programmatic Usage

```python
import httpx

async def analyze_pr_workflow(hook_id: str, base_url: str = "http://192.168.10.44:5003") -> dict:
    async with httpx.AsyncClient() as client:
        # Example using production webhook server
        response = await client.get(f"{base_url}/logs/api/pr-flow/{hook_id}")
        return response.json()

# Usage
workflow_data = await analyze_pr_workflow("f4b3c2d1-a9b8-4c5d-9e8f-1a2b3c4d5e6f")
print(f"Workflow health: {workflow_data['performance_metrics']['workflow_health']}")
```

### Monitoring Integration

The API data can be used for:

- Prometheus metrics collection
- Grafana dashboard visualization
- Alerting on workflow failures or performance degradation
- SLA compliance monitoring

## Best Practices

### Hook ID Management

- Store hook IDs from webhook headers for later analysis
- Correlate hook IDs with external monitoring systems
- Use hook IDs for debugging specific workflow instances

### Performance Monitoring

- Monitor `total_processing_time_ms` for SLA compliance
- Track `workflow_health` status for overall system health
- Alert on `bottlenecks_detected` for proactive optimization

### Error Handling

- Implement proper error handling for 404 and 500 responses
- Retry logic for temporary failures
- Fallback strategies when workflow data is unavailable
