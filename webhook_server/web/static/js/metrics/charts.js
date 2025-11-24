/**
 * Chart.js Configuration for GitHub Webhook Server Metrics Dashboard
 *
 * Provides chart creation, update, and theme management functions for all
 * visualizations in the metrics dashboard.
 *
 * Chart Types:
 * - Event Trends Chart (line) - Shows success/error/total events over time
 * - Event Distribution Chart (pie) - Shows breakdown of event types
 * - API Usage Chart (bar) - Shows API calls per day
 *
 * @module charts
 */

// ============================================================================
// Color Schemes
// ============================================================================

const COLORS = {
  success: {
    solid: 'rgba(16, 185, 129, 1)',      // Green
    alpha50: 'rgba(16, 185, 129, 0.5)',
    alpha20: 'rgba(16, 185, 129, 0.2)',
  },
  error: {
    solid: 'rgba(239, 68, 68, 1)',        // Red
    alpha50: 'rgba(239, 68, 68, 0.5)',
    alpha20: 'rgba(239, 68, 68, 0.2)',
  },
  total: {
    solid: 'rgba(37, 99, 235, 1)',        // Blue
    alpha50: 'rgba(37, 99, 235, 0.5)',
    alpha20: 'rgba(37, 99, 235, 0.2)',
  },
  primary: {
    solid: 'rgba(37, 99, 235, 1)',        // Primary blue
    alpha50: 'rgba(37, 99, 235, 0.5)',
    alpha20: 'rgba(37, 99, 235, 0.2)',
  },
  // Pie chart color palette
  pie: [
    'rgba(37, 99, 235, 0.8)',    // Blue
    'rgba(16, 185, 129, 0.8)',   // Green
    'rgba(251, 191, 36, 0.8)',   // Yellow
    'rgba(239, 68, 68, 0.8)',    // Red
    'rgba(168, 85, 247, 0.8)',   // Purple
    'rgba(236, 72, 153, 0.8)',   // Pink
    'rgba(14, 165, 233, 0.8)',   // Sky
    'rgba(34, 197, 94, 0.8)',    // Emerald
    'rgba(249, 115, 22, 0.8)',   // Orange
    'rgba(139, 92, 246, 0.8)',   // Violet
  ],
};

// Theme-specific colors
const THEME_COLORS = {
  light: {
    gridColor: 'rgba(0, 0, 0, 0.1)',
    textColor: '#374151',
    borderColor: '#e5e7eb',
  },
  dark: {
    gridColor: 'rgba(255, 255, 255, 0.1)',
    textColor: '#d1d5db',
    borderColor: '#374151',
  },
};

// ============================================================================
// Chart Creation Functions
// ============================================================================

/**
 * Create Event Trends Chart (Line Chart)
 *
 * Displays three lines:
 * - Success events (green)
 * - Error events (red)
 * - Total events (blue)
 *
 * @param {string} canvasId - Canvas element ID
 * @returns {Chart} Chart.js instance
 */
function createEventTrendsChart(canvasId) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) {
    console.error(`Canvas element with ID '${canvasId}' not found`);
    return null;
  }

  const isDark = document.body.classList.contains('dark-theme');
  const theme = isDark ? THEME_COLORS.dark : THEME_COLORS.light;

  return new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'Success Events',
          data: [],
          borderColor: COLORS.success.solid,
          backgroundColor: COLORS.success.alpha20,
          borderWidth: 2,
          tension: 0.4,
          fill: true,
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: COLORS.success.solid,
          pointBorderColor: '#fff',
          pointBorderWidth: 2,
        },
        {
          label: 'Error Events',
          data: [],
          borderColor: COLORS.error.solid,
          backgroundColor: COLORS.error.alpha20,
          borderWidth: 2,
          tension: 0.4,
          fill: true,
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: COLORS.error.solid,
          pointBorderColor: '#fff',
          pointBorderWidth: 2,
        },
        {
          label: 'Total Events',
          data: [],
          borderColor: COLORS.total.solid,
          backgroundColor: COLORS.total.alpha20,
          borderWidth: 2,
          tension: 0.4,
          fill: true,
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: COLORS.total.solid,
          pointBorderColor: '#fff',
          pointBorderWidth: 2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false,
      },
      plugins: {
        legend: {
          display: true,
          position: 'bottom',
          labels: {
            color: theme.textColor,
            padding: 15,
            font: {
              size: 12,
              weight: '500',
            },
            usePointStyle: true,
            pointStyle: 'circle',
          },
        },
        tooltip: {
          mode: 'index',
          intersect: false,
          backgroundColor: isDark ? 'rgba(31, 41, 55, 0.95)' : 'rgba(255, 255, 255, 0.95)',
          titleColor: theme.textColor,
          bodyColor: theme.textColor,
          borderColor: theme.borderColor,
          borderWidth: 1,
          padding: 12,
          displayColors: true,
          callbacks: {
            title: (tooltipItems) => {
              return tooltipItems[0].label;
            },
            label: (context) => {
              const label = context.dataset.label || '';
              const value = context.parsed.y;
              return `${label}: ${value}`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: {
            display: false,
          },
          ticks: {
            color: theme.textColor,
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 8,
          },
          border: {
            color: theme.borderColor,
          },
        },
        y: {
          beginAtZero: true,
          grid: {
            color: theme.gridColor,
            drawBorder: false,
          },
          ticks: {
            color: theme.textColor,
            precision: 0,
          },
          border: {
            display: false,
          },
        },
      },
    },
  });
}

/**
 * Create Event Distribution Chart (Pie Chart)
 *
 * Displays event types as pie segments with percentage labels.
 *
 * @param {string} canvasId - Canvas element ID
 * @returns {Chart} Chart.js instance
 */
function createEventDistributionChart(canvasId) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) {
    console.error(`Canvas element with ID '${canvasId}' not found`);
    return null;
  }

  const isDark = document.body.classList.contains('dark-theme');
  const theme = isDark ? THEME_COLORS.dark : THEME_COLORS.light;

  return new Chart(ctx, {
    type: 'pie',
    data: {
      labels: [],
      datasets: [
        {
          data: [],
          backgroundColor: COLORS.pie,
          borderColor: isDark ? '#1f2937' : '#ffffff',
          borderWidth: 2,
          hoverBorderWidth: 3,
          hoverOffset: 8,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          position: 'bottom',
          labels: {
            color: theme.textColor,
            padding: 15,
            font: {
              size: 12,
              weight: '500',
            },
            generateLabels: (chart) => {
              const data = chart.data;
              if (data.labels.length && data.datasets.length) {
                const dataset = data.datasets[0];
                const total = dataset.data.reduce((acc, val) => acc + val, 0);

                return data.labels.map((label, i) => {
                  const value = dataset.data[i];
                  const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;

                  return {
                    text: `${label} (${percentage}%)`,
                    fillStyle: dataset.backgroundColor[i],
                    hidden: false,
                    index: i,
                  };
                });
              }
              return [];
            },
          },
        },
        tooltip: {
          backgroundColor: isDark ? 'rgba(31, 41, 55, 0.95)' : 'rgba(255, 255, 255, 0.95)',
          titleColor: theme.textColor,
          bodyColor: theme.textColor,
          borderColor: theme.borderColor,
          borderWidth: 1,
          padding: 12,
          displayColors: true,
          callbacks: {
            label: (context) => {
              const label = context.label || '';
              const value = context.parsed;
              const dataset = context.dataset;
              const total = dataset.data.reduce((acc, val) => acc + val, 0);
              const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : 0;

              return `${label}: ${value} (${percentage}%)`;
            },
          },
        },
      },
    },
  });
}

/**
 * Create API Usage Chart (Bar Chart)
 *
 * Displays API calls per day as vertical bars.
 *
 * @param {string} canvasId - Canvas element ID
 * @returns {Chart} Chart.js instance
 */
function createAPIUsageChart(canvasId) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) {
    console.error(`Canvas element with ID '${canvasId}' not found`);
    return null;
  }

  const isDark = document.body.classList.contains('dark-theme');
  const theme = isDark ? THEME_COLORS.dark : THEME_COLORS.light;

  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels: [],
      datasets: [
        {
          label: 'API Calls',
          data: [],
          backgroundColor: COLORS.primary.alpha50,
          borderColor: COLORS.primary.solid,
          borderWidth: 2,
          borderRadius: 6,
          hoverBackgroundColor: COLORS.primary.solid,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false,
      },
      plugins: {
        legend: {
          display: true,
          position: 'bottom',
          labels: {
            color: theme.textColor,
            padding: 15,
            font: {
              size: 12,
              weight: '500',
            },
            usePointStyle: true,
            pointStyle: 'rectRounded',
          },
        },
        tooltip: {
          backgroundColor: isDark ? 'rgba(31, 41, 55, 0.95)' : 'rgba(255, 255, 255, 0.95)',
          titleColor: theme.textColor,
          bodyColor: theme.textColor,
          borderColor: theme.borderColor,
          borderWidth: 1,
          padding: 12,
          displayColors: true,
          callbacks: {
            title: (tooltipItems) => {
              return tooltipItems[0].label;
            },
            label: (context) => {
              const label = context.dataset.label || '';
              const value = context.parsed.y;
              return `${label}: ${value}`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: {
            display: false,
          },
          ticks: {
            color: theme.textColor,
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 6,
          },
          border: {
            color: theme.borderColor,
          },
        },
        y: {
          beginAtZero: true,
          grid: {
            color: theme.gridColor,
            drawBorder: false,
          },
          ticks: {
            color: theme.textColor,
            precision: 0,
          },
          border: {
            display: false,
          },
        },
      },
    },
  });
}

// ============================================================================
// Chart Update Functions
// ============================================================================

/**
 * Update Event Trends Chart with new data
 *
 * @param {Chart} chart - Chart.js instance
 * @param {Object} data - Chart data
 * @param {Array<string>} data.labels - Time labels
 * @param {Array<number>} data.success - Success event counts
 * @param {Array<number>} data.errors - Error event counts
 * @param {Array<number>} data.total - Total event counts
 */
function updateEventTrendsChart(chart, data) {
  if (!chart || !data) {
    console.error('Invalid chart or data provided to updateEventTrendsChart');
    return;
  }

  // Update labels
  chart.data.labels = data.labels || [];

  // Update datasets
  if (chart.data.datasets[0]) {
    chart.data.datasets[0].data = data.success || [];
  }
  if (chart.data.datasets[1]) {
    chart.data.datasets[1].data = data.errors || [];
  }
  if (chart.data.datasets[2]) {
    chart.data.datasets[2].data = data.total || [];
  }

  // Trigger chart update
  chart.update('active');
}

/**
 * Update Event Distribution Chart with new data
 *
 * @param {Chart} chart - Chart.js instance
 * @param {Object} data - Chart data
 * @param {Array<string>} data.labels - Event type labels
 * @param {Array<number>} data.values - Event counts
 */
function updateEventDistributionChart(chart, data) {
  if (!chart || !data) {
    console.error('Invalid chart or data provided to updateEventDistributionChart');
    return;
  }

  // Update labels
  chart.data.labels = data.labels || [];

  // Update dataset
  if (chart.data.datasets[0]) {
    chart.data.datasets[0].data = data.values || [];

    // Ensure we have enough colors
    const colorCount = COLORS.pie.length;
    const dataCount = data.values ? data.values.length : 0;
    if (dataCount > colorCount) {
      // Generate additional colors if needed
      const additionalColors = [];
      for (let i = 0; i < dataCount - colorCount; i++) {
        const hue = (i * 137.5) % 360; // Golden angle for distribution
        additionalColors.push(`hsla(${hue}, 70%, 60%, 0.8)`);
      }
      chart.data.datasets[0].backgroundColor = [...COLORS.pie, ...additionalColors];
    }
  }

  // Trigger chart update
  chart.update('active');
}

/**
 * Update API Usage Chart with new data
 *
 * @param {Chart} chart - Chart.js instance
 * @param {Object} data - Chart data
 * @param {Array<string>} data.labels - Date labels
 * @param {Array<number>} data.values - API call counts
 */
function updateAPIUsageChart(chart, data) {
  if (!chart || !data) {
    console.error('Invalid chart or data provided to updateAPIUsageChart');
    return;
  }

  // Update labels
  chart.data.labels = data.labels || [];

  // Update dataset
  if (chart.data.datasets[0]) {
    chart.data.datasets[0].data = data.values || [];
  }

  // Trigger chart update
  chart.update('active');
}

// ============================================================================
// Theme Management
// ============================================================================

/**
 * Update chart theme (dark/light mode)
 *
 * @param {Chart} chart - Chart.js instance
 * @param {boolean} isDark - True for dark theme, false for light theme
 */
function updateChartTheme(chart, isDark) {
  if (!chart) {
    console.error('Invalid chart provided to updateChartTheme');
    return;
  }

  const theme = isDark ? THEME_COLORS.dark : THEME_COLORS.light;

  // Update legend colors
  if (chart.options.plugins?.legend?.labels) {
    chart.options.plugins.legend.labels.color = theme.textColor;
  }

  // Update tooltip colors
  if (chart.options.plugins?.tooltip) {
    chart.options.plugins.tooltip.backgroundColor = isDark
      ? 'rgba(31, 41, 55, 0.95)'
      : 'rgba(255, 255, 255, 0.95)';
    chart.options.plugins.tooltip.titleColor = theme.textColor;
    chart.options.plugins.tooltip.bodyColor = theme.textColor;
    chart.options.plugins.tooltip.borderColor = theme.borderColor;
  }

  // Update scale colors
  if (chart.options.scales?.x) {
    if (chart.options.scales.x.ticks) {
      chart.options.scales.x.ticks.color = theme.textColor;
    }
    if (chart.options.scales.x.border) {
      chart.options.scales.x.border.color = theme.borderColor;
    }
  }

  if (chart.options.scales?.y) {
    if (chart.options.scales.y.grid) {
      chart.options.scales.y.grid.color = theme.gridColor;
    }
    if (chart.options.scales.y.ticks) {
      chart.options.scales.y.ticks.color = theme.textColor;
    }
  }

  // Update pie chart border colors
  if (chart.config.type === 'pie' && chart.data.datasets[0]) {
    chart.data.datasets[0].borderColor = isDark ? '#1f2937' : '#ffffff';
  }

  // Trigger chart update
  chart.update('active');
}

/**
 * Update all charts theme
 *
 * @param {Object} charts - Object containing all chart instances
 * @param {boolean} isDark - True for dark theme, false for light theme
 */
function updateAllChartsTheme(charts, isDark) {
  if (!charts || typeof charts !== 'object') {
    console.error('Invalid charts object provided to updateAllChartsTheme');
    return;
  }

  Object.values(charts).forEach(chart => {
    if (chart) {
      updateChartTheme(chart, isDark);
    }
  });
}

// ============================================================================
// Exports
// ============================================================================

// Export functions for use in dashboard.js
if (typeof module !== 'undefined' && module.exports) {
  // Node.js/CommonJS
  module.exports = {
    createEventTrendsChart,
    createEventDistributionChart,
    createAPIUsageChart,
    updateEventTrendsChart,
    updateEventDistributionChart,
    updateAPIUsageChart,
    updateChartTheme,
    updateAllChartsTheme,
    COLORS,
    THEME_COLORS,
  };
}

// Browser globals
if (typeof window !== 'undefined') {
  window.MetricsCharts = {
    createEventTrendsChart,
    createEventDistributionChart,
    createAPIUsageChart,
    updateEventTrendsChart,
    updateEventDistributionChart,
    updateAPIUsageChart,
    updateChartTheme,
    updateAllChartsTheme,
    COLORS,
    THEME_COLORS,
  };
}
