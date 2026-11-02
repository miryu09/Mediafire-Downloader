const form = document.querySelector("#download-form");
const linkInput = document.querySelector("#mediafire-link");
const destinationInput = document.querySelector("#destination");
const downloadButton = document.querySelector("#download-button");
const emergencyStopButton = document.querySelector("#emergency-stop-button");
const sizeEstimate = document.querySelector("#size-estimate");
const sizeEstimateValue = document.querySelector("#size-estimate-value");
const statusMessage = document.querySelector("#status-message");
const statusPill = document.querySelector("#status-pill");
const logOutput = document.querySelector("#log-output");
const savedLocation = document.querySelector("#saved-location");
const openFolderButton = document.querySelector("#open-folder-button");
const pasteButton = document.querySelector("#paste-button");
const browseFolderButton = document.querySelector("#browse-folder-button");
const settingsDialog = document.querySelector("#settings-dialog");
const settingsOpen = document.querySelector("#settings-open");
const settingsClose = document.querySelector("#settings-close");
const settingsFeedback = document.querySelector("#settings-feedback");
const progressTrack = document.querySelector("#progress-track");
const progressFill = document.querySelector("#progress-fill");
const progressCount = document.querySelector("#progress-count");
const progressEta = document.querySelector("#progress-eta");
const networkSpeed = document.querySelector("#network-speed");
const networkTotal = document.querySelector("#network-total");
const networkGraph = document.querySelector("#network-graph");
const bootstrapConfigKey = "mf-dl-bootstrap-config";
let activeJob = null;
let completedJobId = null;
let nextLogLine = 0;
let destinationSaveTimer = null;
let networkGraphHistory = [];
const MAX_VISIBLE_LOG_LINES = 3000;
const settingsKey = "mf-dl-appearance";
const themes = new Set(["neumorphism", "modern-minimal", "aurora", "midnight", "warm-paper"]);
const modes = new Set(["system", "dark", "light"]);
const accentColors = {
  blue: "#2168b5",
  violet: "#7047bd",
  teal: "#16756e",
  rose: "#a23e59",
  amber: "#88550f",
  coral: "#a63d30",
  indigo: "#4852a0",
};
const accents = new Set(Object.keys(accentColors));
const settings = loadSettings();

function loadSettings() {
  const defaults = {
    theme: "midnight",
    mode: "system",
    accent: "teal",
  };
  try {
    const saved = JSON.parse(localStorage.getItem(settingsKey) || "{}");
    return {
      theme: themes.has(saved.theme) ? saved.theme : defaults.theme,
      mode: modes.has(saved.mode) ? saved.mode : defaults.mode,
      accent: accents.has(saved.accent) ? saved.accent : defaults.accent,
    };
  } catch {
    return defaults;
  }
}

function getContrastColor(hexColor) {
  const channels = [1, 3, 5].map((index) => parseInt(hexColor.slice(index, index + 2), 16) / 255);
  const luminance = channels
    .map((channel) => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
    .reduce((total, channel, index) => total + channel * [0.2126, 0.7152, 0.0722][index], 0);
  const whiteContrast = 1.05 / (luminance + 0.05);
  const darkLuminance = 0.012;
  const darkContrast = (luminance + 0.05) / (darkLuminance + 0.05);
  return whiteContrast >= darkContrast ? "#ffffff" : "#18212b";
}

function applySettings() {
  const root = document.documentElement;
  root.dataset.theme = settings.theme;
  root.dataset.mode = settings.mode;
  root.dataset.accent = settings.accent;
  root.style.setProperty(
    "--accent-contrast",
    getContrastColor(accentColors[settings.accent]),
  );

  document.querySelectorAll("[data-setting]").forEach((button) => {
    const selected = settings[button.dataset.setting] === button.dataset.value;
    button.setAttribute("aria-pressed", String(selected));
  });
  document.querySelectorAll(".color-choice").forEach((button) => {
    button.setAttribute("aria-pressed", String(settings.accent === button.dataset.accent));
  });

  requestAnimationFrame(() => {
    drawTransferGraph(networkGraph, networkGraphHistory, "Network download speed");
  });
}

function saveSettings() {
  try {
    localStorage.setItem(settingsKey, JSON.stringify(settings));
    settingsFeedback.textContent = "Settings saved on this device.";
  } catch {
    settingsFeedback.textContent = "Settings applied, but could not be saved on this device.";
  }
}

async function saveDestinationPreference(destination) {
  const value = destination.trim();
  if (!value) return;

  try {
    const response = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destination: value }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not remember the download folder.");
  } catch (error) {
    statusMessage.textContent = error.message;
  }
}

applySettings();

function localizeError(message) {
  const translations = {
    "Paste a MediaFire file or folder link.": "Paste a MediaFire file or folder link.",
    "Enter a folder to save downloads to.": "Enter a destination folder.",
    "That link was not recognized as a supported MediaFire link.": "This MediaFire link is not recognized.",
    "A download is already running. Wait for it to finish first.": "A download is already running. Wait for it to finish first.",
    "Download session not found.": "Download session not found.",
  };
  return translations[message] || message;
}

function setStatus(state, message, label) {
  statusMessage.textContent = message;
  statusPill.className = `status-pill ${state}`;
  statusPill.replaceChildren();
  const dot = document.createElement("span");
  dot.className = "status-dot";
  statusPill.append(dot, document.createTextNode(` ${label}`));
}

function resetEmergencyStopButton() {
  emergencyStopButton.querySelector("span").textContent = "Stop";
  emergencyStopButton.disabled = false;
}

function clearLog() {
  logOutput.replaceChildren();
  nextLogLine = 0;
}

function appendLines(lines) {
  if (!lines.length && !logOutput.children.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Waiting for downloader output...";
    logOutput.append(empty);
  }
  const fragment = document.createDocumentFragment();
  for (const line of lines) {
    const row = document.createElement("div");
    row.className = "log-line";
    row.textContent = line;
    fragment.append(row);
  }
  if (lines.length && logOutput.querySelector(".empty-state")) logOutput.replaceChildren();
  logOutput.append(fragment);
  while (logOutput.children.length > MAX_VISIBLE_LOG_LINES) {
    logOutput.firstElementChild.remove();
  }
  logOutput.scrollTop = logOutput.scrollHeight;
}

function formatDuration(seconds) {
  const remaining = Math.max(0, Math.ceil(seconds));
  if (remaining < 60) return `${remaining}s`;
  const minutes = Math.floor(remaining / 60);
  const hours = Math.floor(minutes / 60);
  if (hours) return `${hours}h ${minutes % 60}m`;
  return `${minutes}m ${remaining % 60}s`;
}

function formatBytes(bytes) {
  if (bytes < 1000) return `${Math.round(bytes)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1000;
  let unitIndex = 0;
  while (value >= 1000 && unitIndex < units.length - 1) {
    value /= 1000;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unitIndex]}`;
}

function drawTransferGraph(canvas, history, label) {
  const context = canvas.getContext("2d");
  if (!context) return;

  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (!width || !height) return;

  const pixelRatio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * pixelRatio);
  canvas.height = Math.round(height * pixelRatio);
  context.scale(pixelRatio, pixelRatio);
  context.clearRect(0, 0, width, height);

  const style = getComputedStyle(document.documentElement);
  const gridColor = style.getPropertyValue("--line").trim() || "#d3d9e2";
  const graphColor = style.getPropertyValue("--graph-line").trim() || "#7047bd";
  const graphFill = style.getPropertyValue("--graph-fill").trim() || "rgb(112 71 189 / 16%)";
  const values = history.length ? history : [0];
  const maximum = Math.max(...values, 2);
  const top = 7;
  const bottom = height - 7;
  const left = 57;
  const right = width - 5;
  const graphHeight = bottom - top;
  const graphWidth = right - left;

  context.lineWidth = 1;
  context.strokeStyle = gridColor;
  context.beginPath();
  for (let row = 0; row <= 2; row += 1) {
    const fraction = row / 2;
    const y = top + graphHeight * fraction;
    context.moveTo(left, y);
    context.lineTo(right, y);
    const value = maximum * (1 - fraction);
    context.fillStyle = style.getPropertyValue("--muted").trim() || "#788496";
    context.font = "9px Segoe UI, sans-serif";
    context.textAlign = "right";
    context.textBaseline = row === 0 ? "top" : row === 2 ? "bottom" : "middle";
    context.fillText(`${formatBytes(value)}/s`, left - 7, y);
  }
  context.stroke();

  context.beginPath();
  values.forEach((value, index) => {
    const x = values.length === 1
      ? right
      : left + (index / (values.length - 1)) * graphWidth;
    const y = bottom - (Math.min(value, maximum) / maximum) * graphHeight;
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  const lastX = right;
  context.lineTo(lastX, bottom);
  context.lineTo(left, bottom);
  context.closePath();
  context.fillStyle = graphFill;
  context.fill();

  context.beginPath();
  values.forEach((value, index) => {
    const x = values.length === 1
      ? right
      : left + (index / (values.length - 1)) * graphWidth;
    const y = bottom - (Math.min(value, maximum) / maximum) * graphHeight;
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.lineWidth = 2;
  context.lineJoin = "round";
  context.lineCap = "round";
  context.strokeStyle = graphColor;
  context.stroke();

  const lastValue = values[values.length - 1] || 0;
  canvas.setAttribute(
    "aria-label",
    `${label} graph. Current speed ${formatBytes(lastValue)}/s.`,
  );
}

function renderTransferStats(metric, elements, label, totalLabel) {
  const transferredBytes = Math.max(0, metric.transferred_bytes || 0);
  const speed = Math.max(0, metric.speed_bytes_per_second || 0);
  const history = Array.isArray(metric.history) ? metric.history : [];
  elements.speed.textContent = `${formatBytes(speed)}/s`;
  elements.total.textContent = `${formatBytes(transferredBytes)} ${totalLabel}`;
  drawTransferGraph(elements.graph, history, label);
}

function updateTransferStats(network) {
  networkGraphHistory = Array.isArray(network.history) ? network.history : [];
  renderTransferStats(
    {
      transferred_bytes: network.downloaded_bytes,
      speed_bytes_per_second: network.speed_bytes_per_second,
      history: network.history,
    },
    { speed: networkSpeed, total: networkTotal, graph: networkGraph },
    "Network download speed",
    "received",
  );
}

function resetTransferStats() {
  updateTransferStats({ downloaded_bytes: 0, speed_bytes_per_second: 0, history: [] });
}

function updateSizeEstimate(estimate) {
  if (!estimate) return;
  if (estimate.state === "estimating") {
    sizeEstimateValue.textContent = "Calculating total size…";
  } else if (estimate.state === "complete") {
    sizeEstimateValue.textContent = `${formatBytes(estimate.total_bytes)} · ${estimate.file_count} files`;
  } else if (estimate.state === "partial") {
    const itemLabel = estimate.unknown_files === 1 ? "item" : "items";
    sizeEstimateValue.textContent =
      `At least ${formatBytes(estimate.total_bytes)} · size unavailable for ${estimate.unknown_files} ${itemLabel}`;
  } else {
    sizeEstimateValue.textContent = "Total size unavailable";
  }
}

function updateProgress(job, progress, state) {
  const total = progress.total;
  const completed = progress.completed;
  const scanning = progress.folders_pending > 0 || progress.folders_active > 0;
  const running = state === "running";
  const totalKnown = progress.total_known;

  progressTrack.classList.toggle("indeterminate", running && (!totalKnown || scanning));
  const percent = totalKnown && total > 0
    ? Math.min(100, Math.round((completed / total) * 100))
    : state === "finished" && total === 0
      ? 100
      : 0;
  progressFill.style.width = `${percent}%`;
  progressTrack.setAttribute("aria-valuenow", String(percent));
  progressTrack.setAttribute("aria-valuetext", totalKnown
    ? `${completed} of ${total} files processed`
    : progress.phase === "estimating_size" ? "Estimating total download size" : "Counting files");

  if (state === "stopped") {
    progressCount.textContent = `${completed} / ${total} files processed before stop`;
  } else if (progress.phase === "estimating_size") {
    progressCount.textContent = "Estimating total download size…";
  } else if (!totalKnown) {
    progressCount.textContent = "Counting files...";
  } else {
    const failedText = progress.failed ? ` · ${progress.failed} failed` : "";
    const scanText = scanning ? " · Scanning folders" : "";
    progressCount.textContent = `${completed} / ${total} files${failedText}${scanText}`;
  }

  if (state === "finished") {
    progressEta.textContent = "Estimated Time —";
    progressFill.style.width = "100%";
  } else if (state === "failed") {
    progressEta.textContent = "Stopped";
  } else if (!totalKnown || scanning) {
    progressEta.textContent = scanning ? "Estimated Time after scan" : "Calculating...";
  } else if (total <= completed) {
    progressEta.textContent = "Finalizing...";
  } else if (completed === 0) {
    progressEta.textContent = "Estimating...";
  } else {
    const elapsedSeconds = Math.max(1, (Date.now() - job.startedAt) / 1000);
    const filesPerSecond = completed / elapsedSeconds;
    progressEta.textContent = `Estimated Time ${formatDuration((total - completed) / filesPerSecond)}`;
  }

  if (state === "stopped") {
    progressEta.textContent = "Stopped";
    setStatus("stopped", "Download stopped by user.", "Stopped");
  } else if (running && progress.phase === "estimating_size") {
    progressEta.textContent = "Estimating total size…";
    setStatus("running", "Estimating total download size…", "Estimating");
  } else if (state === "failed") {
    setStatus("failed", "The download process stopped. Check the log for details.", "Failed");
  } else if (state === "finished") {
    const summary = `${completed} of ${total} files processed${progress.failed ? ` · ${progress.failed} failed` : ""}.`;
    setStatus(
      progress.failed ? "failed" : "finished",
      summary,
      progress.failed ? "Completed with errors" : "Complete",
    );
  } else if (scanning) {
    setStatus("running", "Scanning folders and counting files...", "Scanning folders");
  } else if (!totalKnown || progress.phase === "resolving") {
    setStatus("running", "Preparing your download...", "Preparing");
  } else if (total > 0 && completed >= total) {
    setStatus("running", "All files processed. Finishing up...", "Finalizing");
  } else {
    setStatus("running", "Downloading files...", "Downloading");
  }
}

async function pollJob(job) {
  while (activeJob === job) {
    try {
      const response = await fetch(
        `/api/job?id=${encodeURIComponent(job.id)}&after=${nextLogLine}`,
        { cache: "no-store" },
      );
      const result = await response.json();
      if (!response.ok) throw new Error(localizeError(result.error || "Could not read download status."));

      appendLines(result.lines);
      nextLogLine = result.next;
      savedLocation.textContent = result.destination;
      updateSizeEstimate(result.size_estimate);
      updateProgress(job, result.progress, result.state);
      updateTransferStats(result.network || {});

      if (result.state === "finished") {
        completedJobId = job.id;
        openFolderButton.hidden = false;
        emergencyStopButton.hidden = true;
        resetEmergencyStopButton();
        downloadButton.disabled = false;
        activeJob = null;
        return;
      }
      if (result.state === "stopped") {
        emergencyStopButton.hidden = true;
        resetEmergencyStopButton();
        downloadButton.disabled = false;
        activeJob = null;
        return;
      }
      if (result.state === "failed") {
        if (result.error) statusMessage.textContent = result.error;
        openFolderButton.hidden = true;
        emergencyStopButton.hidden = true;
        resetEmergencyStopButton();
        downloadButton.disabled = false;
        activeJob = null;
        return;
      }
    } catch (error) {
      setStatus("failed", error.message, "Error");
      emergencyStopButton.hidden = true;
      resetEmergencyStopButton();
      downloadButton.disabled = false;
      activeJob = null;
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 900));
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (activeJob) return;

  clearTimeout(destinationSaveTimer);
  saveDestinationPreference(destinationInput.value);
  downloadButton.disabled = true;
  openFolderButton.hidden = true;
  completedJobId = null;
  clearLog();
  sizeEstimate.hidden = false;
  sizeEstimateValue.textContent = "Calculating total size…";
  savedLocation.textContent = "";
  progressFill.style.width = "0%";
  progressCount.textContent = "Counting files...";
  progressEta.textContent = "Calculating...";
  resetTransferStats();
  progressTrack.classList.add("indeterminate");
  progressTrack.setAttribute("aria-valuenow", "0");
  setStatus("running", "Connecting to MediaFire and preparing your download...", "Working");

  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        link: linkInput.value.trim(),
        destination: destinationInput.value.trim(),
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(localizeError(result.error || "Could not start the download."));

    activeJob = { ...result, startedAt: Date.now() };
    emergencyStopButton.hidden = false;
    savedLocation.textContent = result.destination;
    setStatus("running", "Download started. Progress will appear below.", "Preparing");
    pollJob(activeJob);
  } catch (error) {
    setStatus("failed", error.message, "Error");
    emergencyStopButton.hidden = true;
    downloadButton.disabled = false;
  }
});

emergencyStopButton.addEventListener("click", async () => {
  if (!activeJob || emergencyStopButton.disabled) return;

  emergencyStopButton.disabled = true;
  emergencyStopButton.querySelector("span").textContent = "Stopping…";
  setStatus("running", "Stop requested…", "Stopping");
  try {
    const response = await fetch("/api/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: activeJob.id }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Could not stop the download.");
    }
  } catch (error) {
    emergencyStopButton.disabled = false;
    resetEmergencyStopButton();
    setStatus("running", `Could not stop the download: ${error.message}`, "Downloading");
  }
});

openFolderButton.addEventListener("click", async () => {
  if (!completedJobId || openFolderButton.disabled) return;

  openFolderButton.disabled = true;
  try {
    const response = await fetch("/api/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: completedJobId }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not open the download folder.");
  } catch (error) {
    statusMessage.textContent = error.message;
  } finally {
    openFolderButton.disabled = false;
  }
});

pasteButton.addEventListener("click", async () => {
  try {
    linkInput.value = await navigator.clipboard.readText();
    linkInput.focus();
    pasteButton.textContent = "Pasted";
    setTimeout(() => { pasteButton.textContent = "Paste"; }, 1600);
  } catch {
    linkInput.focus();
    statusMessage.textContent = "Clipboard access is unavailable. Paste your link with Ctrl+V.";
  }
});

browseFolderButton.addEventListener("click", async () => {
  if (!window.pywebview?.api) {
    statusMessage.textContent = "Folder browsing is available in the desktop app.";
    return;
  }

  browseFolderButton.disabled = true;
  try {
    const selected = await window.pywebview.api.select_folder(destinationInput.value);
    if (selected) {
      destinationInput.value = selected;
      destinationInput.focus();
      clearTimeout(destinationSaveTimer);
      saveDestinationPreference(selected);
    }
  } catch (error) {
    statusMessage.textContent = `Could not open the folder picker: ${error.message}`;
  } finally {
    browseFolderButton.disabled = false;
  }
});

destinationInput.addEventListener("input", () => {
  clearTimeout(destinationSaveTimer);
  destinationSaveTimer = setTimeout(
    () => saveDestinationPreference(destinationInput.value),
    400,
  );
});

settingsOpen.addEventListener("click", () => {
  settingsFeedback.textContent = "";
  settingsDialog.showModal();
});

settingsClose.addEventListener("click", () => settingsDialog.close());
settingsDialog.addEventListener("click", (event) => {
  if (event.target === settingsDialog) settingsDialog.close();
});

document.querySelectorAll("[data-setting]").forEach((button) => {
  button.addEventListener("click", () => {
    settings[button.dataset.setting] = button.dataset.value;
    applySettings();
    saveSettings();
  });
});

document.querySelectorAll(".color-choice").forEach((button) => {
  button.addEventListener("click", () => {
    settings.accent = button.dataset.accent;
    applySettings();
    saveSettings();
  });
});

async function initializeApp() {
  try {
    let config;
    const bootstrapConfig = sessionStorage.getItem(bootstrapConfigKey);
    if (bootstrapConfig) {
      sessionStorage.removeItem(bootstrapConfigKey);
      config = JSON.parse(bootstrapConfig);
    } else {
      const response = await fetch("/api/config", { cache: "no-store" });
      if (!response.ok) throw new Error("Could not load app settings.");
      config = await response.json();
    }
    destinationInput.value = config.last_destination || config.default_destination;
  } catch (error) {
    setStatus("failed", error.message, "Error");
  } finally {
    resetTransferStats();
    requestAnimationFrame(() => {
      requestAnimationFrame(() => document.body.classList.add("app-ready"));
    });
  }
}

initializeApp();
