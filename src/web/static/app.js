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
const sidebar = $("#sidebar");
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

function loadSettings() {
    try {
        const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY));
        if (saved) {
            settingModel.value = saved.model || "gpt-4o";
            settingApiKey.value = saved.apiKey || "";
            settingBaseUrl.value = saved.baseUrl || "";
            settingMaxSteps.value = saved.maxSteps || 30;
            settingHeadless.checked = saved.headless !== false;
        }
    } catch { /* ignore */ }
}

function saveSettings() {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({
        model: settingModel.value,
        apiKey: settingApiKey.value,
        baseUrl: settingBaseUrl.value,
        maxSteps: parseInt(settingMaxSteps.value, 10) || 30,
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
        model: settingModel.value || "gpt-4o",
        api_key: getApiKey(),
        base_url: settingBaseUrl.value.trim() || null,
        max_steps: parseInt(settingMaxSteps.value, 10) || 30,
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
            addStepMessage(msg);
            showTyping();
            break;
        case "done":
            hideTyping();
            setRunning(false);
            addResultMessage(msg.success, msg.answer, msg.total_steps, msg.error);
            break;
        case "error":
            hideTyping();
            setRunning(false);
            addResultMessage(false, null, 0, msg.message);
            toast(msg.message || "任务出错", "error");
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
        screenshotHtml = `<img class="step-screenshot" src="data:image/png;base64,${msg.screenshot}" alt="Step ${msg.step} screenshot" onclick="openLightbox(this.src)" loading="lazy" />`;
    }

    let actionDetail = "";
    if (msg.action) {
        const parts = [];
        if (msg.element_id) parts.push(`元素 #${msg.element_id}`);
        if (msg.value) parts.push(`"${escapeHtml(String(msg.value))}"`);
        actionDetail = parts.join(" · ");
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
            ${msg.vlm_output ? `<details class="step-vlm-detail"><summary>📋 VLM 输出</summary><div class="step-vlm">${escapeHtml(msg.vlm_output)}</div></details>` : ""}
            <div class="msg-time">${nowTime()}</div>
        </div>
    `;

    chatMessages.appendChild(div);
    scrollToBottom();
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
    toast("新任务已就绪", "info");
});

btnToggleSidebar.addEventListener("click", () => {
    sidebar.classList.toggle("collapsed");
    saveSidebarState();
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
});

// Example chips binding
function bindExampleChip(chip) {
    chip.addEventListener("click", () => {
        taskInput.value = chip.dataset.task;
        urlInput.value = chip.dataset.url;
        handleSend();
    });
}
$$(".example-chip").forEach(bindExampleChip);

// Auto-save settings on change
[settingModel, settingApiKey, settingBaseUrl, settingMaxSteps, settingHeadless].forEach((el) => {
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
connectWebSocket();
