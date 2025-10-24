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

// Flow Modal functionality
let currentFlowData = null;

function showTimeline(hookId) {
  // Redirect old timeline calls to new modal
  showFlowModal(hookId);
}

function showFlowModal(hookId) {
  if (!hookId) {
    closeFlowModal();
    return;
  }

  // Fetch workflow steps data
  fetch(`/logs/api/workflow-steps/${hookId}`)
    .then(response => {
      if (!response.ok) {
        if (response.status === 404) {
          console.log('No flow data found for hook ID:', hookId);
          return;
        }
        throw new Error('Failed to fetch workflow steps');
      }
      return response.json();
    })
    .then(data => {
      if (data) {
        currentFlowData = data;
        renderFlowModal(data);
        document.getElementById('flowModal').style.display = 'flex';
      }
    })
    .catch(error => console.error('Error fetching flow data:', error));
}

function closeFlowModal() {
  const modal = document.getElementById('flowModal');
  if (modal) {
    modal.style.display = 'none';
  }
  currentFlowData = null;
}

function renderFlowModal(data) {
  // Render summary section using safe DOM methods
  const summaryElement = document.getElementById('flowSummary');
  if (!summaryElement) return;

  // Clear existing content
  while (summaryElement.firstChild) {
    summaryElement.removeChild(summaryElement.firstChild);
  }

  const title = document.createElement('h3');
  title.textContent = 'Flow Overview';
  summaryElement.appendChild(title);

  const grid = document.createElement('div');
  grid.className = 'flow-summary-grid';

  // Helper to create summary items safely
  const createSummaryItem = (label, value) => {
    const item = document.createElement('div');
    item.className = 'flow-summary-item';

    const labelDiv = document.createElement('div');
    labelDiv.className = 'flow-summary-label';
    labelDiv.textContent = label;

    const valueDiv = document.createElement('div');
    valueDiv.className = 'flow-summary-value';
    valueDiv.textContent = value;

    item.appendChild(labelDiv);
    item.appendChild(valueDiv);
    return item;
  };

  const duration = data.total_duration_ms > 0 ? `${(data.total_duration_ms / 1000).toFixed(2)}s` : '< 1s';

  grid.appendChild(createSummaryItem('Hook ID', data.hook_id));
  grid.appendChild(createSummaryItem('Total Steps', data.step_count.toString()));
  grid.appendChild(createSummaryItem('Duration', duration));

  if (data.steps[0] && data.steps[0].repository) {
    grid.appendChild(createSummaryItem('Repository', data.steps[0].repository));
  }

  summaryElement.appendChild(grid);

  // Render vertical flow visualization using safe DOM methods
  const vizElement = document.getElementById('flowVisualization');
  if (!vizElement) return;

  // Clear existing content
  while (vizElement.firstChild) {
    vizElement.removeChild(vizElement.firstChild);
  }

  if (data.steps.length === 0) {
    const emptyMsg = document.createElement('p');
    emptyMsg.style.textAlign = 'center';
    emptyMsg.style.color = 'var(--timestamp-color)';
    emptyMsg.textContent = 'No workflow steps found';
    vizElement.appendChild(emptyMsg);
    return;
  }

  // Create flow steps
  data.steps.forEach((step, index) => {
    const stepType = getStepType(step.message);
    const timeFromStart = `+${(step.relative_time_ms / 1000).toFixed(2)}s`;
    const timestamp = new Date(step.timestamp).toLocaleTimeString();

    const flowStep = document.createElement('div');
    flowStep.className = `flow-step ${stepType}`;
    flowStep.setAttribute('data-step-index', index.toString());
    flowStep.style.cursor = 'pointer';
    flowStep.addEventListener('click', () => filterByStep(index));

    const stepNumber = document.createElement('div');
    stepNumber.className = 'flow-step-number';
    stepNumber.textContent = (index + 1).toString();

    const stepContent = document.createElement('div');
    stepContent.className = 'flow-step-content';

    const stepTitle = document.createElement('div');
    stepTitle.className = 'flow-step-title';
    stepTitle.textContent = step.message;

    const stepTime = document.createElement('div');
    stepTime.className = 'flow-step-time';

    const timestampSpan = document.createElement('span');
    timestampSpan.textContent = timestamp;

    const durationSpan = document.createElement('span');
    durationSpan.className = 'flow-step-duration';
    durationSpan.textContent = timeFromStart;

    stepTime.appendChild(timestampSpan);
    stepTime.appendChild(durationSpan);

    stepContent.appendChild(stepTitle);
    stepContent.appendChild(stepTime);

    flowStep.appendChild(stepNumber);
    flowStep.appendChild(stepContent);

    vizElement.appendChild(flowStep);
  });

  // Add final status
  const hasErrors = data.steps.some(step => step.level === 'ERROR');
  const finalStatus = document.createElement('div');
  finalStatus.className = hasErrors ? 'flow-error' : 'flow-success';

  const statusTitle = document.createElement('h3');
  statusTitle.textContent = hasErrors ? '⚠️ Flow Completed with Errors' : '✓ Flow Completed Successfully';
  finalStatus.appendChild(statusTitle);

  if (hasErrors) {
    const errorMsg = document.createElement('div');
    errorMsg.className = 'flow-error-message';
    errorMsg.textContent = 'Some steps encountered errors. Check the logs for details.';
    finalStatus.appendChild(errorMsg);
  }

  vizElement.appendChild(finalStatus);
}

function getStepType(message) {
  if (message.includes('completed successfully') || message.includes('success')) {
    return 'success';
  } else if (message.includes('failed') || message.includes('error')) {
    return 'error';
  } else if (message.includes('warning')) {
    return 'warning';
  } else {
    return 'info';
  }
}

function filterByStep(stepIndex) {
  if (!currentFlowData || !currentFlowData.steps[stepIndex]) return;

  const step = currentFlowData.steps[stepIndex];

  // Close modal
  closeFlowModal();

  // Set search filter to find logs related to this step
  const searchText = step.message.substring(0, 50);
  document.getElementById('searchFilter').value = searchText;
  debounceFilter();
}

// Auto-show modal when hook ID filter is applied, close when cleared
function checkForTimelineDisplay() {
  const hookId = document.getElementById('hookIdFilter').value.trim();
  if (hookId) {
    showFlowModal(hookId);
  } else {
    closeFlowModal();
  }
}

// Add modal check to hook ID filter specifically
document.getElementById('hookIdFilter').addEventListener('input', () => {
  setTimeout(checkForTimelineDisplay, 300); // Small delay to let the value settle
});

// Close modal button handler
const closeModalBtn = document.getElementById('closeFlowModal');
if (closeModalBtn) {
  closeModalBtn.addEventListener('click', closeFlowModal);
}

// Close modal when clicking outside
const flowModal = document.getElementById('flowModal');
if (flowModal) {
  flowModal.addEventListener('click', (e) => {
    if (e.target === flowModal) {
      closeFlowModal();
    }
  });
}

// Also check on initial load
setTimeout(checkForTimelineDisplay, 1000);
