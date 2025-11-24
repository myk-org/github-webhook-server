/**
 * Metrics Dashboard - Main JavaScript Controller
 *
 * This module handles:
 * - Initial data loading via REST API
 * - KPI card updates
 * - Chart updates via charts.js
 * - Theme management (dark/light mode)
 * - Time range filtering
 * - Manual refresh
 */

// Dashboard Controller
class MetricsDashboard {
    constructor() {
        this.apiClient = null;  // Will be initialized in initialize()
        this.charts = {};  // Will hold Chart.js instances
        this.currentData = {
            summary: null,
            webhooks: null,
            repositories: null
        };
        this.timeRange = '24h';  // Default time range

        this.initialize();
    }

    /**
     * Initialize dashboard - load theme, data, and charts.
     */
    async initialize() {
        console.log('[Dashboard] Initializing metrics dashboard');

        // 1. Initialize API client (from api-client.js loaded globally)
        this.apiClient = window.MetricsAPI?.apiClient;
        if (!this.apiClient) {
            console.error('[Dashboard] MetricsAPI client not found - ensure api-client.js is loaded');
            this.showError('Metrics API client not available. Please refresh the page.');
            return;
        }

        // 2. Set ready status
        this.updateConnectionStatus(true);

        // 3. Initialize theme
        this.initializeTheme();

        // 4. Set up event listeners
        this.setupEventListeners();

        // 5. Populate date inputs with default 24h range logic so they are not empty
        const { startTime, endTime } = this.getTimeRangeDates(this.timeRange);
        const startInput = document.getElementById('startTime');
        const endInput = document.getElementById('endTime');
        if (startInput && endInput) {
            // Format for datetime-local input: YYYY-MM-DDThh:mm
            const formatForInput = (isoString) => {
                const date = new Date(isoString);
                // Adjust for local timezone for display
                const localDate = new Date(date.getTime() - (date.getTimezoneOffset() * 60000));
                return localDate.toISOString().slice(0, 16);
            };
            startInput.value = formatForInput(startTime);
            endInput.value = formatForInput(endTime);
        }

        // 6. Show loading state
        this.showLoading(true);

        try {
            // 7. Load initial data via REST API
            await this.loadInitialData();

            // 8. Initialize charts (calls functions from charts.js)
            this.initializeCharts();

            console.log('[Dashboard] Dashboard initialization complete');
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
            const { startTime, endTime } = this.getTimeRangeDates(this.timeRange);
            console.log(`[Dashboard] Time range: ${this.timeRange} (${startTime} to ${endTime})`);

            // Fetch all data in parallel using apiClient
            // Use bucket='hour' for ranges <= 24h, 'day' for others
            const bucket = (this.timeRange === '1h' || this.timeRange === '24h') ? 'hour' : 'day';

            const [summaryData, webhooksData, reposData, trendsData, contributorsData] = await Promise.all([
                this.apiClient.fetchSummary(startTime, endTime),
                this.apiClient.fetchWebhooks({ limit: 100, start_time: startTime, end_time: endTime }),
                this.apiClient.fetchRepositories(startTime, endTime),
                this.apiClient.fetchTrends(startTime, endTime, bucket).catch(err => {
                    console.warn('[Dashboard] Trends endpoint not available:', err);
                    return { trends: [] }; // Return empty trends if endpoint doesn't exist
                }),
                this.apiClient.fetchContributors(startTime, endTime, 10)
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
            if (trendsData.error) {
                console.error('[Dashboard] Trends fetch error:', trendsData);
                // Don't fail completely if trends fail, just log it
            }

            // Store data
            this.currentData = {
                summary: summaryData.summary || summaryData,
                webhooks: webhooksData.events || webhooksData || [],
                repositories: reposData.repositories || [],
                trends: trendsData.trends || [],
                contributors: contributorsData  // Add contributors data
            };

            console.log('[Dashboard] Initial data loaded:', this.currentData);

            // Update UI with loaded data
            this.updateKPICards(summaryData.summary || summaryData);
            this.updateCharts(this.currentData);

        } catch (error) {
            console.error('[Dashboard] Error loading initial data:', error);
            throw error;
        }
    }

    /**
     * Calculate start and end dates based on selected time range.
     * @param {string} range - Time range identifier
     * @returns {Object} { startTime, endTime } in ISO format
     */
    getTimeRangeDates(range) {
        const now = new Date();
        let start = new Date();

        switch (range) {
            case '1h':
                start.setHours(now.getHours() - 1);
                break;
            case '24h':
                start.setHours(now.getHours() - 24);
                break;
            case '7d':
                start.setDate(now.getDate() - 7);
                break;
            case '30d':
                start.setDate(now.getDate() - 30);
                break;
            case 'custom': {
                // Handle custom range inputs
                const startInput = document.getElementById('startTime');
                const endInput = document.getElementById('endTime');
                if (startInput && endInput && startInput.value && endInput.value) {
                    return {
                        startTime: new Date(startInput.value).toISOString(),
                        endTime: new Date(endInput.value).toISOString()
                    };
                }
                // Fallback to 24h if inputs invalid
                start.setHours(now.getHours() - 24);
                break;
            }
            default:
                // Default to 24h if unknown
                start.setHours(now.getHours() - 24);
        }

        return {
            startTime: start.toISOString(),
            endTime: now.toISOString()
        };
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

        // Total Events - use 0 as fallback, not undefined
        this.updateKPICard('total-events', {
            value: summary.total_events ?? 0,
            trend: summary.total_events_trend ?? 0
        });

        // Success Rate - calculate from available data
        const successRate = summary.success_rate ??
            (summary.total_events > 0 ? (summary.successful_events / summary.total_events * 100) : 0);
        this.updateKPICard('success-rate', {
            value: `${successRate.toFixed(2)}%`,
            trend: summary.success_rate_trend ?? 0
        });

        // Failed Events
        this.updateKPICard('failed-events', {
            value: summary.failed_events ?? 0,
            trend: summary.failed_events_trend ?? 0
        });

        // Average Duration
        const avgDuration = summary.avg_duration_ms ?? summary.avg_processing_time_ms ?? 0;
        this.updateKPICard('avg-duration', {
            value: window.MetricsUtils.formatDuration(avgDuration),
            trend: summary.avg_duration_trend ?? 0
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
        const trends = data.trends;

        try {
            // Update Event Trends Chart (line chart)
            if (this.charts.eventTrends) {
                let trendsData;
                if (trends && trends.length > 0) {
                    // Use aggregated trends data from API
                    trendsData = this.processTrendsData(trends);
                } else if (webhooks) {
                    // Fallback to calculating from webhooks list (less accurate)
                    trendsData = this.prepareEventTrendsData(webhooks);
                }

                if (trendsData) {
                    window.MetricsCharts.updateEventTrendsChart(this.charts.eventTrends, trendsData);
                }
            }

            // Update Event Distribution Chart (pie chart)
            if (this.charts.eventDistribution && summary) {
                const eventDist = summary.event_type_distribution || {};

                if (eventDist && Object.keys(eventDist).length > 0) {
                    const distData = {
                        labels: Object.keys(eventDist),
                        values: Object.values(eventDist)
                    };
                    window.MetricsCharts.updateEventDistributionChart(this.charts.eventDistribution, distData);
                    console.log('[Dashboard] Event distribution chart updated');
                } else {
                    console.warn('[Dashboard] No event type distribution data available');
                }
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

            // Update Recent Events Table
            if (webhooks && Array.isArray(webhooks)) {
                this.updateRecentEventsTable(webhooks);
            } else if (webhooks && Array.isArray(webhooks.events)) {
                // Backward compatibility for old data structure
                this.updateRecentEventsTable(webhooks.events);
            }

            // Update Contributors Tables
            if (data.contributors) {
                this.updateContributorsTables(data.contributors);
            }

            console.log('[Dashboard] Charts updated');
        } catch (error) {
            console.error('[Dashboard] Error updating charts:', error);
        }
    }

    /**
     * Process trends data from API for chart.
     * @param {Array} trends - Trends data from API
     * @returns {Object} Chart data
     */
    processTrendsData(trends) {
        // Sort by bucket time
        const sortedTrends = [...trends].sort((a, b) => new Date(a.bucket) - new Date(b.bucket));

        // Format labels based on bucket granularity
        const labels = sortedTrends.map(t => {
            const date = new Date(t.bucket);
            // Simple heuristic: if buckets are < 24h apart, show time, else date
            // For now just use local time string
            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) +
                   (this.timeRange !== '1h' && this.timeRange !== '24h' ? ` ${date.getMonth() + 1}/${date.getDate()}` : '');
        });

        return {
            labels: labels,
            success: sortedTrends.map(t => t.successful_events),
            errors: sortedTrends.map(t => t.failed_events),
            total: sortedTrends.map(t => t.total_events)
        };
    }

    /**
     * Update repository table with new data.
     *
     * @param {Object} reposData - Repository data ({repositories: [...]})
     */
    updateRepositoryTable(reposData) {
        const tableBody = document.getElementById('repository-table-body');
        if (!tableBody) {
            console.warn('[Dashboard] Repository table body not found');
            return;
        }

        // Handle both {repositories: [...]} and direct array
        const repositories = reposData.repositories || reposData;

        if (!repositories || !Array.isArray(repositories) || repositories.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="3" style="text-align: center;">No repository data available</td></tr>';
            return;
        }

        // Generate table rows - show success_rate as percentage
        const rows = repositories.slice(0, 5).map(repo => {
            const percentage = repo.success_rate || 0; // Already a percentage from API
            return `
                <tr>
                    <td>${this.escapeHtml(repo.repository || 'Unknown')}</td>
                    <td>${repo.total_events || 0}</td>
                    <td>${percentage.toFixed(1)}%</td>
                </tr>
            `;
        }).join('');

        tableBody.innerHTML = rows;
    }

    /**
     * Update recent events table with new data.
     *
     * @param {Array} events - Recent webhook events
     */
    updateRecentEventsTable(events) {
        const tableBody = document.querySelector('#recentEventsTable tbody');
        if (!tableBody) {
            console.warn('[Dashboard] Recent events table body not found');
            return;
        }

        if (!events || !Array.isArray(events) || events.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="4" style="text-align: center;">No recent events</td></tr>';
            return;
        }

        // Generate table rows for last 10 events
        const rows = events.slice(0, 10).map(event => {
            const time = new Date(event.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            const status = event.status || 'unknown';
            const statusClass = status === 'success' ? 'status-success' : status === 'error' ? 'status-error' : 'status-partial';

            return `
                <tr>
                    <td>${time}</td>
                    <td>${this.escapeHtml(event.repository || 'Unknown')}</td>
                    <td>${this.escapeHtml(event.event_type || 'unknown')}</td>
                    <td><span class="${statusClass}">${status}</span></td>
                </tr>
            `;
        }).join('');

        tableBody.innerHTML = rows;
    }

    /**
     * Update PR contributors tables with new data.
     *
     * @param {Object} contributors - Contributors data
     */
    updateContributorsTables(contributors) {
        if (!contributors) {
            console.warn('[Dashboard] No contributors data available');
            return;
        }

        // Update PR Creators table
        this.updateContributorsTable(
            'pr-creators-table-body',
            contributors.pr_creators || [],
            (creator) => `
                <tr>
                    <td>${this.escapeHtml(creator.user)}</td>
                    <td>${creator.total_prs}</td>
                    <td>${creator.merged_prs}</td>
                    <td>${creator.closed_prs}</td>
                    <td>${creator.avg_commits_per_pr || 0}</td>
                </tr>
            `
        );

        // Update PR Reviewers table
        this.updateContributorsTable(
            'pr-reviewers-table-body',
            contributors.pr_reviewers || [],
            (reviewer) => `
                <tr>
                    <td>${this.escapeHtml(reviewer.user)}</td>
                    <td>${reviewer.total_reviews}</td>
                    <td>${reviewer.prs_reviewed}</td>
                    <td>${reviewer.avg_reviews_per_pr}</td>
                </tr>
            `
        );

        // Update PR Approvers table
        this.updateContributorsTable(
            'pr-approvers-table-body',
            contributors.pr_approvers || [],
            (approver) => `
                <tr>
                    <td>${this.escapeHtml(approver.user)}</td>
                    <td>${approver.total_approvals}</td>
                    <td>${approver.prs_approved}</td>
                </tr>
            `
        );
    }

    /**
     * Generic contributor table updater.
     *
     * @param {string} tableBodyId - Table body element ID
     * @param {Array} data - Contributors data array
     * @param {Function} rowGenerator - Function to generate table row HTML
     */
    updateContributorsTable(tableBodyId, data, rowGenerator) {
        const tableBody = document.getElementById(tableBodyId);
        if (!tableBody) {
            console.warn(`[Dashboard] Table body not found: ${tableBodyId}`);
            return;
        }

        if (!data || data.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="5" style="text-align: center;">No data available</td></tr>';
            return;
        }

        const rows = data.map(rowGenerator).join('');
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

        // Custom date inputs
        const startTimeInput = document.getElementById('startTime');
        const endTimeInput = document.getElementById('endTime');

        if (startTimeInput && endTimeInput) {
            const handleCustomDateChange = () => {
                // Switch dropdown to custom if not already
                if (timeRangeSelect && timeRangeSelect.value !== 'custom') {
                    timeRangeSelect.value = 'custom';
                    this.timeRange = 'custom';
                }
                // Only reload if both dates are valid
                if (startTimeInput.value && endTimeInput.value) {
                    this.changeTimeRange('custom');
                }
            };

            startTimeInput.addEventListener('change', handleCustomDateChange);
            endTimeInput.addEventListener('change', handleCustomDateChange);
        }

        // Manual refresh button
        const refreshButton = document.getElementById('refresh-button');
        if (refreshButton) {
            refreshButton.addEventListener('click', () => this.manualRefresh());
        }

        console.log('[Dashboard] Event listeners set up');
    }

    /**
     * Initialize theme from localStorage and apply it.
     */
    initializeTheme() {
        const savedTheme = localStorage.getItem('theme') || 'light';
        document.documentElement.setAttribute('data-theme', savedTheme);
        console.log(`[Dashboard] Theme initialized: ${savedTheme}`);
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

        // If preset selected, populate inputs
        if (timeRange !== 'custom') {
            const { startTime, endTime } = this.getTimeRangeDates(timeRange);
            const startInput = document.getElementById('startTime');
            const endInput = document.getElementById('endTime');

            if (startInput && endInput) {
                // Format for datetime-local input: YYYY-MM-DDThh:mm
                const formatForInput = (isoString) => {
                    const date = new Date(isoString);
                    // Adjust for local timezone for display
                    const localDate = new Date(date.getTime() - (date.getTimezoneOffset() * 60000));
                    return localDate.toISOString().slice(0, 16);
                };
                startInput.value = formatForInput(startTime);
                endInput.value = formatForInput(endTime);
            }
        }

        // For custom range, validation
        if (timeRange === 'custom') {
            const startInput = document.getElementById('startTime');
            const endInput = document.getElementById('endTime');
            if (!startInput?.value || !endInput?.value) {
                return;
            }
        }

        this.showLoading(true);
        try {
            await this.loadInitialData();
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
     * @param {boolean} ready - Dashboard ready status
     */
    updateConnectionStatus(ready) {
        const statusElement = document.getElementById('connection-status');
        const statusText = document.getElementById('statusText');

        if (!statusElement || !statusText) {
            return;
        }

        if (ready) {
            statusElement.className = 'status connected';
            statusText.textContent = 'Ready';
        } else {
            statusElement.className = 'status disconnected';
            statusText.textContent = 'Initializing...';
        }

        console.log(`[Dashboard] Status: ${ready ? 'Ready' : 'Initializing'}`);
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
     * Clean up resources on page unload.
     */
    destroy() {
        console.log('[Dashboard] Destroying dashboard...');

        // Destroy charts
        Object.values(this.charts).forEach(chart => {
            if (chart && typeof chart.destroy === 'function') {
                chart.destroy();
            }
        });

        console.log('[Dashboard] Dashboard destroyed');
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
