let ws = null;
let logEntries = [];

function updateConnectionStatus(connected) {
  const status = document.getElementById('connectionStatus');
  const statusText = document.getElementById('statusText');

  if (connected) {
    status.className = 'status connected';
    statusText.textContent = 'Connected - Real-time updates active';
  } else {
    status.className = 'status disconnected';
    statusText.textContent = 'Disconnected - Real-time updates inactive';
  }
}

function connectWebSocket() {
  if (ws) {
    ws.close();
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

  // Build WebSocket URL with current filter parameters
  const filters = new URLSearchParams();
  const hookId = document.getElementById('hookIdFilter').value.trim();
  const prNumber = document.getElementById('prNumberFilter').value.trim();
  const repository = document.getElementById('repositoryFilter').value.trim();
  const user = document.getElementById('userFilter').value.trim();
  const level = document.getElementById('levelFilter').value;
  const search = document.getElementById('searchFilter').value.trim();

  if (hookId) filters.append('hook_id', hookId);
  if (prNumber) filters.append('pr_number', prNumber);
  if (repository) filters.append('repository', repository);
  if (user) filters.append('github_user', user);
  if (level) filters.append('level', level);
  if (search) filters.append('search', search);

  const wsUrl = `${protocol}//${window.location.host}/logs/ws${filters.toString() ? '?' + filters.toString() : ''}`;

  ws = new WebSocket(wsUrl);

  ws.onopen = function() {
    updateConnectionStatus(true);
    console.log('WebSocket connected');
  };

  ws.onmessage = function(event) {
    const logEntry = JSON.parse(event.data);
    addLogEntry(logEntry);
  };

  ws.onclose = function() {
    updateConnectionStatus(false);
    console.log('WebSocket disconnected');
  };

  ws.onerror = function(error) {
    updateConnectionStatus(false);
    console.error('WebSocket error:', error);
  };
}

function disconnectWebSocket() {
  if (ws) {
    ws.close();
    ws = null;
  }
  updateConnectionStatus(false);
}

// Removed virtual scrolling to prevent scrollbar flashing
// All rendering now uses direct DOM manipulation for stable UI

// Helper function to apply memory bounding to logEntries array
function applyMemoryBounding() {
  const maxEntries = parseInt(document.getElementById('limitFilter').value);
  if (logEntries.length > maxEntries) {
    // Remove oldest entries to keep array size bounded
    logEntries = logEntries.slice(0, maxEntries);
  }
}

function addLogEntry(entry) {
  logEntries.unshift(entry);

  // Apply memory bounding using centralized helper
  applyMemoryBounding();

  clearFilterCache(); // Clear cache when entries change
  renderLogEntriesOptimized();

  // Update displayed count for real-time entries
  updateDisplayedCount();
}

function updateDisplayedCount() {
  const displayedCount = document.getElementById('displayedCount');
  const filteredEntries = filterLogEntries(logEntries);
  displayedCount.textContent = filteredEntries.length;
}

function renderLogEntriesOptimized() {
  const container = document.getElementById('logEntries');
  const filteredEntries = filterLogEntries(logEntries);

  // Always use direct rendering to prevent any scrollbar flashing
  // Completely disabled virtual scrolling to ensure stable UI
  renderLogEntriesDirect(container, filteredEntries);
}

function renderLogEntriesDirect(container, entries) {
  // Use DocumentFragment for efficient DOM manipulation to minimize reflows
  const fragment = document.createDocumentFragment();

  entries.forEach(entry => {
    const entryElement = createLogEntryElement(entry);
    fragment.appendChild(entryElement);
  });

  // Clear and append in one operation to minimize visual flashing
  // Use replaceChildren for better performance and less flashing
  container.replaceChildren(fragment);

  // Debug: Log how many entries were actually rendered
  console.log(`Rendered ${entries.length} entries directly to DOM`);
}

// Virtual scrolling removed to prevent scrollbar flashing
// All rendering now uses direct DOM manipulation only

function createLogEntryElement(entry) {
  const div = document.createElement('div');

  // Whitelist of allowed log levels to prevent class-name injection
  const allowedLevels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'STEP', 'SUCCESS'];
  const safeLevel = allowedLevels.includes(entry.level) ? entry.level : 'INFO'; // Default fallback

  div.className = `log-entry ${safeLevel}`;

  // Use efficient string template
  div.innerHTML = `
    <span class="timestamp">${new Date(entry.timestamp).toLocaleString()}</span>
    <span class="level">[${entry.level}]</span>
    <span class="message">${escapeHtml(entry.message)}</span>
    ${entry.hook_id ? `<span class="hook-id">[Hook: ${escapeHtml(entry.hook_id)}]</span>` : ''}
    ${entry.pr_number ? `<span class="pr-number">[PR: #${entry.pr_number}]</span>` : ''}
    ${entry.repository ? `<span class="repository">[${escapeHtml(entry.repository)}]</span>` : ''}
    ${entry.github_user ? `<span class="user">[User: ${escapeHtml(entry.github_user)}]</span>` : ''}
  `;

  return div;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Alias for backward compatibility
function renderLogEntries() {
  renderLogEntriesOptimized();
}

function renderLogEntriesDirectly(entries) {
  const container = document.getElementById('logEntries');

  // Always use direct rendering for backend-filtered data to ensure all entries show
  renderLogEntriesDirect(container, entries);
}

// Optimized filtering with caching and early exit
let lastFilterHash = '';
let cachedFilteredEntries = [];

function filterLogEntries(entries) {
  const hookId = document.getElementById('hookIdFilter').value.trim();
  const prNumber = document.getElementById('prNumberFilter').value.trim();
  const repository = document.getElementById('repositoryFilter').value.trim();
  const user = document.getElementById('userFilter').value.trim();
  const level = document.getElementById('levelFilter').value;
  const search = document.getElementById('searchFilter').value.trim().toLowerCase();

  // Create hash of current filters for caching
  const filterHash = `${hookId}-${prNumber}-${repository}-${user}-${level}-${search}-${entries.length}`;

  // Return cached result if filters haven't changed
  if (filterHash === lastFilterHash && cachedFilteredEntries.length > 0) {
    return cachedFilteredEntries;
  }

  // Pre-compile search terms for better performance
  const searchTerms = search ? search.split(' ').filter(term => term.length > 0) : [];
  const prNumberInt = prNumber ? parseInt(prNumber) : null;

  // Use optimized filtering with early exits
  const filtered = entries.filter(entry => {
    // Exact matches first (fastest)
    if (hookId && entry.hook_id !== hookId) return false;
    if (prNumberInt && entry.pr_number !== prNumberInt) return false;
    if (repository && entry.repository !== repository) return false;
    if (user && entry.github_user !== user) return false;
    if (level && entry.level !== level) return false;

    // Text search last (slowest)
    if (searchTerms.length > 0) {
      const messageText = entry.message.toLowerCase();
      return searchTerms.every(term => messageText.includes(term));
    }

    return true;
  });

  // Cache the result
  lastFilterHash = filterHash;
  cachedFilteredEntries = filtered;

  return filtered;
}

// Clear filter cache when entries change
function clearFilterCache() {
  lastFilterHash = '';
  cachedFilteredEntries = [];
}

async function loadHistoricalLogs() {
  try {
    // Show loading skeleton
    showLoadingSkeleton();

    // Build API URL with current filter parameters
    const filters = new URLSearchParams();
    const hookId = document.getElementById('hookIdFilter').value.trim();
    const prNumber = document.getElementById('prNumberFilter').value.trim();
    const repository = document.getElementById('repositoryFilter').value.trim();
    const user = document.getElementById('userFilter').value.trim();
    const level = document.getElementById('levelFilter').value;
    const search = document.getElementById('searchFilter').value.trim();
    const limit = document.getElementById('limitFilter').value;

    // Use user-configured limit
    filters.append('limit', limit);
    if (hookId) filters.append('hook_id', hookId);
    if (prNumber) filters.append('pr_number', prNumber);
    if (repository) filters.append('repository', repository);
    if (user) filters.append('github_user', user);
    if (level) filters.append('level', level);
    if (search) filters.append('search', search);

    const response = await fetch(`/logs/api/entries?${filters.toString()}`);

    // Check HTTP status before parsing JSON
    if (!response.ok) {
      let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
      try {
        // Try to parse error message from response body
        const errorData = await response.json();
        if (errorData.detail || errorData.message || errorData.error) {
          errorMessage = errorData.detail || errorData.message || errorData.error;
        }
      } catch (parseError) {
        // If JSON parsing fails, use the status text
      }
      throw new Error(errorMessage);
    }

    const data = await response.json();

    // Update statistics
    updateLogStatistics(data);

    // Progressive loading for large datasets
    if (data.entries.length > 200) {
      await loadEntriesProgressivelyDirect(data.entries);
    } else {
      logEntries = data.entries;
      // Apply memory bounding after loading entries
      applyMemoryBounding();
      clearFilterCache(); // Clear cache when loading new entries
      // Data is already filtered by the backend, render directly without frontend filtering
      renderLogEntriesDirectly(logEntries);
    }

    hideLoadingSkeleton();
  } catch (error) {
    console.error('Error loading historical logs:', error);
    hideLoadingSkeleton();
    showErrorMessage('Failed to load log entries');
  }
}

async function loadEntriesProgressively(entries) {
  const chunkSize = 50;
  logEntries = [];
  clearFilterCache(); // Clear cache when loading new entries

  for (let i = 0; i < entries.length; i += chunkSize) {
    const chunk = entries.slice(i, i + chunkSize);
    logEntries.push(...chunk);
    // Apply memory bounding after each chunk to prevent unbounded growth
    applyMemoryBounding();
    clearFilterCache(); // Clear cache for each chunk
    renderLogEntries();

    // Add small delay to prevent UI blocking
    if (i + chunkSize < entries.length) {
      await new Promise(resolve => setTimeout(resolve, 10));
    }
  }
}

async function loadEntriesProgressivelyDirect(entries) {
  // For backend-filtered data, just render all entries at once
  // Progressive loading isn't needed since data is already filtered and limited
  logEntries = entries;
  // Apply memory bounding after direct assignment
  applyMemoryBounding();
  renderLogEntriesDirectly(logEntries);
  console.log(`Loaded ${entries.length} backend-filtered entries`);
}

function showLoadingSkeleton() {
  const container = document.getElementById('logEntries');
  container.innerHTML = `
    <div class="loading-skeleton">
      ${createSkeletonEntry()}
      ${createSkeletonEntry()}
      ${createSkeletonEntry()}
      ${createSkeletonEntry()}
      ${createSkeletonEntry()}
      <div class="loading-text">Loading log entries...</div>
    </div>
  `;
}

function createSkeletonEntry() {
  return `
    <div class="skeleton-entry">
      <div class="skeleton-line skeleton-timestamp"></div>
      <div class="skeleton-line skeleton-level"></div>
      <div class="skeleton-line skeleton-message"></div>
      <div class="skeleton-line skeleton-meta"></div>
    </div>
  `;
}

function hideLoadingSkeleton() {
  const skeleton = document.querySelector('.loading-skeleton');
  if (skeleton) {
    skeleton.remove();
  }
}

function showErrorMessage(message) {
  const container = document.getElementById('logEntries');
  container.innerHTML = `
    <div class="error-message">
      <span class="error-icon">⚠️</span>
      <span>${message}</span>
      <button id="retryBtn" class="retry-btn">Retry</button>
    </div>
  `;

  // Add event listener to the dynamically created retry button
  const retryBtn = document.getElementById('retryBtn');
  if (retryBtn) {
    retryBtn.addEventListener('click', loadHistoricalLogs);
  }
}

function updateLogStatistics(data) {
  const statsPanel = document.getElementById('logStats');
  const displayedCount = document.getElementById('displayedCount');
  const totalCount = document.getElementById('totalCount');
  const processedCount = document.getElementById('processedCount');

  // Update counts from API response
  displayedCount.textContent = data.entries ? data.entries.length : 0;
  processedCount.textContent = data.entries_processed || '0';

  // Use the total log count estimate for better user information
  totalCount.textContent = data.total_log_count_estimate || 'Unknown';

  // Show the statistics panel
  statsPanel.style.display = 'block';

  // Add indicator for partial scans
  if (data.is_partial_scan) {
    processedCount.innerHTML = `${data.entries_processed} <small style="color: var(--timestamp-color);">(partial scan)</small>`;
  }
}

function clearLogs() {
  logEntries = [];
  clearFilterCache(); // Clear cache when clearing entries

  // Clear the container directly to avoid any scrollbar flashing
  const container = document.getElementById('logEntries');
  container.replaceChildren(); // More efficient than innerHTML = ''

  // Hide stats panel when no entries
  document.getElementById('logStats').style.display = 'none';
}

function exportLogs(format) {
  const filters = new URLSearchParams();
  const hookId = document.getElementById('hookIdFilter').value.trim();
  const prNumber = document.getElementById('prNumberFilter').value.trim();
  const repository = document.getElementById('repositoryFilter').value.trim();
  const user = document.getElementById('userFilter').value.trim();
  const level = document.getElementById('levelFilter').value;
  const search = document.getElementById('searchFilter').value.trim();
  const limit = document.getElementById('limitFilter').value;

  if (hookId) filters.append('hook_id', hookId);
  if (prNumber) filters.append('pr_number', prNumber);
  if (repository) filters.append('repository', repository);
  if (user) filters.append('github_user', user);
  if (level) filters.append('level', level);
  if (search) filters.append('search', search);
  filters.append('limit', limit);
  filters.append('format', format);

  const url = `/logs/api/export?${filters.toString()}`;
  window.open(url, '_blank');
}

function applyFilters() {
  // Reload historical logs with new filters
  loadHistoricalLogs();

  // Reconnect WebSocket with new filters if currently connected
  if (ws && ws.readyState === WebSocket.OPEN) {
    connectWebSocket();
  }
}

// Set up filter event handlers with debouncing
let filterTimeout;
function debounceFilter() {
  // Clear only filter cache, not entry cache
  lastFilterHash = '';

  // Immediate client-side filtering for fast feedback
  renderLogEntries();

  // Debounced server-side filtering for accuracy
  clearTimeout(filterTimeout);
  filterTimeout = setTimeout(() => {
    applyFilters(); // Server-side filter for accurate results
  }, 300); // Slightly longer delay for better UX
}

function clearFilters() {
  document.getElementById('hookIdFilter').value = '';
  document.getElementById('prNumberFilter').value = '';
  document.getElementById('repositoryFilter').value = '';
  document.getElementById('userFilter').value = '';
  document.getElementById('levelFilter').value = '';
  document.getElementById('searchFilter').value = '';
  document.getElementById('limitFilter').value = '1000'; // Reset to default

  // Reload data with cleared filters
  applyFilters();
}

document.getElementById('hookIdFilter').addEventListener('input', debounceFilter);
document.getElementById('prNumberFilter').addEventListener('input', debounceFilter);
document.getElementById('repositoryFilter').addEventListener('input', debounceFilter);
document.getElementById('userFilter').addEventListener('input', debounceFilter);
document.getElementById('levelFilter').addEventListener('change', debounceFilter);
document.getElementById('searchFilter').addEventListener('input', debounceFilter);
document.getElementById('limitFilter').addEventListener('change', debounceFilter);

// Theme management
function toggleTheme() {
  const currentTheme = document.documentElement.getAttribute('data-theme');
  const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

  document.documentElement.setAttribute('data-theme', newTheme);

  // Update theme toggle button icon and accessibility attributes
  const themeToggle = document.querySelector('.theme-toggle');
  themeToggle.textContent = newTheme === 'dark' ? '☀️' : '🌙';
  themeToggle.setAttribute('aria-label', newTheme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
  themeToggle.setAttribute('title', newTheme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');

  // Store theme preference in localStorage
  localStorage.setItem('log-viewer-theme', newTheme);
}

// Initialize theme from localStorage or default to light
function initializeTheme() {
  const savedTheme = localStorage.getItem('log-viewer-theme') || 'light';
  document.documentElement.setAttribute('data-theme', savedTheme);

  // Update theme toggle button icon and accessibility attributes
  const themeToggle = document.querySelector('.theme-toggle');
  themeToggle.textContent = savedTheme === 'dark' ? '☀️' : '🌙';
  themeToggle.setAttribute('aria-label', savedTheme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
  themeToggle.setAttribute('title', savedTheme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
}

// Initialize theme on page load
initializeTheme();

// Initialize timeline collapse state
initializeTimelineState();

// Initialize connection status
updateConnectionStatus(false);

// Initialize event listeners when DOM is ready
function initializeEventListeners() {
  // Theme toggle button
  const themeToggleBtn = document.getElementById('themeToggleBtn');
  if (themeToggleBtn) {
    themeToggleBtn.addEventListener('click', toggleTheme);
  }

  // Control buttons
  const connectBtn = document.getElementById('connectBtn');
  if (connectBtn) {
    connectBtn.addEventListener('click', connectWebSocket);
  }

  const disconnectBtn = document.getElementById('disconnectBtn');
  if (disconnectBtn) {
    disconnectBtn.addEventListener('click', disconnectWebSocket);
  }

  const refreshBtn = document.getElementById('refreshBtn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', loadHistoricalLogs);
  }

  const clearFiltersBtn = document.getElementById('clearFiltersBtn');
  if (clearFiltersBtn) {
    clearFiltersBtn.addEventListener('click', clearFilters);
  }

  const clearLogsBtn = document.getElementById('clearLogsBtn');
  if (clearLogsBtn) {
    clearLogsBtn.addEventListener('click', clearLogs);
  }

  const exportBtn = document.getElementById('exportBtn');
  if (exportBtn) {
    exportBtn.addEventListener('click', () => exportLogs('json'));
  }

  // Timeline header and toggle button
  const timelineHeader = document.getElementById('timelineHeader');
  if (timelineHeader) {
    timelineHeader.addEventListener('click', toggleTimeline);
  }

  const timelineToggle = document.getElementById('timelineToggle');
  if (timelineToggle) {
    timelineToggle.addEventListener('click', (event) => {
      event.stopPropagation();
      toggleTimeline();
    });
  }
}

// Initialize event listeners
initializeEventListeners();

// Load initial data
loadHistoricalLogs();

// Timeline functionality
let currentTimelineData = null;

function showTimeline(hookId) {
  if (!hookId) {
    hideTimeline();
    return;
  }


  // Fetch workflow steps data
  fetch(`/logs/api/workflow-steps/${hookId}`)
    .then(response => {
      if (!response.ok) {
        if (response.status === 404) {
          hideTimeline();
          return;
        }
        throw new Error('Failed to fetch workflow steps');
      }
      return response.json();
    })
    .then(data => {
      currentTimelineData = data;
      renderTimeline(data);
      document.getElementById('timelineSection').style.display = 'block';

      // Ensure the correct collapse state is maintained when showing timeline
      initializeTimelineState();
    })
    .catch(error => {
      hideTimeline();
    });
}

function hideTimeline() {
  document.getElementById('timelineSection').style.display = 'none';
  currentTimelineData = null;
}

function toggleTimeline() {
  const content = document.getElementById('timelineContent');
  const toggle = document.getElementById('timelineToggle');

  if (content.classList.contains('expanded')) {
    // Collapse
    content.classList.remove('expanded');
    content.classList.add('collapsed');
    toggle.textContent = '▶ Expand';

    // Store collapse state in localStorage
    localStorage.setItem('timeline-collapsed', 'true');
  } else {
    // Expand
    content.classList.remove('collapsed');
    content.classList.add('expanded');
    toggle.textContent = '▼ Collapse';

    // Store expand state in localStorage
    localStorage.setItem('timeline-collapsed', 'false');
  }
}

function initializeTimelineState() {
  // Initialize timeline collapse state from localStorage - default to collapsed
  const timelineState = localStorage.getItem('timeline-collapsed');
  const isCollapsed = timelineState === null ? true : timelineState === 'true'; // Default collapsed if no preference set
  const content = document.getElementById('timelineContent');
  const toggle = document.getElementById('timelineToggle');

  if (isCollapsed) {
    content.classList.remove('expanded');
    content.classList.add('collapsed');
    toggle.textContent = '▶ Expand';
  } else {
    content.classList.remove('collapsed');
    content.classList.add('expanded');
    toggle.textContent = '▼ Collapse';
  }
}

function updateTimelineInfo(data) {
  const info = document.getElementById('timelineInfo');
  const duration = data.total_duration_ms > 0 ? `${(data.total_duration_ms / 1000).toFixed(2)}s` : '< 1s';
  info.innerHTML = `
    <div>Hook ID: <strong>${data.hook_id}</strong></div>
    <div>Steps: <strong>${data.step_count}</strong></div>
    <div>Duration: <strong>${duration}</strong></div>
  `;
}

function renderEmptyTimeline() {
  const svg = document.getElementById('timelineSvg');
  svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="var(--text-color)">No workflow steps found</text>';
}

function renderTimelineVisualization(layout, data) {
  const svg = document.getElementById('timelineSvg');

  // Clear existing content
  svg.innerHTML = '';

  // SVG dimensions - much larger and adaptive
  const width = Math.max(1400, layout.totalWidth + 200);
  const height = layout.totalHeight + 150;
  const margin = { left: 75, right: 75, top: 75, bottom: 75 };

  // Update SVG size
  svg.setAttribute('width', width);
  svg.setAttribute('height', height);

  // Draw timeline lines and steps
  layout.lines.forEach((line, lineIndex) => {
    const lineY = margin.top + (lineIndex * layout.lineHeight) + layout.lineHeight / 2;

    // Draw horizontal timeline line for this row
    if (line.steps.length > 0) {
      const lineElement = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      lineElement.setAttribute('class', 'step-line');
      lineElement.setAttribute('x1', margin.left);
      lineElement.setAttribute('y1', lineY);
      lineElement.setAttribute('x2', margin.left + layout.lineWidth);
      lineElement.setAttribute('y2', lineY);
      svg.appendChild(lineElement);
    }

    // Draw steps for this line
    line.steps.forEach((step, stepIndex) => {
      const stepX = margin.left + (stepIndex * layout.stepSpacing) + layout.stepSpacing / 2;

      const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      group.setAttribute('class', 'timeline-step');
      group.setAttribute('data-step-index', step.originalIndex);

      // Step circle - larger
      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('class', `step-circle ${getStepType(step.message)}`);
      circle.setAttribute('cx', stepX);
      circle.setAttribute('cy', lineY);
      circle.setAttribute('r', 12); // Larger circle
      svg.appendChild(circle);
      group.appendChild(circle);

      // Step label - with multi-line text wrapping
      const labelLines = wrapTextToLines(step.message, 25); // Longer text allowed
      labelLines.forEach((line, lineIndex) => {
        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('class', 'step-label');
        label.setAttribute('x', stepX);
        label.setAttribute('y', lineY - 35 + (lineIndex * 14)); // Multi-line spacing
        label.setAttribute('text-anchor', 'middle');
        label.setAttribute('font-size', '12'); // Larger font
        label.textContent = line;
        svg.appendChild(label);
        group.appendChild(label);
      });

      // Time label - larger and positioned better
      const timeLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      timeLabel.setAttribute('class', 'step-time');
      timeLabel.setAttribute('x', stepX);
      timeLabel.setAttribute('y', lineY + 35);
      timeLabel.setAttribute('text-anchor', 'middle');
      timeLabel.setAttribute('font-size', '11'); // Larger time font
      timeLabel.textContent = `+${(step.relative_time_ms / 1000).toFixed(1)}s`;
      svg.appendChild(timeLabel);
      group.appendChild(timeLabel);

      // Step index number - larger and better positioned
      const indexLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      indexLabel.setAttribute('class', 'step-index');
      indexLabel.setAttribute('x', stepX);
      indexLabel.setAttribute('y', lineY + 5);
      indexLabel.setAttribute('text-anchor', 'middle');
      indexLabel.setAttribute('font-size', '13'); // Larger index font
      indexLabel.setAttribute('font-weight', 'bold');
      indexLabel.setAttribute('fill', 'white'); // White text for better contrast
      indexLabel.textContent = (step.originalIndex + 1).toString();
      svg.appendChild(indexLabel);
      group.appendChild(indexLabel);

      // Add hover events
      group.addEventListener('mouseenter', (e) => showTooltip(e, step));
      group.addEventListener('mouseleave', hideTooltip);
      group.addEventListener('click', () => filterByStep(step));

      svg.appendChild(group);
    });
  });
}

function renderTimeline(data) {
  // Update timeline information
  updateTimelineInfo(data);

  // Handle empty state
  if (data.steps.length === 0) {
    renderEmptyTimeline();
    return;
  }

  // Calculate layout for multi-line timeline
  const layout = calculateMultiLineLayout(data.steps, data.total_duration_ms);

  // Render the timeline visualization
  renderTimelineVisualization(layout, data);
}

function getStepType(message) {
  if (message.includes('completed successfully') || message.includes('success')) {
    return 'success';
  } else if (message.includes('failed') || message.includes('error')) {
    return 'failure';
  } else if (message.includes('Starting') || message.includes('Executing')) {
    return 'progress';
  } else {
    return 'info';
  }
}

function truncateText(text, maxLength) {
  return text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
}

function calculateMultiLineLayout(steps, totalDuration) {
  // Layout configuration - much larger for better readability
  const stepsPerLine = 6; // Fewer steps per line for more space
  const stepSpacing = 200; // Much larger horizontal space between steps
  const lineHeight = 120; // Much larger vertical space between lines
  const lineWidth = stepsPerLine * stepSpacing;

  // Organize steps into lines
  const lines = [];
  for (let i = 0; i < steps.length; i += stepsPerLine) {
    const lineSteps = steps.slice(i, i + stepsPerLine).map((step, index) => ({
      ...step,
      originalIndex: i + index
    }));
    lines.push({ steps: lineSteps });
  }

  return {
    lines,
    lineHeight,
    lineWidth,
    stepSpacing,
    totalWidth: lineWidth,
    totalHeight: lines.length * lineHeight
  };
}


function wrapTextToLines(text, maxCharacters) {
  // Smart text wrapping for timeline labels
  const words = text.split(' ');
  const lines = [];
  let currentLine = '';

  for (const word of words) {
    const testLine = currentLine ? `${currentLine} ${word}` : word;
    if (testLine.length <= maxCharacters) {
      currentLine = testLine;
    } else {
      if (currentLine) {
        lines.push(currentLine);
        currentLine = word;
      } else {
        // Single word is too long, truncate it
        lines.push(word.substring(0, maxCharacters - 3) + '...');
        currentLine = '';
      }
    }
  }

  if (currentLine) {
    lines.push(currentLine);
  }

  // Return max 2 lines to prevent overcrowding
  return lines.slice(0, 2);
}

function showTooltip(event, step) {
  const tooltip = document.getElementById('timelineTooltip');
  const timeFromStart = `+${(step.relative_time_ms / 1000).toFixed(2)}s`;

  tooltip.innerHTML = `
    <div><strong>Step:</strong> ${step.message}</div>
    <div><strong>Time:</strong> ${timeFromStart}</div>
    <div><strong>Timestamp:</strong> ${new Date(step.timestamp).toLocaleTimeString()}</div>
    ${step.pr_number ? `<div><strong>PR:</strong> #${step.pr_number}</div>` : ''}
    <div style="margin-top: 5px; font-size: 10px; color: var(--timestamp-color);">Click to filter logs by this step</div>
  `;

  const rect = event.target.getBoundingClientRect();
  const containerRect = document.getElementById('timelineSection').getBoundingClientRect();

  tooltip.style.left = (rect.left - containerRect.left + rect.width / 2) + 'px';
  tooltip.style.top = (rect.top - containerRect.top - tooltip.offsetHeight - 10) + 'px';
  tooltip.style.display = 'block';
}

function hideTooltip() {
  document.getElementById('timelineTooltip').style.display = 'none';
}

function filterByStep(step) {
  // Set search filter to find this specific step message
  document.getElementById('searchFilter').value = step.message.substring(0, 30);
  debounceFilter();
}

// Auto-show timeline when hook ID filter is applied
function checkForTimelineDisplay() {
  const hookId = document.getElementById('hookIdFilter').value.trim();
  if (hookId) {
    showTimeline(hookId);
  } else {
    hideTimeline();
  }
}

// Add timeline check to hook ID filter specifically
document.getElementById('hookIdFilter').addEventListener('input', () => {
  setTimeout(checkForTimelineDisplay, 300); // Small delay to let the value settle
});

// Also check on initial load
setTimeout(checkForTimelineDisplay, 1000);
