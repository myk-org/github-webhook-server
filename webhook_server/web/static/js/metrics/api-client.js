/**
 * Metrics API Client - REST API Wrapper for GitHub Webhook Metrics
 *
 * This module provides a centralized, production-ready client for all metrics API endpoints
 * with comprehensive error handling and timeout management.
 *
 * Features:
 * - Automatic timeout handling with AbortController
 * - Consistent error response format
 * - URL parameter building with proper encoding
 * - Singleton pattern for global access
 *
 * API Endpoints:
 * - GET /api/metrics/summary - Overall metrics summary
 * - GET /api/metrics/webhooks - Recent webhook events (with pagination)
 * - GET /api/metrics/repositories - Repository statistics
 * - GET /api/metrics/webhooks/{delivery_id} - Specific webhook event details
 *
 * Usage:
 *   import { apiClient } from './api-client.js';
 *
 *   // Fetch summary
 *   const summary = await apiClient.fetchSummary();
 *
 *   // Fetch webhooks with filters
 *   const webhooks = await apiClient.fetchWebhooks({
 *       repository: 'org/repo',
 *       status: 'error',
 *       limit: 50
 *   });
 *
 * Error Handling:
 *   All methods return standardized error objects:
 *   {
 *       error: 'Error type',
 *       detail: 'Detailed error message',
 *       status: 404  // HTTP status code (if applicable)
 *   }
 */

class MetricsAPIClient {
    /**
     * Create a new Metrics API client.
     *
     * @param {string} baseURL - Base URL for API endpoints (default: '/api/metrics')
     * @param {number} timeout - Request timeout in milliseconds (default: 10000)
     */
    constructor(baseURL = '/api/metrics', timeout = 10000) {
        this.baseURL = baseURL;
        this.timeout = timeout;
    }

    /**
     * Fetch overall metrics summary.
     *
     * Returns aggregated metrics including:
     * - Total events, success/error/partial counts
     * - Top repositories by event volume
     * - Event type distribution
     * - Average processing time
     *
     * @param {string|null} startTime - ISO 8601 start time filter (optional)
     * @param {string|null} endTime - ISO 8601 end time filter (optional)
     * @returns {Promise<Object>} Summary data or error object
     *
     * Response format (success):
     * {
     *     summary: {
     *         total_events: 1234,
     *         successful_events: 1180,
     *         failed_events: 45,
     *         partial_events: 9,
     *         avg_processing_time_ms: 523.4
     *     },
     *     top_repositories: [
     *         { repository: 'org/repo1', total_events: 450, ... },
     *         ...
     *     ],
     *     event_type_distribution: {
     *         pull_request: 567,
     *         issue_comment: 345,
     *         ...
     *     }
     * }
     *
     * Response format (error):
     * {
     *     error: 'Network error',
     *     detail: 'Failed to connect to server',
     *     status: null
     * }
     */
    async fetchSummary(startTime = null, endTime = null) {
        const params = {};
        if (startTime) params.start_time = startTime;
        if (endTime) params.end_time = endTime;

        return await this._fetch('/summary', params);
    }

    /**
     * Fetch webhook events with filtering and pagination.
     *
     * Supports comprehensive filtering by repository, event type, status, time range,
     * and pagination for efficient data loading.
     *
     * @param {Object} options - Filter and pagination options
     * @param {string} options.repository - Filter by repository (e.g., 'org/repo')
     * @param {string} options.event_type - Filter by event type (e.g., 'pull_request', 'issue_comment')
     * @param {string} options.status - Filter by status ('success', 'error', 'partial')
     * @param {string} options.start_time - ISO 8601 start time filter
     * @param {string} options.end_time - ISO 8601 end time filter
     * @param {number} options.page - Page number (1-indexed, default: 1)
     * @param {number} options.page_size - Items per page (default: 10)
     * @returns {Promise<Object>} Webhook events data or error object
     *
     * Response format (success):
     * {
     *     data: [
     *         {
     *             delivery_id: 'abc123...',
     *             repository: 'org/repo',
     *             event_type: 'pull_request',
     *             action: 'opened',
     *             pr_number: 42,
     *             sender: 'username',
     *             created_at: '2025-11-24T12:34:56.789Z',
     *             processed_at: '2025-11-24T12:35:01.234Z',
     *             duration_ms: 4445,
     *             status: 'success',
     *             error_message: null,
     *             api_calls_count: 12,
     *             token_spend: 150,
     *             token_remaining: 4850
     *         },
     *         ...
     *     ],
     *     pagination: {
     *         total: 1234,
     *         page: 1,
     *         page_size: 100,
     *         total_pages: 13,
     *         has_next: true,
     *         has_prev: false
     *     }
     * }
     *
     * Response format (error):
     * {
     *     error: 'HTTP error',
     *     detail: 'Failed to fetch webhook events',
     *     status: 500
     * }
     */
    async fetchWebhooks(options = {}) {
        const params = {};

        // Add filters if provided
        if (options.repository) params.repository = options.repository;
        if (options.event_type) params.event_type = options.event_type;
        if (options.status) params.status = options.status;
        if (options.start_time) params.start_time = options.start_time;
        if (options.end_time) params.end_time = options.end_time;

        // Add pagination parameters
        if (options.page !== undefined) params.page = options.page;
        if (options.page_size !== undefined) params.page_size = options.page_size;

        return await this._fetch('/webhooks', params);
    }

    /**
     * Fetch repository statistics.
     *
     * Returns per-repository metrics including event counts, success rates,
     * and processing times.
     *
     * @param {string|null} startTime - ISO 8601 start time filter (optional)
     * @param {string|null} endTime - ISO 8601 end time filter (optional)
     * @param {Object} extraParams - Additional parameters (page, page_size, repository, user)
     * @returns {Promise<Object>} Repository statistics or error object
     *
     * Response format (success):
     * {
     *     time_range: {
     *         start_time: '2025-11-01T00:00:00Z',
     *         end_time: '2025-11-25T23:59:59Z'
     *     },
     *     data: [
     *         {
     *             repository: 'org/repo1',
     *             total_events: 450,
     *             successful_events: 440,
     *             failed_events: 8,
     *             partial_events: 2,
     *             avg_processing_time_ms: 523.4,
     *             last_event_at: '2025-11-24T12:34:56.789Z'
     *         },
     *         ...
     *     ],
     *     pagination: {
     *         total: 50,
     *         page: 1,
     *         page_size: 10,
     *         total_pages: 5,
     *         has_next: true,
     *         has_prev: false
     *     }
     * }
     *
     * Response format (error):
     * {
     *     error: 'Request timeout',
     *     detail: 'Request exceeded 10000ms timeout',
     *     status: null
     * }
     */
    async fetchRepositories(startTime = null, endTime = null, extraParams = {}) {
        const params = { ...extraParams };
        if (startTime) params.start_time = startTime;
        if (endTime) params.end_time = endTime;

        const response = await this._fetch('/repositories', params);
        if (response.error) return response;

        // Normalize response: extract data array while preserving pagination
        return {
            repositories: response.data || [],
            data: response.data || [],
            pagination: response.pagination,
            time_range: response.time_range
        };
    }

    /**
     * Fetch event trends (time series data).
     *
     * Returns aggregated event counts over time buckets.
     *
     * @param {string|null} startTime - ISO 8601 start time filter
     * @param {string|null} endTime - ISO 8601 end time filter
     * @param {string} bucket - Time bucket ('hour', 'day')
     * @returns {Promise<Object>} Trends data or error object
     */
    async fetchTrends(startTime = null, endTime = null, bucket = 'hour') {
        const params = { bucket };
        if (startTime) params.start_time = startTime;
        if (endTime) params.end_time = endTime;

        return await this._fetch('/trends', params);
    }

    /**
     * Fetch PR contributors statistics.
     *
     * Returns PR creators, reviewers, and approvers with activity metrics.
     *
     * @param {string|null} startTime - ISO 8601 start time filter (optional)
     * @param {string|null} endTime - ISO 8601 end time filter (optional)
     * @param {number} limit - Maximum contributors per category (default: 10)
     * @param {Object} extraParams - Additional parameters (repository, user, page, page_size)
     * @returns {Promise<Object>} Contributors data or error object
     */
    async fetchContributors(startTime = null, endTime = null, limit = 10, extraParams = {}) {
        const params = { limit, ...extraParams };
        if (startTime) params.start_time = startTime;
        if (endTime) params.end_time = endTime;

        return await this._fetch('/contributors', params);
    }

    /**
     * Fetch user pull requests.
     *
     * Returns pull requests for a specific user or all users.
     *
     * @param {string|null} startTime - ISO 8601 start time filter (optional)
     * @param {string|null} endTime - ISO 8601 end time filter (optional)
     * @param {Object} params - Additional parameters (user, repository, page, page_size)
     * @returns {Promise<Object>} User PRs data with pagination or error object
     */
    async fetchUserPRs(startTime = null, endTime = null, params = {}) {
        const queryParams = { ...params };
        if (startTime) queryParams.start_time = startTime;
        if (endTime) queryParams.end_time = endTime;

        return await this._fetch('/user-prs', queryParams);
    }

    /**
     * Fetch specific webhook event by delivery ID.
     *
     * Returns complete details for a single webhook event including full payload.
     *
     * @param {string} deliveryId - GitHub webhook delivery ID
     * @returns {Promise<Object>} Webhook event details or error object
     *
     * Response format (success):
     * {
     *     delivery_id: 'abc123...',
     *     repository: 'org/repo',
     *     event_type: 'pull_request',
     *     action: 'opened',
     *     pr_number: 42,
     *     sender: 'username',
     *     created_at: '2025-11-24T12:34:56.789Z',
     *     processed_at: '2025-11-24T12:35:01.234Z',
     *     duration_ms: 4445,
     *     status: 'success',
     *     error_message: null,
     *     api_calls_count: 12,
     *     token_spend: 150,
     *     token_remaining: 4850,
     *     payload: { ... }  // Full GitHub webhook payload
     * }
     *
     * Response format (error - not found):
     * {
     *     error: 'Not found',
     *     detail: 'Webhook event not found',
     *     status: 404
     * }
     */
    async fetchWebhookById(deliveryId) {
        if (!deliveryId) {
            return {
                error: 'Invalid parameter',
                detail: 'deliveryId is required',
                status: null
            };
        }

        return await this._fetch(`/webhooks/${encodeURIComponent(deliveryId)}`);
    }

    /**
     * Internal fetch wrapper with timeout and error handling.
     *
     * @private
     * @param {string} endpoint - API endpoint path (e.g., '/summary', '/webhooks')
     * @param {Object} params - Query parameters as key-value pairs
     * @returns {Promise<Object>} Response data or standardized error object
     */
    async _fetch(endpoint, params = {}) {
        const controller = new AbortController();

        // Set up timeout
        const timeoutId = setTimeout(() => {
            controller.abort();
            console.warn(`[API Client] Request timeout for ${endpoint}`);
        }, this.timeout);

        try {
            // Build URL with query parameters
            const url = this._buildURL(endpoint, params);
            console.log(`[API Client] Fetching: ${url}`);

            // Execute fetch with timeout signal
            const response = await fetch(url, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                },
                signal: controller.signal
            });

            // Clear timeout on successful response
            clearTimeout(timeoutId);

            // Handle HTTP errors
            if (!response.ok) {
                return await this._handleHTTPError(response);
            }

            // Parse JSON response
            try {
                const data = await response.json();
                console.log(`[API Client] Success: ${endpoint}`, data);
                return data;
            } catch (parseError) {
                console.error(`[API Client] JSON parse error for ${endpoint}:`, parseError);
                return {
                    error: 'Invalid response format',
                    detail: 'Server returned invalid JSON response',
                    status: response.status
                };
            }

        } catch (error) {
            // Clear timeout
            clearTimeout(timeoutId);

            // Handle different error types
            if (error.name === 'AbortError') {
                console.warn(`[API Client] Request aborted: ${endpoint}`);
                return {
                    error: 'Request timeout',
                    detail: `Request exceeded ${this.timeout}ms timeout`,
                    status: null
                };
            }

            // Network errors (no connection, DNS failure, etc.)
            if (error instanceof TypeError) {
                console.error(`[API Client] Network error for ${endpoint}:`, error);
                return {
                    error: 'Network error',
                    detail: 'Failed to connect to server. Please check your network connection.',
                    status: null
                };
            }

            // Generic error fallback
            console.error(`[API Client] Unexpected error for ${endpoint}:`, error);
            return {
                error: 'Unknown error',
                detail: error.message || 'An unexpected error occurred',
                status: null
            };
        }
    }

    /**
     * Handle HTTP error responses with detailed error extraction.
     *
     * @private
     * @param {Response} response - Fetch API Response object
     * @returns {Promise<Object>} Standardized error object
     */
    async _handleHTTPError(response) {
        console.error(`[API Client] HTTP ${response.status} error: ${response.url}`);

        // Try to extract error detail from response body
        let detail = `HTTP ${response.status} error`;
        try {
            const errorData = await response.json();
            if (errorData.detail) {
                detail = errorData.detail;
            } else if (errorData.message) {
                detail = errorData.message;
            }
        } catch (error) {
            // Failed to parse error response - use default detail
            detail = response.statusText || detail;
        }

        // Return standardized error object
        return {
            error: 'HTTP error',
            detail: detail,
            status: response.status
        };
    }

    /**
     * Build complete URL with query parameters.
     *
     * @private
     * @param {string} endpoint - API endpoint path
     * @param {Object} params - Query parameters as key-value pairs
     * @returns {string} Complete URL with encoded query string
     */
    _buildURL(endpoint, params = {}) {
        const url = new URL(this.baseURL + endpoint, window.location.origin);

        // Add query parameters
        for (const [key, value] of Object.entries(params)) {
            if (value !== null && value !== undefined) {
                url.searchParams.append(key, value);
            }
        }

        return url.toString();
    }

    /**
     * Check if API is available by fetching summary endpoint.
     *
     * Useful for health checks and determining if metrics server is enabled.
     * Distinguishes between "metrics disabled" and "temporary failures".
     *
     * @returns {Promise<Object>} Object with availability status and reason
     * @returns {boolean} available - True if API is available
     * @returns {string} reason - Reason for unavailability ('disabled', 'network_error', 'server_error', etc.)
     * @returns {number|null} status - HTTP status code if available
     *
     * @example
     * const { available, reason, status } = await apiClient.isAvailable();
     * if (!available) {
     *     if (reason === 'disabled') {
     *         console.log('Metrics feature is disabled');
     *     } else {
     *         console.log('Temporary failure:', reason);
     *     }
     * }
     */
    async isAvailable() {
        const result = await this.fetchSummary();

        if (!result.error) {
            return { available: true, reason: 'ok', status: 200 };
        }

        // Distinguish between metrics disabled vs temporary failure
        const status = result.status;
        let reason = 'unknown';

        if (status === 404) {
            reason = 'disabled';  // Endpoint not found - metrics feature disabled
        } else if (status === 503) {
            reason = 'service_unavailable';  // Service temporarily unavailable
        } else if (status >= 500) {
            reason = 'server_error';  // Server-side error
        } else if (status >= 400 && status < 500) {
            reason = 'client_error';  // Client-side error (auth, bad request, etc.)
        } else if (!status) {
            reason = 'network_error';  // Network failure (no response)
        }

        return {
            available: false,
            reason: reason,
            status: status,
            detail: result.detail || result.error
        };
    }
}

// Export singleton instance for global access
export const apiClient = new MetricsAPIClient();

// Also export class for testing or multiple instances
export { MetricsAPIClient };

// Browser globals for non-module usage
if (typeof window !== 'undefined') {
    window.MetricsAPI = {
        apiClient: apiClient,
        MetricsAPIClient: MetricsAPIClient
    };
}
