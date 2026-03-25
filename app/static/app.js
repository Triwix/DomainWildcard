const {
  MAX_SUPPORTED_WORDS,
  parseWordlistLines,
  parseUploadText,
  applyCleanup,
  dedupeWords,
  sortWords,
  wordsToText,
  isOverWordLimit,
  getSearchToggleConfig,
} = window.WordlistUtils;

const WORDLIST_DRAFT_KEY = "domain-search-wordlist-draft-v1";
const WORDLIST_TOOL_KEY = "domain-search-wordlist-tools-v1";
const WORDLIST_COLLECTION_KEY = "domain-search-wordlist-collection-v1";
const EDITOR_COLLAPSED_KEY = "domain-search-editor-collapsed-v1";
const RATE_PANEL_COLLAPSED_KEY = "domain-search-rate-panel-collapsed-v1";
const UNDO_LIMIT = 80;
const RATE_POLL_INTERVAL_MS = 3500;
const LIVE_STATUS_RENDER_INTERVAL_MS = 120;
const LIVE_AVAILABLE_RENDER_INTERVAL_MS = 120;

const form = document.getElementById("search-form");
const searchToggleBtn = document.getElementById("search-toggle");
const themeToggle = document.getElementById("theme-toggle");
const patternInput = document.getElementById("pattern");
const forceRecheckInput = document.getElementById("force-recheck");

const wordlistUploadInput = document.getElementById("wordlist-upload");
const mergeWordlistsBtn = document.getElementById("merge-wordlists-btn");
const clearWordlistsBtn = document.getElementById("clear-wordlists-btn");
const uploadAssignment = document.getElementById("upload-assignment");
const loadedWordlists = document.getElementById("loaded-wordlists");

const editorCollapseToggle = document.getElementById("editor-collapse-toggle");
const editorTargetSelect = document.getElementById("editor-target-select");
const editorBody = document.getElementById("editor-body");
const editorLabel = document.getElementById("wordlist-editor-label");
const editorTextarea = document.getElementById("wordlist-editor");
const minLengthInput = document.getElementById("min-length");
const maxLengthInput = document.getElementById("max-length");
const includeSubstringInput = document.getElementById("include-substring");
const excludeSubstringInput = document.getElementById("exclude-substring");
const allowedCharsOnlyInput = document.getElementById("allowed-char-only");
const applyCleanupBtn = document.getElementById("apply-cleanup-btn");
const dedupeBtn = document.getElementById("dedupe-btn");
const editorSortModeSelect = document.getElementById("editor-sort-mode");
const sortEditorBtn = document.getElementById("sort-editor-btn");
const undoBtn = document.getElementById("undo-btn");
const redoBtn = document.getElementById("redo-btn");
const exportWordlistBtn = document.getElementById("export-wordlist-btn");
const perfWarning = document.getElementById("perf-warning");
const editorControls = Array.from(document.querySelectorAll("[data-editor-control='true']"));

const wlTotalEl = document.getElementById("wl-total");
const wlUniqueEl = document.getElementById("wl-unique");
const wlFilteredEl = document.getElementById("wl-filtered");
const wlUsableEl = document.getElementById("wl-usable");

const availableList = document.getElementById("available-list");
const unknownList = document.getElementById("unknown-list");
const filterInput = document.getElementById("filter");
const sortModeSelect = document.getElementById("sort-mode");
const availableViewMeta = document.getElementById("available-view-meta");
const statusEl = document.getElementById("status");
const progressTextEl = document.getElementById("progress-text");
const availableCountEl = document.getElementById("available-count");
const takenCountEl = document.getElementById("taken-count");
const unknownCountEl = document.getElementById("unknown-count");
const invalidCountEl = document.getElementById("invalid-count");
const cacheHitCountEl = document.getElementById("cache-hit-count");
const cacheMissCountEl = document.getElementById("cache-miss-count");
const progressBar = document.getElementById("progress-bar");
const statusMessage = document.getElementById("status-message");
const copyBtn = document.getElementById("copy-btn");
const exportMenu = document.getElementById("export-menu");
const exportMenuToggle = document.getElementById("export-menu-toggle");
const exportMenuList = document.getElementById("export-menu-list");
const txtBtn = document.getElementById("download-txt");
const csvBtn = document.getElementById("download-csv");
const jsonBtn = document.getElementById("download-json");
const ratePanelToggle = document.getElementById("rate-panel-toggle");
const ratePanelBody = document.getElementById("rate-panel-body");
const rateEmpty = document.getElementById("rate-empty");
const rateStatusBody = document.getElementById("rate-status-body");
const speedVerisignInput = document.getElementById("speed-verisign");
const speedPirInput = document.getElementById("speed-pir");
const speedIdentityInput = document.getElementById("speed-identity");
const speedRegistryCoInput = document.getElementById("speed-registryco");
const speedResetBackoffInput = document.getElementById("speed-reset-backoff");
const speedStatus = document.getElementById("speed-status");
const speedApplyBtn = document.getElementById("speed-apply-btn");
const speedResetBtn = document.getElementById("speed-reset-btn");

let jobId = null;
let eventSource = null;
let scanState = "idle";
let availableDomains = [];
let availableSet = new Set();
let undoStack = [];
let redoStack = [];
let persistTimer = null;
let editorLocked = false;
let wordlistEntries = [];
let activeWordlistId = null;
let wordlistIdCounter = 0;
let editorCollapsed = false;
let ratePanelCollapsed = false;
let ratePollTimer = null;
let pendingStatusSnapshot = null;
let statusRenderTimer = null;
let pendingAvailableDomains = [];
let availableRenderTimer = null;
let forceAvailableRerender = false;
let lastRenderedFilter = "";
let lastRenderedSort = "earliest";
let lastRenderedMode = "full";
let manualRateConfig = { defaults: {}, overrides: {}, supported_hosts: [] };

const MANUAL_SPEED_INPUTS = {
  "rdap.verisign.com": speedVerisignInput,
  "rdap.publicinterestregistry.org": speedPirInput,
  "rdap.identitydigital.services": speedIdentityInput,
  "rdap.registry.co": speedRegistryCoInput,
};

function setExportMenuOpen(open) {
  if (!exportMenuToggle || !exportMenuList) {
    return;
  }
  const nextOpen = Boolean(open) && !exportMenuToggle.disabled;
  exportMenuToggle.setAttribute("aria-expanded", nextOpen ? "true" : "false");
  exportMenuList.classList.toggle("hidden", !nextOpen);
}

function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  themeToggle.textContent = theme === "dark" ? "Light Mode" : "Dark Mode";
  try {
    window.localStorage.setItem("domain-search-theme", theme);
  } catch (_error) {
    // Ignore storage failures.
  }
}

function initTheme() {
  let preferred = null;
  try {
    preferred = window.localStorage.getItem("domain-search-theme");
  } catch (_error) {
    preferred = null;
  }

  if (preferred !== "dark" && preferred !== "light") {
    preferred = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  setTheme(preferred);
}

function setScanState(newState) {
  scanState = newState;
  const cfg = getSearchToggleConfig(scanState);
  searchToggleBtn.textContent = cfg.label;
  searchToggleBtn.disabled = cfg.disabled;
  searchToggleBtn.classList.toggle("is-stop", cfg.mode === "stop");
  searchToggleBtn.classList.toggle("is-stopping", cfg.mode === "stopping");
  updateRatePolling();
}

function setEditorLocked(locked) {
  editorLocked = Boolean(locked);
  for (const control of editorControls) {
    control.disabled = editorLocked;
  }
  updateUploadSummaries();
  updateUndoRedoButtons();
}

function setEditorCollapsed(collapsed) {
  editorCollapsed = Boolean(collapsed);
  editorBody.classList.toggle("is-collapsed", editorCollapsed);
  editorCollapseToggle.textContent = editorCollapsed ? "Expand Editor" : "Collapse Editor";
  try {
    window.localStorage.setItem(EDITOR_COLLAPSED_KEY, editorCollapsed ? "1" : "0");
  } catch (_error) {
    // Ignore storage failures.
  }
}

function setRatePanelCollapsed(collapsed) {
  ratePanelCollapsed = Boolean(collapsed);
  ratePanelBody.classList.toggle("is-collapsed", ratePanelCollapsed);
  ratePanelToggle.textContent = ratePanelCollapsed ? "Expand" : "Collapse";
  try {
    window.localStorage.setItem(RATE_PANEL_COLLAPSED_KEY, ratePanelCollapsed ? "1" : "0");
  } catch (_error) {
    // Ignore storage failures.
  }
}

function renderRateStatus(hosts) {
  rateStatusBody.innerHTML = "";
  if (!Array.isArray(hosts) || hosts.length === 0) {
    rateEmpty.classList.remove("hidden");
    return;
  }

  rateEmpty.classList.add("hidden");
  for (const host of hosts) {
    const tr = document.createElement("tr");
    const policyLabel = host.manual_override ? `${host.policy || "Adaptive default"} (manual)` : (host.policy || "Adaptive default");

    const last = host.last_status ? `HTTP ${host.last_status}` : host.last_error || "n/a";
    const cells = [
      host.host || "n/a",
      `${host.interval_seconds}s`,
      policyLabel,
      String(host.total_requests || 0),
      String(host.total_429 || 0),
      last,
    ];

    for (const value of cells) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    }
    rateStatusBody.appendChild(tr);
  }
}

async function refreshRateStatus() {
  try {
    const response = await fetch("/api/rate-status");
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    renderRateStatus(payload.hosts || []);
  } catch (_error) {
    // Silently ignore transient polling issues.
  }
}

function updateRatePolling() {
  const shouldPoll = scanState === "running" || scanState === "cancelling";
  if (shouldPoll && !ratePollTimer) {
    refreshRateStatus();
    ratePollTimer = setInterval(() => {
      refreshRateStatus();
    }, RATE_POLL_INTERVAL_MS);
    return;
  }

  if (!shouldPoll && ratePollTimer) {
    clearInterval(ratePollTimer);
    ratePollTimer = null;
    refreshRateStatus();
  }
}

function parsePositiveInt(rawValue) {
  const trimmed = String(rawValue || "").trim();
  if (!trimmed) {
    return null;
  }
  const num = Number(trimmed);
  if (!Number.isFinite(num)) {
    return null;
  }
  if (num < 0) {
    return 0;
  }
  return Math.floor(num);
}

function parsePositiveInterval(rawValue) {
  const trimmed = String(rawValue || "").trim();
  if (!trimmed) {
    return null;
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value <= 0) {
    return Number.NaN;
  }
  return value;
}

function wildcardCount(pattern) {
  return (String(pattern || "").match(/\*/g) || []).length;
}

function renderManualSpeedStatus() {
  const overrides = manualRateConfig.overrides || {};
  const activeKeys = Object.keys(overrides);
  if (!activeKeys.length) {
    speedStatus.textContent = "Using automatic host defaults.";
    return;
  }
  const summary = activeKeys
    .map((host) => `${host}: ${Number(overrides[host]).toFixed(6)}s`)
    .join(" | ");
  speedStatus.textContent = `Manual overrides active (${activeKeys.length}): ${summary}`;
}

function applyManualRateConfigToInputs() {
  const defaults = manualRateConfig.defaults || {};
  const overrides = manualRateConfig.overrides || {};
  for (const [hostKey, input] of Object.entries(MANUAL_SPEED_INPUTS)) {
    if (!input) {
      continue;
    }
    const defaultValue = Number(defaults[hostKey]);
    input.placeholder = Number.isFinite(defaultValue) && defaultValue > 0 ? String(defaultValue) : "auto";
    if (Object.prototype.hasOwnProperty.call(overrides, hostKey)) {
      const overrideValue = Number(overrides[hostKey]);
      input.value = Number.isFinite(overrideValue) && overrideValue > 0 ? String(overrideValue) : "";
    } else {
      input.value = "";
    }
  }
  renderManualSpeedStatus();
}

async function refreshRateConfig() {
  if (!speedApplyBtn || !speedResetBtn) {
    return;
  }
  try {
    const response = await fetch("/api/rate-config");
    if (!response.ok) {
      throw new Error(`Rate config fetch failed with HTTP ${response.status}`);
    }
    const payload = await response.json();
    manualRateConfig = payload || { defaults: {}, overrides: {}, supported_hosts: [] };
    applyManualRateConfigToInputs();
  } catch (error) {
    speedStatus.textContent = String(error);
  }
}

function collectManualOverridesFromInputs() {
  const overrides = {};
  for (const [hostKey, input] of Object.entries(MANUAL_SPEED_INPUTS)) {
    if (!input) {
      continue;
    }
    const parsed = parsePositiveInterval(input.value);
    if (Number.isNaN(parsed)) {
      throw new Error(`Invalid speed value for ${hostKey}. Use a positive number of seconds.`);
    }
    if (parsed !== null) {
      overrides[hostKey] = parsed;
    }
  }
  return overrides;
}

async function applyManualSpeedOverrides() {
  if (!speedApplyBtn) {
    return;
  }
  const overrides = collectManualOverridesFromInputs();
  speedApplyBtn.disabled = true;
  try {
    const response = await fetch("/api/rate-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        overrides,
        replace: true,
        reset_backoff: Boolean(speedResetBackoffInput && speedResetBackoffInput.checked),
      }),
    });
    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || `Rate config update failed with HTTP ${response.status}`);
    }
    manualRateConfig = await response.json();
    applyManualRateConfigToInputs();
    await refreshRateStatus();
    statusMessage.textContent = "Manual speed overrides applied.";
  } catch (error) {
    speedStatus.textContent = String(error);
    statusMessage.textContent = String(error);
  } finally {
    speedApplyBtn.disabled = false;
  }
}

async function resetManualSpeedOverrides() {
  if (!speedResetBtn) {
    return;
  }
  speedResetBtn.disabled = true;
  try {
    const resetBackoff = Boolean(speedResetBackoffInput && speedResetBackoffInput.checked);
    const response = await fetch(`/api/rate-config?reset_backoff=${resetBackoff ? "true" : "false"}`, {
      method: "DELETE",
    });
    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || `Rate config reset failed with HTTP ${response.status}`);
    }
    manualRateConfig = await response.json();
    applyManualRateConfigToInputs();
    await refreshRateStatus();
    statusMessage.textContent = "Manual speed overrides cleared. Back to automatic defaults.";
  } catch (error) {
    speedStatus.textContent = String(error);
    statusMessage.textContent = String(error);
  } finally {
    speedResetBtn.disabled = false;
  }
}

function normalizeWordArray(words) {
  const normalized = [];
  for (const raw of words || []) {
    const item = String(raw || "").trim().toLowerCase();
    if (!item || item.startsWith("#")) {
      continue;
    }
    normalized.push(item);
  }
  return normalized;
}

function makeWordlistId() {
  wordlistIdCounter += 1;
  return `wl-${wordlistIdCounter}`;
}

function makeWordlistEntry(name, words) {
  return {
    id: makeWordlistId(),
    name: String(name || "Wordlist").trim() || "Wordlist",
    words: normalizeWordArray(words),
  };
}

function ensureWordlistEntries() {
  if (!Array.isArray(wordlistEntries)) {
    wordlistEntries = [];
  }
  if (wordlistEntries.length === 0) {
    const fallback = makeWordlistEntry("List 1", []);
    wordlistEntries = [fallback];
    activeWordlistId = fallback.id;
    return;
  }
  const activeExists = wordlistEntries.some((entry) => entry.id === activeWordlistId);
  if (!activeExists) {
    activeWordlistId = wordlistEntries[0].id;
  }
}

function getActiveWordlistIndex() {
  ensureWordlistEntries();
  const idx = wordlistEntries.findIndex((entry) => entry.id === activeWordlistId);
  return idx >= 0 ? idx : 0;
}

function getActiveWordlist() {
  ensureWordlistEntries();
  return wordlistEntries[getActiveWordlistIndex()];
}

function getEditorParseResult() {
  return parseWordlistLines(editorTextarea.value);
}

function syncActiveWordsFromEditor() {
  const parseResult = getEditorParseResult();
  const active = getActiveWordlist();
  if (active) {
    active.words = normalizeWordArray(parseResult.words);
  }
  return parseResult;
}

function getWordlistParseResultByIndex(index) {
  ensureWordlistEntries();
  const clampedIndex = Number(index);
  if (!Number.isInteger(clampedIndex) || clampedIndex < 0 || clampedIndex >= wordlistEntries.length) {
    return parseWordlistLines("");
  }
  const entry = wordlistEntries[clampedIndex];
  if (entry.id === activeWordlistId) {
    return getEditorParseResult();
  }
  return parseWordlistLines(wordsToText(entry.words));
}

function getPrimaryParseResult() {
  return getWordlistParseResultByIndex(0);
}

function getSecondaryParseResult() {
  return getWordlistParseResultByIndex(1);
}

function clearUndoRedoStacks() {
  undoStack = [];
  redoStack = [];
  updateUndoRedoButtons();
}

function setEditorLabelText() {
  const idx = getActiveWordlistIndex();
  const labelPrefix = idx === 0 ? "List #1 (Primary wildcard list)" : idx === 1 ? "List #2 (Secondary wildcard list)" : `List #${idx + 1}`;
  editorLabel.textContent = `Editable ${labelPrefix} (one word per line)`;
}

function updateEditorTargetSelect() {
  ensureWordlistEntries();
  editorTargetSelect.innerHTML = "";
  for (let idx = 0; idx < wordlistEntries.length; idx += 1) {
    const entry = wordlistEntries[idx];
    const option = document.createElement("option");
    option.value = entry.id;
    option.textContent = `${idx + 1}. ${entry.name}`;
    editorTargetSelect.appendChild(option);
  }
  editorTargetSelect.value = activeWordlistId;
  editorTargetSelect.disabled = editorLocked || wordlistEntries.length <= 1;
}

function renderLoadedWordlists() {
  ensureWordlistEntries();
  loadedWordlists.innerHTML = "";
  const fragment = document.createDocumentFragment();

  for (let idx = 0; idx < wordlistEntries.length; idx += 1) {
    const entry = wordlistEntries[idx];
    const dedupedCount = parseWordlistLines(wordsToText(entry.words)).dedupedWords.length;
    const li = document.createElement("li");
    li.className = entry.id === activeWordlistId ? "loaded-wordlist-item is-active" : "loaded-wordlist-item";

    const label = document.createElement("button");
    label.type = "button";
    label.className = "loaded-wordlist-label";
    label.dataset.action = "edit";
    label.dataset.wordlistId = entry.id;
    label.disabled = editorLocked;
    label.textContent = `#${idx + 1} ${entry.name} (${dedupedCount})`;
    li.appendChild(label);

    const controls = document.createElement("div");
    controls.className = "loaded-wordlist-controls";
    controls.innerHTML = `
      <button type="button" data-action="up" data-wordlist-id="${entry.id}" ${editorLocked || idx === 0 ? "disabled" : ""}>↑</button>
      <button type="button" data-action="down" data-wordlist-id="${entry.id}" ${editorLocked || idx === wordlistEntries.length - 1 ? "disabled" : ""}>↓</button>
      <button type="button" data-action="remove" data-wordlist-id="${entry.id}" ${editorLocked ? "disabled" : ""}>Remove</button>
    `;
    li.appendChild(controls);
    fragment.appendChild(li);
  }

  loadedWordlists.appendChild(fragment);
}

function setActiveWordlist(nextId, options = {}) {
  ensureWordlistEntries();
  const entry = wordlistEntries.find((item) => item.id === nextId);
  if (!entry) {
    updateEditorTargetSelect();
    setEditorLabelText();
    return;
  }

  if (entry.id === activeWordlistId) {
    editorTargetSelect.value = activeWordlistId;
    setEditorLabelText();
    return;
  }

  if (!options.skipSyncCurrent) {
    syncActiveWordsFromEditor();
  }

  activeWordlistId = entry.id;
  editorTargetSelect.value = activeWordlistId;
  editorTextarea.value = wordsToText(entry.words);

  if (!options.preserveHistory) {
    clearUndoRedoStacks();
  }

  setEditorLabelText();
  refreshWordlistEditor();
}

function updateEditorTargetAvailability() {
  updateEditorTargetSelect();
  setEditorLabelText();
}

function getSecondaryDedupedWords() {
  return getSecondaryParseResult().dedupedWords;
}

function setStatus(snapshot) {
  const status = snapshot.status || "idle";
  statusEl.textContent = status;

  const total = snapshot.total_candidates || snapshot.valid_domains || 0;
  const processed = snapshot.progress_processed ?? snapshot.processed ?? 0;
  progressTextEl.textContent = `${processed} / ${total}`;
  availableCountEl.textContent = String(snapshot.available_count || 0);
  takenCountEl.textContent = String(snapshot.taken_count || 0);
  unknownCountEl.textContent = String(snapshot.unknown_count || 0);
  invalidCountEl.textContent = String(snapshot.invalid_count || 0);
  cacheHitCountEl.textContent = String(snapshot.cache_hits || 0);
  cacheMissCountEl.textContent = String(snapshot.cache_misses || 0);

  const pct = total === 0 ? 0 : Math.min(100, Math.round((processed / total) * 100));
  progressBar.style.width = `${pct}%`;

  if (status === "running") {
    statusMessage.textContent = "Scanning in progress. Streaming available domains live.";
  } else if (status === "completed") {
    statusMessage.textContent = "Scan complete. Export available domains as TXT, CSV, or JSON.";
  } else if (status === "cancelled") {
    statusMessage.textContent = "Scan stopped. Results so far are still available for export.";
  } else if (status === "failed") {
    statusMessage.textContent = `Scan failed: ${(snapshot.errors || []).join("; ")}`;
  }

  if (status === "running" || status === "queued") {
    // Keep the toggle in Stopping... if user has already requested cancellation.
    if (scanState !== "cancelling") {
      setScanState("running");
    }
    setEditorLocked(true);
  } else {
    setScanState("idle");
    setEditorLocked(false);
  }
}

function isScanActive() {
  return scanState === "running" || scanState === "cancelling";
}

function shouldDeferAlphabeticalSort(forceSort = false) {
  if (forceSort) {
    return false;
  }
  const sortMode = sortModeSelect.value;
  return isScanActive() && (sortMode === "az" || sortMode === "za" || sortMode === "len_asc" || sortMode === "len_desc");
}

function getLiveListMode() {
  const query = filterInput.value.trim().toLowerCase();
  if (query) {
    return "full";
  }
  const sortMode = sortModeSelect.value;
  if (sortMode === "recent") {
    return "recent";
  }
  if (sortMode === "earliest" || shouldDeferAlphabeticalSort(false)) {
    return "earliest";
  }
  return "full";
}

function getViewedDomains(options = {}) {
  const forceSort = Boolean(options.forceSort);
  const sortMode = sortModeSelect.value;
  const query = filterInput.value.trim().toLowerCase();

  let domains = availableDomains.slice();
  if (query) {
    domains = domains.filter((domain) => domain.toLowerCase().includes(query));
  }

  if (sortMode === "az") {
    if (!shouldDeferAlphabeticalSort(forceSort)) {
      domains.sort((a, b) => a.localeCompare(b));
    }
  } else if (sortMode === "za") {
    if (!shouldDeferAlphabeticalSort(forceSort)) {
      domains.sort((a, b) => b.localeCompare(a));
    }
  } else if (sortMode === "len_asc") {
    if (!shouldDeferAlphabeticalSort(forceSort)) {
      domains.sort((a, b) => a.length - b.length || a.localeCompare(b));
    }
  } else if (sortMode === "len_desc") {
    if (!shouldDeferAlphabeticalSort(forceSort)) {
      domains.sort((a, b) => b.length - a.length || a.localeCompare(b));
    }
  } else if (sortMode === "recent") {
    domains.reverse();
  }

  return domains;
}

function updateDownloads() {
  if (!jobId) {
    txtBtn.dataset.url = "";
    csvBtn.dataset.url = "";
    jsonBtn.dataset.url = "";
    txtBtn.disabled = true;
    csvBtn.disabled = true;
    jsonBtn.disabled = true;
    exportMenuToggle.disabled = true;
    setExportMenuOpen(false);
    return;
  }

  const selectedSort = sortModeSelect.value;
  const effectiveSort = shouldDeferAlphabeticalSort() ? "earliest" : selectedSort;
  const params = new URLSearchParams({ sort: effectiveSort });
  const query = filterInput.value.trim();
  if (query) {
    params.set("q", query);
  }

  txtBtn.dataset.url = `/api/jobs/${jobId}/export.txt?${params.toString()}`;
  csvBtn.dataset.url = `/api/jobs/${jobId}/export.csv?${params.toString()}`;
  jsonBtn.dataset.url = `/api/jobs/${jobId}/export.json?${params.toString()}`;
  txtBtn.disabled = false;
  csvBtn.disabled = false;
  jsonBtn.disabled = false;
  exportMenuToggle.disabled = false;
}

function inferDownloadFilename(response, fallback) {
  const cd = response.headers.get("Content-Disposition") || response.headers.get("content-disposition");
  if (!cd) {
    return fallback;
  }
  const match = cd.match(/filename\\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?/i);
  if (!match) {
    return fallback;
  }
  const encoded = match[1] || match[2];
  if (!encoded) {
    return fallback;
  }
  try {
    return decodeURIComponent(encoded);
  } catch (_error) {
    return encoded;
  }
}

async function triggerExportDownload(format) {
  if (!jobId) {
    statusMessage.textContent = "No completed job available to export yet.";
    return;
  }

  const buttonByFormat = {
    txt: txtBtn,
    csv: csvBtn,
    json: jsonBtn,
  };
  const button = buttonByFormat[format];
  const url = button && button.dataset ? button.dataset.url : "";
  if (!url) {
    statusMessage.textContent = "Export link is not ready yet.";
    return;
  }

  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Export failed with HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const fallback = `available-${jobId}.${format}`;
    const filename = inferDownloadFilename(response, fallback);
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(objectUrl);
    setExportMenuOpen(false);
  } catch (error) {
    statusMessage.textContent = String(error);
  }
}

function updateAvailableViewMeta(viewedCount, forceSort = false) {
  const deferredAlphabetical = shouldDeferAlphabeticalSort(Boolean(forceSort));
  if (deferredAlphabetical) {
    availableViewMeta.textContent = `${viewedCount} in current view (alphabetical/length sort deferred while scan is active)`;
  } else {
    availableViewMeta.textContent = `${viewedCount} in current view`;
  }
}

function rememberAvailableRenderState() {
  lastRenderedFilter = filterInput.value.trim().toLowerCase();
  lastRenderedSort = sortModeSelect.value;
  lastRenderedMode = getLiveListMode();
}

function renderAvailable(options = {}) {
  availableList.innerHTML = "";
  const viewed = getViewedDomains(options);
  const fragment = document.createDocumentFragment();

  for (const domain of viewed) {
    const li = document.createElement("li");
    li.textContent = domain;
    fragment.appendChild(li);
  }

  availableList.appendChild(fragment);
  updateAvailableViewMeta(viewed.length, Boolean(options.forceSort));
  rememberAvailableRenderState();
  updateDownloads();
}

function renderAvailableIncremental(newDomains) {
  if (!Array.isArray(newDomains) || newDomains.length === 0) {
    return;
  }

  const mode = getLiveListMode();
  const currentFilter = filterInput.value.trim().toLowerCase();
  const currentSort = sortModeSelect.value;

  if (
    mode === "full"
    || currentFilter !== lastRenderedFilter
    || currentSort !== lastRenderedSort
    || mode !== lastRenderedMode
  ) {
    renderAvailable({ forceSort: true });
    return;
  }

  const fragment = document.createDocumentFragment();
  if (mode === "recent") {
    for (let idx = newDomains.length - 1; idx >= 0; idx -= 1) {
      const li = document.createElement("li");
      li.textContent = newDomains[idx];
      fragment.appendChild(li);
    }
    availableList.prepend(fragment);
  } else {
    for (const domain of newDomains) {
      const li = document.createElement("li");
      li.textContent = domain;
      fragment.appendChild(li);
    }
    availableList.appendChild(fragment);
  }

  updateAvailableViewMeta(availableDomains.length, false);
  rememberAvailableRenderState();
  updateDownloads();
}

function resetLiveRenderQueues() {
  pendingStatusSnapshot = null;
  if (statusRenderTimer) {
    clearTimeout(statusRenderTimer);
    statusRenderTimer = null;
  }
  pendingAvailableDomains = [];
  forceAvailableRerender = false;
  if (availableRenderTimer) {
    clearTimeout(availableRenderTimer);
    availableRenderTimer = null;
  }
}

function flushStatusRender() {
  if (statusRenderTimer) {
    clearTimeout(statusRenderTimer);
    statusRenderTimer = null;
  }
  if (!pendingStatusSnapshot) {
    return;
  }
  const snapshot = pendingStatusSnapshot;
  pendingStatusSnapshot = null;
  setStatus(snapshot);
}

function queueStatusRender(snapshot, immediate = false) {
  pendingStatusSnapshot = snapshot;
  if (immediate) {
    flushStatusRender();
    return;
  }
  if (statusRenderTimer) {
    return;
  }
  statusRenderTimer = setTimeout(() => {
    flushStatusRender();
  }, LIVE_STATUS_RENDER_INTERVAL_MS);
}

function flushAvailableRender() {
  if (availableRenderTimer) {
    clearTimeout(availableRenderTimer);
    availableRenderTimer = null;
  }

  const domains = pendingAvailableDomains;
  const rerender = forceAvailableRerender;
  pendingAvailableDomains = [];
  forceAvailableRerender = false;

  if (rerender) {
    renderAvailable({ forceSort: true });
    return;
  }
  if (domains.length === 0) {
    return;
  }
  renderAvailableIncremental(domains);
}

function queueAvailableRender(newDomains = [], { forceRerender = false, immediate = false } = {}) {
  if (Array.isArray(newDomains) && newDomains.length > 0) {
    pendingAvailableDomains.push(...newDomains);
  }
  if (forceRerender) {
    forceAvailableRerender = true;
  }
  if (immediate) {
    flushAvailableRender();
    return;
  }
  if (availableRenderTimer) {
    return;
  }
  availableRenderTimer = setTimeout(() => {
    flushAvailableRender();
  }, LIVE_AVAILABLE_RENDER_INTERVAL_MS);
}

function appendUnknown(item) {
  const li = document.createElement("li");
  const status = item.http_status ? `HTTP ${item.http_status}` : "No HTTP status";
  const message = item.error || "Unknown error";
  li.textContent = `${item.domain} | ${status} | ${message}`;
  unknownList.prepend(li);

  while (unknownList.children.length > 100) {
    unknownList.removeChild(unknownList.lastChild);
  }
}

function updateUndoRedoButtons() {
  undoBtn.disabled = editorLocked || undoStack.length === 0;
  redoBtn.disabled = editorLocked || redoStack.length === 0;
}

function pushUndo(words) {
  undoStack.push(words.slice());
  if (undoStack.length > UNDO_LIMIT) {
    undoStack.shift();
  }
  redoStack = [];
  updateUndoRedoButtons();
}

function persistDraftSoon() {
  if (persistTimer) {
    clearTimeout(persistTimer);
  }

  persistTimer = setTimeout(() => {
    persistTimer = null;
    try {
      syncActiveWordsFromEditor();
      window.localStorage.setItem(
        WORDLIST_COLLECTION_KEY,
        JSON.stringify({
          counter: wordlistIdCounter,
          activeWordlistId,
          entries: wordlistEntries.map((entry) => ({
            id: entry.id,
            name: entry.name,
            words: entry.words,
          })),
        }),
      );
      const toolState = {
        minLength: minLengthInput.value,
        maxLength: maxLengthInput.value,
        includeSubstring: includeSubstringInput.value,
        excludeSubstring: excludeSubstringInput.value,
        allowedCharsOnly: allowedCharsOnlyInput.checked,
        editorSortMode: editorSortModeSelect.value,
        forceRecheck: forceRecheckInput.checked,
      };
      window.localStorage.setItem(WORDLIST_TOOL_KEY, JSON.stringify(toolState));
    } catch (_error) {
      // Ignore persistence failures.
    }
  }, 180);
}

function restoreDraft() {
  try {
    const rawCollection = window.localStorage.getItem(WORDLIST_COLLECTION_KEY);
    let restoredEntries = [];
    let restoredActiveId = null;

    if (rawCollection) {
      const parsedCollection = JSON.parse(rawCollection);
      if (Array.isArray(parsedCollection.entries)) {
        restoredEntries = parsedCollection.entries
          .map((entry) => ({
            id: String(entry.id || "").trim() || makeWordlistId(),
            name: String(entry.name || "Wordlist").trim() || "Wordlist",
            words: normalizeWordArray(entry.words || []),
          }))
          .filter((entry) => entry.id);
      }
      if (Number.isInteger(parsedCollection.counter) && parsedCollection.counter > 0) {
        wordlistIdCounter = parsedCollection.counter;
      }
      restoredActiveId = String(parsedCollection.activeWordlistId || "").trim() || null;
    } else {
      // Backward compatibility with older local storage keys.
      const primaryDraft = window.localStorage.getItem(WORDLIST_DRAFT_KEY);
      const secondaryDraft = window.localStorage.getItem("domain-search-wordlist-secondary-v1");
      restoredEntries = [makeWordlistEntry("List 1", parseWordlistLines(primaryDraft || "").words)];
      const secondaryWordsFromDraft = parseWordlistLines(secondaryDraft || "").words;
      if (secondaryWordsFromDraft.length) {
        restoredEntries.push(makeWordlistEntry("List 2", secondaryWordsFromDraft));
      }
      restoredActiveId = window.localStorage.getItem("domain-search-editor-target-v1");
    }

    if (restoredEntries.length === 0) {
      restoredEntries = [makeWordlistEntry("List 1", [])];
    }
    wordlistEntries = restoredEntries;
    activeWordlistId = wordlistEntries.some((entry) => entry.id === restoredActiveId)
      ? restoredActiveId
      : wordlistEntries[0].id;

    const rawTools = window.localStorage.getItem(WORDLIST_TOOL_KEY);
    if (rawTools) {
      const parsed = JSON.parse(rawTools);
      minLengthInput.value = parsed.minLength || "";
      maxLengthInput.value = parsed.maxLength || "";
      includeSubstringInput.value = parsed.includeSubstring || "";
      excludeSubstringInput.value = parsed.excludeSubstring || "";
      allowedCharsOnlyInput.checked = Boolean(parsed.allowedCharsOnly);
      editorSortModeSelect.value = parsed.editorSortMode === "za" ? "za" : "az";
      if (typeof parsed.forceRecheck === "boolean") {
        forceRecheckInput.checked = parsed.forceRecheck;
      }
    }

    const collapsedValue = window.localStorage.getItem(EDITOR_COLLAPSED_KEY);
    setEditorCollapsed(collapsedValue === "1");
    const rateCollapsedValue = window.localStorage.getItem(RATE_PANEL_COLLAPSED_KEY);
    setRatePanelCollapsed(rateCollapsedValue === "1");
  } catch (_error) {
    wordlistEntries = [makeWordlistEntry("List 1", [])];
    activeWordlistId = wordlistEntries[0].id;
    setEditorCollapsed(false);
    setRatePanelCollapsed(false);
  }

  ensureWordlistEntries();
  editorTextarea.value = wordsToText(getActiveWordlist().words);
  updateEditorTargetSelect();
  setEditorLabelText();
}

function updateUploadSummaries() {
  const primaryCount = getPrimaryParseResult().dedupedWords.length;
  const secondaryCount = getSecondaryParseResult().dedupedWords.length;

  if (wordlistEntries.length <= 1) {
    uploadAssignment.textContent = `#1 is active (${primaryCount} usable words). Wildcards 2-4 reuse #1.`;
  } else {
    uploadAssignment.textContent = `#1 primary (${primaryCount} words) | #2 secondary for wildcards 2-4 (${secondaryCount} words).`;
  }
  renderLoadedWordlists();
  updateEditorTargetSelect();
  setEditorLabelText();
  persistDraftSoon();
}

function swapWordlists(indexA, indexB) {
  const tmp = wordlistEntries[indexA];
  wordlistEntries[indexA] = wordlistEntries[indexB];
  wordlistEntries[indexB] = tmp;
}

function moveWordlist(wordlistId, direction) {
  const idx = wordlistEntries.findIndex((entry) => entry.id === wordlistId);
  if (idx < 0) {
    return;
  }
  const next = idx + direction;
  if (next < 0 || next >= wordlistEntries.length) {
    return;
  }
  syncActiveWordsFromEditor();
  swapWordlists(idx, next);
  updateUploadSummaries();
}

function removeWordlist(wordlistId) {
  const idx = wordlistEntries.findIndex((entry) => entry.id === wordlistId);
  if (idx < 0) {
    return;
  }
  syncActiveWordsFromEditor();
  wordlistEntries.splice(idx, 1);
  if (wordlistEntries.length === 0) {
    const fallback = makeWordlistEntry("List 1", []);
    wordlistEntries.push(fallback);
    activeWordlistId = fallback.id;
  } else if (!wordlistEntries.some((entry) => entry.id === activeWordlistId)) {
    activeWordlistId = wordlistEntries[Math.min(idx, wordlistEntries.length - 1)].id;
  }
  editorTextarea.value = wordsToText(getActiveWordlist().words);
  clearUndoRedoStacks();
  refreshWordlistEditor();
}

function mergeAllWordlists() {
  syncActiveWordsFromEditor();
  if (wordlistEntries.length <= 1) {
    statusMessage.textContent = "Nothing to merge yet. Upload at least two lists.";
    return;
  }
  const seen = new Set();
  const merged = [];
  for (const entry of wordlistEntries) {
    for (const word of entry.words) {
      if (seen.has(word)) {
        continue;
      }
      seen.add(word);
      merged.push(word);
    }
  }

  const mergedEntry = makeWordlistEntry("Merged List", merged);
  wordlistEntries = [mergedEntry];
  activeWordlistId = mergedEntry.id;
  editorTextarea.value = wordsToText(mergedEntry.words);
  clearUndoRedoStacks();
  refreshWordlistEditor();
  statusMessage.textContent = `Merged into one list with ${merged.length} unique words.`;
}

function applyEditorWords(nextWords, pushHistoryEntry) {
  const current = getEditorParseResult().words;
  const sameLength = current.length === nextWords.length;
  const unchanged = sameLength && current.every((word, idx) => word === nextWords[idx]);
  if (unchanged) {
    return;
  }

  if (pushHistoryEntry) {
    pushUndo(current);
  }

  editorTextarea.value = wordsToText(nextWords);
  refreshWordlistEditor();
}

function refreshWordlistEditor() {
  const parseResult = syncActiveWordsFromEditor();

  wlTotalEl.textContent = String(parseResult.totalLines);
  wlUniqueEl.textContent = String(parseResult.uniqueCount);
  wlFilteredEl.textContent = String(parseResult.filteredOutCount);
  wlUsableEl.textContent = String(parseResult.usableCount);

  const overLimit = isOverWordLimit(parseResult.dedupedWords.length, MAX_SUPPORTED_WORDS);
  perfWarning.classList.toggle("hidden", !overLimit);

  updateUndoRedoButtons();
  updateEditorTargetAvailability();
  updateUploadSummaries();
  persistDraftSoon();
}

function closeStream() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  resetLiveRenderQueues();
}

async function fetchSnapshot() {
  if (!jobId) {
    return;
  }

  const response = await fetch(`/api/jobs/${jobId}`);
  if (!response.ok) {
    return;
  }

  const snapshot = await response.json();
  queueStatusRender(snapshot, true);

  if (Array.isArray(snapshot.available_domains)) {
    availableDomains = [];
    availableSet = new Set();
    for (const domain of snapshot.available_domains) {
      if (availableSet.has(domain)) {
        continue;
      }
      availableSet.add(domain);
      availableDomains.push(domain);
    }
    queueAvailableRender([], { forceRerender: true, immediate: true });
  }

  if (Array.isArray(snapshot.recent_unknowns)) {
    unknownList.innerHTML = "";
    for (const item of snapshot.recent_unknowns.slice().reverse()) {
      appendUnknown(item);
    }
  }
}

async function finalizeFromServer() {
  flushAvailableRender();
  flushStatusRender();
  await fetchSnapshot();
  closeStream();
}

function mergeAvailableDomains(domains) {
  const added = [];
  for (const domain of domains || []) {
    if (!domain || availableSet.has(domain)) {
      continue;
    }
    availableSet.add(domain);
    availableDomains.push(domain);
    added.push(domain);
  }
  return added;
}

function openStream() {
  if (!jobId) {
    return;
  }

  closeStream();
  eventSource = new EventSource(`/api/jobs/${jobId}/events`);

  const isCurrentJobEvent = (payload) => {
    if (!payload || typeof payload !== "object") {
      return true;
    }
    if (!Object.prototype.hasOwnProperty.call(payload, "job_id")) {
      return true;
    }
    return payload.job_id === jobId;
  };

  eventSource.addEventListener("snapshot", (event) => {
    const snapshot = JSON.parse(event.data);
    if (!isCurrentJobEvent(snapshot)) {
      return;
    }

    if (Array.isArray(snapshot.available_domains)) {
      availableDomains = [];
      availableSet = new Set();
      for (const domain of snapshot.available_domains) {
        if (availableSet.has(domain)) {
          continue;
        }
        availableSet.add(domain);
        availableDomains.push(domain);
      }
      queueAvailableRender([], { forceRerender: true, immediate: true });
    }

    if (Array.isArray(snapshot.recent_unknowns)) {
      unknownList.innerHTML = "";
      for (const item of snapshot.recent_unknowns.slice().reverse()) {
        appendUnknown(item);
      }
    }

    queueStatusRender(snapshot, true);
  });

  eventSource.addEventListener("progress", (event) => {
    const data = JSON.parse(event.data);
    if (!isCurrentJobEvent(data) || scanState === "cancelling") {
      return;
    }
    queueStatusRender({
      status: "running",
      total_candidates: data.total_candidates ?? data.total,
      valid_domains: data.valid_domains,
      processed: data.processed,
      progress_processed: data.progress_processed ?? data.processed,
      available_count: data.available_count,
      taken_count: data.taken_count,
      unknown_count: data.unknown_count,
      invalid_count: data.invalid_count,
      cache_hits: data.cache_hits,
      cache_misses: data.cache_misses,
    });
  });

  eventSource.addEventListener("available", (event) => {
    const data = JSON.parse(event.data);
    if (!isCurrentJobEvent(data) || scanState === "cancelling") {
      return;
    }
    const domain = data.result && data.result.domain;
    const added = mergeAvailableDomains(domain ? [domain] : []);
    if (added.length === 0) {
      return;
    }
    queueAvailableRender(added);
  });

  eventSource.addEventListener("available_batch", (event) => {
    const data = JSON.parse(event.data);
    if (!isCurrentJobEvent(data) || scanState === "cancelling") {
      return;
    }
    const results = Array.isArray(data.results) ? data.results : [];
    const domains = results.map((item) => item && item.domain).filter((item) => typeof item === "string");
    const added = mergeAvailableDomains(domains);
    if (added.length === 0) {
      return;
    }
    queueAvailableRender(added);
  });

  eventSource.addEventListener("completed", async () => {
    await finalizeFromServer();
  });

  eventSource.addEventListener("failed", async () => {
    await finalizeFromServer();
  });

  eventSource.addEventListener("cancelled", async () => {
    await finalizeFromServer();
  });

  eventSource.onerror = async () => {
    await fetchSnapshot();
  };
}

function resetResultsUI() {
  resetLiveRenderQueues();
  availableDomains = [];
  availableSet = new Set();
  availableList.innerHTML = "";
  unknownList.innerHTML = "";
  rememberAvailableRenderState();

  setStatus({
    status: "queued",
    valid_domains: 0,
    processed: 0,
    available_count: 0,
    taken_count: 0,
    unknown_count: 0,
    invalid_count: 0,
    cache_hits: 0,
    cache_misses: 0,
  });

  statusMessage.textContent = "Preparing your scan...";
  updateDownloads();
}

function createWordlistBlob(words) {
  const text = `${wordsToText(words)}\n`;
  return new Blob([text], { type: "text/plain" });
}

function validateWordlistLimits(primaryWords, secondaryList) {
  if (isOverWordLimit(primaryWords.length, MAX_SUPPORTED_WORDS)) {
    statusMessage.textContent = `List #1 exceeds max supported size (${MAX_SUPPORTED_WORDS} words).`;
    return false;
  }

  if (secondaryList && isOverWordLimit(secondaryList.length, MAX_SUPPORTED_WORDS)) {
    statusMessage.textContent = `List #2 exceeds max supported size (${MAX_SUPPORTED_WORDS} words).`;
    return false;
  }

  return true;
}

async function startScan() {
  if (scanState === "running" || scanState === "cancelling") {
    return;
  }

  const patternValue = patternInput.value;
  const stars = wildcardCount(patternValue);
  if (stars < 1 || stars > 4) {
    statusMessage.textContent = "Pattern must contain between 1 and 4 * wildcards.";
    return;
  }

  syncActiveWordsFromEditor();
  ensureWordlistEntries();
  const primaryWordsForScan = getPrimaryParseResult().dedupedWords;
  if (!primaryWordsForScan.length) {
    statusMessage.textContent = "No usable words in list #1. Upload or edit the wordlist first.";
    return;
  }

  let secondaryList = null;
  if (stars >= 2) {
    secondaryList = getSecondaryDedupedWords();
    if (!secondaryList.length) {
      secondaryList = null;
    }
  }

  if (!validateWordlistLimits(primaryWordsForScan, secondaryList)) {
    return;
  }

  closeStream();
  resetResultsUI();
  setScanState("running");
  setEditorLocked(true);

  const formData = new FormData();
  formData.set("pattern", patternValue);
  formData.set("force_recheck", forceRecheckInput.checked ? "true" : "false");
  formData.set("wordlist", createWordlistBlob(primaryWordsForScan), "editor-list-1.txt");
  if (stars >= 2 && secondaryList) {
    formData.set("wordlist_secondary", createWordlistBlob(secondaryList), "editor-list-2.txt");
  }

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || "Failed to create job");
    }

    const data = await response.json();
    jobId = data.job_id;
    updateDownloads();
    openStream();
  } catch (error) {
    statusMessage.textContent = String(error);
    setScanState("idle");
    setEditorLocked(false);
  }
}

async function stopScan() {
  if (!jobId || scanState !== "running") {
    return;
  }

  setScanState("cancelling");
  statusMessage.textContent = "Stopping search...";

  try {
    const response = await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    if (!response.ok && response.status !== 409) {
      const err = await response.json();
      throw new Error(err.detail || "Failed to stop the job");
    }
    // Stop consuming in-flight progress events from this stream and refresh terminal state directly.
    closeStream();
    await fetchSnapshot();
  } catch (error) {
    statusMessage.textContent = String(error);
    setScanState("running");
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (scanState === "running") {
    await stopScan();
    return;
  }
  if (scanState === "cancelling") {
    return;
  }
  await startScan();
});

wordlistUploadInput.addEventListener("change", async () => {
  if (editorLocked) {
    return;
  }

  const files = Array.from(wordlistUploadInput.files || []);
  if (!files.length) {
    return;
  }

  try {
    syncActiveWordsFromEditor();
    const nextEntries = [];
    let totalLoaded = 0;
    for (const file of files) {
      const raw = await file.text();
      const parsedWords = parseUploadText(raw);
      totalLoaded += parsedWords.length;
      nextEntries.push(makeWordlistEntry(file.name, parsedWords));
    }

    const currentPrimaryCount = getPrimaryParseResult().dedupedWords.length;
    if (wordlistEntries.length === 1 && currentPrimaryCount === 0) {
      wordlistEntries = nextEntries;
    } else {
      wordlistEntries.push(...nextEntries);
    }

    activeWordlistId = nextEntries[0].id;
    editorTextarea.value = wordsToText(getActiveWordlist().words);
    clearUndoRedoStacks();
    refreshWordlistEditor();
    statusMessage.textContent = `Loaded ${nextEntries.length} list${nextEntries.length === 1 ? "" : "s"} (${totalLoaded} words).`;
  } catch (_error) {
    statusMessage.textContent = "Could not read one of the uploaded wordlist files.";
  } finally {
    wordlistUploadInput.value = "";
  }
});

loadedWordlists.addEventListener("click", (event) => {
  if (editorLocked) {
    return;
  }
  const target = event.target;
  if (!(target instanceof HTMLButtonElement)) {
    return;
  }
  const action = target.dataset.action;
  const wordlistId = target.dataset.wordlistId;
  if (!action || !wordlistId) {
    return;
  }
  if (action === "edit") {
    setActiveWordlist(wordlistId);
    return;
  }
  if (action === "up") {
    moveWordlist(wordlistId, -1);
    return;
  }
  if (action === "down") {
    moveWordlist(wordlistId, 1);
    return;
  }
  if (action === "remove") {
    removeWordlist(wordlistId);
  }
});

mergeWordlistsBtn.addEventListener("click", () => {
  if (editorLocked) {
    return;
  }
  mergeAllWordlists();
});

clearWordlistsBtn.addEventListener("click", () => {
  if (editorLocked) {
    return;
  }
  wordlistEntries = [makeWordlistEntry("List 1", [])];
  activeWordlistId = wordlistEntries[0].id;
  editorTextarea.value = "";
  clearUndoRedoStacks();
  refreshWordlistEditor();
  statusMessage.textContent = "Cleared all loaded lists.";
});

editorTargetSelect.addEventListener("change", () => {
  if (editorLocked) {
    return;
  }
  setActiveWordlist(editorTargetSelect.value);
});

forceRecheckInput.addEventListener("change", () => {
  persistDraftSoon();
});

editorCollapseToggle.addEventListener("click", () => {
  setEditorCollapsed(!editorCollapsed);
});

ratePanelToggle.addEventListener("click", () => {
  setRatePanelCollapsed(!ratePanelCollapsed);
});

if (speedApplyBtn) {
  speedApplyBtn.addEventListener("click", async () => {
    await applyManualSpeedOverrides();
  });
}

if (speedResetBtn) {
  speedResetBtn.addEventListener("click", async () => {
    await resetManualSpeedOverrides();
  });
}

exportMenuToggle.addEventListener("click", (event) => {
  event.preventDefault();
  const isOpen = exportMenuToggle.getAttribute("aria-expanded") === "true";
  setExportMenuOpen(!isOpen);
});

document.addEventListener("click", (event) => {
  if (!(event.target instanceof Node)) {
    return;
  }
  if (!exportMenu.contains(event.target)) {
    setExportMenuOpen(false);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setExportMenuOpen(false);
  }
});

txtBtn.addEventListener("click", async () => {
  await triggerExportDownload("txt");
});

csvBtn.addEventListener("click", async () => {
  await triggerExportDownload("csv");
});

jsonBtn.addEventListener("click", async () => {
  await triggerExportDownload("json");
});

editorTextarea.addEventListener("input", () => {
  refreshWordlistEditor();
});

applyCleanupBtn.addEventListener("click", () => {
  if (editorLocked) {
    return;
  }

  const parseResult = getEditorParseResult();
  const minLen = parsePositiveInt(minLengthInput.value);
  const maxLen = parsePositiveInt(maxLengthInput.value);

  const nextWords = applyCleanup(parseResult.words, {
    minLen,
    maxLen,
    includeText: includeSubstringInput.value,
    excludeText: excludeSubstringInput.value,
    allowedCharsOnly: allowedCharsOnlyInput.checked,
  });

  applyEditorWords(nextWords, true);
});

dedupeBtn.addEventListener("click", () => {
  if (editorLocked) {
    return;
  }
  const parseResult = getEditorParseResult();
  const nextWords = dedupeWords(parseResult.words);
  applyEditorWords(nextWords, true);
});

sortEditorBtn.addEventListener("click", () => {
  if (editorLocked) {
    return;
  }
  const parseResult = getEditorParseResult();
  const nextWords = sortWords(parseResult.words, editorSortModeSelect.value);
  applyEditorWords(nextWords, true);
});

undoBtn.addEventListener("click", () => {
  if (editorLocked || undoStack.length === 0) {
    return;
  }

  const currentWords = getEditorParseResult().words;
  const prevWords = undoStack.pop();
  redoStack.push(currentWords);

  editorTextarea.value = wordsToText(prevWords);
  refreshWordlistEditor();
});

redoBtn.addEventListener("click", () => {
  if (editorLocked || redoStack.length === 0) {
    return;
  }

  const currentWords = getEditorParseResult().words;
  const nextWords = redoStack.pop();
  undoStack.push(currentWords);

  editorTextarea.value = wordsToText(nextWords);
  refreshWordlistEditor();
});

exportWordlistBtn.addEventListener("click", () => {
  const parseResult = getEditorParseResult();
  if (!parseResult.words.length) {
    statusMessage.textContent = "No words to export.";
    return;
  }

  const text = `${wordsToText(parseResult.words)}\n`;
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  const active = getActiveWordlist();
  const safeName = String((active && active.name) || "edited-wordlist")
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "") || "edited-wordlist";
  anchor.href = url;
  anchor.download = `${safeName}.txt`;
  anchor.click();
  URL.revokeObjectURL(url);
});

filterInput.addEventListener("input", () => {
  renderAvailable({ forceSort: true });
});

sortModeSelect.addEventListener("change", () => {
  renderAvailable({ forceSort: true });
});

copyBtn.addEventListener("click", async () => {
  const viewed = getViewedDomains();
  if (!viewed.length) {
    return;
  }
  await navigator.clipboard.writeText(viewed.join("\n"));
  statusMessage.textContent = `Copied ${viewed.length} available domains from current view.`;
});

themeToggle.addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme") || "light";
  setTheme(current === "dark" ? "light" : "dark");
});

initTheme();
restoreDraft();
setScanState("idle");
setEditorLocked(false);
refreshWordlistEditor();
renderAvailable();
updateDownloads();
refreshRateStatus();
refreshRateConfig();
