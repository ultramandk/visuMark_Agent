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
const settingProvider = $("#setting-provider");
const settingModel = $("#setting-model");
const settingApiKey = $("#setting-api-key");
const settingBaseUrl = $("#setting-base-url");
const settingMaxSteps = $("#setting-max-steps");
const settingHeadless = $("#setting-headless");
const settingMode = $("#setting-mode");

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
    anthropic:{ model: "claude-sonnet-4-6",    baseUrl: "https://api.anthropic.com" },
    local:   { model: "/root/autodl-tmp/Qwen3-VL-8B-Instruc",  baseUrl: "http://127.0.0.1:8000/v1" },
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
            settingModel.value = saved.model || "/root/autodl-tmp/GUI";
            settingApiKey.value = saved.apiKey || "";
            settingBaseUrl.value = saved.baseUrl || "";
            settingMaxSteps.value = saved.maxSteps || 30;
            settingHeadless.checked = saved.headless === true;
            settingMode.value = saved.mode || "som";
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
        mode: settingMode.value,
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
        provider: settingProvider.value || "qwen",
        model: settingModel.value || PROVIDER_DEFAULTS[settingProvider.value]?.model || "/root/autodl-tmp/GUI",
        api_key: getApiKey(),
        base_url: settingBaseUrl.value.trim() || null,
        max_steps: parseInt(settingMaxSteps.value, 10) || 30,
        headless: settingHeadless.checked,
        mode: settingMode.value || "som",
    };
}

// ============================================================================
// Message handling
// ============================================================================

function handleMessage(msg) {
    switch (msg.type) {
        case "step_phase":
            handleStepPhase(msg);
            break;
        case "step":
            hideTyping();
            // Fall through to finalize the bubble, then re-show typing
            msg.phase = "complete";
            handleStepPhase(msg);
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
        case "captcha_required":
            hideTyping();
            addCaptchaMessage(msg);
            break;
    }
}

// Track phase timers per step
const stepTimers = {};

function handleStepPhase(msg) {
    removeWelcome();
    const stepId = "step-" + msg.step;
    let bubble = document.getElementById(stepId);

    // Phase: perceive → create bubble with screenshot
    if (msg.phase === "perceive") {
        stepTimers[msg.step] = { perceive: Date.now() };
        if (!bubble) {
            bubble = createStepBubble(msg.step, msg.screenshot, msg.elements);
            chatMessages.appendChild(bubble);
        }
        scrollToBottom();
        return;
    }

    // If bubble doesn't exist yet (phase arrived before perceive), create placeholder
    if (!bubble) {
        bubble = createStepBubble(msg.step, null, 0);
        chatMessages.appendChild(bubble);
    }

    const phaseEl = bubble.querySelector(".step-phases");

    // Phase: reasoning → show thinking animation
    if (msg.phase === "reasoning") {
        if (stepTimers[msg.step]) {
            stepTimers[msg.step].reasoning = Date.now();
            stepTimers[msg.step]._phaseStart = Date.now();  // for live timer display
        }
        updatePhaseContent(phaseEl, "reasoning", "🧠", "VLM 思考中...", true);
    }

    // Phase: acting → lock in reasoning duration, record acting start
    if (msg.phase === "acting") {
        if (stepTimers[msg.step]) {
            const now = Date.now();
            if (stepTimers[msg.step].reasoning) {
                stepTimers[msg.step].reasoningDuration =
                    ((now - stepTimers[msg.step].reasoning) / 1000).toFixed(1);
            }
            stepTimers[msg.step].acting = now;
            stepTimers[msg.step]._phaseStart = now;
        }
        const label = msg.label || (msg.action ? msg.action.toUpperCase() : "");
        let desc = label;
        if (msg.element_id) desc += " · 元素 #" + msg.element_id;
        if (msg.value) desc += " · \"" + escapeHtml(String(msg.value)) + "\"";
        updatePhaseContent(phaseEl, "acting", "🖱️", desc, false);

        // Replace screenshot with highlighted version (red box on target)
        if (msg.highlighted_screenshot) {
            const img = bubble.querySelector(".step-screenshot");
            if (img) {
                img.src = "data:image/png;base64," + msg.highlighted_screenshot;
                // Add highlight indicator label
                const existing = bubble.querySelector(".screenshot-label.before");
                if (existing) existing.textContent = "操作前 🔴 目标";
            }
        }
    }

    // Phase: verifying → lock in acting duration, record verifying start
    if (msg.phase === "verifying") {
        if (stepTimers[msg.step]) {
            const now = Date.now();
            if (stepTimers[msg.step].acting) {
                stepTimers[msg.step].actingDuration =
                    ((now - stepTimers[msg.step].acting) / 1000).toFixed(1);
            }
            stepTimers[msg.step].verifying = now;
            stepTimers[msg.step]._phaseStart = now;
        }
        updatePhaseContent(phaseEl, "verifying", "🔍", "验证中...", true);
    }

    // Final step → complete the bubble
    if (msg.phase !== "perceive" && msg.phase !== "reasoning" &&
        msg.phase !== "acting" && msg.phase !== "verifying") {
        finalizeStepBubble(bubble, msg);
    }

    scrollToBottom();
}

function createStepBubble(step, screenshot, elements) {
    const div = document.createElement("div");
    div.className = "message agent";
    div.id = "step-" + step;

    let screenshotHtml = "";
    if (screenshot) {
        screenshotHtml = `<img class="step-screenshot" src="data:image/png;base64,${screenshot}" alt="Step ${step} 截图" onclick="openLightbox(this.src)" loading="lazy" />`;
    }

    div.innerHTML = `
        <div class="bubble">
            <div class="step-header">
                <span class="step-number">📍 第 ${step} 步</span>
                ${elements ? `<span class="step-elements">${elements} 个元素</span>` : ""}
            </div>
            ${screenshotHtml}
            <div class="step-phases"></div>
        </div>
    `;
    return div;
}

function updatePhaseContent(container, phaseClass, icon, text, showTimer) {
    // Replace all phase content
    let html = `<div class="step-phase ${phaseClass}">`;
    html += `<span class="phase-icon">${icon}</span>`;
    html += `<span class="phase-text">${escapeHtml(text)}</span>`;
    if (showTimer) {
        html += `<span class="phase-timer" data-phase="${phaseClass}"></span>`;
    }
    html += `</div>`;
    container.innerHTML = html;

    // Start live timer
    if (showTimer) {
        const timerEl = container.querySelector(".phase-timer");
        if (timerEl) {
            const start = Date.now();
            const update = () => {
                if (!timerEl.parentElement) return;  // Element removed
                const elapsed = ((Date.now() - start) / 1000).toFixed(1);
                timerEl.textContent = elapsed + "s";
                requestAnimationFrame(update);
            };
            update();
        }
    }
}

function finalizeStepBubble(bubble, msg) {
    const timers = stepTimers[msg.step] || {};

    // Lock in verifying duration
    if (timers.verifying && !timers.verifyingDuration) {
        timers.verifyingDuration = ((Date.now() - timers.verifying) / 1000).toFixed(1);
    }

    const vt = timers.reasoningDuration ? timers.reasoningDuration + "s" : null;
    const at = timers.actingDuration ? timers.actingDuration + "s" : null;
    const vft = timers.verifyingDuration ? timers.verifyingDuration + "s" : null;

    // Step header with action tag
    const header = bubble.querySelector(".step-header");
    if (header && msg.action) {
        const successClass = msg.success ? "success" : "fail";
        header.innerHTML = `
            <span class="step-number">📍 第 ${msg.step} 步</span>
            <span class="step-action ${successClass}">${msg.action}</span>
        `;
    }

    // Add detail
    const bubbleEl = bubble.querySelector(".bubble");
    const phasesEl = bubble.querySelector(".step-phases");
    let detailHtml = "";
    if (msg.description) {
        detailHtml += `<div class="step-detail">${escapeHtml(msg.description)}</div>`;
    }

    // Post screenshot
    if (msg.post_screenshot) {
        detailHtml += `<div class="screenshot-compare">`;
        detailHtml += `<div class="screenshot-side"><span class="screenshot-label before">操作前</span><img class="step-screenshot" src="data:image/png;base64,${msg.screenshot}" alt="操作前" onclick="openLightbox(this.src)" loading="lazy" /></div>`;
        detailHtml += `<div class="screenshot-side"><span class="screenshot-label after">操作后</span><img class="step-screenshot" src="data:image/png;base64,${msg.post_screenshot}" alt="操作后" onclick="openLightbox(this.src)" loading="lazy" /></div>`;
        detailHtml += `</div>`;
    }

    // Verification result
    if (msg.verification) {
        const v = msg.verification;
        const vClass = v.effect_achieved ? "verified" : (v.should_retry ? "failed" : "neutral");
        detailHtml += `<div class="step-verification ${vClass}">`;
        detailHtml += `<div class="verification-header ${vClass}">`;
        detailHtml += `<span class="verification-icon">${v.effect_achieved ? "✅" : "❌"}</span>`;
        detailHtml += `<span>${v.effect_achieved ? "操作成功" : "操作未达到预期效果"}</span>`;
        detailHtml += `</div>`;
        if (v.observation) detailHtml += `<div class="verification-observation">📝 ${escapeHtml(v.observation)}</div>`;
        if (v.rollback_action || v.retry_action) {
            detailHtml += `<div class="verification-recovery">`;
            if (v.rollback_action) detailHtml += `<div class="recovery-action rollback"><span class="recovery-icon">↩️</span><span>回退: ${buildActionDesc(v.rollback_action)}</span></div>`;
            if (v.retry_action) detailHtml += `<div class="recovery-action retry"><span class="recovery-icon">🔄</span><span>重试: ${buildActionDesc(v.retry_action)}</span></div>`;
            detailHtml += `</div>`;
        }
        detailHtml += `</div>`;
    }

    // Timing
    if (vt || at || vft) {
        detailHtml += `<div class="step-timing">`;
        if (vt) detailHtml += `🧠 VLM: ${vt}`;
        if (at) detailHtml += `🖱️ 执行: ${at}`;
        if (vft) detailHtml += `🔍 验证: ${vft}`;
        detailHtml += `</div>`;
    }

    // VLM output
    if (msg.vlm_output) {
        detailHtml += `<details class="step-vlm-detail"><summary>📋 VLM 输出</summary><div class="step-vlm">${escapeHtml(msg.vlm_output)}</div></details>`;
    }

    detailHtml += `<div class="msg-time">${nowTime()}</div>`;

    // Replace phases with final content
    if (phasesEl) {
        phasesEl.outerHTML = detailHtml;
    } else if (bubbleEl) {
        bubbleEl.insertAdjacentHTML("beforeend", detailHtml);
    }

    delete stepTimers[msg.step];
}

function buildActionDesc(obj) {
    if (!obj) return "";
    const parts = [];
    if (obj.action) parts.push(obj.action.toUpperCase());
    if (obj.element_id) parts.push("#" + obj.element_id);
    if (obj.value) parts.push('"' + escapeHtml(String(obj.value)) + '"');
    return parts.join(" ");
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

function addCaptchaMessage(msg) {
    const div = document.createElement("div");
    const isLogin = msg.variant === "login";
    const cssClass = isLogin ? "captcha login-variant" : "captcha captcha-variant";
    const icon = isLogin ? "🔑" : "🔐";
    const title = isLogin ? "需要人工登录" : "检测到验证码";
    const btnText = isLogin ? "✅ 已登录，继续" : "✅ 已完成验证，继续";

    div.className = "message " + cssClass;
    div.innerHTML = `
        <div class="bubble">
            <div class="captcha-icon">${icon}</div>
            <div class="captcha-title">${title}</div>
            <div class="captcha-text">${escapeHtml(msg.message || "请在浏览器窗口手动完成操作")}</div>
            ${msg.screenshot ? `<img class="step-screenshot" src="data:image/png;base64,${msg.screenshot}" alt="验证页面" onclick="openLightbox(this.src)" loading="lazy" />` : ""}
            <button class="btn-continue" onclick="continueTask(this)">${btnText}</button>
            <div class="msg-time">${nowTime()}</div>
        </div>
    `;
    chatMessages.appendChild(div);
    scrollToBottom();
}

function continueTask(btn) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "continue" }));
    if (btn) { btn.disabled = true; btn.textContent = "⏳ 等待中..."; }
    // Disable all other continue buttons too (previous CAPTCHA bubbles)
    document.querySelectorAll(".btn-continue").forEach(b => { b.disabled = true; });
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

    // Clear old agent messages + reset state for new task
    const oldMessages = chatMessages.querySelectorAll(
        ".message.agent, .message.result, .message.captcha"
    );
    oldMessages.forEach(m => m.remove());
    for (const key of Object.keys(stepTimers)) {
        delete stepTimers[key];
    }

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
    for (const key of Object.keys(stepTimers)) { delete stepTimers[key]; }
    chatMessages.innerHTML = `
        <div class="welcome-message" id="welcome-message">
            <div class="welcome-icon">🤖</div>
            <h3>欢迎使用 VisuMark Agent</h3>
            <p>在下方输入任务描述和目标网址，Agent 将使用视觉语言模型自动操控浏览器完成任务。</p>
            <div class="welcome-examples">
                <h4>示例任务</h4>
                <button class="example-chip" data-task="中山大学广州校区南校园在哪里" data-url="https://www.bing.com">🏫 中山大学南校园</button>
                <button class="example-chip" data-task="打开buff，查询精英之作的价格" data-url="https://www.bing.com">🎮 Buff 精英之作</button>
                <button class="example-chip" data-task="登录QQ邮箱，给739862481@qq.com发送一封端午节祝福邮件，标题'端午安康'，正文简短的节日祝福即可" data-url="https://mail.qq.com">📧 QQ邮箱发端午祝福</button>
                <button class="example-chip" data-task="今天天气怎么样" data-url="https://www.bing.com">🌤️ 查询今天天气</button>
                <button class="example-chip" data-task="搜索从广州到北京的航班" data-url="https://www.bing.com">✈️ 广州到北京航班</button>
                <button class="example-chip" data-task="华南理工大学有哪些王牌专业" data-url="https://www.bing.com">🏫 华工王牌专业</button>
                <button class="example-chip" data-task="打开淘宝搜索机械键盘并告诉我前三名的价格" data-url="https://www.bing.com">🛒 淘宝搜索机械键盘</button>
                <button class="example-chip" data-task="2026年世界杯在哪个国家举办" data-url="https://www.bing.com">⚽ 2026世界杯举办地</button>
                <button class="example-chip" data-task="B站番剧排行榜第一名是什么" data-url="https://www.bing.com">📺 B站番剧排行榜</button>
                <button class="example-chip" data-task="最新的特斯拉Model 3价格是多少" data-url="https://www.bing.com">🚗 特斯拉Model 3价格</button>
                <button class="example-chip" data-task="帮我查一下深圳今天有什么演唱会" data-url="https://www.bing.com">🎵 深圳今日演唱会</button>
                <button class="example-chip" data-task="Python和JavaScript哪个更适合初学者" data-url="https://www.bing.com">💻 编程语言对比</button>
                <button class="example-chip" data-task="这个网页的标题和描述是什么" data-url="https://example.com">📄 读取网页信息</button>
                <button class="example-chip" data-task="翻译 'Hello World' 到中文" data-url="https://fanyi.baidu.com">🌐 百度翻译</button>
                <button class="example-chip" data-task="查询 serial 这个单词的含义" data-url="https://dict.cn">📖 词典查词</button>
                <button class="example-chip" data-task="今天的NBA比赛结果是什么" data-url="https://www.baidu.com">🏀 NBA比赛结果</button>
                <button class="example-chip" data-task="深圳到广州的高铁时刻表" data-url="https://www.baidu.com">🚄 高铁时刻表</button>
                <button class="example-chip" data-task="豆瓣电影TOP250第一名是什么" data-url="https://www.baidu.com">🎬 豆瓣电影TOP250</button>
                <button class="example-chip" data-task="查询人民币对美元的汇率" data-url="https://www.baidu.com">💰 汇率查询</button>
                <button class="example-chip" data-task="打开百度翻译，把'人工智能正在改变世界'翻译成英文" data-url="https://fanyi.baidu.com">🌐 翻译整句到英文</button>
                <button class="example-chip" data-task="查询今天黄金的实时价格（人民币/克）" data-url="https://www.baidu.com">🥇 实时金价查询</button>
                <button class="example-chip" data-task="搜索从深圳北到广州南的高铁，告诉我最早一班的时间" data-url="https://www.baidu.com">🚄 高铁最早班次</button>
                <button class="example-chip" data-task="本周末深圳天气怎么样？适合出门吗" data-url="https://www.bing.com">🌦️ 周末天气+出行建议</button>
                <button class="example-chip" data-task="今天有什么热门新闻" data-url="https://www.baidu.com">📰 今日热门新闻</button>
                <button class="example-chip" data-task="搜索'周杰伦'，找到他的出生日期和代表作前3首" data-url="https://www.baidu.com">🎤 明星信息查询</button>
                <button class="example-chip" data-task="彭博社今天的头条新闻标题是什么" data-url="https://www.bloomberg.com">📈 彭博社头条</button>
                <button class="example-chip" data-task="搜索南昌现在的天气，然后查一下南昌到广州的火车票，告诉我最早的一班是几点" data-url="https://www.bing.com">🌤️🚄 天气+火车票组合查询</button>
                <button class="example-chip" data-task="分别搜索茅台和腾讯的最新股价，比较哪个涨幅更高" data-url="https://www.bing.com">📊 股票对比查询</button>
                <button class="example-chip" data-task="搜索iPhone 16 Pro Max和三Samsung Galaxy S25 Ultra的详细参数，告诉我哪一个屏幕更大" data-url="https://www.bing.com">📱 手机参数对比</button>
                <button class="example-chip" data-task="查询今天人民币对美元、欧元、日元三种货币的汇率" data-url="https://www.bing.com">💱 多币种汇率查询</button>
                <button class="example-chip" data-task="搜索'阿尔伯特·爱因斯坦'，然后进入他的维基百科页面，告诉我他的出生地和逝世年份" data-url="https://www.bing.com">🔬 维基百科信息提取</button>
                <button class="example-chip" data-task="在百度翻译中输入'I love programming'翻译成中文，再翻译成日文" data-url="https://fanyi.baidu.com">🌐 多语种翻译</button>
                <button class="example-chip" data-task="搜索暗黑破坏神4，看看它在哪个平台可以玩，Metacritic评分是多少" data-url="https://www.bing.com">🎮 游戏信息查询</button>
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

// Provider change → auto-fill model + base URL
settingProvider.addEventListener("change", onProviderChange);

// Auto-save settings on change
[settingProvider, settingModel, settingApiKey, settingBaseUrl, settingMaxSteps, settingHeadless, settingMode].forEach((el) => {
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
