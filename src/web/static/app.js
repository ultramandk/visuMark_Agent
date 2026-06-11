/**
 * VisuMark Agent — Web UI Application Logic
 *
 * Manages WebSocket connection to the agent backend,
 * renders chat messages, handles user input, and provides
 * screenshot lightbox functionality.
 */

// ============================================================================
// State
// ============================================================================
let ws = null;
let isRunning = false;
let currentTask = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_DELAY = 10000; // 10 seconds max
const HISTORY_KEY = "visumark_history";
const SETTINGS_KEY = "visumark_settings";
const SIDEBAR_KEY = "visumark_sidebar_collapsed";
const SCREENSHOT_PANEL_KEY = "visumark_screenshot_collapsed";

// Screenshot panel state
let currentScreenshot = null;
let currentTargetBbox = null;
let currentActionLabel = null;

// ============================================================================
// DOM references
// ============================================================================
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const chatMessages = $("#chat-messages");
const taskInput = $("#task-input");
const urlInput = $("#url-input");
const btnSend = $("#btn-send");
const btnAdvanced = $("#btn-advanced");
const advancedSettings = $("#advanced-settings");
const btnNewTask = $("#btn-new-task");
const btnToggleSidebar = $("#btn-toggle-sidebar");
const btnToggleScreenshot = $("#btn-toggle-screenshot");
const sidebar = $("#sidebar");
const screenshotPanel = $("#screenshot-panel");
const screenshotImg = $("#screenshot-img");
const screenshotOverlay = $("#screenshot-overlay");
const screenshotPlaceholder = $("#screenshot-placeholder");
const screenshotStepBadge = $("#screenshot-step-badge");
const actionOverlayInfo = $("#action-overlay-info");
const actionInfoLabel = actionOverlayInfo ? actionOverlayInfo.querySelector(".action-info-label") : null;
const statusDot = $("#status-dot");
const statusText = $("#status-text");
const lightbox = $("#lightbox");
const lightboxImg = $("#lightbox-img");
const lightboxClose = $("#lightbox-close");
const toastContainer = $("#toast-container");

// Dynamic element lookup (may be recreated when chat is cleared)
function getTypingIndicator() {
    return $("#typing-indicator");
}

// Settings inputs
const settingProvider = $("#setting-provider");
const settingModel = $("#setting-model");
const settingApiKey = $("#setting-api-key");
const settingBaseUrl = $("#setting-base-url");
const settingMaxSteps = $("#setting-max-steps");
const settingHeadless = $("#setting-headless");

// ============================================================================
// Helpers
// ============================================================================

function scrollToBottom() {
    chatMessages.scrollTo({
        top: chatMessages.scrollHeight,
        behavior: "smooth",
    });
}

function setStatus(state) {
    statusDot.className = "status-dot " + state;
    const labels = { connected: "已连接", running: "运行中", error: "出错", "": "就绪" };
    statusText.textContent = labels[state] || "就绪";
}

function setRunning(running) {
    isRunning = running;
    taskInput.disabled = running;
    urlInput.disabled = running;
    if (running) {
        btnSend.classList.add("running");
        btnSend.textContent = "⏹";
        btnSend.title = "停止任务";
        setStatus("running");
        showTyping();
    } else {
        btnSend.classList.remove("running");
        btnSend.textContent = "▶";
        btnSend.title = "执行任务 (Enter)";
        setStatus(ws && ws.readyState === WebSocket.OPEN ? "connected" : "");
        hideTyping();
    }
}

function showTyping() {
    const el = getTypingIndicator();
    if (el) el.classList.add("visible");
    scrollToBottom();
}

function hideTyping() {
    const el = getTypingIndicator();
    if (el) el.classList.remove("visible");
}

function nowTime() {
    return new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

// ============================================================================
// Toast Notifications
// ============================================================================

function toast(message, type) {
    type = type || "info";
    const el = document.createElement("div");
    el.className = "toast " + type;
    const icons = { info: "ℹ️", success: "✅", error: "❌" };
    el.innerHTML = `<span>${icons[type] || ""}</span> ${escapeHtml(message)}`;
    toastContainer.appendChild(el);

    // Auto-remove after 3.5 seconds
    setTimeout(() => {
        el.classList.add("removing");
        el.addEventListener("animationend", () => el.remove());
    }, 3500);
}

// ============================================================================
// Settings Persistence
// ============================================================================

// Provider defaults — auto-fill model and base URL when switching
const PROVIDER_DEFAULTS = {
    qwen:    { model: "qwen3-vl-8b-instruct", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
    openai:  { model: "gpt-4o",               baseUrl: "https://api.openai.com/v1" },
    anthropic:{ model: "claude-sonnet-4-6",   baseUrl: "https://api.anthropic.com" },
    local:   { model: "qwen3-vl:8b",          baseUrl: "http://localhost:11434/v1" },
};

function onProviderChange() {
    const p = settingProvider.value;
    const defs = PROVIDER_DEFAULTS[p] || PROVIDER_DEFAULTS.qwen;
    settingModel.value = defs.model;
    settingBaseUrl.value = defs.baseUrl;
    saveSettings();
}

function loadSettings() {
    try {
        const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY));
        if (saved) {
            settingProvider.value = saved.provider || "qwen";
            settingModel.value = saved.model || "qwen3-vl-8b-instruct";
            settingApiKey.value = saved.apiKey || "";
            settingBaseUrl.value = saved.baseUrl || "";
            settingMaxSteps.value = saved.maxSteps || 15;
            settingHeadless.checked = saved.headless !== false;
        }
    } catch { /* ignore */ }
}

function saveSettings() {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({
        provider: settingProvider.value,
        model: settingModel.value,
        apiKey: settingApiKey.value,
        baseUrl: settingBaseUrl.value,
        maxSteps: parseInt(settingMaxSteps.value, 10) || 15,
        headless: settingHeadless.checked,
    }));
}

function getApiKey() {
    return settingApiKey.value.trim() || null;
}

// ============================================================================
// Sidebar Persistence
// ============================================================================

function loadSidebarState() {
    try {
        if (localStorage.getItem(SIDEBAR_KEY) === "true") {
            sidebar.classList.add("collapsed");
        }
    } catch { /* ignore */ }
}

function saveSidebarState() {
    localStorage.setItem(SIDEBAR_KEY, sidebar.classList.contains("collapsed"));
}

// ============================================================================
// Screenshot Panel Persistence
// ============================================================================

function loadScreenshotPanelState() {
    try {
        if (localStorage.getItem(SCREENSHOT_PANEL_KEY) === "true") {
            screenshotPanel.classList.add("collapsed");
            btnToggleScreenshot.classList.add("active");
        }
    } catch { /* ignore */ }
}

function saveScreenshotPanelState() {
    localStorage.setItem(SCREENSHOT_PANEL_KEY, screenshotPanel.classList.contains("collapsed"));
}

// ============================================================================
// Screenshot Panel — update & highlight logic
// ============================================================================

function updateScreenshotPanel(screenshotBase64, targetBbox, targetLabel, stepNum, success) {
    if (!screenshotPanel || !screenshotImg || !screenshotOverlay) return;

    // Update step badge
    if (screenshotStepBadge) {
        screenshotStepBadge.textContent = stepNum ? `第 ${stepNum} 步` : "等待中";
        screenshotStepBadge.classList.toggle("active", !!stepNum);
    }

    // Show the image, hide placeholder
    if (screenshotPlaceholder) screenshotPlaceholder.classList.add("hidden");
    screenshotImg.style.display = "block";
    screenshotOverlay.style.display = "block";

    // Set new screenshot
    screenshotImg.src = "data:image/png;base64," + screenshotBase64;

    // Wait for image to load, then resize canvas and draw highlight
    screenshotImg.onload = function () {
        // Resize canvas to match displayed image size
        var rect = screenshotImg.getBoundingClientRect();
        var containerRect = screenshotOverlay.parentElement.getBoundingClientRect();
        screenshotOverlay.width = rect.width;
        screenshotOverlay.height = rect.height;
        screenshotOverlay.style.width = rect.width + "px";
        screenshotOverlay.style.height = rect.height + "px";
        screenshotOverlay.style.left = rect.left - containerRect.left + "px";
        screenshotOverlay.style.top = rect.top - containerRect.top + "px";

        // Draw target highlight if bbox provided
        if (targetBbox && targetBbox.length === 4) {
            drawTargetHighlight(screenshotOverlay, targetBbox, targetLabel);
        } else {
            clearHighlight(screenshotOverlay);
        }
    };

    // Update action info bar
    updateActionInfo(targetLabel, success);

    // Store current state
    currentScreenshot = screenshotBase64;
    currentTargetBbox = targetBbox;
    currentActionLabel = targetLabel;
}

function drawTargetHighlight(canvas, bbox, label) {
    var ctx = canvas.getContext("2d");
    var w = canvas.width;
    var h = canvas.height;

    // Clear previous drawing
    ctx.clearRect(0, 0, w, h);

    // Convert normalized bbox to pixel coordinates
    var x = bbox[0] * w;
    var y = bbox[1] * h;
    var bw = bbox[2] * w;
    var bh = bbox[3] * h;

    // Clamp to canvas bounds
    x = Math.max(0, x);
    y = Math.max(0, y);
    bw = Math.min(bw, w - x);
    bh = Math.min(bh, h - y);

    if (bw < 4 || bh < 4) return; // too small, skip

    // Draw semi-transparent fill
    ctx.fillStyle = "rgba(63, 185, 80, 0.12)";
    ctx.fillRect(x, y, bw, bh);

    // Draw border with glow effect
    ctx.shadowColor = "rgba(63, 185, 80, 0.7)";
    ctx.shadowBlur = 8;
    ctx.strokeStyle = "#3fb950";
    ctx.lineWidth = 3;
    ctx.strokeRect(x, y, bw, bh);

    // Reset shadow for text
    ctx.shadowColor = "transparent";
    ctx.shadowBlur = 0;

    // Draw label badge
    if (label) {
        var fontSize = Math.max(11, Math.min(13, bw / label.length * 1.8));
        ctx.font = "bold " + fontSize + "px -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', sans-serif";
        var textMetrics = ctx.measureText(label);
        var tw = textMetrics.width;
        var th = fontSize + 4;
        var padding = 6;

        // Label background
        var lx = x;
        var ly = Math.max(0, y - th - padding * 2);
        // If label would go above canvas, put it inside the rect at the top
        if (ly < 4) {
            ly = y + 4;
        }

        ctx.fillStyle = "rgba(63, 185, 80, 0.92)";
        var rx = lx + tw + padding * 2;
        var ry = ly + th + padding;
        var radius = 4;
        ctx.beginPath();
        ctx.moveTo(lx + radius, ly);
        ctx.lineTo(rx - radius, ly);
        ctx.quadraticCurveTo(rx, ly, rx, ly + radius);
        ctx.lineTo(rx, ry - radius);
        ctx.quadraticCurveTo(rx, ry, rx - radius, ry);
        ctx.lineTo(lx + radius, ry);
        ctx.quadraticCurveTo(lx, ry, lx, ry - radius);
        ctx.lineTo(lx, ly + radius);
        ctx.quadraticCurveTo(lx, ly, lx + radius, ly);
        ctx.closePath();
        ctx.fill();

        // Label text
        ctx.fillStyle = "#ffffff";
        ctx.textBaseline = "middle";
        ctx.fillText(label, lx + padding, ly + th / 2 + padding);
    }
}

function clearHighlight(canvas) {
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
}

function updateActionInfo(label, success) {
    if (!actionInfoLabel) return;
    actionInfoLabel.textContent = label || "等待操作...";
    actionInfoLabel.classList.remove("highlight-action", "highlight-success", "highlight-fail");
    if (label) {
        if (success === true) {
            actionInfoLabel.classList.add("highlight-success");
        } else if (success === false) {
            actionInfoLabel.classList.add("highlight-fail");
        } else {
            actionInfoLabel.classList.add("highlight-action");
        }
    }
}

function resetScreenshotPanel() {
    if (!screenshotPanel) return;
    if (screenshotPlaceholder) screenshotPlaceholder.classList.remove("hidden");
    if (screenshotImg) {
        screenshotImg.style.display = "none";
        screenshotImg.src = "";
    }
    if (screenshotOverlay) {
        screenshotOverlay.style.display = "none";
        clearHighlight(screenshotOverlay);
    }
    if (screenshotStepBadge) {
        screenshotStepBadge.textContent = "等待中";
        screenshotStepBadge.classList.remove("active");
    }
    updateActionInfo(null, null);
    currentScreenshot = null;
    currentTargetBbox = null;
    currentActionLabel = null;
}

// ============================================================================
// WebSocket with Auto-Reconnect
// ============================================================================

function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/agent`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log("[WS] connected");
        reconnectAttempts = 0;
        setStatus("connected");
        toast("已连接到服务器", "success");
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
    };

    ws.onclose = () => {
        console.log("[WS] disconnected");
        if (isRunning) {
            setRunning(false);
            addResultMessage(false, "连接中断");
        }
        setStatus("");
        ws = null;

        // Auto-reconnect if not running (exponential backoff)
        if (!isRunning) {
            const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), MAX_RECONNECT_DELAY);
            reconnectAttempts++;
            console.log(`[WS] reconnecting in ${delay}ms (attempt ${reconnectAttempts})`);
            setTimeout(() => {
                if (!ws || ws.readyState === WebSocket.CLOSED) {
                    connectWebSocket();
                }
            }, delay);
        }
    };

    ws.onerror = (err) => {
        console.error("[WS] error", err);
        setStatus("error");
        toast("连接出错，正在重试...", "error");
    };
}

function startTask(task, url) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        connectWebSocket();
        // Wait briefly for connection, then send
        const checkAndSend = () => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify(buildTaskConfig(task, url)));
            } else {
                addResultMessage(false, "无法连接到服务器");
                setRunning(false);
                toast("无法连接到服务器", "error");
            }
        };
        setTimeout(checkAndSend, 500);
    } else {
        ws.send(JSON.stringify(buildTaskConfig(task, url)));
    }
}

function stopTask() {
    if (ws) {
        ws.close();
        ws = null;
    }
    setRunning(false);
    addResultMessage(false, "已手动停止");
    toast("任务已停止", "info");
}

function buildTaskConfig(task, url) {
    return {
        task,
        url,
        provider: settingProvider.value || "qwen",
        model: settingModel.value || PROVIDER_DEFAULTS[settingProvider.value]?.model || "qwen3-vl-8b-instruct",
        api_key: getApiKey(),
        base_url: settingBaseUrl.value.trim() || null,
        max_steps: parseInt(settingMaxSteps.value, 10) || 15,
        headless: settingHeadless.checked,
    };
}

// ============================================================================
// Message handling
// ============================================================================

function handleMessage(msg) {
    switch (msg.type) {
        case "step":
            hideTyping();
            // Update screenshot panel with new screenshot and target highlight
            if (msg.screenshot) {
                updateScreenshotPanel(
                    msg.screenshot,
                    msg.target_bbox || null,
                    msg.target_label || null,
                    msg.step,
                    msg.success
                );
            }
            addStepMessage(msg);
            showTyping();
            break;
        case "done":
            hideTyping();
            setRunning(false);
            addResultMessage(msg.success, msg.answer, msg.total_steps, msg.error);
            // Update screenshot panel for final state
            updateActionInfo(
                msg.success ? ("✅ " + (msg.answer || "任务完成")) : ("❌ " + (msg.error || "任务失败")),
                msg.success
            );
            if (screenshotStepBadge) {
                screenshotStepBadge.textContent = msg.success ? "已完成" : "失败";
                screenshotStepBadge.classList.remove("active");
            }
            break;
        case "error":
            hideTyping();
            setRunning(false);
            addResultMessage(false, null, 0, msg.message);
            toast(msg.message || "任务出错", "error");
            updateActionInfo("❌ " + (msg.message || "出错"), false);
            break;
    }
}

function removeWelcome() {
    const welcome = chatMessages.querySelector(".welcome-message");
    if (welcome) welcome.remove();
}

function addUserMessage(task, url) {
    removeWelcome();
    const div = document.createElement("div");
    div.className = "message user";
    div.innerHTML = `
        <div class="bubble">
            <div class="task-text">${escapeHtml(task)}</div>
            <div class="url-text">🌐 ${escapeHtml(url)}</div>
            <div class="msg-time">${nowTime()}</div>
        </div>
    `;
    chatMessages.appendChild(div);
    scrollToBottom();
}

function addStepMessage(msg) {
    removeWelcome();
    const div = document.createElement("div");
    div.className = "message agent";

    const actionLabel = msg.action || "unknown";
    const successClass = msg.success ? "success" : "fail";

    let screenshotHtml = "";
    if (msg.screenshot) {
        // Before/after comparison layout
        screenshotHtml = '<div class="screenshot-compare">';
        screenshotHtml += `<div class="screenshot-side"><span class="screenshot-label before">操作前</span><img class="step-screenshot" src="data:image/png;base64,${msg.screenshot}" alt="操作前截图" onclick="openLightbox(this.src)" loading="lazy" /></div>`;
        if (msg.post_screenshot) {
            screenshotHtml += `<div class="screenshot-side"><span class="screenshot-label after">操作后</span><img class="step-screenshot" src="data:image/png;base64,${msg.post_screenshot}" alt="操作后截图" onclick="openLightbox(this.src)" loading="lazy" /></div>`;
        }
        screenshotHtml += '</div>';
    }

    let actionDetail = "";
    if (msg.action) {
        const parts = [];
        if (msg.element_id) parts.push(`元素 #${msg.element_id}`);
        if (msg.value) parts.push(`"${escapeHtml(String(msg.value))}"`);
        actionDetail = parts.join(" · ");
    }

    // Build verification display HTML
    let verificationHtml = "";
    if (msg.verification) {
        const v = msg.verification;
        let vClass, vHeaderText, vIcon;

        if (v.effect_achieved) {
            vClass = "verified";
            vHeaderText = "操作成功";
            vIcon = "✅";
        } else {
            vClass = v.should_retry ? "failed" : "neutral";
            vHeaderText = v.should_retry ? "操作未达到预期效果" : "操作未生效";
            vIcon = v.should_retry ? "❌" : "⚠️";
        }

        verificationHtml = `
            <div class="step-verification ${vClass}">
                <div class="verification-header ${vClass}">
                    <span class="verification-icon">${vIcon}</span>
                    <span>${vHeaderText}</span>
                </div>
                ${v.observation ? `<div class="verification-observation">📝 ${escapeHtml(v.observation)}</div>` : ""}
                ${(v.rollback_action || v.retry_action) ? '<div class="verification-recovery">' : ""}
                    ${v.rollback_action ? buildRecoveryActionHtml("↩️ 回退", v.rollback_action, "rollback") : ""}
                    ${v.retry_action ? buildRecoveryActionHtml("🔄 重试", v.retry_action, "retry") : ""}
                ${(v.rollback_action || v.retry_action) ? '</div>' : ""}
            </div>`;
    }

    div.innerHTML = `
        <div class="bubble">
            <div class="step-header">
                <span class="step-number">📍 第 ${msg.step} 步</span>
                <span class="step-action ${successClass}">${actionLabel}</span>
            </div>
            ${actionDetail ? `<div class="step-detail">${actionDetail}</div>` : ""}
            ${msg.description ? `<div class="step-detail">${escapeHtml(msg.description)}</div>` : ""}
            ${screenshotHtml}
            ${verificationHtml}
            ${msg.vlm_output ? `<details class="step-vlm-detail"><summary>📋 VLM 输出</summary><div class="step-vlm">${escapeHtml(msg.vlm_output)}</div></details>` : ""}
            <div class="msg-time">${nowTime()}</div>
        </div>
    `;

    chatMessages.appendChild(div);
    scrollToBottom();
}

function buildRecoveryActionHtml(label, actionObj, cssClass) {
    if (!actionObj) return "";
    const parts = [];
    if (actionObj.action) parts.push(actionObj.action.toUpperCase());
    if (actionObj.element_id) parts.push("#" + actionObj.element_id);
    if (actionObj.value) parts.push('"' + escapeHtml(String(actionObj.value)) + '"');
    const desc = parts.join(" ");
    return `<div class="recovery-action ${cssClass}">
        <span class="recovery-icon">${label.split(" ")[0]}</span>
        <span>${label.split(" ")[1] || ""}: ${desc}</span>
    </div>`;
}

function addResultMessage(success, answer, totalSteps, error) {
    const div = document.createElement("div");
    div.className = `message result ${success ? "success" : "fail"}`;

    const icon = success ? "✅" : "❌";
    const title = success ? (answer || "任务完成") : (error || "任务失败");
    const stats = totalSteps ? `共 ${totalSteps} 步` : "";

    div.innerHTML = `
        <div class="bubble">
            <div class="result-icon">${icon}</div>
            <div class="result-answer">${escapeHtml(title)}</div>
            ${stats ? `<div class="result-stats">${stats}</div>` : ""}
            <div class="msg-time">${nowTime()}</div>
        </div>
    `;

    chatMessages.appendChild(div);
    scrollToBottom();
}

// ============================================================================
// UI Actions
// ============================================================================

function handleSend() {
    if (isRunning) {
        stopTask();
        return;
    }

    const task = taskInput.value.trim();
    const url = urlInput.value.trim();

    if (!task) {
        taskInput.focus();
        return;
    }
    if (!url) {
        urlInput.focus();
        return;
    }

    currentTask = { task, url };
    addUserMessage(task, url);
    saveSettings();
    setRunning(true);
    resetScreenshotPanel();
    if (screenshotStepBadge) screenshotStepBadge.textContent = "运行中...";
    startTask(task, url);
}

function openLightbox(src) {
    lightbox.classList.remove("hidden");
    lightboxImg.src = src;
}

function closeLightbox() {
    lightbox.classList.add("hidden");
    // Delay clearing src to allow fade-out animation
    setTimeout(() => { lightboxImg.src = ""; }, 300);
}

// ============================================================================
// Event bindings
// ============================================================================

btnSend.addEventListener("click", handleSend);

taskInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
    }
});

// Ctrl+Enter also sends
taskInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.ctrlKey) {
        e.preventDefault();
        handleSend();
    }
});

btnAdvanced.addEventListener("click", () => {
    advancedSettings.classList.toggle("hidden");
    btnAdvanced.classList.toggle("active");
});

btnNewTask.addEventListener("click", () => {
    taskInput.value = "";
    urlInput.value = "https://example.com";
    taskInput.focus();
    chatMessages.innerHTML = `
        <div class="welcome-message" id="welcome-message">
            <div class="welcome-icon">🤖</div>
            <h3>欢迎使用 VisuMark Agent</h3>
            <p>在下方输入任务描述和目标网址，Agent 将使用视觉语言模型自动操控浏览器完成任务。</p>
            <div class="welcome-examples">
                <h4>示例任务</h4>
                <button class="example-chip" data-task="打开 Hacker News 并告诉我今天的头条新闻是什么" data-url="https://news.ycombinator.com">📰 Hacker News 头条</button>
                <button class="example-chip" data-task="搜索从北京到上海的航班" data-url="https://www.google.com/travel/flights">✈️ 搜索航班</button>
                <button class="example-chip" data-task="这个页面的标题是什么？" data-url="https://example.com">📄 读取页面标题</button>
            </div>
        </div>
        <div class="typing-indicator" id="typing-indicator">
            <div class="bubble">
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
            </div>
        </div>
    `;
    // Re-bind example chips
    $$(".example-chip").forEach(bindExampleChip);
    // Reset screenshot panel
    resetScreenshotPanel();
    toast("新任务已就绪", "info");
});

btnToggleSidebar.addEventListener("click", () => {
    sidebar.classList.toggle("collapsed");
    saveSidebarState();
});

btnToggleScreenshot.addEventListener("click", () => {
    screenshotPanel.classList.toggle("collapsed");
    btnToggleScreenshot.classList.toggle("active", screenshotPanel.classList.contains("collapsed"));
    saveScreenshotPanelState();
});

lightboxClose.addEventListener("click", closeLightbox);
lightbox.addEventListener("click", (e) => {
    if (e.target === lightbox) closeLightbox();
});

document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeLightbox();

    // Ctrl+K to focus task input
    if (e.key === "k" && e.ctrlKey && !isRunning) {
        e.preventDefault();
        taskInput.focus();
    }

    // Ctrl+B to toggle sidebar
    if (e.key === "b" && e.ctrlKey) {
        e.preventDefault();
        sidebar.classList.toggle("collapsed");
        saveSidebarState();
    }

    // Ctrl+M to toggle screenshot panel
    if (e.key === "m" && e.ctrlKey) {
        e.preventDefault();
        screenshotPanel.classList.toggle("collapsed");
        btnToggleScreenshot.classList.toggle("active", screenshotPanel.classList.contains("collapsed"));
        saveScreenshotPanelState();
    }
});

// Click screenshot image to open lightbox
if (screenshotImg) {
    screenshotImg.addEventListener("click", () => {
        if (screenshotImg.src && screenshotImg.style.display !== "none") {
            openLightbox(screenshotImg.src);
        }
    });
}

// Example chips binding
function bindExampleChip(chip) {
    chip.addEventListener("click", () => {
        taskInput.value = chip.dataset.task;
        urlInput.value = chip.dataset.url;
        handleSend();
    });
}
$$(".example-chip").forEach(bindExampleChip);

// Provider change → auto-fill model + base URL
settingProvider.addEventListener("change", onProviderChange);

// Auto-save settings on change
[settingProvider, settingModel, settingApiKey, settingBaseUrl, settingMaxSteps, settingHeadless].forEach((el) => {
    el.addEventListener("change", saveSettings);
    if (el.tagName === "INPUT" && el.type !== "checkbox") {
        el.addEventListener("blur", saveSettings);
    }
});

// ============================================================================
// Utility
// ============================================================================

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}


// ============================================================================
// Init
// ============================================================================

loadSettings();
loadSidebarState();
loadScreenshotPanelState();
connectWebSocket();
