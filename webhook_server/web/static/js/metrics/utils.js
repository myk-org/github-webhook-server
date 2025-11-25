/**
 * Utility Functions for GitHub Webhook Metrics Dashboard
 *
 * Common helper functions for time formatting, number formatting,
 * data processing, DOM manipulation, and validation.
 *
 * No external dependencies - vanilla JavaScript only.
 */

// ============================================================================
// Time and Duration Formatting
// ============================================================================

/**
 * Format milliseconds to human-readable duration
 * @param {number} ms - Duration in milliseconds
 * @returns {string} Formatted duration (e.g., "5.8s", "1m 30s", "2h 15m")
 */
function formatDuration(ms) {
    if (ms == null || isNaN(ms)) {
        return '-';
    }

    const absMs = Math.abs(ms);

    // Less than 1 second - show milliseconds
    if (absMs < 1000) {
        return `${Math.round(absMs)}ms`;
    }

    // Less than 1 minute - show seconds with 1 decimal
    if (absMs < 60000) {
        return `${(absMs / 1000).toFixed(1)}s`;
    }

    // Less than 1 hour - show minutes and seconds
    if (absMs < 3600000) {
        const mins = Math.floor(absMs / 60000);
        const secs = Math.floor((absMs % 60000) / 1000);
        return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
    }

    // Hours and minutes
    const hours = Math.floor(absMs / 3600000);
    const mins = Math.floor((absMs % 3600000) / 60000);
    return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
}

/**
 * Format ISO timestamp to local time
 * @param {string} isoString - ISO 8601 timestamp
 * @param {boolean} includeSeconds - Whether to include seconds in output
 * @returns {string} Formatted local time (e.g., "2024-11-24 14:35:22")
 */
function formatTimestamp(isoString, includeSeconds = true) {
    if (!isoString) {
        return '-';
    }

    try {
        const date = new Date(isoString);
        if (isNaN(date.getTime())) {
            return '-';
        }

        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        const hours = String(date.getHours()).padStart(2, '0');
        const minutes = String(date.getMinutes()).padStart(2, '0');
        const seconds = String(date.getSeconds()).padStart(2, '0');

        if (includeSeconds) {
            return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
        }
        return `${year}-${month}-${day} ${hours}:${minutes}`;
    } catch (error) {
        console.error('Error formatting timestamp:', error);
        return '-';
    }
}

/**
 * Format ISO timestamp to relative time
 * @param {string} isoString - ISO 8601 timestamp
 * @returns {string} Relative time (e.g., "2 minutes ago", "5 hours ago")
 */
function formatRelativeTime(isoString) {
    if (!isoString) {
        return '-';
    }

    try {
        const date = new Date(isoString);
        if (isNaN(date.getTime())) {
            return '-';
        }

        const now = new Date();
        const diffMs = now - date;
        const diffSec = Math.floor(diffMs / 1000);

        // Future time
        if (diffSec < 0) {
            return 'in the future';
        }

        // Just now (< 10 seconds)
        if (diffSec < 10) {
            return 'just now';
        }

        // Seconds ago (< 1 minute)
        if (diffSec < 60) {
            return `${diffSec} seconds ago`;
        }

        // Minutes ago (< 1 hour)
        const diffMin = Math.floor(diffSec / 60);
        if (diffMin < 60) {
            return diffMin === 1 ? '1 minute ago' : `${diffMin} minutes ago`;
        }

        // Hours ago (< 1 day)
        const diffHours = Math.floor(diffMin / 60);
        if (diffHours < 24) {
            return diffHours === 1 ? '1 hour ago' : `${diffHours} hours ago`;
        }

        // Days ago (< 30 days)
        const diffDays = Math.floor(diffHours / 24);
        if (diffDays < 30) {
            return diffDays === 1 ? '1 day ago' : `${diffDays} days ago`;
        }

        // Months ago (< 12 months)
        const diffMonths = Math.floor(diffDays / 30);
        if (diffMonths < 12) {
            return diffMonths === 1 ? '1 month ago' : `${diffMonths} months ago`;
        }

        // Years ago
        const diffYears = Math.floor(diffMonths / 12);
        return diffYears === 1 ? '1 year ago' : `${diffYears} years ago`;
    } catch (error) {
        console.error('Error formatting relative time:', error);
        return '-';
    }
}

// ============================================================================
// Number Formatting
// ============================================================================

/**
 * Format number with thousand separators
 * @param {number} num - Number to format
 * @returns {string} Formatted number (e.g., "8,745")
 */
function formatNumber(num) {
    if (num == null || isNaN(num)) {
        return '-';
    }

    return num.toLocaleString('en-US');
}

/**
 * Format number as percentage
 * @param {number} num - Number in percentage form (0-100, not 0-1)
 * @param {number} decimals - Number of decimal places
 * @returns {string} Formatted percentage (e.g., "96.32%")
 */
function formatPercentage(num, decimals = 2) {
    if (num == null) {
        return '-';
    }

    const value = Number(num);
    if (!Number.isFinite(value)) {
        return '-';
    }

    return `${value.toFixed(decimals)}%`;
}

/**
 * Format bytes to human-readable size
 * @param {number} bytes - Number of bytes
 * @param {number} decimals - Number of decimal places
 * @returns {string} Formatted size (e.g., "1.5 MB")
 */
function formatBytes(bytes, decimals = 2) {
    if (bytes == null || isNaN(bytes)) {
        return '-';
    }

    if (bytes === 0) {
        return '0 Bytes';
    }

    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB'];
    const i = Math.floor(Math.log(Math.abs(bytes)) / Math.log(k));
    const safeIndex = Math.min(i, sizes.length - 1);
    const size = bytes / Math.pow(k, safeIndex);

    return `${size.toFixed(decimals)} ${sizes[safeIndex]}`;
}

// ============================================================================
// Data Processing
// ============================================================================

/**
 * Calculate trend between current and previous values
 * @param {number} current - Current value
 * @param {number} previous - Previous value
 * @returns {Object} Trend object with direction, value, and icon
 */
function calculateTrend(current, previous) {
    if (current == null || isNaN(current)) {
        return { direction: 'neutral', value: '-', icon: '→' };
    }

    if (previous == null || isNaN(previous) || previous === 0) {
        return { direction: 'neutral', value: '-', icon: '→' };
    }

    const change = ((current - previous) / previous) * 100;

    // No significant change (< 0.1%)
    if (Math.abs(change) < 0.1) {
        return { direction: 'neutral', value: '0%', icon: '→' };
    }

    return {
        direction: change > 0 ? 'up' : 'down',
        value: `${Math.abs(change).toFixed(1)}%`,
        icon: change > 0 ? '↑' : '↓'
    };
}

/**
 * Aggregate events by time range
 * @param {Array} events - Array of event objects with timestamp property
 * @param {string} range - Time range: 'hour', 'day', 'week'
 * @returns {Object} Object with time buckets as keys
 */
function aggregateByTimeRange(events, range = 'hour') {
    if (!Array.isArray(events) || events.length === 0) {
        return {};
    }

    const buckets = {};

    events.forEach(event => {
        if (!event || !event.timestamp) {
            return;
        }

        try {
            const date = new Date(event.timestamp);
            if (isNaN(date.getTime())) {
                return;
            }

            let bucketKey;

            switch (range) {
                case 'hour':
                    // Bucket by hour: "2024-11-24T14"
                    bucketKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}T${String(date.getHours()).padStart(2, '0')}`;
                    break;

                case 'day':
                    // Bucket by day: "2024-11-24"
                    bucketKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
                    break;

                case 'week': {
                    // Bucket by week: "2024-W47"
                    const weekNumber = getWeekNumber(date);
                    bucketKey = `${date.getFullYear()}-W${String(weekNumber).padStart(2, '0')}`;
                    break;
                }

                default:
                    bucketKey = date.toISOString();
            }

            if (!buckets[bucketKey]) {
                buckets[bucketKey] = [];
            }
            buckets[bucketKey].push(event);
        } catch (error) {
            console.error('Error aggregating event:', error);
        }
    });

    return buckets;
}

/**
 * Get ISO week number for a date
 * @param {Date} date - Date object
 * @returns {number} ISO week number (1-53)
 */
function getWeekNumber(date) {
    const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const dayNum = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - dayNum);
    const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
}

/**
 * Calculate success rate percentage
 * @param {number} successful - Number of successful events
 * @param {number} total - Total number of events
 * @returns {number} Success rate percentage (0-100)
 */
function calculateSuccessRate(successful, total) {
    if (total == null || isNaN(total) || total === 0) {
        return 0;
    }

    if (successful == null || isNaN(successful)) {
        return 0;
    }

    return (successful / total) * 100;
}

// ============================================================================
// DOM Helpers
// ============================================================================

/**
 * Escape HTML to prevent XSS attacks
 * @param {string} str - String to escape
 * @returns {string} Escaped string safe for HTML insertion
 */
function escapeHTML(str) {
    if (str == null) {
        return '';
    }

    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

/**
 * Debounce function calls
 * @param {Function} func - Function to debounce
 * @param {number} delay - Delay in milliseconds
 * @returns {Function} Debounced function
 */
function debounce(func, delay = 300) {
    let timeoutId;

    return function debounced(...args) {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => {
            func.apply(this, args);
        }, delay);
    };
}

/**
 * Throttle function calls
 * @param {Function} func - Function to throttle
 * @param {number} limit - Minimum time between calls in milliseconds
 * @returns {Function} Throttled function
 */
function throttle(func, limit = 300) {
    let inThrottle;
    let lastFunc;
    let lastRan;

    return function throttled(...args) {
        if (!inThrottle) {
            func.apply(this, args);
            lastRan = Date.now();
            inThrottle = true;
        } else {
            clearTimeout(lastFunc);
            lastFunc = setTimeout(() => {
                if ((Date.now() - lastRan) >= limit) {
                    func.apply(this, args);
                    lastRan = Date.now();
                }
                inThrottle = false;
            }, limit - (Date.now() - lastRan));
        }
    };
}

// ============================================================================
// Storage Helpers
// ============================================================================

/**
 * Get value from localStorage with fallback
 * @param {string} key - Storage key
 * @param {*} defaultValue - Default value if key not found
 * @returns {*} Stored value or default value
 */
function getLocalStorage(key, defaultValue = null) {
    try {
        const item = localStorage.getItem(key);
        if (item === null) {
            return defaultValue;
        }

        // Try to parse as JSON
        try {
            return JSON.parse(item);
        } catch {
            // Return as string if not valid JSON
            return item;
        }
    } catch (error) {
        console.error('Error reading from localStorage:', error);
        return defaultValue;
    }
}

/**
 * Set value to localStorage safely
 * @param {string} key - Storage key
 * @param {*} value - Value to store
 * @returns {boolean} True if successful, false otherwise
 */
function setLocalStorage(key, value) {
    try {
        const serialized = typeof value === 'string' ? value : JSON.stringify(value);
        localStorage.setItem(key, serialized);
        return true;
    } catch (error) {
        console.error('Error writing to localStorage:', error);
        return false;
    }
}

// ============================================================================
// Validation
// ============================================================================

/**
 * Validate time range
 * @param {string|Date} startTime - Start time
 * @param {string|Date} endTime - End time
 * @returns {boolean} True if valid time range
 */
function isValidTimeRange(startTime, endTime) {
    if (!startTime || !endTime) {
        return false;
    }

    try {
        const start = new Date(startTime);
        const end = new Date(endTime);

        if (isNaN(start.getTime()) || isNaN(end.getTime())) {
            return false;
        }

        // End time must be after start time
        return end > start;
    } catch (error) {
        console.error('Error validating time range:', error);
        return false;
    }
}

/**
 * Validate repository format (org/repo)
 * @param {string} repo - Repository string to validate
 * @returns {boolean} True if valid repository format
 */
function isValidRepository(repo) {
    if (!repo || typeof repo !== 'string') {
        return false;
    }

    // Repository format: org/repo
    // - org: alphanumeric, hyphens (1-39 chars)
    // - repo: alphanumeric, hyphens, underscores, dots (1-100 chars)
    const repoPattern = /^[a-zA-Z0-9-]{1,39}\/[a-zA-Z0-9._-]{1,100}$/;
    return repoPattern.test(repo);
}

// ============================================================================
// Export Functions (for module usage)
// ============================================================================

// Export all functions for potential module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        // Time and Duration
        formatDuration,
        formatTimestamp,
        formatRelativeTime,
        // Number Formatting
        formatNumber,
        formatPercentage,
        formatBytes,
        // Data Processing
        calculateTrend,
        aggregateByTimeRange,
        calculateSuccessRate,
        // DOM Helpers
        escapeHTML,
        debounce,
        throttle,
        // Storage Helpers
        getLocalStorage,
        setLocalStorage,
        // Validation
        isValidTimeRange,
        isValidRepository
    };
}

// Browser globals for non-module usage
if (typeof window !== 'undefined') {
    window.MetricsUtils = {
        // Time and Duration
        formatDuration,
        formatTimestamp,
        formatRelativeTime,
        // Number Formatting
        formatNumber,
        formatPercentage,
        formatBytes,
        // Data Processing
        calculateTrend,
        aggregateByTimeRange,
        calculateSuccessRate,
        // DOM Helpers
        escapeHTML,
        debounce,
        throttle,
        // Storage Helpers
        getLocalStorage,
        setLocalStorage,
        // Validation
        isValidTimeRange,
        isValidRepository
    };
}

// ESM exports (modern module syntax)
export {
    // Time and Duration
    formatDuration,
    formatTimestamp,
    formatRelativeTime,
    // Number Formatting
    formatNumber,
    formatPercentage,
    formatBytes,
    // Data Processing
    calculateTrend,
    aggregateByTimeRange,
    calculateSuccessRate,
    // DOM Helpers
    escapeHTML,
    debounce,
    throttle,
    // Storage Helpers
    getLocalStorage,
    setLocalStorage,
    // Validation
    isValidTimeRange,
    isValidRepository
};
