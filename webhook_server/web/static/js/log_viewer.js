let ws = null;
let logEntries = [];

// Configuration constants
const CONFIG = {
  // Maximum number of entries to fetch when loading PR details
  // This prevents performance issues with very large datasets
  PR_FETCH_LIMIT: 10000,
};

function updateConnectionStatus(connected) {
  const status = document.getElementById("connectionStatus");
  const statusText = document.getElementById("statusText");

  if (connected) {
    status.className = "status connected";
    statusText.textContent = "Connected - Real-time updates active";
  } else {
    status.className = "status disconnected";
    statusText.textContent = "Disconnected - Real-time updates inactive";
  }
}

function connectWebSocket() {
  if (ws) {
    ws.close();
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";

  // Build WebSocket URL with current filter parameters
  const filters = new URLSearchParams();
  const hookId = document.getElementById("hookIdFilter").value.trim();
  const prNumber = document.getElementById("prNumberFilter").value.trim();
  const repository = document.getElementById("repositoryFilter").value.trim();
  const user = document.getElementById("userFilter").value.trim();
  const level = document.getElementById("levelFilter").value;
  const search = document.getElementById("searchFilter").value.trim();

  if (hookId) filters.append("hook_id", hookId);
  if (prNumber) filters.append("pr_number", prNumber);
  if (repository) filters.append("repository", repository);
  if (user) filters.append("github_user", user);
  if (level) filters.append("level", level);
  if (search) filters.append("search", search);

  const wsUrl = `${protocol}//${window.location.host}/logs/ws${
    filters.toString() ? "?" + filters.toString() : ""
  }`;

  ws = new WebSocket(wsUrl);

  ws.onopen = function () {
    updateConnectionStatus(true);
    console.log("WebSocket connected");
  };

  ws.onmessage = function (event) {
    const logEntry = JSON.parse(event.data);
    addLogEntry(logEntry);
  };

  ws.onclose = function () {
    updateConnectionStatus(false);
    console.log("WebSocket disconnected");
  };

  ws.onerror = function (error) {
    updateConnectionStatus(false);
    console.error("WebSocket error:", error);
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
  const maxEntries = parseInt(document.getElementById("limitFilter").value);
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
  const displayedCount = document.getElementById("displayedCount");
  const filteredEntries = filterLogEntries(logEntries);
  displayedCount.textContent = filteredEntries.length;
}

function renderLogEntriesOptimized() {
  const container = document.getElementById("logEntries");
  const filteredEntries = filterLogEntries(logEntries);

  // Always use direct rendering to prevent any scrollbar flashing
  // Completely disabled virtual scrolling to ensure stable UI
  renderLogEntriesDirect(container, filteredEntries);
}

function renderLogEntriesDirect(container, entries) {
  // Use DocumentFragment for efficient DOM manipulation to minimize reflows
  const fragment = document.createDocumentFragment();

  entries.forEach((entry) => {
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
  const div = document.createElement("div");

  // Whitelist of allowed log levels to prevent class-name injection
  const allowedLevels = [
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "STEP",
    "SUCCESS",
  ];
  const safeLevel = allowedLevels.includes(entry.level) ? entry.level : "INFO"; // Default fallback

  div.className = `log-entry ${safeLevel}`;

  // Create timestamp
  const timestamp = document.createElement("span");
  timestamp.className = "timestamp";
  timestamp.textContent = new Date(entry.timestamp).toLocaleString();
  div.appendChild(timestamp);

  // Create level
  const level = document.createElement("span");
  level.className = "level";
  level.textContent = `[${entry.level}]`;
  div.appendChild(level);

  // Create message
  const message = document.createElement("span");
  message.className = "message";
  message.textContent = entry.message;
  div.appendChild(message);

  // Create clickable hook ID link if present
  if (entry.hook_id) {
    const hookIdSpan = document.createElement("span");
    hookIdSpan.className = "hook-id";
    hookIdSpan.textContent = "[Hook: ";

    const hookLink = document.createElement("span");
    hookLink.className = "hook-id-link";
    hookLink.textContent = entry.hook_id;
    hookLink.title = "Click to view workflow";
    hookLink.style.cursor = "pointer";
    hookLink.addEventListener("click", () => {
      showFlowModal(entry.hook_id);
    });

    hookIdSpan.appendChild(hookLink);
    const closeBracket = document.createTextNode("]");
    hookIdSpan.appendChild(closeBracket);
    div.appendChild(hookIdSpan);
  }

  // Add other metadata - make PR number clickable
  if (entry.pr_number) {
    const prSpan = document.createElement("span");
    prSpan.className = "pr-number";
    prSpan.textContent = "[PR: #";

    const prLink = document.createElement("span");
    prLink.className = "pr-number-link";
    prLink.textContent = entry.pr_number;
    prLink.title = "Click to view all webhook flows for this PR";
    prLink.style.cursor = "pointer";
    prLink.addEventListener("click", () => {
      showPrModal(entry.pr_number);
    });

    prSpan.appendChild(prLink);
    const closeBracket = document.createTextNode("]");
    prSpan.appendChild(closeBracket);
    div.appendChild(prSpan);
  }

  if (entry.repository) {
    const repoSpan = document.createElement("span");
    repoSpan.className = "repository";
    repoSpan.textContent = `[${entry.repository}]`;
    div.appendChild(repoSpan);
  }

  if (entry.github_user) {
    const userSpan = document.createElement("span");
    userSpan.className = "user";
    userSpan.textContent = `[User: ${entry.github_user}]`;
    div.appendChild(userSpan);
  }

  return div;
}

// Alias for backward compatibility
function renderLogEntries() {
  renderLogEntriesOptimized();
}

function renderLogEntriesDirectly(entries) {
  const container = document.getElementById("logEntries");

  // Always use direct rendering for backend-filtered data to ensure all entries show
  renderLogEntriesDirect(container, entries);
}

// Optimized filtering with caching and early exit
let lastFilterHash = "";
let cachedFilteredEntries = [];

function filterLogEntries(entries) {
  const hookId = document.getElementById("hookIdFilter").value.trim();
  const prNumber = document.getElementById("prNumberFilter").value.trim();
  const repository = document.getElementById("repositoryFilter").value.trim();
  const user = document.getElementById("userFilter").value.trim();
  const level = document.getElementById("levelFilter").value;
  const search = document
    .getElementById("searchFilter")
    .value.trim()
    .toLowerCase();

  // Create hash of current filters for caching
  const filterHash = `${hookId}-${prNumber}-${repository}-${user}-${level}-${search}-${entries.length}`;

  // Return cached result if filters haven't changed
  if (filterHash === lastFilterHash && cachedFilteredEntries.length > 0) {
    return cachedFilteredEntries;
  }

  // Pre-compile search terms for better performance
  const searchTerms = search
    ? search.split(" ").filter((term) => term.length > 0)
    : [];
  const prNumberInt = prNumber ? parseInt(prNumber) : null;

  // Use optimized filtering with early exits
  const filtered = entries.filter((entry) => {
    // Exact matches first (fastest)
    if (hookId && entry.hook_id !== hookId) return false;
    if (prNumberInt && entry.pr_number !== prNumberInt) return false;
    if (repository && entry.repository !== repository) return false;
    if (user && entry.github_user !== user) return false;
    if (level && entry.level !== level) return false;

    // Text search last (slowest)
    if (searchTerms.length > 0) {
      const messageText = entry.message.toLowerCase();
      return searchTerms.every((term) => messageText.includes(term));
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
  lastFilterHash = "";
  cachedFilteredEntries = [];
}

async function loadHistoricalLogs() {
  try {
    // Show loading skeleton
    showLoadingSkeleton();

    // Build API URL with current filter parameters
    const filters = new URLSearchParams();
    const hookId = document.getElementById("hookIdFilter").value.trim();
    const prNumber = document.getElementById("prNumberFilter").value.trim();
    const repository = document.getElementById("repositoryFilter").value.trim();
    const user = document.getElementById("userFilter").value.trim();
    const level = document.getElementById("levelFilter").value;
    const search = document.getElementById("searchFilter").value.trim();
    const limit = document.getElementById("limitFilter").value;

    // Use user-configured limit
    filters.append("limit", limit);
    if (hookId) filters.append("hook_id", hookId);
    if (prNumber) filters.append("pr_number", prNumber);
    if (repository) filters.append("repository", repository);
    if (user) filters.append("github_user", user);
    if (level) filters.append("level", level);
    if (search) filters.append("search", search);

    const response = await fetch(`/logs/api/entries?${filters.toString()}`);

    // Check HTTP status before parsing JSON
    if (!response.ok) {
      let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
      try {
        // Try to parse error message from response body
        const errorData = await response.json();
        if (errorData.detail || errorData.message || errorData.error) {
          errorMessage =
            errorData.detail || errorData.message || errorData.error;
        }
      } catch {
        // If JSON parsing fails, use the status text
      }
      throw new Error(errorMessage);
    }

    const data = await response.json();

    // Update statistics
    updateLogStatistics(data);

    // Progressive loading for large datasets
    if (data.entries.length > 200) {
      await loadEntriesDirectly(data.entries);
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
    console.error("Error loading historical logs:", error);
    hideLoadingSkeleton();
    showErrorMessage("Failed to load log entries");
  }
}

async function loadEntriesDirectly(entries) {
  // Backend-filtered entries are assigned and rendered all at once
  // All entries are displayed immediately - backend handles chunked streaming
  logEntries = entries;
  // Apply memory bounding after direct assignment
  applyMemoryBounding();
  hideLoadingSkeleton();
  renderLogEntriesDirectly(logEntries);
  console.log(
    `Loaded and rendered ${entries.length} backend-filtered entries at once`,
  );
}

function showLoadingSkeleton() {
  const container = document.getElementById("logEntries");
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
  const skeleton = document.querySelector(".loading-skeleton");
  if (skeleton) {
    skeleton.remove();
  }
}

function showErrorMessage(message) {
  const container = document.getElementById("logEntries");

  // Create error message structure safely using DOM methods to prevent XSS
  const errorDiv = document.createElement("div");
  errorDiv.className = "error-message";

  const iconSpan = document.createElement("span");
  iconSpan.className = "error-icon";
  iconSpan.textContent = "⚠️";

  const messageSpan = document.createElement("span");
  messageSpan.textContent = message; // Safe - automatically escapes HTML

  const retryBtn = document.createElement("button");
  retryBtn.id = "retryBtn";
  retryBtn.className = "retry-btn";
  retryBtn.textContent = "Retry";
  retryBtn.addEventListener("click", loadHistoricalLogs);

  errorDiv.appendChild(iconSpan);
  errorDiv.appendChild(messageSpan);
  errorDiv.appendChild(retryBtn);

  container.replaceChildren(errorDiv);
}

function updateLogStatistics(data) {
  const statsPanel = document.getElementById("logStats");
  const displayedCount = document.getElementById("displayedCount");
  const totalCount = document.getElementById("totalCount");
  const processedCount = document.getElementById("processedCount");

  // Update counts from API response
  displayedCount.textContent = data.entries ? data.entries.length : 0;
  processedCount.textContent = data.entries_processed || "0";

  // Use the total log count estimate for better user information
  totalCount.textContent = data.total_log_count_estimate || "Unknown";

  // Show the statistics panel
  statsPanel.style.display = "block";

  // Add indicator for partial scans
  if (data.is_partial_scan) {
    // Clear existing content and rebuild safely to prevent XSS
    processedCount.textContent = ""; // Clear first

    // Add the count as safe text
    const countText = document.createTextNode(
      String(data.entries_processed || "0") + " ",
    );
    processedCount.appendChild(countText);

    // Add the partial scan indicator
    const partialIndicator = document.createElement("small");
    partialIndicator.style.color = "var(--timestamp-color)";
    partialIndicator.textContent = "(partial scan)";
    processedCount.appendChild(partialIndicator);
  }
}

function clearLogs() {
  logEntries = [];
  clearFilterCache(); // Clear cache when clearing entries

  // Clear the container directly to avoid any scrollbar flashing
  const container = document.getElementById("logEntries");
  container.replaceChildren(); // More efficient than innerHTML = ''

  // Hide stats panel when no entries
  document.getElementById("logStats").style.display = "none";
}

function exportLogs(format) {
  const filters = new URLSearchParams();
  const hookId = document.getElementById("hookIdFilter").value.trim();
  const prNumber = document.getElementById("prNumberFilter").value.trim();
  const repository = document.getElementById("repositoryFilter").value.trim();
  const user = document.getElementById("userFilter").value.trim();
  const level = document.getElementById("levelFilter").value;
  const search = document.getElementById("searchFilter").value.trim();
  const limit = document.getElementById("limitFilter").value;

  if (hookId) filters.append("hook_id", hookId);
  if (prNumber) filters.append("pr_number", prNumber);
  if (repository) filters.append("repository", repository);
  if (user) filters.append("github_user", user);
  if (level) filters.append("level", level);
  if (search) filters.append("search", search);
  filters.append("limit", limit);
  filters.append("format", format);

  const url = `/logs/api/export?${filters.toString()}`;
  window.open(url, "_blank");
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
  lastFilterHash = "";

  // Immediate client-side filtering for fast feedback
  renderLogEntries();

  // Debounced server-side filtering for accuracy
  clearTimeout(filterTimeout);
  filterTimeout = setTimeout(() => {
    applyFilters(); // Server-side filter for accurate results
  }, 300); // Slightly longer delay for better UX
}

function clearFilters() {
  document.getElementById("hookIdFilter").value = "";
  document.getElementById("prNumberFilter").value = "";
  document.getElementById("repositoryFilter").value = "";
  document.getElementById("userFilter").value = "";
  document.getElementById("levelFilter").value = "";
  document.getElementById("searchFilter").value = "";
  document.getElementById("limitFilter").value = "1000"; // Reset to default

  // Reload data with cleared filters
  applyFilters();
}

document
  .getElementById("hookIdFilter")
  .addEventListener("input", debounceFilter);
document
  .getElementById("prNumberFilter")
  .addEventListener("input", debounceFilter);
document
  .getElementById("repositoryFilter")
  .addEventListener("input", debounceFilter);
document.getElementById("userFilter").addEventListener("input", debounceFilter);
document
  .getElementById("levelFilter")
  .addEventListener("change", debounceFilter);
document
  .getElementById("searchFilter")
  .addEventListener("input", debounceFilter);
document
  .getElementById("limitFilter")
  .addEventListener("change", debounceFilter);

// Theme management
function toggleTheme() {
  const currentTheme = document.documentElement.getAttribute("data-theme");
  const newTheme = currentTheme === "dark" ? "light" : "dark";

  document.documentElement.setAttribute("data-theme", newTheme);

  // Update theme toggle button icon and accessibility attributes
  const themeToggle = document.querySelector(".theme-toggle");
  themeToggle.textContent = newTheme === "dark" ? "☀️" : "🌙";
  themeToggle.setAttribute(
    "aria-label",
    newTheme === "dark" ? "Switch to light theme" : "Switch to dark theme",
  );
  themeToggle.setAttribute(
    "title",
    newTheme === "dark" ? "Switch to light theme" : "Switch to dark theme",
  );

  // Store theme preference in localStorage
  localStorage.setItem("log-viewer-theme", newTheme);
}

// Initialize theme from localStorage or default to light
function initializeTheme() {
  const savedTheme = localStorage.getItem("log-viewer-theme") || "light";
  document.documentElement.setAttribute("data-theme", savedTheme);

  // Update theme toggle button icon and accessibility attributes
  const themeToggle = document.querySelector(".theme-toggle");
  themeToggle.textContent = savedTheme === "dark" ? "☀️" : "🌙";
  themeToggle.setAttribute(
    "aria-label",
    savedTheme === "dark" ? "Switch to light theme" : "Switch to dark theme",
  );
  themeToggle.setAttribute(
    "title",
    savedTheme === "dark" ? "Switch to light theme" : "Switch to dark theme",
  );
}

// Initialize theme on page load
initializeTheme();

// Initialize connection status
updateConnectionStatus(false);

// Initialize event listeners when DOM is ready
function initializeEventListeners() {
  // Theme toggle button
  const themeToggleBtn = document.getElementById("themeToggleBtn");
  if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", toggleTheme);
  }

  // Control buttons
  const connectBtn = document.getElementById("connectBtn");
  if (connectBtn) {
    connectBtn.addEventListener("click", connectWebSocket);
  }

  const disconnectBtn = document.getElementById("disconnectBtn");
  if (disconnectBtn) {
    disconnectBtn.addEventListener("click", disconnectWebSocket);
  }

  const refreshBtn = document.getElementById("refreshBtn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", loadHistoricalLogs);
  }

  const clearFiltersBtn = document.getElementById("clearFiltersBtn");
  if (clearFiltersBtn) {
    clearFiltersBtn.addEventListener("click", clearFilters);
  }

  const clearLogsBtn = document.getElementById("clearLogsBtn");
  if (clearLogsBtn) {
    clearLogsBtn.addEventListener("click", clearLogs);
  }

  const exportBtn = document.getElementById("exportBtn");
  if (exportBtn) {
    exportBtn.addEventListener("click", () => exportLogs("json"));
  }

  // Flow modal event listeners
  const closeModalBtn = document.getElementById("closeFlowModal");
  if (closeModalBtn) {
    closeModalBtn.addEventListener("click", closeFlowModal);
  }

  const flowModal = document.getElementById("flowModal");
  if (flowModal) {
    flowModal.addEventListener("click", (e) => {
      if (e.target === flowModal) {
        closeFlowModal();
      }
    });
  }

  // PR modal event listeners
  const closePrModalBtn = document.getElementById("closePrModal");
  if (closePrModalBtn) {
    closePrModalBtn.addEventListener("click", closePrModal);
  }

  const prModal = document.getElementById("prModal");
  if (prModal) {
    prModal.addEventListener("click", (e) => {
      if (e.target === prModal) {
        closePrModal();
      }
    });
  }
}

// Initialize event listeners
initializeEventListeners();

// Load initial data
loadHistoricalLogs();

// Flow Modal functionality
let currentFlowData = null;
let currentFlowController = null;
let flowModalKeydownHandler = null;
let flowModalPreviousFocus = null;
let currentStepLogsController = null;

// eslint-disable-next-line no-unused-vars
function showTimeline(hookId) {
  // Redirect old timeline calls to new modal (backward compatibility shim)
  showFlowModal(hookId);
}

function showFlowModal(hookId) {
  if (!hookId) {
    closeFlowModal();
    return;
  }

  // Hide step logs section when opening new modal
  const flowLogsSection = document.getElementById("flowLogs");
  if (flowLogsSection) {
    flowLogsSection.style.display = "none";
  }

  // Cancel previous fetch if still in progress
  if (currentFlowController) {
    currentFlowController.abort();
  }

  // Create new AbortController for this fetch
  currentFlowController = new AbortController();

  // Show modal with loading indicator
  const modal = document.getElementById("flowModal");
  modal.style.display = "flex";
  showFlowModalLoading();

  // Fetch workflow steps data
  fetch(`/logs/api/workflow-steps/${hookId}`, {
    signal: currentFlowController.signal,
  })
    .then((response) => {
      if (!response.ok) {
        if (response.status === 404) {
          console.log("No flow data found for hook ID:", hookId);
          showFlowModalError("No workflow data found for this hook");
          return;
        }
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      return response.json();
    })
    .then((data) => {
      if (data) {
        currentFlowData = data;
        renderFlowModal(data);
        setupFlowModalAccessibility();
      }
    })
    .catch((error) => {
      if (error.name === "AbortError") {
        // Request was cancelled, ignore silently
        return;
      }
      console.error("Error fetching flow data:", error);
      showFlowModalError("Failed to load workflow data. Please try again.");
    });
}

function closeFlowModal() {
  const modal = document.getElementById("flowModal");
  if (modal) {
    modal.style.display = "none";
  }
  currentFlowData = null;

  // Remove keyboard event listener
  if (flowModalKeydownHandler) {
    document.removeEventListener("keydown", flowModalKeydownHandler);
    flowModalKeydownHandler = null;
  }

  // Restore focus to the element that opened the modal
  if (flowModalPreviousFocus) {
    flowModalPreviousFocus.focus();
    flowModalPreviousFocus = null;
  }
}

// PR Modal functionality
let currentPrController = null;
let prModalKeydownHandler = null;
let prModalPreviousFocus = null;

function showPrModal(prNumber) {
  if (!prNumber) {
    closePrModal();
    return;
  }

  // Cancel previous fetch if still in progress
  if (currentPrController) {
    currentPrController.abort();
  }

  // Create new AbortController for this fetch
  currentPrController = new AbortController();

  // Show modal with loading indicator
  const modal = document.getElementById("prModal");
  modal.style.display = "flex";
  showPrModalLoading();

  // Fetch all log entries for this PR number
  const params = new URLSearchParams({
    pr_number: prNumber,
    limit: CONFIG.PR_FETCH_LIMIT.toString(),
  });

  fetch(`/logs/api/entries?${params}`, { signal: currentPrController.signal })
    .then((response) => {
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      return response.json();
    })
    .then((data) => {
      if (data.entries && data.entries.length > 0) {
        // Extract unique hook IDs (deduplicate)
        const hookIds = data.entries
          .map((e) => e.hook_id)
          .filter((id) => id !== null && id !== undefined);
        const uniqueHookIds = [...new Set(hookIds)];

        if (uniqueHookIds.length === 0) {
          console.log("No hook IDs found for PR:", prNumber);
          showPrModalError(`No workflow events found for PR #${prNumber}`);
          return;
        }

        renderPrModal(prNumber, uniqueHookIds, data.entries[0].repository);
        setupPrModalAccessibility();
      } else {
        showPrModalError(`No log entries found for PR #${prNumber}`);
      }
    })
    .catch((error) => {
      if (error.name === "AbortError") {
        // Request was cancelled, ignore silently
        return;
      }
      console.error("Error fetching PR data:", error);
      showPrModalError("Failed to load PR data. Please try again.");
    });
}

function closePrModal() {
  const modal = document.getElementById("prModal");
  if (modal) {
    modal.style.display = "none";
  }

  // Remove keyboard event listener
  if (prModalKeydownHandler) {
    document.removeEventListener("keydown", prModalKeydownHandler);
    prModalKeydownHandler = null;
  }

  // Restore focus to the element that opened the modal
  if (prModalPreviousFocus) {
    prModalPreviousFocus.focus();
    prModalPreviousFocus = null;
  }
}

// Keyboard accessibility for Flow Modal
function setupFlowModalAccessibility() {
  const modal = document.getElementById("flowModal");
  if (!modal) return;

  // Set ARIA attributes for screen reader support
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-labelledby", "flowModalTitle");
  modal.setAttribute("aria-describedby", "flowSummary");

  // Save the element that had focus before modal opened
  flowModalPreviousFocus = document.activeElement;

  // Find all focusable elements in the modal
  const focusableElements = modal.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
  );
  const firstFocusable = focusableElements[0];
  const lastFocusable = focusableElements[focusableElements.length - 1];

  // Move focus to first interactive element in modal
  if (firstFocusable) {
    firstFocusable.focus();
  }

  // Create and attach keyboard handler
  flowModalKeydownHandler = function (e) {
    // Close modal on Escape key
    if (e.key === "Escape") {
      e.preventDefault();
      closeFlowModal();
      return;
    }

    // Trap focus within modal using Tab
    if (e.key === "Tab") {
      if (e.shiftKey) {
        // Shift+Tab: moving backwards
        if (document.activeElement === firstFocusable) {
          e.preventDefault();
          lastFocusable.focus();
        }
      } else {
        // Tab: moving forwards
        if (document.activeElement === lastFocusable) {
          e.preventDefault();
          firstFocusable.focus();
        }
      }
    }
  };

  document.addEventListener("keydown", flowModalKeydownHandler);
}

// Keyboard accessibility for PR Modal
function setupPrModalAccessibility() {
  const modal = document.getElementById("prModal");
  if (!modal) return;

  // Set ARIA attributes for screen reader support
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-labelledby", "prModalTitle");
  modal.setAttribute("aria-describedby", "prSummary");

  // Save the element that had focus before modal opened
  prModalPreviousFocus = document.activeElement;

  // Find all focusable elements in the modal
  const focusableElements = modal.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
  );
  const firstFocusable = focusableElements[0];
  const lastFocusable = focusableElements[focusableElements.length - 1];

  // Move focus to first interactive element in modal
  if (firstFocusable) {
    firstFocusable.focus();
  }

  // Create and attach keyboard handler
  prModalKeydownHandler = function (e) {
    // Close modal on Escape key
    if (e.key === "Escape") {
      e.preventDefault();
      closePrModal();
      return;
    }

    // Trap focus within modal using Tab
    if (e.key === "Tab") {
      if (e.shiftKey) {
        // Shift+Tab: moving backwards
        if (document.activeElement === firstFocusable) {
          e.preventDefault();
          lastFocusable.focus();
        }
      } else {
        // Tab: moving forwards
        if (document.activeElement === lastFocusable) {
          e.preventDefault();
          firstFocusable.focus();
        }
      }
    }
  };

  document.addEventListener("keydown", prModalKeydownHandler);
}

function renderPrModal(prNumber, hookIds, repository) {
  // Render summary section
  const summaryElement = document.getElementById("prSummary");
  if (!summaryElement) return;

  // Clear existing content
  while (summaryElement.firstChild) {
    summaryElement.removeChild(summaryElement.firstChild);
  }

  const title = document.createElement("h3");
  title.textContent = `PR #${prNumber} Workflow Overview`;
  summaryElement.appendChild(title);

  const info = document.createElement("p");
  info.textContent = `Found ${hookIds.length} unique webhook event${
    hookIds.length !== 1 ? "s" : ""
  }${repository ? ` for ${repository}` : ""}`;
  info.style.margin = "8px 0 0 0";
  info.style.color = "var(--timestamp-color)";
  summaryElement.appendChild(info);

  // Render hook ID list
  const listElement = document.getElementById("prHookList");
  if (!listElement) return;

  // Clear existing content
  while (listElement.firstChild) {
    listElement.removeChild(listElement.firstChild);
  }

  if (hookIds.length === 0) {
    const emptyMsg = document.createElement("p");
    emptyMsg.style.textAlign = "center";
    emptyMsg.style.color = "var(--timestamp-color)";
    emptyMsg.textContent = "No webhook events found";
    listElement.appendChild(emptyMsg);
    return;
  }

  // Create clickable list items for each hook ID
  hookIds.forEach((hookId, index) => {
    const hookItem = document.createElement("div");
    hookItem.className = "pr-hook-item";
    hookItem.addEventListener("click", () => {
      closePrModal();
      showFlowModal(hookId);
    });

    const icon = document.createElement("span");
    icon.className = "pr-hook-icon";
    icon.textContent = "🔗";

    const hookIdSpan = document.createElement("span");
    hookIdSpan.className = "pr-hook-id";
    hookIdSpan.textContent = `Event ${index + 1}: ${hookId}`;

    hookItem.appendChild(icon);
    hookItem.appendChild(hookIdSpan);
    listElement.appendChild(hookItem);
  });
}

// Flow Modal loading and error helper functions
function showFlowModalLoading() {
  const summaryElement = document.getElementById("flowSummary");
  const vizElement = document.getElementById("flowVisualization");

  if (summaryElement) {
    while (summaryElement.firstChild) {
      summaryElement.removeChild(summaryElement.firstChild);
    }
    const loadingDiv = document.createElement("div");
    loadingDiv.className = "modal-loading";
    loadingDiv.style.textAlign = "center";
    loadingDiv.style.padding = "24px";
    loadingDiv.style.color = "var(--timestamp-color)";

    const spinner = document.createElement("div");
    spinner.className = "loading-spinner";
    spinner.textContent = "⏳";
    spinner.style.fontSize = "32px";
    spinner.style.marginBottom = "12px";

    const text = document.createElement("div");
    text.textContent = "Loading workflow data...";

    loadingDiv.appendChild(spinner);
    loadingDiv.appendChild(text);
    summaryElement.appendChild(loadingDiv);
  }

  if (vizElement) {
    while (vizElement.firstChild) {
      vizElement.removeChild(vizElement.firstChild);
    }
  }
}

function showFlowModalError(errorMessage) {
  const summaryElement = document.getElementById("flowSummary");
  const vizElement = document.getElementById("flowVisualization");

  if (summaryElement) {
    while (summaryElement.firstChild) {
      summaryElement.removeChild(summaryElement.firstChild);
    }
    const errorDiv = document.createElement("div");
    errorDiv.className = "modal-error";
    errorDiv.style.textAlign = "center";
    errorDiv.style.padding = "24px";

    const icon = document.createElement("div");
    icon.style.fontSize = "48px";
    icon.style.marginBottom = "12px";
    icon.textContent = "⚠️";

    const message = document.createElement("div");
    message.style.color = "var(--error-color, #dc3545)";
    message.style.fontSize = "16px";
    message.style.marginBottom = "16px";
    message.textContent = errorMessage;

    const closeBtn = document.createElement("button");
    closeBtn.textContent = "Close";
    closeBtn.className = "btn-secondary";
    closeBtn.style.padding = "8px 16px";
    closeBtn.style.cursor = "pointer";
    closeBtn.addEventListener("click", closeFlowModal);

    errorDiv.appendChild(icon);
    errorDiv.appendChild(message);
    errorDiv.appendChild(closeBtn);
    summaryElement.appendChild(errorDiv);
  }

  if (vizElement) {
    while (vizElement.firstChild) {
      vizElement.removeChild(vizElement.firstChild);
    }
  }
}

// PR Modal loading and error helper functions
function showPrModalLoading() {
  const summaryElement = document.getElementById("prSummary");
  const listElement = document.getElementById("prHookList");

  if (summaryElement) {
    while (summaryElement.firstChild) {
      summaryElement.removeChild(summaryElement.firstChild);
    }
    const loadingDiv = document.createElement("div");
    loadingDiv.className = "modal-loading";
    loadingDiv.style.textAlign = "center";
    loadingDiv.style.padding = "24px";
    loadingDiv.style.color = "var(--timestamp-color)";

    const spinner = document.createElement("div");
    spinner.className = "loading-spinner";
    spinner.textContent = "⏳";
    spinner.style.fontSize = "32px";
    spinner.style.marginBottom = "12px";

    const text = document.createElement("div");
    text.textContent = "Loading PR data...";

    loadingDiv.appendChild(spinner);
    loadingDiv.appendChild(text);
    summaryElement.appendChild(loadingDiv);
  }

  if (listElement) {
    while (listElement.firstChild) {
      listElement.removeChild(listElement.firstChild);
    }
  }
}

function showPrModalError(errorMessage) {
  const summaryElement = document.getElementById("prSummary");
  const listElement = document.getElementById("prHookList");

  if (summaryElement) {
    while (summaryElement.firstChild) {
      summaryElement.removeChild(summaryElement.firstChild);
    }
    const errorDiv = document.createElement("div");
    errorDiv.className = "modal-error";
    errorDiv.style.textAlign = "center";
    errorDiv.style.padding = "24px";

    const icon = document.createElement("div");
    icon.style.fontSize = "48px";
    icon.style.marginBottom = "12px";
    icon.textContent = "⚠️";

    const message = document.createElement("div");
    message.style.color = "var(--error-color, #dc3545)";
    message.style.fontSize = "16px";
    message.style.marginBottom = "16px";
    message.textContent = errorMessage;

    const closeBtn = document.createElement("button");
    closeBtn.textContent = "Close";
    closeBtn.className = "btn-secondary";
    closeBtn.style.padding = "8px 16px";
    closeBtn.style.cursor = "pointer";
    closeBtn.addEventListener("click", closePrModal);

    errorDiv.appendChild(icon);
    errorDiv.appendChild(message);
    errorDiv.appendChild(closeBtn);
    summaryElement.appendChild(errorDiv);
  }

  if (listElement) {
    while (listElement.firstChild) {
      listElement.removeChild(listElement.firstChild);
    }
  }
}

function groupStepsByTaskId(steps) {
  // Show all steps by default - don't filter aggressively
  // Only filter out truly redundant internal steps
  const filteredSteps = steps.filter((step) => {
    // Filter out only very specific internal messages that add no value
    const message = step.message ? step.message.toLowerCase() : "";

    // Keep all steps except these specific redundant ones
    const redundantPatterns = [
      "signature verification successful",
      "processing webhook for repository:",
    ];

    return !redundantPatterns.some((pattern) => message.includes(pattern));
  });

  const groups = [];
  const ungrouped = [];
  const taskMap = new Map();

  filteredSteps.forEach((step, index) => {
    if (step.task_id) {
      if (!taskMap.has(step.task_id)) {
        taskMap.set(step.task_id, {
          task_id: step.task_id,
          task_title: step.task_title || step.task_id,
          steps: [],
          start_time: step.timestamp,
          end_time: step.timestamp,
          start_index: index,
        });
      }
      const group = taskMap.get(step.task_id);
      group.steps.push({ ...step, original_index: index });
      if (new Date(step.timestamp) > new Date(group.end_time)) {
        group.end_time = step.timestamp;
      }
    } else {
      ungrouped.push({ ...step, original_index: index });
    }
  });

  // Calculate duration and status for each group
  taskMap.forEach((group) => {
    const startMs = new Date(group.start_time).getTime();
    const endMs = new Date(group.end_time).getTime();
    group.duration_ms = endMs - startMs;

    // Determine group status based on step levels
    if (group.steps.some((s) => s.level === "ERROR")) {
      group.status = "error";
    } else if (group.steps.some((s) => s.level === "SUCCESS")) {
      group.status = "success";
    } else {
      group.status = "in_progress";
    }

    groups.push(group);
  });

  // Sort groups by start index to maintain chronological order
  groups.sort((a, b) => a.start_index - b.start_index);

  return { groups, ungrouped };
}

function renderFlowModal(data) {
  // Render summary section using safe DOM methods
  const summaryElement = document.getElementById("flowSummary");
  if (!summaryElement) return;

  // Clear existing content
  while (summaryElement.firstChild) {
    summaryElement.removeChild(summaryElement.firstChild);
  }

  const title = document.createElement("h3");
  title.textContent = "Flow Overview";
  summaryElement.appendChild(title);

  const grid = document.createElement("div");
  grid.className = "flow-summary-grid";

  // Helper to create summary items safely
  const createSummaryItem = (label, value) => {
    const item = document.createElement("div");
    item.className = "flow-summary-item";

    const labelDiv = document.createElement("div");
    labelDiv.className = "flow-summary-label";
    labelDiv.textContent = label;

    const valueDiv = document.createElement("div");
    valueDiv.className = "flow-summary-value";
    valueDiv.textContent = value;

    item.appendChild(labelDiv);
    item.appendChild(valueDiv);
    return item;
  };

  const duration =
    data.total_duration_ms > 0
      ? `${(data.total_duration_ms / 1000).toFixed(2)}s`
      : "< 1s";

  grid.appendChild(createSummaryItem("Hook ID", data.hook_id));
  grid.appendChild(
    createSummaryItem("Total Steps", data.step_count.toString()),
  );
  grid.appendChild(createSummaryItem("Duration", duration));

  if (data.steps[0] && data.steps[0].repository) {
    grid.appendChild(createSummaryItem("Repository", data.steps[0].repository));
  }

  summaryElement.appendChild(grid);

  // Render vertical flow visualization using safe DOM methods
  const vizElement = document.getElementById("flowVisualization");
  if (!vizElement) return;

  // Clear existing content
  while (vizElement.firstChild) {
    vizElement.removeChild(vizElement.firstChild);
  }

  if (data.steps.length === 0) {
    const emptyMsg = document.createElement("p");
    emptyMsg.style.textAlign = "center";
    emptyMsg.style.color = "var(--timestamp-color)";
    emptyMsg.textContent = "No workflow steps found";
    vizElement.appendChild(emptyMsg);
    return;
  }

  // Group steps by task_id
  const { groups, ungrouped } = groupStepsByTaskId(data.steps);

  // Render grouped steps
  groups.forEach((group) => {
    renderTaskGroup(group, vizElement);
  });

  // Render ungrouped steps
  ungrouped.forEach((step) => {
    renderSingleStep(step, vizElement);
  });

  // Add final status
  const hasErrors = data.steps.some((step) => step.level === "ERROR");
  const finalStatus = document.createElement("div");
  finalStatus.className = hasErrors ? "flow-error" : "flow-success";

  const statusTitle = document.createElement("h3");
  statusTitle.textContent = hasErrors
    ? "⚠️ Flow Completed with Errors"
    : "✓ Flow Completed Successfully";
  finalStatus.appendChild(statusTitle);

  if (hasErrors) {
    const errorMsg = document.createElement("div");
    errorMsg.className = "flow-error-message";
    errorMsg.textContent =
      "Some steps encountered errors. Check the logs for details.";
    finalStatus.appendChild(errorMsg);
  }

  vizElement.appendChild(finalStatus);
}

function renderTaskGroup(group, parentElement) {
  const taskGroupContainer = document.createElement("div");
  taskGroupContainer.className = "task-group";

  // Create group header
  const groupHeader = document.createElement("div");
  groupHeader.className = "task-group-header";
  groupHeader.style.cursor = "pointer";

  // Collapse arrow
  const arrow = document.createElement("span");
  arrow.className = "task-group-arrow collapsed";
  arrow.textContent = "►";

  // Status icon
  const statusIcon = document.createElement("span");
  statusIcon.className = `task-group-status task-group-${group.status}`;
  if (group.status === "success") {
    statusIcon.textContent = "✓";
  } else if (group.status === "error") {
    statusIcon.textContent = "✗";
  } else {
    statusIcon.textContent = "◷";
  }

  // Task title
  const taskTitle = document.createElement("span");
  taskTitle.className = "task-group-title";
  taskTitle.textContent = group.task_title;

  // Duration
  const duration = document.createElement("span");
  duration.className = "task-group-duration";
  duration.textContent = `${(group.duration_ms / 1000).toFixed(2)}s`;

  groupHeader.appendChild(arrow);
  groupHeader.appendChild(statusIcon);
  groupHeader.appendChild(taskTitle);
  groupHeader.appendChild(duration);

  // Create nested steps container
  const stepsContainer = document.createElement("div");
  stepsContainer.className = "task-group-steps";
  stepsContainer.style.display = "none"; // Start collapsed

  group.steps.forEach((step) => {
    renderSingleStep(step, stepsContainer, true);
  });

  // Toggle expand/collapse
  groupHeader.addEventListener("click", () => {
    const isCollapsed = stepsContainer.style.display === "none";
    stepsContainer.style.display = isCollapsed ? "block" : "none";
    arrow.className = isCollapsed
      ? "task-group-arrow expanded"
      : "task-group-arrow collapsed";
  });

  taskGroupContainer.appendChild(groupHeader);
  taskGroupContainer.appendChild(stepsContainer);
  parentElement.appendChild(taskGroupContainer);
}

function renderSingleStep(step, parentElement, isNested = false) {
  const stepType = getStepType(step.level);
  const timeFromStart = `+${(step.relative_time_ms / 1000).toFixed(2)}s`;
  const timestamp = new Date(step.timestamp).toLocaleTimeString();

  const flowStepContainer = document.createElement("div");
  flowStepContainer.className = isNested
    ? "flow-step-container nested"
    : "flow-step-container";

  const flowStep = document.createElement("div");
  flowStep.className = `flow-step ${stepType}`;
  flowStep.setAttribute("data-step-index", step.original_index.toString());
  flowStep.style.cursor = "pointer";
  flowStep.addEventListener("click", () => filterByStep(step.original_index));

  const stepNumber = document.createElement("div");
  stepNumber.className = "flow-step-number";
  stepNumber.textContent = (step.original_index + 1).toString();

  const stepContent = document.createElement("div");
  stepContent.className = "flow-step-content";

  const stepTitle = document.createElement("div");
  stepTitle.className = "flow-step-title";
  stepTitle.textContent = step.message;

  const stepTime = document.createElement("div");
  stepTime.className = "flow-step-time";

  const timestampSpan = document.createElement("span");
  timestampSpan.textContent = timestamp;

  const durationSpan = document.createElement("span");
  durationSpan.className = "flow-step-duration";
  durationSpan.textContent = timeFromStart;

  stepTime.appendChild(timestampSpan);
  stepTime.appendChild(durationSpan);

  stepContent.appendChild(stepTitle);
  stepContent.appendChild(stepTime);

  flowStep.appendChild(stepNumber);
  flowStep.appendChild(stepContent);

  // Create logs container for this step (hidden by default)
  const stepLogsContainer = document.createElement("div");
  stepLogsContainer.className = "step-logs-container";
  stepLogsContainer.style.display = "none";
  stepLogsContainer.setAttribute(
    "data-step-logs",
    step.original_index.toString(),
  );

  flowStepContainer.appendChild(flowStep);
  flowStepContainer.appendChild(stepLogsContainer);

  parentElement.appendChild(flowStepContainer);
}

function getStepType(level) {
  // Accept level parameter to determine step type based on log level
  const levelUpper = typeof level === "string" ? level.toUpperCase() : "";

  if (levelUpper === "SUCCESS") {
    return "success";
  } else if (levelUpper === "ERROR") {
    return "error";
  } else if (levelUpper === "WARNING") {
    return "warning";
  } else {
    return "info";
  }
}

async function filterByStep(stepIndex) {
  if (!currentFlowData || !currentFlowData.steps[stepIndex]) return;

  const step = currentFlowData.steps[stepIndex];
  const logsContainer = document.querySelector(
    `[data-step-logs="${stepIndex}"]`,
  );

  if (!logsContainer) return;

  // Toggle: if this step's logs are already showing, hide them
  if (logsContainer.style.display === "block") {
    logsContainer.style.display = "none";
    logsContainer.innerHTML = "";
    return;
  }

  // Hide all other step logs
  document.querySelectorAll(".step-logs-container").forEach((container) => {
    container.style.display = "none";
    container.innerHTML = "";
  });

  // Show logs for this step
  await showStepLogsInModal(step, logsContainer);
}

async function showStepLogsInModal(step, logsContainer) {
  if (!logsContainer) return;

  // Show loading state
  logsContainer.style.display = "block";
  logsContainer.textContent = "Loading logs...";

  // Cancel previous fetch if still in progress
  if (currentStepLogsController) {
    currentStepLogsController.abort();
  }

  // Create new AbortController for this fetch
  currentStepLogsController = new AbortController();

  try {
    // Using full message for precision to avoid ambiguous matches
    const searchText = step.message;
    const hookId = currentFlowData.hook_id;

    const params = new URLSearchParams({
      hook_id: hookId,
      search: searchText,
      limit: "100",
    });

    const response = await fetch(`/logs/api/entries?${params}`, {
      signal: currentStepLogsController.signal,
    });
    if (!response.ok) throw new Error("Failed to fetch logs");

    const data = await response.json();

    // Clear and display logs using safe DOM methods
    logsContainer.textContent = "";

    if (data.entries.length === 0) {
      const emptyMsg = document.createElement("div");
      emptyMsg.textContent = "No logs found for this step";
      emptyMsg.style.textAlign = "center";
      emptyMsg.style.color = "var(--timestamp-color)";
      emptyMsg.style.padding = "12px";
      logsContainer.appendChild(emptyMsg);
      return;
    }

    // Render log entries
    data.entries.forEach((entry) => {
      const logEntry = document.createElement("div");
      logEntry.className = `log-entry ${entry.level}`;

      const timestamp = document.createElement("span");
      timestamp.className = "timestamp";
      timestamp.textContent = new Date(entry.timestamp).toLocaleString();

      const level = document.createElement("span");
      level.className = "level";
      level.textContent = ` [${entry.level}] `;

      const message = document.createElement("span");
      message.className = "message";
      message.textContent = entry.message;

      logEntry.appendChild(timestamp);
      logEntry.appendChild(level);
      logEntry.appendChild(message);

      logsContainer.appendChild(logEntry);
    });

    // Scroll to the logs container
    logsContainer.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    if (error.name === "AbortError") {
      // Request was cancelled, ignore silently
      return;
    }
    console.error("Error fetching step logs:", error);
    logsContainer.textContent = "Error loading logs";
  }
}
