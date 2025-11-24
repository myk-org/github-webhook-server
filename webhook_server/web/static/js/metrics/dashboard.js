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
        this.repositoryFilter = '';  // Repository filter (empty = show all)
        this.userFilter = '';  // User filter (empty = show all)

        // Pagination state for each section
        this.pagination = {
            topRepositories: { page: 1, pageSize: 10, total: 0, totalPages: 0 },
            recentEvents: { page: 1, pageSize: 10, total: 0, totalPages: 0 },
            prCreators: { page: 1, pageSize: 10, total: 0, totalPages: 0 },
            prReviewers: { page: 1, pageSize: 10, total: 0, totalPages: 0 },
            prApprovers: { page: 1, pageSize: 10, total: 0, totalPages: 0 },
            userPrs: { page: 1, pageSize: 10, total: 0, totalPages: 0 }
        };

        // Load saved page sizes from localStorage
        Object.keys(this.pagination).forEach(section => {
            const saved = localStorage.getItem(`pageSize_${section}`);
            if (saved) {
                this.pagination[section].pageSize = parseInt(saved);
            }
        });

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

            const [summaryData, webhooksData, reposData, trendsData, contributorsData, userPrsData] = await Promise.all([
                this.apiClient.fetchSummary(startTime, endTime),
                this.apiClient.fetchWebhooks({ page: 1, page_size: 10, start_time: startTime, end_time: endTime }),
                this.apiClient.fetchRepositories(startTime, endTime, { page: 1, page_size: 10 }),
                this.apiClient.fetchTrends(startTime, endTime, bucket).catch(err => {
                    console.warn('[Dashboard] Trends endpoint not available:', err);
                    return { trends: [] }; // Return empty trends if endpoint doesn't exist
                }),
                this.apiClient.fetchContributors(startTime, endTime, 10, { page: 1, page_size: 10 }),
                this.apiClient.fetchUserPRs(startTime, endTime, { page: 1, page_size: 10 }).catch(err => {
                    console.warn('[Dashboard] User PRs endpoint error:', err);
                    return { data: [], pagination: { total: 0, page: 1, page_size: 10, total_pages: 0 } };
                })
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

            // Store data (preserve full paginated responses for tables)
            this.currentData = {
                summary: summaryData.summary || summaryData,
                webhooks: webhooksData,  // Store full response with pagination
                repositories: reposData,  // Store full response with pagination
                trends: trendsData.trends || [],
                contributors: contributorsData,  // Store full response with pagination
                eventTypeDistribution: summaryData.event_type_distribution || {}  // Store top-level event_type_distribution
            };

            console.log('[Dashboard] Initial data loaded:', this.currentData);

            // Update UI with loaded data
            this.updateKPICards(summaryData.summary || summaryData);
            this.updateCharts(this.currentData);

            // Update User PRs table
            console.log('[Dashboard] Updating User PRs table with data:', userPrsData);
            this.updateUserPRsTable(userPrsData);

            // Populate user filter dropdown
            this.populateUserFilter();

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

        // Create working copy to avoid mutating original data
        // This allows filter to be cleared and original data restored
        // Extract arrays from paginated responses for filtering
        const workingData = {
            summary: { ...data.summary },
            webhooks: data.webhooks?.data || data.webhooks || [],
            repositories: data.repositories?.data || data.repositories?.repositories || data.repositories || [],
            trends: data.trends,
            contributors: data.contributors ? {
                pr_creators: data.contributors.pr_creators?.data || data.contributors.pr_creators || [],
                pr_reviewers: data.contributors.pr_reviewers?.data || data.contributors.pr_reviewers || [],
                pr_approvers: data.contributors.pr_approvers?.data || data.contributors.pr_approvers || []
            } : null,
            eventTypeDistribution: data.eventTypeDistribution
        };

        const summary = workingData.summary;
        let webhooks = workingData.webhooks;
        let repositories = workingData.repositories;
        const trends = workingData.trends;

        // Apply repository filter
        let filteredWebhooks = webhooks;
        let filteredRepositories = repositories;
        let filteredContributors = workingData.contributors;
        let filteredSummary = summary;

        if (this.repositoryFilter) {
            // Filter webhooks and repositories
            filteredWebhooks = this.filterDataByRepository(webhooks);
            filteredRepositories = this.filterDataByRepository(repositories);

            // Recalculate event type distribution from filtered webhooks
            const eventTypeCount = {};
            filteredWebhooks.forEach(event => {
                const eventType = event.event_type || 'unknown';
                eventTypeCount[eventType] = (eventTypeCount[eventType] || 0) + 1;
            });
            workingData.eventTypeDistribution = eventTypeCount;

            // Filter contributors by repository
            // Extract repository from webhook events to find users active in this repo
            if (workingData.contributors) {
                const usersInRepo = new Set();
                filteredWebhooks.forEach(event => {
                    const user = event.sender || event.user || (event.payload && (event.payload.sender || event.payload.user));
                    if (user) {
                        usersInRepo.add(user);
                    }
                });

                filteredContributors = {
                    pr_creators: (workingData.contributors.pr_creators || []).filter(c => usersInRepo.has(c.user)),
                    pr_reviewers: (workingData.contributors.pr_reviewers || []).filter(c => usersInRepo.has(c.user)),
                    pr_approvers: (workingData.contributors.pr_approvers || []).filter(c => usersInRepo.has(c.user))
                };
            }

            // Recalculate summary for filtered data
            filteredSummary = {
                ...summary,  // Keep original fields
                total_events: filteredWebhooks.length,
                successful_events: filteredWebhooks.filter(e => e.status === 'success').length,
                failed_events: filteredWebhooks.filter(e => e.status === 'error').length,
            };
            filteredSummary.success_rate = filteredSummary.total_events > 0
                ? (filteredSummary.successful_events / filteredSummary.total_events * 100)
                : 0;

            console.log(`[Dashboard] Filtered by repository: ${filteredWebhooks.length} events, ${filteredRepositories.length} repos`);
        }

        // Apply user filter second (on already-filtered data)
        if (this.userFilter && filteredContributors) {
            filteredContributors = {
                pr_creators: this.filterDataByUser(filteredContributors.pr_creators || []),
                pr_reviewers: this.filterDataByUser(filteredContributors.pr_reviewers || []),
                pr_approvers: this.filterDataByUser(filteredContributors.pr_approvers || [])
            };

            console.log(`[Dashboard] Filtered by user: ${filteredContributors.pr_creators.length} creators, ${filteredContributors.pr_reviewers.length} reviewers, ${filteredContributors.pr_approvers.length} approvers`);
        }

        // ALWAYS update KPI cards (whether filtered or not)
        this.updateKPICards(filteredSummary);

        // Use filtered data for chart updates
        webhooks = filteredWebhooks;
        repositories = filteredRepositories;
        if (filteredContributors) {
            workingData.contributors = filteredContributors;
        }

        try {
            // Update Event Trends Chart (line chart)
            if (this.charts.eventTrends) {
                let trendsData;

                // When filtering by repository, always use filtered webhooks
                if (this.repositoryFilter) {
                    // Use filtered webhooks to calculate trends
                    trendsData = this.prepareEventTrendsData(webhooks);
                    console.log('[Dashboard] Event Trends using filtered webhooks data:', {
                        totalEvents: webhooks.length,
                        errors: trendsData.errors.reduce((a, b) => a + b, 0),
                        success: trendsData.success.reduce((a, b) => a + b, 0)
                    });
                } else if (trends && trends.length > 0) {
                    // Use aggregated trends data from API
                    trendsData = this.processTrendsData(trends);
                    console.log('[Dashboard] Event Trends using API trends data:', {
                        buckets: trends.length,
                        totalFailed: trends.reduce((sum, t) => sum + t.failed_events, 0),
                        totalSuccess: trends.reduce((sum, t) => sum + t.successful_events, 0)
                    });
                } else if (webhooks) {
                    // Fallback to calculating from webhooks list (less accurate)
                    trendsData = this.prepareEventTrendsData(webhooks);
                    console.log('[Dashboard] Event Trends using fallback webhooks data:', {
                        totalEvents: webhooks.length,
                        errors: trendsData.errors.reduce((a, b) => a + b, 0),
                        success: trendsData.success.reduce((a, b) => a + b, 0)
                    });
                }

                if (trendsData) {
                    window.MetricsCharts.updateEventTrendsChart(this.charts.eventTrends, trendsData);
                    console.log('[Dashboard] Event Trends chart data:', {
                        totalErrors: trendsData.errors.reduce((a, b) => a + b, 0),
                        totalSuccess: trendsData.success.reduce((a, b) => a + b, 0),
                        totalTotal: trendsData.total.reduce((a, b) => a + b, 0)
                    });
                }
            }

            // Update Event Distribution Chart (pie chart)
            if (this.charts.eventDistribution && summary) {
                const eventDist = workingData.eventTypeDistribution || summary.event_type_distribution || {};

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
            if (data.repositories) {
                this.updateRepositoryTable(data.repositories);
            }

            // Update Recent Events Table
            if (data.webhooks) {
                this.updateRecentEventsTable(data.webhooks);
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
     * @param {Object} reposData - Repository data with pagination ({repositories: [...], pagination: {...}})
     */
    updateRepositoryTable(reposData) {
        const tableBody = document.getElementById('repository-table-body');
        if (!tableBody) {
            console.warn('[Dashboard] Repository table body not found');
            return;
        }

        // Handle both paginated response and legacy format
        const repositories = reposData.data || reposData.repositories || reposData;
        const pagination = reposData.pagination;

        // Update pagination state if available
        if (pagination) {
            this.pagination.topRepositories = {
                page: pagination.page,
                pageSize: pagination.page_size,
                total: pagination.total,
                totalPages: pagination.total_pages
            };
        }

        if (!repositories || !Array.isArray(repositories) || repositories.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="3" style="text-align: center;">No repository data available</td></tr>';
            return;
        }

        // Generate table rows - show success_rate as percentage
        const rows = repositories.map(repo => {
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

        // Add pagination controls
        const container = document.querySelector('[data-section="top-repositories"] .chart-content');
        const existingControls = container?.querySelector('.pagination-controls');
        if (existingControls) {
            existingControls.remove();
        }

        if (container && pagination) {
            container.insertAdjacentHTML('beforeend', this.createPaginationControls('topRepositories'));
        }
    }

    /**
     * Update recent events table with new data.
     *
     * @param {Object|Array} eventsData - Recent webhook events (can be array or {data: [...], pagination: {...}})
     */
    updateRecentEventsTable(eventsData) {
        const tableBody = document.querySelector('#recentEventsTable tbody');
        if (!tableBody) {
            console.warn('[Dashboard] Recent events table body not found');
            return;
        }

        // Handle both array format and paginated response format
        const events = Array.isArray(eventsData) ? eventsData : (eventsData.data || eventsData.events || []);
        const pagination = Array.isArray(eventsData) ? null : eventsData.pagination;

        // Update pagination state if available
        if (pagination) {
            this.pagination.recentEvents = {
                page: pagination.page,
                pageSize: pagination.page_size,
                total: pagination.total,
                totalPages: pagination.total_pages
            };
        }

        if (!events || !Array.isArray(events) || events.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="4" style="text-align: center;">No recent events</td></tr>';
            return;
        }

        // Generate table rows
        const rows = events.map(event => {
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

        // Add pagination controls
        const container = document.querySelector('[data-section="recent-events"] .chart-content');
        const existingControls = container?.querySelector('.pagination-controls');
        if (existingControls) {
            existingControls.remove();
        }

        if (container && pagination) {
            container.insertAdjacentHTML('beforeend', this.createPaginationControls('recentEvents'));
        }
    }

    /**
     * Update PR contributors tables with new data.
     *
     * @param {Object} contributors - Contributors data with pagination
     */
    updateContributorsTables(contributors) {
        if (!contributors) {
            console.warn('[Dashboard] No contributors data available');
            return;
        }

        // Extract data and pagination for PR Creators
        const prCreatorsData = contributors.pr_creators?.data || contributors.pr_creators || [];
        const prCreatorsPagination = contributors.pr_creators?.pagination;

        if (prCreatorsPagination) {
            this.pagination.prCreators = {
                page: prCreatorsPagination.page,
                pageSize: prCreatorsPagination.page_size,
                total: prCreatorsPagination.total,
                totalPages: prCreatorsPagination.total_pages
            };
        }

        // Update PR Creators table
        this.updateContributorsTable(
            'pr-creators-table-body',
            prCreatorsData,
            (creator) => `
                <tr>
                    <td><span class="clickable-username" data-user="${this.escapeHtml(creator.user)}">${this.escapeHtml(creator.user)}</span></td>
                    <td>${creator.total_prs}</td>
                    <td>${creator.merged_prs}</td>
                    <td>${creator.closed_prs}</td>
                    <td>${creator.avg_commits_per_pr || 0}</td>
                </tr>
            `
        );

        // Add pagination controls for PR Creators
        const creatorsContainer = document.querySelector('[data-section="pr-creators"]');
        const creatorsExistingControls = creatorsContainer?.querySelector('.pagination-controls');
        if (creatorsExistingControls) {
            creatorsExistingControls.remove();
        }
        if (creatorsContainer && prCreatorsPagination) {
            creatorsContainer.insertAdjacentHTML('beforeend', this.createPaginationControls('prCreators'));
        }

        // Extract data and pagination for PR Reviewers
        const prReviewersData = contributors.pr_reviewers?.data || contributors.pr_reviewers || [];
        const prReviewersPagination = contributors.pr_reviewers?.pagination;

        if (prReviewersPagination) {
            this.pagination.prReviewers = {
                page: prReviewersPagination.page,
                pageSize: prReviewersPagination.page_size,
                total: prReviewersPagination.total,
                totalPages: prReviewersPagination.total_pages
            };
        }

        // Update PR Reviewers table
        this.updateContributorsTable(
            'pr-reviewers-table-body',
            prReviewersData,
            (reviewer) => `
                <tr>
                    <td><span class="clickable-username" data-user="${this.escapeHtml(reviewer.user)}">${this.escapeHtml(reviewer.user)}</span></td>
                    <td>${reviewer.total_reviews}</td>
                    <td>${reviewer.prs_reviewed}</td>
                    <td>${reviewer.avg_reviews_per_pr}</td>
                </tr>
            `
        );

        // Add pagination controls for PR Reviewers
        const reviewersContainer = document.querySelector('[data-section="pr-reviewers"]');
        const reviewersExistingControls = reviewersContainer?.querySelector('.pagination-controls');
        if (reviewersExistingControls) {
            reviewersExistingControls.remove();
        }
        if (reviewersContainer && prReviewersPagination) {
            reviewersContainer.insertAdjacentHTML('beforeend', this.createPaginationControls('prReviewers'));
        }

        // Extract data and pagination for PR Approvers
        const prApproversData = contributors.pr_approvers?.data || contributors.pr_approvers || [];
        const prApproversPagination = contributors.pr_approvers?.pagination;

        if (prApproversPagination) {
            this.pagination.prApprovers = {
                page: prApproversPagination.page,
                pageSize: prApproversPagination.page_size,
                total: prApproversPagination.total,
                totalPages: prApproversPagination.total_pages
            };
        }

        // Update PR Approvers table
        this.updateContributorsTable(
            'pr-approvers-table-body',
            prApproversData,
            (approver) => `
                <tr>
                    <td><span class="clickable-username" data-user="${this.escapeHtml(approver.user)}">${this.escapeHtml(approver.user)}</span></td>
                    <td>${approver.total_approvals}</td>
                    <td>${approver.prs_approved}</td>
                </tr>
            `
        );

        // Add pagination controls for PR Approvers
        const approversContainer = document.querySelector('[data-section="pr-approvers"]');
        const approversExistingControls = approversContainer?.querySelector('.pagination-controls');
        if (approversExistingControls) {
            approversExistingControls.remove();
        }
        if (approversContainer && prApproversPagination) {
            approversContainer.insertAdjacentHTML('beforeend', this.createPaginationControls('prApprovers'));
        }
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

        // Repository filter
        const repositoryFilterInput = document.getElementById('repositoryFilter');
        if (repositoryFilterInput) {
            repositoryFilterInput.addEventListener('input', (e) => this.filterByRepository(e.target.value));
        }

        // User filter
        const userFilterSelect = document.getElementById('userFilter');
        if (userFilterSelect) {
            userFilterSelect.addEventListener('change', (e) => this.filterByUser(e.target.value));
        }

        // Clickable usernames
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('clickable-username')) {
                const username = e.target.dataset.user;
                const userFilterSelect = document.getElementById('userFilter');
                if (userFilterSelect) {
                    userFilterSelect.value = username;
                    this.filterByUser(username);
                }
            }
        });

        // Pagination listeners
        this.setupPaginationListeners();

        // Collapse buttons
        this.setupCollapseButtons();

        // Chart settings buttons
        const eventTrendsSettings = document.getElementById('eventTrendsSettings');
        if (eventTrendsSettings) {
            eventTrendsSettings.addEventListener('click', () => this.openModal('eventTrendsModal'));
        }

        const apiUsageSettings = document.getElementById('apiUsageSettings');
        if (apiUsageSettings) {
            apiUsageSettings.addEventListener('click', () => this.openModal('apiUsageModal'));
        }

        // Close modal buttons
        document.querySelectorAll('.close-modal').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const modal = e.target.closest('.modal');
                if (modal) this.closeModal(modal.id);
            });
        });

        // Click outside modal to close
        document.querySelectorAll('.modal').forEach(modal => {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) this.closeModal(modal.id);
            });
        });

        // Event Trends settings
        document.getElementById('showSuccess')?.addEventListener('change', () => this.updateTrendsVisibility());
        document.getElementById('showErrors')?.addEventListener('change', () => this.updateTrendsVisibility());
        document.getElementById('showTotal')?.addEventListener('change', () => this.updateTrendsVisibility());
        document.querySelectorAll('input[name="trendChartType"]').forEach(radio => {
            radio.addEventListener('change', (e) => this.changeTrendsChartType(e.target.value));
        });
        document.getElementById('exportTrendsCsv')?.addEventListener('click', () => this.exportTrendsData('csv'));
        document.getElementById('exportTrendsJson')?.addEventListener('click', () => this.exportTrendsData('json'));
        document.getElementById('downloadTrendsChart')?.addEventListener('click', () => this.downloadChart('eventTrendsChart'));

        // API Usage settings
        document.getElementById('apiTopN')?.addEventListener('change', (e) => this.updateApiTopN(parseInt(e.target.value)));
        document.querySelectorAll('input[name="apiSortOrder"]').forEach(radio => {
            radio.addEventListener('change', (e) => this.updateApiSortOrder(e.target.value));
        });
        document.querySelectorAll('input[name="apiChartType"]').forEach(radio => {
            radio.addEventListener('change', (e) => this.changeApiChartType(e.target.value));
        });
        document.getElementById('exportApiCsv')?.addEventListener('click', () => this.exportApiData('csv'));
        document.getElementById('exportApiJson')?.addEventListener('click', () => this.exportApiData('json'));
        document.getElementById('downloadApiChart')?.addEventListener('click', () => this.downloadChart('apiUsageChart'));

        console.log('[Dashboard] Event listeners set up');
    }

    /**
     * Set up collapse button listeners and restore collapsed state.
     */
    setupCollapseButtons() {
        const collapseButtons = document.querySelectorAll('.collapse-btn');
        collapseButtons.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const sectionId = e.target.dataset.section;
                this.toggleSection(sectionId);
            });
        });

        // Restore collapsed state from localStorage
        this.restoreCollapsedSections();
    }

    /**
     * Toggle a section's collapsed state.
     * @param {string} sectionId - Section identifier
     */
    toggleSection(sectionId) {
        const section = document.querySelector(`[data-section="${sectionId}"]`);
        if (!section) {
            console.warn(`[Dashboard] Section not found: ${sectionId}`);
            return;
        }

        section.classList.toggle('collapsed');

        // Update button icon
        const btn = section.querySelector(`.collapse-btn[data-section="${sectionId}"]`);
        if (btn) {
            btn.textContent = section.classList.contains('collapsed') ? '▲' : '▼';
            btn.title = section.classList.contains('collapsed') ? 'Expand' : 'Collapse';
        }

        // Save state
        this.saveCollapsedState(sectionId, section.classList.contains('collapsed'));

        console.log(`[Dashboard] Section ${sectionId} ${section.classList.contains('collapsed') ? 'collapsed' : 'expanded'}`);
    }

    /**
     * Save collapsed state to localStorage.
     * @param {string} sectionId - Section identifier
     * @param {boolean} isCollapsed - Whether section is collapsed
     */
    saveCollapsedState(sectionId, isCollapsed) {
        const state = JSON.parse(localStorage.getItem('collapsedSections') || '{}');
        state[sectionId] = isCollapsed;
        localStorage.setItem('collapsedSections', JSON.stringify(state));
    }

    /**
     * Restore collapsed sections from localStorage.
     */
    restoreCollapsedSections() {
        const state = JSON.parse(localStorage.getItem('collapsedSections') || '{}');
        Object.keys(state).forEach(sectionId => {
            if (state[sectionId]) {
                const section = document.querySelector(`[data-section="${sectionId}"]`);
                if (section) {
                    section.classList.add('collapsed');
                    const btn = section.querySelector(`.collapse-btn[data-section="${sectionId}"]`);
                    if (btn) {
                        btn.textContent = '▲';
                        btn.title = 'Expand';
                    }
                }
            }
        });
        console.log('[Dashboard] Collapsed sections restored from localStorage');
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

        // Recreate charts with new theme colors
        if (this.currentData && this.currentData.summary) {
            // Destroy existing charts
            Object.values(this.charts).forEach(chart => {
                if (chart && typeof chart.destroy === 'function') {
                    chart.destroy();
                }
            });

            // Clear charts object
            this.charts = {};

            // Recreate charts with new theme
            this.initializeCharts();
        }
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
     * Filter dashboard data by repository name.
     *
     * @param {string} filterValue - Repository name or partial name to filter by
     */
    filterByRepository(filterValue) {
        const newFilter = filterValue.trim().toLowerCase();

        // Check if filter actually changed
        if (newFilter === this.repositoryFilter) {
            return;  // No change, skip update
        }

        this.repositoryFilter = newFilter;
        console.log(`[Dashboard] Filtering by repository: "${this.repositoryFilter || '(showing all)'}"`);

        // ALWAYS re-render charts and tables (even when filter is cleared)
        if (this.currentData) {
            this.updateCharts(this.currentData);
        }
    }

    /**
     * Filter data array by repository name.
     *
     * @param {Array} data - Array of data objects with 'repository' field
     * @returns {Array} Filtered data
     */
    filterDataByRepository(data) {
        if (!this.repositoryFilter || !Array.isArray(data)) {
            return data;  // No filter or invalid data, return as-is
        }

        return data.filter(item => {
            const repo = (item.repository || '').toLowerCase();
            return repo.includes(this.repositoryFilter);
        });
    }

    /**
     * Filter dashboard data by user.
     *
     * @param {string} filterValue - User to filter by
     */
    filterByUser(filterValue) {
        const newFilter = filterValue.trim();

        // Check if filter actually changed
        if (newFilter === this.userFilter) {
            return;  // No change, skip update
        }

        this.userFilter = newFilter;
        console.log(`[Dashboard] Filtering by user: "${this.userFilter || '(showing all users)'}"`);

        // Re-render charts and tables
        if (this.currentData) {
            this.updateCharts(this.currentData);
        }
    }

    /**
     * Filter data array by user.
     *
     * @param {Array} data - Array of contributor data
     * @returns {Array} Filtered data
     */
    filterDataByUser(data) {
        if (!this.userFilter || !Array.isArray(data)) {
            return data;  // No filter or invalid data, return as-is
        }

        return data.filter(item => {
            const user = (item.user || '').toLowerCase();
            return user === this.userFilter.toLowerCase();
        });
    }

    /**
     * Populate user filter dropdown from contributors data.
     */
    populateUserFilter() {
        const userFilterSelect = document.getElementById('userFilter');
        if (!userFilterSelect) {
            console.warn('[Dashboard] User filter dropdown not found');
            return;
        }

        // Collect all unique users from contributors data
        const users = new Set();

        if (this.currentData.contributors) {
            const { pr_creators, pr_reviewers, pr_approvers } = this.currentData.contributors;

            // Extract data arrays from paginated responses
            const creatorsData = pr_creators?.data || pr_creators || [];
            const reviewersData = pr_reviewers?.data || pr_reviewers || [];
            const approversData = pr_approvers?.data || pr_approvers || [];

            // Add users from all contributor types
            [...creatorsData, ...reviewersData, ...approversData]
                .forEach(contributor => {
                    if (contributor.user) {
                        users.add(contributor.user);
                    }
                });
        }

        // Clear existing options except "All Users"
        userFilterSelect.innerHTML = '<option value="">All Users</option>';

        // Add user options sorted alphabetically
        Array.from(users).sort().forEach(user => {
            const option = document.createElement('option');
            option.value = user;
            option.textContent = user;
            userFilterSelect.appendChild(option);
        });

        console.log(`[Dashboard] User filter populated with ${users.size} users`);
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
     * Shows top N repositories by API usage.
     *
     * @param {Array} repositories - Array of repository statistics
     * @param {number} topN - Number of top repositories to show (default: 7)
     * @param {string} sortOrder - Sort order ('asc' or 'desc', default: 'desc')
     * @returns {Object} Chart data with labels and values arrays
     */
    prepareAPIUsageData(repositories, topN = 7, sortOrder = 'desc') {
        if (!repositories || !Array.isArray(repositories)) {
            return { labels: [], values: [] };
        }

        // Filter and sort by total_api_calls
        let sorted = repositories.filter(r => r.total_api_calls > 0);

        if (sortOrder === 'asc') {
            sorted.sort((a, b) => a.total_api_calls - b.total_api_calls);
        } else {
            sorted.sort((a, b) => b.total_api_calls - a.total_api_calls);
        }

        // Take top N
        sorted = sorted.slice(0, topN);

        return {
            labels: sorted.map(r => r.repository?.split('/')[1] || r.repository || 'Unknown'),
            values: sorted.map(r => r.total_api_calls || 0)
        };
    }

    /**
     * Open a modal dialog.
     * @param {string} modalId - The ID of the modal to open
     */
    openModal(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.classList.add('show');
            console.log(`[Dashboard] Opened modal: ${modalId}`);
        }
    }

    /**
     * Close a modal dialog.
     * @param {string} modalId - The ID of the modal to close
     */
    closeModal(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.classList.remove('show');
            console.log(`[Dashboard] Closed modal: ${modalId}`);
        }
    }

    /**
     * Update Event Trends chart dataset visibility.
     */
    updateTrendsVisibility() {
        const showSuccess = document.getElementById('showSuccess')?.checked;
        const showErrors = document.getElementById('showErrors')?.checked;
        const showTotal = document.getElementById('showTotal')?.checked;

        const chart = this.charts.eventTrends;
        if (chart && chart.data.datasets) {
            // Datasets: [0] Success, [1] Errors, [2] Total
            chart.data.datasets[0].hidden = !showSuccess;
            chart.data.datasets[1].hidden = !showErrors;
            chart.data.datasets[2].hidden = !showTotal;
            chart.update();
            console.log('[Dashboard] Updated Event Trends visibility');
        }
    }

    /**
     * Change Event Trends chart type.
     * @param {string} type - Chart type ('line', 'area', 'bar')
     */
    changeTrendsChartType(type) {
        const chart = this.charts.eventTrends;
        if (chart && chart.data.datasets) {
            chart.data.datasets.forEach(dataset => {
                if (type === 'area') {
                    dataset.fill = true;
                    dataset.type = 'line';
                } else if (type === 'bar') {
                    dataset.fill = false;
                    dataset.type = 'bar';
                } else {
                    dataset.fill = false;
                    dataset.type = 'line';
                }
            });
            chart.update();
            console.log(`[Dashboard] Changed Event Trends chart type to: ${type}`);
        }
    }

    /**
     * Update API Usage chart top N repositories.
     * @param {number} n - Number of top repositories to show
     */
    updateApiTopN(n) {
        if (this.currentData && this.currentData.repositories) {
            const apiData = this.prepareAPIUsageData(this.currentData.repositories, n);
            if (this.charts.apiUsage) {
                window.MetricsCharts.updateAPIUsageChart(this.charts.apiUsage, apiData);
                console.log(`[Dashboard] Updated API Usage to show top ${n} repositories`);
            }
        }
    }

    /**
     * Update API Usage chart sort order.
     * @param {string} order - Sort order ('asc' or 'desc')
     */
    updateApiSortOrder(order) {
        console.log(`[Dashboard] API sort order changed to: ${order}`);
        // Re-render with new sort order
        if (this.currentData && this.currentData.repositories) {
            const apiData = this.prepareAPIUsageData(this.currentData.repositories, undefined, order);
            if (this.charts.apiUsage) {
                window.MetricsCharts.updateAPIUsageChart(this.charts.apiUsage, apiData);
            }
        }
    }

    /**
     * Change API Usage chart type.
     * @param {string} type - Chart type ('bar', 'horizontalBar', 'line')
     */
    changeApiChartType(type) {
        const chart = this.charts.apiUsage;
        if (chart) {
            if (type === 'horizontalBar') {
                chart.config.options.indexAxis = 'y';
                chart.config.type = 'bar';
            } else if (type === 'line') {
                chart.config.options.indexAxis = 'x';
                chart.config.type = 'line';
            } else {
                chart.config.options.indexAxis = 'x';
                chart.config.type = 'bar';
            }
            chart.update();
            console.log(`[Dashboard] Changed API Usage chart type to: ${type}`);
        }
    }

    /**
     * Export Event Trends data.
     * @param {string} format - Export format ('csv' or 'json')
     */
    exportTrendsData(format) {
        const data = this.currentData.trends || [];
        if (data.length === 0) {
            console.warn('[Dashboard] No trends data to export');
            return;
        }
        this.downloadData(data, `event-trends.${format}`, format);
        console.log(`[Dashboard] Exported Event Trends data as ${format}`);
    }

    /**
     * Export API Usage data.
     * @param {string} format - Export format ('csv' or 'json')
     */
    exportApiData(format) {
        const data = this.currentData.repositories || [];
        if (data.length === 0) {
            console.warn('[Dashboard] No API usage data to export');
            return;
        }
        this.downloadData(data, `api-usage.${format}`, format);
        console.log(`[Dashboard] Exported API Usage data as ${format}`);
    }

    /**
     * Download data as CSV or JSON file.
     * @param {Array} data - Data array to download
     * @param {string} filename - Output filename
     * @param {string} format - Format ('csv' or 'json')
     */
    downloadData(data, filename, format) {
        let content, mimeType;

        if (format === 'csv') {
            // Convert to CSV
            if (!data.length) return;
            const headers = Object.keys(data[0]).join(',');
            const rows = data.map(row => Object.values(row).join(','));
            content = [headers, ...rows].join('\n');
            mimeType = 'text/csv';
        } else {
            // JSON format
            content = JSON.stringify(data, null, 2);
            mimeType = 'application/json';
        }

        const blob = new Blob([content], { type: mimeType });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    }

    /**
     * Download chart as PNG image.
     * @param {string} chartId - Canvas element ID
     */
    downloadChart(chartId) {
        const canvas = document.getElementById(chartId);
        if (!canvas) {
            console.warn(`[Dashboard] Canvas not found: ${chartId}`);
            return;
        }

        const url = canvas.toDataURL('image/png');
        const a = document.createElement('a');
        a.href = url;
        a.download = `${chartId}.png`;
        a.click();
        console.log(`[Dashboard] Downloaded chart: ${chartId}`);
    }

    /**
     * Create pagination controls HTML
     * @param {string} section - Section identifier
     * @returns {string} Pagination HTML
     */
    createPaginationControls(section) {
        const state = this.pagination[section];
        const { page, pageSize, total, totalPages } = state;

        const hasNext = page < totalPages;
        const hasPrev = page > 1;

        return `
            <div class="pagination-controls">
                <div class="pagination-size">
                    <label>Show</label>
                    <select class="page-size-select" data-section="${section}">
                        <option value="10" ${pageSize === 10 ? 'selected' : ''}>10</option>
                        <option value="25" ${pageSize === 25 ? 'selected' : ''}>25</option>
                        <option value="50" ${pageSize === 50 ? 'selected' : ''}>50</option>
                        <option value="100" ${pageSize === 100 ? 'selected' : ''}>100</option>
                    </select>
                    <label>per page</label>
                </div>
                <div class="pagination-nav">
                    <button class="btn-pagination" data-section="${section}" data-action="prev"
                            ${!hasPrev ? 'disabled' : ''}>← Prev</button>
                    <span class="pagination-info">Page ${page} of ${totalPages || 1}</span>
                    <button class="btn-pagination" data-section="${section}" data-action="next"
                            ${!hasNext ? 'disabled' : ''}>Next →</button>
                </div>
                <div class="pagination-total">
                    <span>Total: ${total} items</span>
                </div>
            </div>
        `;
    }

    /**
     * Handle page size change
     * @param {string} section - Section identifier
     * @param {number} newSize - New page size
     */
    async changePageSize(section, newSize) {
        this.pagination[section].pageSize = newSize;
        this.pagination[section].page = 1; // Reset to page 1
        localStorage.setItem(`pageSize_${section}`, newSize);

        await this.loadSectionData(section);
    }

    /**
     * Handle page navigation
     * @param {string} section - Section identifier
     * @param {string} action - 'next' or 'prev'
     */
    async navigatePage(section, action) {
        const state = this.pagination[section];

        if (action === 'next' && state.page < state.totalPages) {
            state.page++;
        } else if (action === 'prev' && state.page > 1) {
            state.page--;
        }

        await this.loadSectionData(section);
    }

    /**
     * Set up pagination event listeners
     */
    setupPaginationListeners() {
        // Page size selectors
        document.addEventListener('change', (e) => {
            if (e.target.classList.contains('page-size-select')) {
                const section = e.target.dataset.section;
                const newSize = parseInt(e.target.value);
                this.changePageSize(section, newSize);
            }
        });

        // Navigation buttons
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('btn-pagination')) {
                const section = e.target.dataset.section;
                const action = e.target.dataset.action;
                if (!e.target.disabled) {
                    this.navigatePage(section, action);
                }
            }
        });
    }

    /**
     * Load data for a specific section with pagination
     * @param {string} section - Section identifier
     */
    async loadSectionData(section) {
        const state = this.pagination[section];
        const { startTime, endTime } = this.getTimeRangeDates(this.timeRange);

        this.showLoading(true);

        try {
            let data;
            const params = {
                page: state.page,
                page_size: state.pageSize
            };

            // Add filters
            if (this.repositoryFilter) {
                params.repository = this.repositoryFilter;
            }
            if (this.userFilter) {
                params.user = this.userFilter;
            }

            switch (section) {
                case 'topRepositories':
                    data = await this.apiClient.fetchRepositories(startTime, endTime, params);
                    this.updateRepositoryTable(data);
                    break;
                case 'recentEvents':
                    params.start_time = startTime;
                    params.end_time = endTime;
                    data = await this.apiClient.fetchWebhooks(params);
                    this.updateRecentEventsTable(data);
                    break;
                case 'prCreators':
                case 'prReviewers':
                case 'prApprovers':
                    data = await this.apiClient.fetchContributors(startTime, endTime, state.pageSize, params);
                    this.updateContributorsTables(data);
                    break;
                case 'userPrs':
                    data = await this.apiClient.fetchUserPRs(startTime, endTime, params);
                    this.updateUserPRsTable(data);
                    break;
            }
        } catch (error) {
            console.error(`[Dashboard] Error loading ${section} data:`, error);
        } finally {
            this.showLoading(false);
        }
    }

    /**
     * Update User PRs table with new data.
     * @param {Object} prsData - User PRs data with pagination
     */
    updateUserPRsTable(prsData) {
        const tableBody = document.getElementById('user-prs-table-body');
        if (!tableBody) return;

        const prs = prsData.data || [];
        const pagination = prsData.pagination;

        if (pagination) {
            this.pagination.userPrs = {
                page: pagination.page,
                pageSize: pagination.page_size,
                total: pagination.total,
                totalPages: pagination.total_pages
            };
        }

        if (!prs || prs.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="7" style="text-align: center;">No pull requests found</td></tr>';
        } else {
            const rows = prs.map(pr => {
                const created = new Date(pr.created_at).toLocaleDateString();
                const updated = new Date(pr.updated_at).toLocaleDateString();
                const stateClass = pr.state === 'open' ? 'status-success' : 'status-error';
                const mergedBadge = pr.merged ? '<span class="badge-merged">Merged</span>' : '';

                return `
                    <tr class="pr-row" data-pr-id="${pr.pr_number}">
                        <td>#${pr.pr_number}</td>
                        <td>${this.escapeHtml(pr.title)}</td>
                        <td>${this.escapeHtml(pr.repository)}</td>
                        <td><span class="${stateClass}">${pr.state}</span> ${mergedBadge}</td>
                        <td>${created}</td>
                        <td>${updated}</td>
                        <td>${pr.commits_count || 0}</td>
                    </tr>
                `;
            }).join('');
            tableBody.innerHTML = rows;
        }

        // Add pagination controls
        const container = document.querySelector('[data-section="user-prs"] .chart-content');
        const existingControls = container?.querySelector('.pagination-controls');
        if (existingControls) {
            existingControls.remove();
        }

        if (container && pagination) {
            container.insertAdjacentHTML('beforeend', this.createPaginationControls('userPrs'));
        }
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
