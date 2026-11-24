/* OnePost — Refined Interactive Controller */
const state = {
  bundle: null,
  copy: null,
  settings: null,
  accounts: {},
  subtitleCues: [],
  subtitleSentences: [],
  tags: {
    dy_tags: [],
    sp_tags: [],
    wb_tags: [],
  },
  activeJobId: null,
  pollTimer: null,
  activeUploadXhr: null,
  lightboxIdx: 0,
};

const POSTER_VARIANTS = [
  { key: "portrait_3x4", label: "3:4 Douyin & Shipinhao" },
  { key: "landscape_4x3", label: "4:3 Dual Cover" },
  { key: "weibo_cover", label: "16:9 Weibo Cover" },
];

const $ = (id) => document.getElementById(id);

function showToast(message, type = "info", durationMs = 4200) {
  const stack = $("toastStack");
  if (!stack) return;
  const item = document.createElement("div");
  item.className = `toast-item ${type}`;
  item.innerHTML = `<span>${escapeHtml(message)}</span><button type="button" class="toast-close" aria-label="Dismiss">&times;</button>`;
  const close = () => {
    item.remove();
  };
  item.querySelector(".toast-close").addEventListener("click", close);
  stack.appendChild(item);
  if (durationMs > 0) {
    setTimeout(close, durationMs);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  initTheme();
  initTagComposers();
  bindEvents();
  updateSelectedButtonCount();
  await Promise.all([fetchSettings(), fetchAccounts(false), checkChromeBridgeStatus()]);
});

function initTheme() {
  const saved = localStorage.getItem("OnePost_theme") || "light";
  document.documentElement.setAttribute("data-theme", saved);
}

function bindEvents() {
  $("btnThemeToggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") || "light";
    const next = cur === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("OnePost_theme", next);
  });

  $("btnLoadDemo").addEventListener("click", loadDemoBundle);
  $("btnRefreshAccounts").addEventListener("click", () => fetchAccounts(false));
  $("btnScanFolder").addEventListener("click", () => scanFolder());
  $("inputFolderPath").addEventListener("keydown", (e) => {
    if (e.key === "Enter") scanFolder();
  });

  // Drag & Drop Upload (full-window + dropZone without child flicker)
  const dz = $("dropZone");
  const fi = $("fileInputBundle");
  let globalDragDepth = 0;

  dz.addEventListener("click", () => fi.click());
  dz.addEventListener("dragover", (e) => {
    e.preventDefault();
    dz.classList.add("dragover");
  });
  dz.addEventListener("dragleave", (e) => {
    if (!dz.contains(e.relatedTarget)) {
      dz.classList.remove("dragover");
    }
  });
  dz.addEventListener("drop", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    globalDragDepth = 0;
    $("globalDropOverlay").classList.add("hidden");
    dz.classList.remove("dragover");
    if (e.dataTransfer.files?.length) {
      await uploadDroppedFiles(e.dataTransfer.files);
    }
  });
  fi.addEventListener("change", async () => {
    if (fi.files?.length) {
      await uploadDroppedFiles(fi.files);
    }
  });

  document.addEventListener("dragenter", (e) => {
    if (e.dataTransfer?.types && Array.from(e.dataTransfer.types).includes("Files")) {
      globalDragDepth += 1;
      $("globalDropOverlay").classList.remove("hidden");
    }
  });
  document.addEventListener("dragover", (e) => {
    if (e.dataTransfer?.types && Array.from(e.dataTransfer.types).includes("Files")) {
      e.preventDefault();
    }
  });
  document.addEventListener("dragleave", () => {
    globalDragDepth = Math.max(0, globalDragDepth - 1);
    if (globalDragDepth === 0) {
      $("globalDropOverlay").classList.add("hidden");
    }
  });
  document.addEventListener("drop", async (e) => {
    if (!e.dataTransfer?.files?.length) return;
    e.preventDefault();
    globalDragDepth = 0;
    $("globalDropOverlay").classList.add("hidden");
    dz.classList.remove("dragover");
    await uploadDroppedFiles(e.dataTransfer.files);
  });

  // Upload cancellation & 3-slot replacement buttons
  $("btnCancelUpload").addEventListener("click", () => {
    if (state.activeUploadXhr) {
      state.activeUploadXhr.abort();
      state.activeUploadXhr = null;
      showToast("Upload cancelled.", "warning");
    }
  });
  $("btnReplaceVideo").addEventListener("click", () => $("slotInputVideo").click());
  $("btnReplacePoster").addEventListener("click", () => $("slotInputPoster").click());
  $("btnReplaceSubtitle").addEventListener("click", () => $("slotInputSubtitle").click());
  $("slotInputVideo").addEventListener("change", async () => {
    if ($("slotInputVideo").files?.length) await uploadDroppedFiles($("slotInputVideo").files, "video");
  });
  $("slotInputPoster").addEventListener("change", async () => {
    if ($("slotInputPoster").files?.length) await uploadDroppedFiles($("slotInputPoster").files, "poster");
  });
  $("slotInputSubtitle").addEventListener("change", async () => {
    if ($("slotInputSubtitle").files?.length) await uploadDroppedFiles($("slotInputSubtitle").files, "subtitle");
  });

  // Candidate overrides
  ["selVideo", "selPoster", "selSubtitle"].forEach((id) => {
    $(id).addEventListener("change", () => {
      if (!state.bundle) return;
      scanFolder({
        video_override: $("selVideo").value || null,
        poster_override: $("selPoster").value || null,
        subtitle_override: $("selSubtitle").value || null,
      });
    });
  });

  // Side-by-side poster thumbnails -> click to enlarge in lightbox with Prev/Next navigation
  document.querySelectorAll(".poster-thumb").forEach((btn, idx) => {
    btn.addEventListener("click", () => openLightboxByIndex(idx));
  });
  $("btnLightboxPrev").addEventListener("click", () => openLightboxByIndex((state.lightboxIdx + POSTER_VARIANTS.length - 1) % POSTER_VARIANTS.length));
  $("btnLightboxNext").addEventListener("click", () => openLightboxByIndex((state.lightboxIdx + 1) % POSTER_VARIANTS.length));
  $("btnCloseLightbox").addEventListener("click", () => $("posterLightbox").classList.add("hidden"));
  $("posterLightbox").addEventListener("click", (e) => {
    if (e.target === $("posterLightbox")) $("posterLightbox").classList.add("hidden");
  });

  // Synced Karaoke playback, clickable teaser hook & transcript toggle
  $("videoPreview").addEventListener("timeupdate", syncKaraokeCue);
  $("teaserHookBox").addEventListener("click", jumpToTeaserHook);
  $("teaserHookBox").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      jumpToTeaserHook();
    }
  });
  $("transcriptToggleBtn").addEventListener("click", () => {
    $("transcriptList").classList.toggle("hidden");
  });

  document.addEventListener("keydown", (e) => {
    if (!$("posterLightbox").classList.contains("hidden")) {
      if (e.key === "ArrowLeft") openLightboxByIndex((state.lightboxIdx + POSTER_VARIANTS.length - 1) % POSTER_VARIANTS.length);
      if (e.key === "ArrowRight") openLightboxByIndex((state.lightboxIdx + 1) % POSTER_VARIANTS.length);
    }
  });

  // Magic prompt & tone chips
  $("btnGenerateCopy").addEventListener("click", regenerateCopy);
  $("inputUserHint").addEventListener("keydown", (e) => {
    if (e.key === "Enter") regenerateCopy();
  });
  document.querySelectorAll(".tone-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      $("inputUserHint").value = chip.dataset.prompt || "";
      if (state.bundle) regenerateCopy();
    });
  });

  // Platform switches
  ["douyin", "shipinhao", "weibo", "x"].forEach((plat) => {
    $(`chk_${plat}`).addEventListener("change", (e) => {
      const card = document.querySelector(`.p-card[data-plat="${plat}"]`);
      card.classList.toggle("disabled-card", !e.target.checked);
      updateSelectedButtonCount();
    });
  });

  // Character counters & X SVG progress ring
  bindCounter("dy_title", "cnt_dy_title", 30);
  bindCounter("sp_short", "cnt_sp_short", 16, 6);
  bindCounter("sp_title", "cnt_sp_title", 30);
  bindCounter("wb_title", "cnt_wb_title", 30);
  bindXRing("x_text", "cnt_x_text", "xRingCircle");

  $("opt_x_long").addEventListener("change", (e) => {
    if (state.copy?.x) {
      $("x_text").value = e.target.checked
        ? state.copy.x.long_note || state.copy.x.text
        : state.copy.x.text;
    }
    $("x_text").dispatchEvent(new Event("input"));
  });

  // Publish & Activity Drawer
  $("btnDryRun").addEventListener("click", () => submitPublish(true));
  $("btnPublishAll").addEventListener("click", () => submitPublish(false));
  $("btnToggleDrawer").addEventListener("click", () => {
    $("activityDrawer").classList.toggle("hidden");
  });
  $("btnCloseDrawer").addEventListener("click", () => {
    $("activityDrawer").classList.add("hidden");
  });

  // Settings modal
  $("btnOpenSettings").addEventListener("click", () => $("settingsModal").classList.remove("hidden"));
  $("btnCloseSettings").addEventListener("click", () => $("settingsModal").classList.add("hidden"));
  $("settingsModal").addEventListener("click", (e) => {
    if (e.target === $("settingsModal")) $("settingsModal").classList.add("hidden");
  });
  $("cfg_x_mode").addEventListener("change", toggleXApiFields);
  $("btnSaveSettings").addEventListener("click", saveSettings);

  // Real Chrome Bridge & Cookie Paste Modal
  $("btnChromeBridge").addEventListener("click", () => connectAccount("all"));
  $("btnCloseConnectModal").addEventListener("click", closeConnectModal);
  $("connectModal").addEventListener("click", (e) => {
    if (e.target === $("connectModal")) closeConnectModal();
  });
  document.querySelectorAll(".c-tab").forEach((tab) => {
    tab.addEventListener("click", () => switchConnectTab(tab.dataset.ctab));
  });
  $("btnModalOpenChrome").addEventListener("click", () => openRealChrome(state.connectingPlatform));
  $("btnModalSyncChrome").addEventListener("click", syncFromRealChrome);
  $("btnSaveXCookies").addEventListener("click", saveManualXCookies);
  $("btnImportRawCookies").addEventListener("click", importRawCookies);
}

/* Interactive Hashtag Pill Composer */
function initTagComposers() {
  document.querySelectorAll(".tag-composer").forEach((box) => {
    const targetId = box.dataset.target;
    const input = box.querySelector(".tag-ghost-input");

    box.addEventListener("click", () => input.focus());

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === "," || e.key === "，") {
        e.preventDefault();
        const val = input.value.trim().replace(/^#+/, "");
        if (val && !state.tags[targetId].includes(val)) {
          state.tags[targetId].push(val);
          renderTags(targetId);
        }
        input.value = "";
      } else if (e.key === "Backspace" && !input.value && state.tags[targetId].length) {
        state.tags[targetId].pop();
        renderTags(targetId);
      }
    });

    input.addEventListener("blur", () => {
      const val = input.value.trim().replace(/^#+/, "");
      if (val && !state.tags[targetId].includes(val)) {
        state.tags[targetId].push(val);
        renderTags(targetId);
      }
      input.value = "";
    });
  });
}

function setTagsFor(targetId, list) {
  state.tags[targetId] = (list || [])
    .map((t) => String(t).trim().replace(/^#+/, ""))
    .filter(Boolean);
  renderTags(targetId);
}

function renderTags(targetId) {
  const container = $(`pills_${targetId}`);
  const hidden = $(targetId);
  if (!container || !hidden) return;
  container.innerHTML = "";
  const items = state.tags[targetId] || [];
  items.forEach((tag, idx) => {
    const pill = document.createElement("span");
    pill.className = "tag-pill";
    pill.innerHTML = `#${escapeHtml(tag)} <button type="button" aria-label="Remove">&times;</button>`;
    pill.querySelector("button").addEventListener("click", (e) => {
      e.stopPropagation();
      state.tags[targetId].splice(idx, 1);
      renderTags(targetId);
    });
    container.appendChild(pill);
  });
  hidden.value = items.join(", ");
}

function updateSelectedButtonCount() {
  const count = ["douyin", "shipinhao", "weibo", "x"].filter((p) => $(`chk_${p}`).checked).length;
  const span = $("btnPublishAll").querySelector("span");
  if (span) span.textContent = `Publish Selected (${count})`;
}

function bindCounter(inputId, counterId, maxLen, minLen = 0) {
  const el = $(inputId);
  const cnt = $(counterId);
  el.addEventListener("input", () => {
    const len = el.value.trim().length;
    cnt.textContent = `${len}/${maxLen}`;
    cnt.classList.toggle("over", len > maxLen || (minLen > 0 && len > 0 && len < minLen));
  });
}

function bindXRing(inputId, counterId, circleId) {
  const el = $(inputId);
  const cnt = $(counterId);
  const circle = $(circleId);
  const circumference = 2 * Math.PI * 8; // r=8 -> 50.265

  el.addEventListener("input", () => {
    const len = el.value.trim().length;
    const limit = $("opt_x_long").checked ? 4000 : 280;
    cnt.textContent = `${len}/${limit}`;
    cnt.classList.toggle("over", len > limit);

    const pct = Math.min(1, len / limit);
    circle.style.strokeDashoffset = String(circumference * (1 - pct));
    if (len > limit) {
      circle.style.stroke = "#e11d48";
    } else if (pct > 0.85) {
      circle.style.stroke = "#d97706";
    } else {
      circle.style.stroke = "var(--indigo)";
    }
  });
}

async function fetchSettings() {
  const res = await fetch("/api/settings");
  const cfg = await res.json();
  state.settings = cfg;

  $("cfg_cn_direct").checked = Boolean(cfg.network?.cn_direct_bypass_proxy ?? true);
  $("cfg_x_use_proxy").checked = Boolean(cfg.network?.x_use_proxy ?? true);
  $("cfg_x_proxy").value = cfg.network?.x_proxy_url || "";

  $("cfg_ai_base").value = cfg.ai?.base_url || "";
  $("cfg_ai_model").value = cfg.ai?.model || "";
  $("cfg_ai_key").value = cfg.ai?.api_key || "";

  $("cfg_x_mode").value = cfg.x_config?.mode || "browser";
  $("cfg_x_premium").checked = Boolean(cfg.x_config?.is_premium ?? true);
  $("cfg_x_apikey").value = cfg.x_config?.api_key || "";
  $("cfg_x_apisecret").value = cfg.x_config?.api_secret || "";
  $("cfg_x_token").value = cfg.x_config?.access_token || "";
  $("cfg_x_tokensecret").value = cfg.x_config?.access_token_secret || "";
  toggleXApiFields();

  $("cfg_chrome_path").value = cfg.chrome_path || "";
  $("cfg_mp_enabled").checked = Boolean(cfg.multipost?.enabled);
  $("cfg_mp_url").value = cfg.multipost?.api_url || "http://127.0.0.1:10721/api/post";
  $("optVisibleBrowser").checked = !Boolean(cfg.headless_upload);

  $("xRouteBadge").textContent =
    cfg.network?.x_use_proxy && cfg.network?.x_proxy_url
      ? `Proxy · ${cfg.network.x_proxy_url.replace(/^https?:\/\//, "")}`
      : "Direct Network";
  $("xTierHint").textContent = cfg.x_config?.is_premium
    ? "3–5m+ HD Ready (X Premium)"
    : "Standard Tier (<=140s)";
}

function toggleXApiFields() {
  const isApi = $("cfg_x_mode").value === "api";
  $("xApiKeysGroup").classList.toggle("hidden", !isApi);
}

async function saveSettings() {
  const payload = {
    ...state.settings,
    chrome_path: $("cfg_chrome_path").value.trim(),
    headless_upload: !$("optVisibleBrowser").checked,
    network: {
      cn_direct_bypass_proxy: $("cfg_cn_direct").checked,
      x_use_proxy: $("cfg_x_use_proxy").checked,
      x_proxy_url: $("cfg_x_proxy").value.trim(),
    },
    ai: {
      ...(state.settings?.ai || {}),
      base_url: $("cfg_ai_base").value.trim(),
      model: $("cfg_ai_model").value.trim(),
      api_key: $("cfg_ai_key").value.trim(),
    },
    x_config: {
      ...(state.settings?.x_config || {}),
      mode: $("cfg_x_mode").value,
      is_premium: $("cfg_x_premium").checked,
      api_key: $("cfg_x_apikey").value.trim(),
      api_secret: $("cfg_x_apisecret").value.trim(),
      access_token: $("cfg_x_token").value.trim(),
      access_token_secret: $("cfg_x_tokensecret").value.trim(),
    },
    multipost: {
      enabled: $("cfg_mp_enabled").checked,
      api_url: $("cfg_mp_url").value.trim(),
    },
  };

  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  $("settingsModal").classList.add("hidden");
  await Promise.all([fetchSettings(), fetchAccounts(false)]);
}

async function fetchAccounts(deep = false) {
  const res = await fetch(`/api/accounts?deep=${deep}`);
  const data = await res.json();
  state.accounts = data.accounts || {};
  renderAccountStrip();
}

function renderAccountStrip() {
  const strip = $("accountStrip");
  strip.innerHTML = "";
  ["douyin", "shipinhao", "weibo", "x"].forEach((plat) => {
    const info = state.accounts[plat] || { label: plat, ready: false, detail: "Checking..." };
    const el = document.createElement("div");
    el.className = "channel-pill-card";
    el.innerHTML = `
      <div class="ch-info">
        <span class="ch-status-ring ${info.ready ? "ready" : "pending"}"></span>
        <div class="ch-text">
          <div class="ch-name">${escapeHtml(info.label)}</div>
          <div class="ch-sub" title="${escapeHtml(info.detail)}">${escapeHtml(info.ready ? "Session connected" : "Click Connect to scan QR")}</div>
        </div>
      </div>
      <button type="button" class="btn-connect">${info.ready ? "Connected" : "Connect"}</button>
    `;
    el.querySelector("button").addEventListener("click", () => connectAccount(plat));
    strip.appendChild(el);
  });
}

async function checkChromeBridgeStatus() {
  try {
    const res = await fetch("/api/chrome/status");
    const st = await res.json();
    const dot = $("chromeBridgeDot");
    const lbl = $("chromeBridgeLabel");
    if (st.running) {
      dot.classList.add("live");
      lbl.textContent = "Chrome Live · Sync";
    } else {
      dot.classList.remove("live");
      lbl.textContent = "Open Real Chrome & Sync";
    }
    return st;
  } catch (_) {
    return { running: false };
  }
}

function switchConnectTab(tabName) {
  document.querySelectorAll(".c-tab").forEach((b) => b.classList.toggle("active", b.dataset.ctab === tabName));
  $("tabChromeBridge").classList.toggle("hidden", tabName !== "chrome");
  $("tabCookiePaste").classList.toggle("hidden", tabName !== "paste");
}

async function connectAccount(platform) {
  state.connectingPlatform = platform === "all" ? null : platform;
  const labels = {
    douyin: "Connect Douyin (抖音)",
    shipinhao: "Connect Shipinhao (视频号)",
    weibo: "Connect Weibo (新浪微博)",
    x: "Connect X (Twitter)",
  };
  $("connectModalTitle").textContent = labels[platform] || "Connect Real Chrome Login Status";
  $("xSpecificFields").classList.toggle("hidden", platform !== "x");
  $("pasteCookieMsg").textContent = "";
  $("inputRawCookies").value = "";
  switchConnectTab("chrome");
  $("connectModal").classList.remove("hidden");

  const st = await checkChromeBridgeStatus();
  $("chromeSyncStatusMsg").textContent = st.running
    ? "Real Chrome is running! Log into your accounts in Chrome, then click '2. Sync Chrome Login Now'."
    : "Click '1. Open Real Chrome' to launch your persistent Chrome browser.";
}

function closeConnectModal() {
  $("connectModal").classList.add("hidden");
}

async function openRealChrome(platform) {
  $("btnModalOpenChrome").disabled = true;
  $("chromeSyncStatusMsg").textContent = "Launching your real Google Chrome window...";
  try {
    const res = await fetch("/api/chrome/launch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ platform: platform || null }),
    });
    const data = await res.json();
    $("chromeSyncStatusMsg").textContent = data.chrome?.message || "Chrome launched.";
    if (data.accounts) {
      state.accounts = data.accounts;
      renderAccountStrip();
    }
    await checkChromeBridgeStatus();
  } catch (err) {
    $("chromeSyncStatusMsg").textContent = `Launch error: ${err.message}`;
  } finally {
    $("btnModalOpenChrome").disabled = false;
  }
}

async function syncFromRealChrome() {
  $("btnModalSyncChrome").disabled = true;
  $("chromeSyncStatusMsg").textContent = "Reading live login sessions from your open Chrome...";
  try {
    const res = await fetch("/api/chrome/sync", { method: "POST" });
    const data = await res.json();
    $("chromeSyncStatusMsg").textContent = data.sync?.message || "Sync complete.";
    if (data.accounts) {
      state.accounts = data.accounts;
      renderAccountStrip();
    }
    await checkChromeBridgeStatus();
    if (data.sync?.synced_platforms?.length) {
      appendLog(data.sync.message);
    }
  } catch (err) {
    $("chromeSyncStatusMsg").textContent = `Sync error: ${err.message}`;
  } finally {
    $("btnModalSyncChrome").disabled = false;
  }
}

async function importRawCookies() {
  const plat = state.connectingPlatform || "douyin";
  const raw = $("inputRawCookies").value.trim();
  if (!raw) {
    $("pasteCookieMsg").textContent = "Please paste Cookie JSON or Copy-as-cURL first.";
    return;
  }
  $("btnImportRawCookies").disabled = true;
  $("pasteCookieMsg").textContent = "Importing cookies...";
  try {
    const res = await fetch(`/api/accounts/${plat}/import-cookies`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ raw_text: raw }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Cookie import failed");
    state.accounts = data.accounts || state.accounts;
    renderAccountStrip();
    $("pasteCookieMsg").textContent = `Connected ${plat}!`;
    setTimeout(closeConnectModal, 700);
  } catch (err) {
    $("pasteCookieMsg").textContent = `Error: ${err.message}`;
  } finally {
    $("btnImportRawCookies").disabled = false;
  }
}

async function saveManualXCookies() {
  const authToken = $("inputXAuthToken").value.trim();
  const ct0 = $("inputXCt0").value.trim();
  if (!authToken || !ct0) {
    $("pasteCookieMsg").textContent = "Please paste both auth_token and ct0.";
    return;
  }
  $("btnSaveXCookies").disabled = true;
  try {
    const res = await fetch("/api/accounts/x/cookies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auth_token: authToken, ct0 }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to save X cookies");
    state.accounts = data.accounts || state.accounts;
    renderAccountStrip();
    $("pasteCookieMsg").textContent = "X account connected!";
    setTimeout(closeConnectModal, 700);
  } catch (err) {
    $("pasteCookieMsg").textContent = `Error: ${err.message}`;
  } finally {
    $("btnSaveXCookies").disabled = false;
  }
}

async function loadDemoBundle() {
  setBusy(true, "Loading Demo...");
  try {
    const res = await fetch("/api/bundle/demo", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to load demo");
    $("inputFolderPath").value = data.bundle.folder_path;
    applyBundleAndCopy(data.bundle, data.copy);
    showToast("Demo bundle loaded!", "success");
  } catch (err) {
    appendLog(`Error: ${err.message}`);
    showToast(`Failed to load demo: ${err.message}`, "error");
  } finally {
    setBusy(false);
  }
}

async function scanFolder(overrides = {}) {
  const folderPath = $("inputFolderPath").value.trim();
  if (!folderPath) {
    $("inputFolderPath").focus();
    showToast("Paste a bundle folder path first.", "warning");
    return;
  }
  setBusy(true, "Scanning...");
  try {
    const res = await fetch("/api/bundle/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_path: folderPath, ...overrides }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Scan failed");
    applyBundleAndCopy(data.bundle, data.copy);
    showToast(`Scanned '${data.bundle.bundle_name}'`, "success");
  } catch (err) {
    $("activityDrawer").classList.remove("hidden");
    appendLog(`Scan error: ${err.message}`);
    showToast(`Scan failed: ${err.message}`, "error");
  } finally {
    setBusy(false);
  }
}

function setUploadStepper(stage, pct, stageLabel) {
  const panel = $("uploadProgressPanel");
  panel.classList.remove("hidden");
  $("uploadStageLabel").textContent = stageLabel;
  $("uploadPercentLabel").textContent = `${pct}%`;
  $("uploadProgressFill").style.width = `${pct}%`;

  const steps = ["uStepTransfer", "uStepProcess", "uStepCopy"];
  steps.forEach((id, idx) => {
    const el = $(id);
    el.classList.toggle("done", idx < stage);
    el.classList.toggle("active", idx === stage);
  });
}

async function uploadDroppedFiles(fileList, slotMode = "bundle") {
  const fd = new FormData();
  Array.from(fileList).forEach((f) => fd.append("files", f));
  fd.append("bundle_title", $("inputUserHint").value.trim());

  if (slotMode !== "bundle" && state.bundle) {
    if (slotMode !== "video" && state.bundle.video?.file_path) {
      fd.append("existing_video", state.bundle.video.file_path);
    }
    if (slotMode !== "poster" && state.bundle.poster?.variants?.original) {
      fd.append("existing_poster", state.bundle.poster.variants.original);
    }
    if (slotMode !== "subtitle" && state.bundle.subtitle?.file_path) {
      fd.append("existing_subtitle", state.bundle.subtitle.file_path);
    }
  }

  setBusy(true, "Uploading...");
  setUploadStepper(0, 2, "Preparing file upload...");
  let copyTimer = null;

  try {
    const data = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      state.activeUploadXhr = xhr;
      xhr.open("POST", "/api/bundle/upload");

      xhr.upload.onprogress = (ev) => {
        if (!ev.lengthComputable) return;
        const pct = Math.min(99, Math.round((ev.loaded / Math.max(ev.total, 1)) * 100));
        const loadedMb = (ev.loaded / (1024 * 1024)).toFixed(1);
        const totalMb = (ev.total / (1024 * 1024)).toFixed(1);
        if (ev.loaded < ev.total) {
          setUploadStepper(0, Math.round(pct * 0.72), `Uploading files... ${pct}% (${loadedMb} / ${totalMb} MB)`);
        } else {
          setUploadStepper(1, 82, "Analyzing video & framing covers...");
          if (!copyTimer) {
            copyTimer = setTimeout(() => setUploadStepper(2, 94, "Drafting 4-platform copy..."), 700);
          }
        }
      };

      xhr.onload = () => {
        if (copyTimer) clearTimeout(copyTimer);
        let parsed = {};
        try {
          parsed = JSON.parse(xhr.responseText || "{}");
        } catch (_) {}
        if (xhr.status >= 200 && xhr.status < 300) {
          setUploadStepper(3, 100, "Bundle ready!");
          resolve(parsed);
        } else {
          reject(new Error(parsed.detail || `Upload failed (HTTP ${xhr.status})`));
        }
      };
      xhr.onerror = () => {
        if (copyTimer) clearTimeout(copyTimer);
        reject(new Error("Network error while uploading files."));
      };
      xhr.onabort = () => {
        if (copyTimer) clearTimeout(copyTimer);
        reject(new Error("UPLOAD_ABORTED"));
      };
      xhr.send(fd);
    });

    $("inputFolderPath").value = data.bundle.folder_path;
    applyBundleAndCopy(data.bundle, data.copy);
    if (data.ignored_files?.length) {
      showToast(`Skipped unsupported file(s): ${data.ignored_files.join(", ")}`, "warning", 5500);
    } else {
      showToast(`Loaded '${data.bundle.bundle_name}' successfully!`, "success");
    }
  } catch (err) {
    if (err.message !== "UPLOAD_ABORTED") {
      appendLog(`Upload error: ${err.message}`);
      showToast(err.message, "error", 5200);
    }
  } finally {
    if (copyTimer) clearTimeout(copyTimer);
    state.activeUploadXhr = null;
    $("fileInputBundle").value = "";
    $("slotInputVideo").value = "";
    $("slotInputPoster").value = "";
    $("slotInputSubtitle").value = "";
    setTimeout(() => $("uploadProgressPanel").classList.add("hidden"), 450);
    setBusy(false);
  }
}

function renderAssetSlots(bundle) {
  $("bundleAssetSlots").classList.remove("hidden");

  const v = bundle.video;
  const vSlot = document.querySelector('.asset-slot[data-slot="video"]');
  vSlot.classList.toggle("ready", Boolean(v));
  $("slotVideoName").textContent = v ? v.file_name : "Missing";
  if (v) {
    const dims = v.width && v.height ? `${v.width}×${v.height}` : v.aspect_label;
    const dur = v.duration_label && v.duration_label !== "Ready to publish" ? `${v.duration_label} · ` : "";
    $("slotVideoMeta").textContent = `${dur}${dims} · ${v.size_mb} MB`;
    $("btnReplaceVideo").textContent = "Replace";
  } else {
    $("slotVideoMeta").textContent = "Drop or pick .mp4/.mov";
    $("btnReplaceVideo").textContent = "Add";
  }

  const p = bundle.poster;
  const hasPoster = Boolean(p?.variants?.original);
  const pSlot = document.querySelector('.asset-slot[data-slot="poster"]');
  pSlot.classList.toggle("ready", hasPoster);
  $("slotPosterName").textContent = hasPoster ? (p.file_name || "Poster Ready") : "Missing";
  $("slotPosterMeta").textContent = hasPoster ? "3:4 · 4:3 · 16:9 framed" : "Drop or pick .png/.jpg";
  $("btnReplacePoster").textContent = hasPoster ? "Replace" : "Add";

  const s = bundle.subtitle;
  const sSlot = document.querySelector('.asset-slot[data-slot="subtitle"]');
  sSlot.classList.toggle("ready", Boolean(s));
  $("slotSubName").textContent = s ? s.file_name : "Missing";
  $("slotSubMeta").textContent = s
    ? `${s.cue_count} cues · ${s.is_karaoke ? "Karaoke" : s.format.toUpperCase()}`
    : "Drop or pick .srt/.ass";
  $("btnReplaceSubtitle").textContent = s ? "Replace" : "Add";
}

function openLightboxByIndex(idx) {
  state.lightboxIdx = idx;
  const item = POSTER_VARIANTS[idx] || POSTER_VARIANTS[0];
  document.querySelectorAll(".poster-thumb").forEach((b, i) => b.classList.toggle("active", i === idx));
  const src = state.bundle?.poster?.variants?.[item.key] || state.bundle?.poster?.variants?.original;
  if (src) {
    $("lightboxCaption").textContent = item.label;
    $("posterPreviewImg").src = `/api/media?path=${encodeURIComponent(src)}&t=${Date.now()}`;
    $("posterLightbox").classList.remove("hidden");
  }
}

function jumpToTeaserHook() {
  const video = $("videoPreview");
  if (!video.src) return;
  video.currentTime = 0;
  video.play().catch(() => {});
}

function renderTranscriptList(sentences) {
  state.subtitleSentences = sentences || [];
  const section = $("transcriptSection");
  const list = $("transcriptList");
  if (!state.subtitleSentences.length) {
    section.classList.add("hidden");
    list.innerHTML = "";
    return;
  }
  section.classList.remove("hidden");
  $("transcriptCountBadge").textContent = `${state.subtitleSentences.length} lines`;
  list.innerHTML = "";
  state.subtitleSentences.forEach((item, idx) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "transcript-row";
    row.dataset.idx = String(idx);
    row.innerHTML = `<span class="transcript-time">[${escapeHtml(item.time_label)}]</span><span>${escapeHtml(item.text)}</span>`;
    row.addEventListener("click", () => {
      const video = $("videoPreview");
      if (video.src) {
        video.currentTime = Number(item.start_sec || 0);
        video.play().catch(() => {});
      }
    });
    list.appendChild(row);
  });
}

function applyBundleAndCopy(bundle, copy) {
  state.bundle = bundle;
  state.copy = copy;

  $("bundleBadge").textContent = bundle.bundle_name || "Ready";
  $("bundleBadge").className = "badge badge-success";

  renderAssetSlots(bundle);

  const cands = bundle.candidates || {};
  const hasMultiple =
    (cands.videos?.length || 0) > 1 ||
    (cands.posters?.length || 0) > 1 ||
    (cands.subtitles?.length || 0) > 1;
  $("candidateSelectors").classList.toggle("hidden", !hasMultiple);
  populateSelect("selVideo", cands.videos || [], bundle.video?.file_path);
  populateSelect("selPoster", cands.posters || [], bundle.poster?.variants?.original);
  populateSelect("selSubtitle", cands.subtitles || [], bundle.subtitle?.file_path);

  // Cinema video
  const videoEl = $("videoPreview");
  const coverForPlayer = bundle.poster?.variants?.weibo_cover || bundle.poster?.variants?.landscape_4x3 || bundle.poster?.variants?.original;
  if (coverForPlayer) {
    videoEl.poster = `/api/media?path=${encodeURIComponent(coverForPlayer)}&t=${Date.now()}`;
  } else {
    videoEl.removeAttribute("poster");
  }
  if (bundle.video?.file_path) {
    videoEl.src = `/api/media?path=${encodeURIComponent(bundle.video.file_path)}`;
    $("videoEmptyState").classList.add("hidden");
    const durPart = bundle.video.duration_label && bundle.video.duration_label !== "Ready to publish" ? `${bundle.video.duration_label} · ` : "";
    const dimPart = bundle.video.width && bundle.video.height ? `${bundle.video.width}×${bundle.video.height} · ` : "";
    $("videoSpecsLabel").textContent = `${bundle.video.file_name} · ${durPart}${dimPart}${bundle.video.size_mb} MB`;
  } else {
    videoEl.removeAttribute("src");
    $("videoEmptyState").classList.remove("hidden");
    $("videoSpecsLabel").textContent = "--";
  }

  // Karaoke SRT
  const sub = bundle.subtitle;
  state.subtitleCues = sub?.cues || [];
  renderTranscriptList(sub?.sentences || []);
  if (sub) {
    $("karaokeStatusBadge").textContent = sub.is_karaoke
      ? `KARAOKE ACTIVE · ${sub.cue_count} CUES`
      : `SUBTITLES · ${sub.cue_count} CUES`;
    $("karaokeLiveLine").classList.remove("muted-gap");
    $("karaokeLiveLine").innerHTML = state.subtitleCues.length
      ? formatKaraokeHtml(state.subtitleCues[0].raw_text, state.subtitleCues[0].clean_text)
      : "Subtitle loaded.";
    if (sub.teaser_hook_text) {
      $("teaserHookBox").classList.remove("hidden");
      $("teaserHookText").textContent = sub.teaser_hook_text;
    } else {
      $("teaserHookBox").classList.add("hidden");
    }
  } else {
    $("karaokeStatusBadge").textContent = "NO SRT FILE";
    $("karaokeLiveLine").classList.add("muted-gap");
    $("karaokeLiveLine").textContent = "Add a karaoke .srt file to preview synced highlights";
    $("teaserHookBox").classList.add("hidden");
  }

  // Side-by-Side Poster Gallery Thumbnails
  renderPosterGallery(bundle.poster?.variants || {});
  populateCopyEditors(copy);

  const warnings = [...(bundle.video?.warnings || [])];
  if (copy?.ai_warning) warnings.push(copy.ai_warning);
  const wb = $("warningBanner");
  if (warnings.length) {
    wb.textContent = warnings.join("  ·  ");
    wb.classList.remove("hidden");
  } else {
    wb.classList.add("hidden");
  }

  appendLog(`Bundle '${bundle.bundle_name}' loaded with Poster Card & Karaoke Copy.`);
}

function renderPosterGallery(variants) {
  ["portrait_3x4", "landscape_4x3", "weibo_cover"].forEach((key) => {
    const img = $(`thumb_${key}`);
    const empty = img?.parentElement?.querySelector(".thumb-empty");
    const path = variants[key] || variants.original;
    if (img && path) {
      img.src = `/api/media?path=${encodeURIComponent(path)}&t=${Date.now()}`;
      img.classList.remove("hidden");
      if (empty) empty.classList.add("hidden");
    } else if (img) {
      img.classList.add("hidden");
      if (empty) empty.classList.remove("hidden");
    }
  });
}

function populateSelect(id, items, selectedVal) {
  const sel = $(id);
  sel.innerHTML = "";
  if (!items.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "(None)";
    sel.appendChild(opt);
    return;
  }
  items.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p;
    opt.textContent = p.split(/[\\/]/).pop();
    if (p === selectedVal) opt.selected = true;
    sel.appendChild(opt);
  });
}

function syncKaraokeCue() {
  if (!state.subtitleCues.length) return;
  const t = $("videoPreview").currentTime;
  const lineEl = $("karaokeLiveLine");
  const active = state.subtitleCues.find((c) => t >= c.start_sec && t <= c.end_sec);
  if (active) {
    lineEl.classList.remove("muted-gap");
    lineEl.innerHTML = formatKaraokeHtml(active.raw_text, active.clean_text);
  } else if (state.subtitleCues.length) {
    lineEl.classList.add("muted-gap");
    lineEl.textContent = "...";
  }

  if (state.subtitleSentences.length) {
    const activeSentIdx = state.subtitleSentences.findIndex((s) => t >= s.start_sec && t <= s.end_sec);
    const list = $("transcriptList");
    list.querySelectorAll(".transcript-row").forEach((row, idx) => {
      const isAct = idx === activeSentIdx;
      row.classList.toggle("active", isAct);
      if (isAct && !list.classList.contains("hidden")) {
        row.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    });
  }
}

function formatKaraokeHtml(rawText, cleanText) {
  if (!rawText) return escapeHtml(cleanText || "");
  let safe = escapeHtml(rawText);
  safe = safe
    .replace(/&lt;u&gt;(.*?)&lt;\/u&gt;/gi, '<span class="k-hl">$1</span>')
    .replace(/&lt;b&gt;(.*?)&lt;\/b&gt;/gi, "<strong>$1</strong>")
    .replace(/&lt;i&gt;(.*?)&lt;\/i&gt;/gi, "<em>$1</em>")
    .replace(/&lt;font\s+color=(?:&quot;|&#39;|')([^&"']+)(?:&quot;|&#39;|')&gt;(.*?)&lt;\/font&gt;/gi, '<span class="k-hl">$2</span>')
    .replace(/\{[^}]*\}/g, "");
  return safe.replace(/\r?\n/g, " &middot; ");
}

async function regenerateCopy() {
  if (!state.bundle) {
    await loadDemoBundle();
    return;
  }
  const btn = $("btnGenerateCopy");
  const prevText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Writing...";
  try {
    const res = await fetch("/api/copy/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bundle: state.bundle,
        user_hint: $("inputUserHint").value.trim(),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Copy generation failed");
    state.copy = data.copy;
    populateCopyEditors(data.copy);
  } catch (err) {
    appendLog(`Copy error: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = prevText;
  }
}

function populateCopyEditors(copy) {
  if (!copy) return;
  $("copySourceBadge").textContent = copy.source === "smart_srt_draft" ? "SRT Smart Draft" : copy.source;

  $("dy_title").value = copy.douyin?.title || "";
  $("dy_desc").value = copy.douyin?.description || "";
  setTagsFor("dy_tags", copy.douyin?.tags || []);

  $("sp_short").value = copy.shipinhao?.short_title || "";
  $("sp_title").value = copy.shipinhao?.title || "";
  $("sp_desc").value = copy.shipinhao?.description || "";
  setTagsFor("sp_tags", copy.shipinhao?.tags || []);

  $("wb_title").value = copy.weibo?.title || "";
  $("wb_desc").value = copy.weibo?.description || "";
  setTagsFor("wb_tags", copy.weibo?.tags || []);

  $("x_text").value = $("opt_x_long").checked ? copy.x?.long_note || copy.x?.text || "" : copy.x?.text || "";

  ["dy_title", "sp_short", "sp_title", "wb_title", "x_text"].forEach((id) =>
    $(id).dispatchEvent(new Event("input"))
  );
}

function collectCopyFromEditors() {
  return {
    douyin: {
      title: $("dy_title").value.trim(),
      description: $("dy_desc").value.trim(),
      tags: [...state.tags.dy_tags],
    },
    shipinhao: {
      short_title: $("sp_short").value.trim(),
      title: $("sp_title").value.trim(),
      description: $("sp_desc").value.trim(),
      tags: [...state.tags.sp_tags],
    },
    weibo: {
      title: $("wb_title").value.trim(),
      description: $("wb_desc").value.trim(),
      tags: [...state.tags.wb_tags],
    },
    x: {
      text: $("x_text").value.trim(),
      long_note: $("x_text").value.trim(),
    },
  };
}

async function submitPublish(dryRun) {
  if (!state.bundle?.video?.file_path) {
    await loadDemoBundle();
  }
  if (!state.bundle?.video?.file_path) return;

  const platforms = ["douyin", "shipinhao", "weibo", "x"].filter((p) => $(`chk_${p}`).checked);
  if (!platforms.length) return;

  $("activityDrawer").classList.remove("hidden");

  const body = {
    video_path: state.bundle.video.file_path,
    poster_path: state.bundle.poster?.variants?.original || null,
    covers: state.bundle.poster?.variants || {},
    platforms,
    copy_payload: collectCopyFromEditors(),
    options: {
      schedule: $("optSchedule").value.trim(),
      shipinhao_draft: $("opt_sp_draft").checked,
      x_use_long_note: $("opt_x_long").checked,
      headless: !$("optVisibleBrowser").checked,
    },
    dry_run: dryRun,
  };

  setBusy(true, dryRun ? "Checking..." : "Publishing...");
  try {
    const res = await fetch("/api/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Dispatch failed");
    startJobPolling(data.job);
  } catch (err) {
    appendLog(`Publish error: ${err.message}`);
    setBusy(false);
  }
}

function startJobPolling(initialJob) {
  state.activeJobId = initialJob.job_id;
  renderJobStatus(initialJob);
  if (state.pollTimer) clearInterval(state.pollTimer);

  state.pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${state.activeJobId}`);
      if (!res.ok) return;
      const data = await res.json();
      renderJobStatus(data.job);
      if (data.job.status !== "running") {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
        setBusy(false);
      }
    } catch (_) {}
  }, 750);
}

function renderJobStatus(job) {
  const pills = $("jobPlatformPills");
  pills.innerHTML = "";
  Object.entries(job.platform_states || {}).forEach(([, st]) => {
    const span = document.createElement("span");
    span.className = `job-pill ${st.status}`;
    span.textContent = `${st.label.split(" ")[0]} · ${st.status}`;
    span.title = st.message || "";
    pills.appendChild(span);
  });
  if (job.logs?.length) {
    const logEl = $("jobLogs");
    logEl.textContent = job.logs.join("\n");
    logEl.scrollTop = logEl.scrollHeight;
  }
}

function appendLog(line) {
  const logEl = $("jobLogs");
  const stamp = new Date().toTimeString().slice(0, 8);
  logEl.textContent += `\n[${stamp}] ${line}`;
  logEl.scrollTop = logEl.scrollHeight;
}

function setBusy(busy, label = "") {
  $("btnPublishAll").disabled = busy;
  $("btnDryRun").disabled = busy;
  const span = $("btnPublishAll").querySelector("span");
  if (busy && label && span) {
    span.textContent = label;
  } else {
    updateSelectedButtonCount();
  }
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
