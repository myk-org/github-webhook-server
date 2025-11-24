/**
 * Metrics Dashboard - Main JavaScript Controller
 *
 * This module handles:
 * - WebSocket connection for real-time metrics updates
 * - Initial data loading via REST API
 * - KPI card updates
 * - Chart updates via charts.js
 * - Theme management (dark/light mode)
 * - Time range filtering
 */

// WebSocket Client Class with Auto-Reconnect
class MetricsWebSocketClient {
    /**
     * Create a WebSocket client with auto-reconnect capability.
     *
     * @param {string} url - WebSocket URL (ws:// or wss://)
     * @param {Object} options - Configuration options
     * @param {Function} options.onUpdate - Callback for data updates
     * @param {Function} options.onConnectionChange - Callback for connection status changes
     */
    constructor(url, options = {}) {
        this.url = url;
        this.reconnectDelay = 1000;  // Start with 1 second
        this.maxReconnectDelay = 30000;  // Max 30 seconds
        this.onUpdate = options.onUpdate || (() => {});
        this.onConnectionChange = options.onConnectionChange || (() => {});
        this.ws = null;
        this.isManualDisconnect = false;
        this.reconnectTimer = null;

        this.connect();
    }

    /**
     * Establish WebSocket connection with error handling.
     */
    connect() {
        try {
            console.log(`[WebSocket] Connecting to ${this.url}`);
            this.ws = new WebSocket(this.url);

            this.ws.onopen = () => {
                console.log('[WebSocket] Connected successfully');
                this.reconnectDelay = 1000;  // Reset backoff on successful connection
                this.onConnectionChange(true);
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('[WebSocket] Received update:', data);
                    this.onUpdate(data);
                } catch (error) {
                    console.error('[WebSocket] Error parsing message:', error);
                }
            };

            this.ws.onclose = (event) => {
                console.log(`[WebSocket] Disconnected (code: ${event.code}, reason: ${event.reason})`);
                this.onConnectionChange(false);

                // Only attempt reconnection if not manually disconnected
                if (!this.isManualDisconnect) {
                    this.scheduleReconnect();
                }
            };

            this.ws.onerror = (error) => {
                console.error('[WebSocket] Error:', error);
                // Connection will close, triggering onclose which handles reconnection
            };

        } catch (error) {
            console.error('[WebSocket] Error creating WebSocket:', error);
            this.scheduleReconnect();
        }
    }

    /**
     * Schedule reconnection with exponential backoff.
     */
    scheduleReconnect() {
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
        }

        console.log(`[WebSocket] Reconnecting in ${this.reconnectDelay}ms...`);

        this.reconnectTimer = setTimeout(() => {
            this.connect();
        }, this.reconnectDelay);

        // Exponential backoff: double the delay, up to max
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
    }

    /**
     * Manually disconnect WebSocket (prevents auto-reconnect).
     */
    disconnect() {
        console.log('[WebSocket] Manually disconnecting');
        this.isManualDisconnect = true;

        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }

        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }

    /**
     * Send message to server via WebSocket.
     *
     * @param {Object} message - Message to send (will be JSON stringified)
     * @returns {boolean} True if sent successfully, false otherwise
     */
    send(message) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
            return true;
        }
        console.warn('[WebSocket] Cannot send message - connection not open');
        return false;
    }
}


// Dashboard Controller
class MetricsDashboard {
    constructor() {
        this.wsClient = null;
        this.apiClient = null;  // Will be initialized in init()
        this.charts = {};  // Will hold Chart.js instances
        this.currentData = {
            summary: null,
            webhooks: null,
            repositories: null
        };
        this.timeRange = '24h';  // Default time range
        this.autoRefresh = true;

        this.init();
    }

    /**
     * Initialize dashboard - load theme, data, WebSocket, charts.
     */
    async init() {
        console.log('[Dashboard] Initializing...');

        // 1. Initialize API client (from api-client.js loaded globally)
        this.apiClient = window.MetricsAPI?.apiClient;
        if (!this.apiClient) {
            console.error('[Dashboard] MetricsAPI client not found - ensure api-client.js is loaded');
            this.showError('Metrics API client not available. Please refresh the page.');
            return;
        }

        // 2. Load and apply theme from localStorage
        this.loadTheme();

        // 3. Set up event listeners
        this.setupEventListeners();

        // 4. Show loading state
        this.showLoading(true);

        try {
            // 5. Load initial data via REST API
            await this.loadInitialData();

            // 6. Initialize charts (calls functions from charts.js)
            this.initializeCharts();

            // 7. Initialize WebSocket connection for real-time updates
            this.initWebSocket();

            console.log('[Dashboard] Initialization complete');
        } catch (error) {
            console.error('[Dashboard] Initialization error:', error);
            this.showError('Failed to load dashboard data. Please refresh the page.');
        } finally {
            this.showLoading(false);
        }
    }

    /**
     * Load initial data from REST API endpoints.
     */
    async loadInitialData() {
        console.log('[Dashboard] Loading initial data...');

        try {
            // Fetch all data in parallel using apiClient
            const [summaryData, webhooksData, reposData] = await Promise.all([
                this.apiClient.fetchSummary(),
                this.apiClient.fetchWebhooks({ limit: 100 }),
                this.apiClient.fetchRepositories()
            ]);

            // Check for errors in responses
            if (summaryData.error) {
                console.error('[Dashboard] Summary fetch error:', summaryData);
                throw new Error(summaryData.detail || 'Failed to fetch summary data');
            }
            if (webhooksData.error) {
                console.error('[Dashboard] Webhooks fetch error:', webhooksData);
                throw new Error(webhooksData.detail || 'Failed to fetch webhooks data');
            }
            if (reposData.error) {
                console.error('[Dashboard] Repositories fetch error:', reposData);
                throw new Error(reposData.detail || 'Failed to fetch repositories data');
            }

            // Store data
            this.currentData = {
                summary: summaryData,
                webhooks: webhooksData.events || [],
                repositories: reposData.repositories || []
            };

            console.log('[Dashboard] Initial data loaded:', this.currentData);

            // Update UI with loaded data
            this.updateKPICards(summaryData);
            this.updateCharts(this.currentData);

        } catch (error) {
            console.error('[Dashboard] Error loading initial data:', error);
            throw error;
        }
    }

    /**
     * Initialize WebSocket connection for real-time updates.
     */
    initWebSocket() {
        console.log('[Dashboard] Initializing WebSocket...');

        // Construct WebSocket URL
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        const wsUrl = `${protocol}//${host}/metrics/ws`;

        // Create WebSocket client
        this.wsClient = new MetricsWebSocketClient(wsUrl, {
            onUpdate: (data) => this.handleWebSocketUpdate(data),
            onConnectionChange: (connected) => this.updateConnectionStatus(connected)
        });
    }

    /**
     * Handle WebSocket update message.
     *
     * @param {Object} data - Update data from server
     */
    handleWebSocketUpdate(data) {
        console.log('[Dashboard] WebSocket update received:', data);

        if (!data || !data.type) {
            console.warn('[Dashboard] Invalid WebSocket message format');
            return;
        }

        switch (data.type) {
            case 'metric_update':
                this.handleMetricUpdate(data);
                break;

            case 'heartbeat':
                // Server heartbeat - no action needed
                console.debug('[Dashboard] Heartbeat received');
                break;

            default:
                console.warn(`[Dashboard] Unknown message type: ${data.type}`);
        }
    }

    /**
     * Handle metric update from WebSocket.
     *
     * @param {Object} data - Metric update data
     */
    handleMetricUpdate(data) {
        if (!data.data) {
            console.warn('[Dashboard] Metric update missing data');
            return;
        }

        const { event, summary_delta } = data.data;

        // Update summary data with delta
        if (summary_delta && this.currentData.summary) {
            this.applyDeltaToSummary(summary_delta);
            this.updateKPICards(this.currentData.summary);
        }

        // Add new event to webhooks data
        if (event && this.currentData.webhooks) {
            this.addEventToWebhooks(event);
        }

        // Update charts with new data
        this.updateCharts(this.currentData);

        // Show brief notification
        this.showUpdateNotification();
    }

    /**
     * Apply delta changes to summary data.
     *
     * @param {Object} delta - Summary delta from server
     */
    applyDeltaToSummary(delta) {
        if (!this.currentData.summary) {
            return;
        }

        const summary = this.currentData.summary;

        // Apply delta to totals
        if (delta.total_events !== undefined) {
            summary.total_events = (summary.total_events || 0) + delta.total_events;
        }
        if (delta.successful_events !== undefined) {
            summary.successful_events = (summary.successful_events || 0) + delta.successful_events;
        }
        if (delta.failed_events !== undefined) {
            summary.failed_events = (summary.failed_events || 0) + delta.failed_events;
        }

        // Recalculate success rate
        if (summary.total_events > 0) {
            summary.success_rate = (summary.successful_events / summary.total_events) * 100;
        }

        console.log('[Dashboard] Summary updated with delta:', summary);
    }

    /**
     * Add new event to webhooks data.
     *
     * @param {Object} event - New webhook event
     */
    addEventToWebhooks(event) {
        if (!this.currentData.webhooks) {
            this.currentData.webhooks = { events: [] };
        }

        // Prepend new event to list
        this.currentData.webhooks.events.unshift(event);

        // Keep only latest 100 events in memory
        if (this.currentData.webhooks.events.length > 100) {
            this.currentData.webhooks.events = this.currentData.webhooks.events.slice(0, 100);
        }

        console.log('[Dashboard] Event added to webhooks:', event);
    }

    /**
     * Update KPI cards with new data.
     *
     * @param {Object} summary - Summary data
     */
    updateKPICards(summary) {
        if (!summary) {
            console.warn('[Dashboard] No summary data to update KPI cards');
            return;
        }

        // Total Events
        this.updateKPICard('total-events', {
            value: summary.total_events || 0,
            trend: summary.total_events_trend || 0
        });

        // Success Rate
        this.updateKPICard('success-rate', {
            value: `${(summary.success_rate || 0).toFixed(2)}%`,
            trend: summary.success_rate_trend || 0
        });

        // Failed Events
        this.updateKPICard('failed-events', {
            value: summary.failed_events || 0,
            trend: summary.failed_events_trend || 0
        });

        // Average Duration
        const avgDuration = summary.avg_duration_ms || 0;
        this.updateKPICard('avg-duration', {
            value: this.formatDuration(avgDuration),
            trend: summary.avg_duration_trend || 0
        });

        console.log('[Dashboard] KPI cards updated');
    }

    /**
     * Update individual KPI card.
     *
     * @param {string} cardId - KPI card element ID
     * @param {Object} data - Card data
     */
    updateKPICard(cardId, data) {
        const cardElement = document.getElementById(cardId);
        if (!cardElement) {
            console.warn(`[Dashboard] KPI card not found: ${cardId}`);
            return;
        }

        // Update value
        const valueElement = cardElement.querySelector('.kpi-value');
        if (valueElement) {
            valueElement.textContent = data.value;
        }

        // Update trend
        const trendElement = cardElement.querySelector('.kpi-trend');
        if (trendElement) {
            const trend = data.trend || 0;
            const trendClass = trend > 0 ? 'positive' : trend < 0 ? 'negative' : 'neutral';
            const trendIcon = trend > 0 ? '↑' : trend < 0 ? '↓' : '→';

            trendElement.className = `kpi-trend ${trendClass}`;
            trendElement.innerHTML = `
                <span class="trend-icon">${trendIcon}</span>
                <span class="trend-value">${Math.abs(trend).toFixed(1)}%</span>
                <span class="trend-period">vs last period</span>
            `;
        }
    }

    /**
     * Initialize all charts (calls functions from charts.js).
     */
    initializeCharts() {
        console.log('[Dashboard] Initializing charts...');

        if (!window.MetricsCharts) {
            console.error('[Dashboard] MetricsCharts library not loaded');
            return;
        }

        if (!this.currentData.summary || !this.currentData.webhooks || !this.currentData.repositories) {
            console.warn('[Dashboard] Missing data for chart initialization');
            return;
        }

        try {
            // Event Trends Chart (line chart)
            this.charts.eventTrends = window.MetricsCharts.createEventTrendsChart('eventTrendsChart');

            // Event Distribution Pie Chart
            this.charts.eventDistribution = window.MetricsCharts.createEventDistributionChart('eventDistributionChart');

            // API Usage Chart (bar chart)
            this.charts.apiUsage = window.MetricsCharts.createAPIUsageChart('apiUsageChart');

            // Initial chart update with data
            this.updateCharts(this.currentData);

            console.log('[Dashboard] Charts initialized:', Object.keys(this.charts));
        } catch (error) {
            console.error('[Dashboard] Error initializing charts:', error);
        }
    }

    /**
     * Update all charts with new data.
     *
     * @param {Object} data - Complete dashboard data
     */
    updateCharts(data) {
        if (!data || !window.MetricsCharts) {
            console.warn('[Dashboard] No data or MetricsCharts library not available');
            return;
        }

        const summary = data.summary;
        const webhooks = data.webhooks;
        const repositories = data.repositories;

        try {
            // Update Event Trends Chart (line chart)
            if (this.charts.eventTrends && webhooks) {
                const trendsData = this.prepareEventTrendsData(webhooks);
                window.MetricsCharts.updateEventTrendsChart(this.charts.eventTrends, trendsData);
            }

            // Update Event Distribution Chart (pie chart)
            if (this.charts.eventDistribution && summary?.event_type_distribution) {
                const distData = {
                    labels: Object.keys(summary.event_type_distribution),
                    values: Object.values(summary.event_type_distribution)
                };
                window.MetricsCharts.updateEventDistributionChart(this.charts.eventDistribution, distData);
            }

            // Update API Usage Chart (bar chart)
            if (this.charts.apiUsage && repositories) {
                const apiData = this.prepareAPIUsageData(repositories);
                window.MetricsCharts.updateAPIUsageChart(this.charts.apiUsage, apiData);
            }

            // Update Repository Table
            if (repositories) {
                this.updateRepositoryTable({ repositories });
            }

            console.log('[Dashboard] Charts updated');
        } catch (error) {
            console.error('[Dashboard] Error updating charts:', error);
        }
    }

    /**
     * Update repository table with new data.
     *
     * @param {Object} repositories - Repository data
     */
    updateRepositoryTable(repositories) {
        const tableBody = document.getElementById('repository-table-body');
        if (!tableBody) {
            console.warn('[Dashboard] Repository table body not found');
            return;
        }

        if (!repositories || !repositories.repositories || repositories.repositories.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="3" style="text-align: center;">No repository data available</td></tr>';
            return;
        }

        // Generate table rows
        const rows = repositories.repositories.slice(0, 5).map(repo => `
            <tr>
                <td>${this.escapeHtml(repo.repository_name || 'Unknown')}</td>
                <td>${repo.total_events || 0}</td>
                <td>${(repo.percentage || 0).toFixed(1)}%</td>
            </tr>
        `).join('');

        tableBody.innerHTML = rows;
    }

    /**
     * Set up event listeners for UI controls.
     */
    setupEventListeners() {
        // Theme toggle button
        const themeToggle = document.getElementById('theme-toggle');
        if (themeToggle) {
            themeToggle.addEventListener('click', () => this.toggleTheme());
        }

        // Time range selector
        const timeRangeSelect = document.getElementById('time-range-select');
        if (timeRangeSelect) {
            timeRangeSelect.addEventListener('change', (e) => this.changeTimeRange(e.target.value));
        }

        // Auto-refresh toggle
        const autoRefreshToggle = document.getElementById('auto-refresh-toggle');
        if (autoRefreshToggle) {
            autoRefreshToggle.addEventListener('change', (e) => {
                this.autoRefresh = e.target.checked;
                console.log(`[Dashboard] Auto-refresh ${this.autoRefresh ? 'enabled' : 'disabled'}`);
            });
        }

        // Manual refresh button
        const refreshButton = document.getElementById('refresh-button');
        if (refreshButton) {
            refreshButton.addEventListener('click', () => this.manualRefresh());
        }

        console.log('[Dashboard] Event listeners set up');
    }

    /**
     * Load theme from localStorage and apply it.
     */
    loadTheme() {
        const savedTheme = localStorage.getItem('theme') || 'light';
        document.documentElement.setAttribute('data-theme', savedTheme);
        console.log(`[Dashboard] Theme loaded: ${savedTheme}`);
    }

    /**
     * Toggle between dark and light theme.
     */
    toggleTheme() {
        const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
        const newTheme = currentTheme === 'light' ? 'dark' : 'light';

        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);

        console.log(`[Dashboard] Theme changed to: ${newTheme}`);
    }

    /**
     * Change time range and reload data.
     *
     * @param {string} timeRange - New time range ('24h', '7d', '30d', etc.)
     */
    async changeTimeRange(timeRange) {
        console.log(`[Dashboard] Changing time range to: ${timeRange}`);
        this.timeRange = timeRange;

        this.showLoading(true);
        try {
            await this.loadInitialData();
            this.updateCharts(this.currentData);
        } catch (error) {
            console.error('[Dashboard] Error changing time range:', error);
            this.showError('Failed to load data for selected time range');
        } finally {
            this.showLoading(false);
        }
    }

    /**
     * Manually refresh all data.
     */
    async manualRefresh() {
        console.log('[Dashboard] Manual refresh triggered');

        this.showLoading(true);
        try {
            await this.loadInitialData();
            this.updateCharts(this.currentData);
            this.showSuccessNotification('Dashboard refreshed successfully');
        } catch (error) {
            console.error('[Dashboard] Error during manual refresh:', error);
            this.showError('Failed to refresh dashboard');
        } finally {
            this.showLoading(false);
        }
    }

    /**
     * Update connection status indicator.
     *
     * @param {boolean} connected - WebSocket connection status
     */
    updateConnectionStatus(connected) {
        const statusIndicator = document.getElementById('connection-status');
        if (!statusIndicator) {
            return;
        }

        if (connected) {
            statusIndicator.className = 'connection-status connected';
            statusIndicator.title = 'Connected - Real-time updates active';
        } else {
            statusIndicator.className = 'connection-status disconnected';
            statusIndicator.title = 'Disconnected - Attempting to reconnect...';
        }

        console.log(`[Dashboard] Connection status: ${connected ? 'connected' : 'disconnected'}`);
    }

    /**
     * Show loading spinner.
     *
     * @param {boolean} show - Whether to show or hide loading spinner
     */
    showLoading(show) {
        const spinner = document.getElementById('loading-spinner');
        if (spinner) {
            spinner.style.display = show ? 'flex' : 'none';
        }
    }

    /**
     * Show error message.
     *
     * @param {string} message - Error message to display
     */
    showError(message) {
        console.error(`[Dashboard] Error: ${message}`);
        // Could implement toast notification here
        alert(message);
    }

    /**
     * Show brief update notification.
     */
    showUpdateNotification() {
        const notification = document.getElementById('update-notification');
        if (!notification) {
            return;
        }

        notification.style.display = 'block';
        setTimeout(() => {
            notification.style.display = 'none';
        }, 2000);
    }

    /**
     * Show success notification.
     *
     * @param {string} message - Success message
     */
    showSuccessNotification(message) {
        console.log(`[Dashboard] Success: ${message}`);
        // Could implement toast notification here
    }

    /**
     * Prepare event trends data for line chart.
     * Groups events by hour for the last 24 hours.
     *
     * @param {Array} events - Array of webhook events
     * @returns {Object} Chart data with labels, success, errors, and total arrays
     */
    prepareEventTrendsData(events) {
        if (!events || !Array.isArray(events)) {
            return { labels: [], success: [], errors: [], total: [] };
        }

        const now = new Date();
        const hours = [];
        const successCounts = [];
        const errorCounts = [];
        const totalCounts = [];

        // Create 24 hourly buckets
        for (let i = 23; i >= 0; i--) {
            const hour = new Date(now.getTime() - i * 3600000);
            hours.push(hour.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }));
            successCounts.push(0);
            errorCounts.push(0);
            totalCounts.push(0);
        }

        // Count events in each bucket
        events.forEach(event => {
            const eventTime = new Date(event.created_at);
            const hoursDiff = Math.floor((now - eventTime) / 3600000);
            if (hoursDiff >= 0 && hoursDiff < 24) {
                const index = 23 - hoursDiff;
                totalCounts[index]++;
                if (event.status === 'success') {
                    successCounts[index]++;
                } else if (event.status === 'error') {
                    errorCounts[index]++;
                }
            }
        });

        return {
            labels: hours,
            success: successCounts,
            errors: errorCounts,
            total: totalCounts
        };
    }

    /**
     * Prepare API usage data for bar chart.
     * Shows top 7 repositories by API usage.
     *
     * @param {Array} repositories - Array of repository statistics
     * @returns {Object} Chart data with labels and values arrays
     */
    prepareAPIUsageData(repositories) {
        if (!repositories || !Array.isArray(repositories)) {
            return { labels: [], values: [] };
        }

        // Sort by total_api_calls and take top 7
        const sorted = repositories
            .filter(r => r.total_api_calls > 0)
            .sort((a, b) => b.total_api_calls - a.total_api_calls)
            .slice(0, 7);

        return {
            labels: sorted.map(r => r.repository?.split('/')[1] || r.repository || 'Unknown'),
            values: sorted.map(r => r.total_api_calls || 0)
        };
    }

    /**
     * Format duration in milliseconds to human-readable string.
     *
     * @param {number} ms - Duration in milliseconds
     * @returns {string} Formatted duration
     */
    formatDuration(ms) {
        if (ms < 1000) {
            return `${ms}ms`;
        } else if (ms < 60000) {
            return `${(ms / 1000).toFixed(1)}s`;
        } else {
            const minutes = Math.floor(ms / 60000);
            const seconds = ((ms % 60000) / 1000).toFixed(0);
            return `${minutes}m ${seconds}s`;
        }
    }

    /**
     * Escape HTML to prevent XSS.
     *
     * @param {string} text - Text to escape
     * @returns {string} Escaped text
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Clean up resources on page unload.
     */
    destroy() {
        console.log('[Dashboard] Destroying dashboard...');

        // Disconnect WebSocket
        if (this.wsClient) {
            this.wsClient.disconnect();
        }

        // Destroy charts
        Object.values(this.charts).forEach(chart => {
            if (chart && typeof chart.destroy === 'function') {
                chart.destroy();
            }
        });

        console.log('[Dashboard] Dashboard destroyed');
    }
}


// Initialize dashboard on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    console.log('[Dashboard] DOM loaded, initializing dashboard...');

    // Create global dashboard instance
    window.metricsDashboard = new MetricsDashboard();

    // Clean up on page unload
    window.addEventListener('beforeunload', () => {
        if (window.metricsDashboard) {
            window.metricsDashboard.destroy();
        }
    });
});
