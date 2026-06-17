/**
 * VisuMark Agent — Web UI Application Logic
 *
 * Manages WebSocket connection to the agent backend,
 * split-screen browser panel + chat, slide-over history sidebar.
 */

// ============================================================================
// State
// ============================================================================
let ws = null;
let isRunning = false;
let currentTask = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_DELAY = 10000;
const HISTORY_KEY = "visumark_history";
const SETTINGS_KEY = "visumark_settings";

// ============================================================================
// DOM references
// ============================================================================
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const chatMessages     = $("#chat-messages");
const taskInput        = $("#task-input");
const urlInput         = $("#url-input");
const btnSend          = $("#btn-send");
const btnAdvanced      = $("#btn-advanced");
const advancedSettings = $("#advanced-settings");
const btnNewTask       = $("#btn-new-task");
const btnToggleSidebar = $("#btn-toggle-sidebar");
const btnCloseSidebar  = $("#btn-close-sidebar");
const sidebarOverlay   = $("#sidebar-overlay");
const statusDot        = $("#status-dot");
const statusText       = $("#status-text");
const taskIndicator    = $("#task-indicator");
const lightbox         = $("#lightbox");
const lightboxImg      = $("#lightbox-img");
const lightboxClose    = $("#lightbox-close");
const toastContainer   = $("#toast-container");

/* Browser panel elements */
const browserPanel         = $("#browser-panel");
const browserViewport      = $("#browser-viewport");
const browserScreenshot    = $("#browser-screenshot");
const browserPlaceholder   = $("#browser-placeholder");
const browserStreamWrapper = $("#browser-stream-wrapper");
const browserInputOverlay  = $("#browser-input-overlay");
const browserUrl           = $("#browser-url");
const browserStepInfo      = $("#browser-step-info");
const browserElemCount     = $("#browser-elem-count");

/* Screencast state */
let screencastMeta = null;     // { deviceWidth, deviceHeight, pageScaleFactor }
let isScreencastActive = false;
let screencastTimeoutId = null;
let screencastWs = null;     // Separate WebSocket for screencast
const SCREENCAST_TIMEOUT_MS = 3000;  // 3s without a frame → stream considered dead

// Dynamic element lookup
function getTypingIndicator() {
    return $("#typing-indicator");
}

// Settings inputs
const settingProvider  = $("#setting-provider");
const settingModel     = $("#setting-model");
const settingApiKey    = $("#setting-api-key");
const settingBaseUrl   = $("#setting-base-url");
const settingMaxSteps  = $("#setting-max-steps");
const settingHeadless  = $("#setting-headless");
const settingMode      = $("#setting-mode");
const settingDebug      = $("#setting-debug");

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
        btnSend.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>';
        btnSend.title = "停止任务";
        setStatus("running");
        showTyping();
        connectScreencast();
    } else {
        btnSend.classList.remove("running");
        disconnectScreencast();
        btnSend.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M5 12h14M12 5l7 7-7 7"/></svg>';
        btnSend.title = "执行任务 (Enter)";
        setStatus(ws && ws.readyState === WebSocket.OPEN ? "connected" : "");
        hideTyping();
        // Reset browser panel
        browserStepInfo.textContent = "";
        browserElemCount.textContent = "";
        browserUrl.textContent = "";
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
// Browser Panel
// ============================================================================

function updateBrowserPanel(screenshotB64, url, step, elementsCount) {
    if (screenshotB64) {
        browserPlaceholder.style.display = "none";
        browserStreamWrapper.style.display = "";
        browserScreenshot.style.display = "";
        browserScreenshot.src = "data:image/png;base64," + screenshotB64;
    }
    if (url !== undefined) {
        browserUrl.textContent = url || "";
    }
    if (step !== undefined) {
        browserStepInfo.textContent = step > 0 ? `第 ${step} 步` : "";
    }
    if (elementsCount !== undefined && elementsCount > 0) {
        browserElemCount.textContent = `${elementsCount} 个可交互元素`;
    }
}

function resetBrowserPanel() {
    clearScreencastTimeout();
    browserPlaceholder.style.display = "";
    browserStreamWrapper.style.display = "none";
    browserScreenshot.style.display = "none";
    browserScreenshot.src = "";
    browserUrl.textContent = "";
    browserStepInfo.textContent = "";
    browserElemCount.textContent = "";
    isScreencastActive = false;
    screencastMeta = null;
}

// ============================================================================
// Screencast — live browser streaming via CDP
// ============================================================================

function connectScreencast() {
    if (screencastWs && screencastWs.readyState === WebSocket.OPEN) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws/screencast`;
    screencastWs = new WebSocket(url);
    screencastWs.onopen = () => console.debug("[screencast] connected");
    screencastWs.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "screencast_frame") {
            handleScreencastFrame(msg);
        }
    };
    screencastWs.onclose = () => { screencastWs = null; };
    screencastWs.onerror = () => { screencastWs = null; };
}

function disconnectScreencast() {
    if (screencastWs) {
        screencastWs.close();
        screencastWs = null;
    }
    clearScreencastTimeout();
    isScreencastActive = false;
    screencastMeta = null;
}

function startScreencastTimeout() {
    clearScreencastTimeout();
    screencastTimeoutId = setTimeout(() => {
        console.warn("[screencast] No frame for " + (SCREENCAST_TIMEOUT_MS / 1000) + "s — stream dead, falling back to agent screenshots");
        isScreencastActive = false;
        screencastTimeoutId = null;
        browserStepInfo.textContent = "实时画面已断开";
        toast("实时画面流已断开，切换为步骤截图模式", "info");
    }, SCREENCAST_TIMEOUT_MS);
}

function clearScreencastTimeout() {
    if (screencastTimeoutId !== null) {
        clearTimeout(screencastTimeoutId);
        screencastTimeoutId = null;
    }
}

function handleScreencastFrame(msg) {
    if (!msg.data) return;

    // Got a frame — reset the dead-stream timeout
    clearScreencastTimeout();

    // Hide placeholder, show stream wrapper
    browserPlaceholder.style.display = "none";
    browserStreamWrapper.style.display = "";

    // Store metadata for coordinate mapping
    if (msg.metadata) {
        screencastMeta = msg.metadata;
    }

    // Update the live frame
    browserScreenshot.src = "data:image/jpeg;base64," + msg.data;
    browserScreenshot.style.display = "";
    isScreencastActive = true;

    // Update footer
    browserStepInfo.textContent = "实时画面";

    // Arm the timeout — stream considered dead if no frame arrives in time
    startScreencastTimeout();
}

// ============================================================================
// Browser Input Capture — forwards mouse/key events to CDP
// ============================================================================

function initBrowserInputCapture() {
    if (!browserInputOverlay) return;

    // Track mouse button state
    let mouseDown = false;
    let lastMouseX = 0;
    let lastMouseY = 0;

    function getScaledCoords(e) {
        if (!screencastMeta || !browserStreamWrapper) {
            return { x: e.clientX - browserInputOverlay.getBoundingClientRect().left,
                     y: e.clientY - browserInputOverlay.getBoundingClientRect().top };
        }
        // Map from display coordinates to device (viewport) coordinates
        const rect = browserInputOverlay.getBoundingClientRect();
        const displayW = rect.width;
        const displayH = rect.height;
        const deviceW = screencastMeta.deviceWidth || 1280;
        const deviceH = screencastMeta.deviceHeight || 720;
        const scale = screencastMeta.pageScaleFactor || 1;

        // Calculate the actual image display area within the container
        // object-fit: contain means we need to account for letterboxing
        const imgAspect = deviceW / deviceH;
        const containerAspect = displayW / displayH;

        let imgDisplayW, imgDisplayH, offsetX, offsetY;
        if (imgAspect > containerAspect) {
            // Image is wider — letterbox top/bottom
            imgDisplayW = displayW;
            imgDisplayH = displayW / imgAspect;
            offsetX = 0;
            offsetY = (displayH - imgDisplayH) / 2;
        } else {
            // Image is taller — letterbox left/right
            imgDisplayH = displayH;
            imgDisplayW = displayH * imgAspect;
            offsetX = (displayW - imgDisplayW) / 2;
            offsetY = 0;
        }

        // Map click position to image coordinates, then scale to device
        const imgX = e.clientX - rect.left - offsetX;
        const imgY = e.clientY - rect.top - offsetY;
        const deviceX = Math.round((imgX / imgDisplayW) * deviceW);
        const deviceY = Math.round((imgY / imgDisplayH) * deviceH);

        return {
            x: Math.max(0, Math.min(deviceW, deviceX)),
            y: Math.max(0, Math.min(deviceH, deviceY)),
        };
    }

    function sendInput(inputData) {
        if (!ws || ws.readyState !== WebSocket.OPEN || !isRunning) return;
        ws.send(JSON.stringify({
            type: "browser_input",
            input: inputData,
        }));
    }

    function getButton(e) {
        if (e.button === 0) return "left";
        if (e.button === 1) return "middle";
        if (e.button === 2) return "right";
        return "none";
    }

    // ── Mouse events ──
    browserInputOverlay.addEventListener("mousedown", (e) => {
        e.preventDefault();
        mouseDown = true;
        const coords = getScaledCoords(e);
        lastMouseX = coords.x;
        lastMouseY = coords.y;
        sendInput({
            kind: "mouse",
            mouseType: "mousePressed",
            x: coords.x,
            y: coords.y,
            button: getButton(e),
            clickCount: 1,
        });
    });

    browserInputOverlay.addEventListener("mouseup", (e) => {
        e.preventDefault();
        mouseDown = false;
        const coords = getScaledCoords(e);
        sendInput({
            kind: "mouse",
            mouseType: "mouseReleased",
            x: coords.x,
            y: coords.y,
            button: getButton(e),
            clickCount: 1,
        });
    });

    browserInputOverlay.addEventListener("mousemove", (e) => {
        if (!mouseDown && !isRunning) return;
        const coords = getScaledCoords(e);
        lastMouseX = coords.x;
        lastMouseY = coords.y;
        if (mouseDown) {
            // Only send move events when button is pressed (drag)
            sendInput({
                kind: "mouse",
                mouseType: "mouseMoved",
                x: coords.x,
                y: coords.y,
                button: "left",
            });
        }
    });

    browserInputOverlay.addEventListener("wheel", (e) => {
        e.preventDefault();
        const coords = getScaledCoords(e);
        sendInput({
            kind: "wheel",
            x: coords.x,
            y: coords.y,
            deltaX: Math.round(e.deltaX),
            deltaY: Math.round(e.deltaY),
        });
    }, { passive: false });

    // Prevent context menu on the overlay
    browserInputOverlay.addEventListener("contextmenu", (e) => {
        e.preventDefault();
    });

    // ── Keyboard events (captured from document when overlay has focus) ──
    // Make overlay focusable
    browserInputOverlay.tabIndex = 0;
    browserInputOverlay.style.outline = "none";

    browserInputOverlay.addEventListener("keydown", (e) => {
        if (!isRunning) return;
        e.preventDefault();
        sendInput({
            kind: "key",
            keyType: "keyDown",
            key: e.key,
            code: e.code,
            text: e.key.length === 1 ? e.key : "",
            windowsVirtualKeyCode: e.keyCode || 0,
        });
        // Also send char event for printable characters
        if (e.key.length === 1) {
            sendInput({
                kind: "key",
                keyType: "char",
                key: e.key,
                code: e.code,
                text: e.key,
                windowsVirtualKeyCode: e.keyCode || 0,
            });
        }
    });

    browserInputOverlay.addEventListener("keyup", (e) => {
        if (!isRunning) return;
        e.preventDefault();
        sendInput({
            kind: "key",
            keyType: "keyUp",
            key: e.key,
            code: e.code,
            text: "",
            windowsVirtualKeyCode: e.keyCode || 0,
        });
    });

    // Focus the overlay when clicked
    browserInputOverlay.addEventListener("click", (e) => {
        browserInputOverlay.focus();
    });

    // Update cursor when typing
    browserInputOverlay.addEventListener("keydown", () => {
        browserInputOverlay.classList.add("typing");
    });
    browserInputOverlay.addEventListener("keyup", () => {
        browserInputOverlay.classList.remove("typing");
    });
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

    setTimeout(() => {
        el.classList.add("removing");
        el.addEventListener("animationend", () => el.remove());
    }, 3500);
}

// ============================================================================
// Settings Persistence
// ============================================================================

const PROVIDER_DEFAULTS = {
    qwen:     { model: "qwen3-vl-8b-instruct", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
    openai:   { model: "gpt-4o",               baseUrl: "https://api.openai.com/v1" },
    anthropic:{ model: "claude-sonnet-4-6",    baseUrl: "https://api.anthropic.com" },
    local:    { model: "/root/autodl-tmp/Qwen3-VL-8B-Instruc", baseUrl: "http://127.0.0.1:8000/v1" },
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
            settingDebug.checked = saved.debug === true;
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
        debug: settingDebug.checked,
    }));
}

function getApiKey() {
    return settingApiKey.value.trim() || null;
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
    disconnectScreencast();
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
        debug: settingDebug.checked,
    };
}

// ============================================================================
// Message handling
// ============================================================================

function handleMessage(msg) {
    console.log("[ws msg]", msg.type, msg.phase || "");
    switch (msg.type) {
        case "step_phase":
            handleStepPhase(msg);
            break;
        case "step":
            hideTyping();
            msg.phase = "complete";
            handleStepPhase(msg);
            showTyping();
            break;
        case "done":
            hideTyping();
            setRunning(false);
            addResultMessage(msg.success, msg.answer, msg.total_steps, msg.error, msg.answer_image);
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
    console.log("[phase]", msg.phase, "step", msg.step, "hasScreenshot:", !!msg.screenshot, "hasPost:", !!msg.post_screenshot);
    removeWelcome();
    const stepId = "step-" + msg.step;
    let bubble = document.getElementById(stepId);

    // Phase: perceive → update browser panel + create bubble
    if (msg.phase === "perceive") {
        stepTimers[msg.step] = { perceive: Date.now() };
        // Only update browser panel with agent screenshot if screencast is not active
        // (screencast provides live streaming, agent screenshots have SoM annotations)
        if (!isScreencastActive) {
            updateBrowserPanel(msg.screenshot, null, msg.step, msg.elements);
        } else {
            // Still update element count in footer (screencast doesn't know about elements)
            browserElemCount.textContent = msg.elements ? `${msg.elements} 个可交互元素` : "";
        }
        if (!bubble) {
            bubble = createStepBubble(msg.step, null, msg.elements);
            chatMessages.appendChild(bubble);
        }
        scrollToBottom();
        return;
    }

    // If bubble doesn't exist yet, create placeholder
    if (!bubble) {
        bubble = createStepBubble(msg.step, null, 0);
        chatMessages.appendChild(bubble);
    }

    const phaseEl = bubble.querySelector(".step-phases");

    // Phase: reasoning
    if (msg.phase === "reasoning") {
        if (stepTimers[msg.step]) {
            stepTimers[msg.step].reasoning = Date.now();
            stepTimers[msg.step]._phaseStart = Date.now();
        }
        updatePhaseContent(phaseEl, "reasoning", "🧠", "VLM 思考中...", true);
    }

    // Phase: acting
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

        // Update browser panel with highlighted screenshot (only if screencast not active)
        if (msg.highlighted_screenshot && !isScreencastActive) {
            updateBrowserPanel(msg.highlighted_screenshot, null, msg.step, null);
        }

        // Update step bubble screenshot to highlighted version
        const img = bubble.querySelector(".step-screenshot");
        if (img && msg.highlighted_screenshot) {
            img.src = "data:image/png;base64," + msg.highlighted_screenshot;
            const existing = bubble.querySelector(".screenshot-label.before");
            if (existing) existing.textContent = "操作前 🔴 目标";
        }
    }

    // Phase: post_action — show post screenshot immediately (before verify)
    if (msg.phase === "post_action") {
        if (msg.post_screenshot) {
            updatePostScreenshot(bubble, msg.post_screenshot);
        }
        return;
    }

    // Phase: verifying
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
        msg.phase !== "acting" && msg.phase !== "verifying" && msg.phase !== "post_action") {
        finalizeStepBubble(bubble, msg);
        // Update browser panel with post-action screenshot (only if screencast not active)
        if (msg.post_screenshot && !isScreencastActive) {
            updateBrowserPanel(msg.post_screenshot, null, msg.step, null);
        }
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

function updatePostScreenshot(bubble, postB64) {
    const existing = bubble.querySelector(".screenshot-compare");
    const preImg = bubble.querySelector(".step-screenshot");
    if (!preImg) return;
    // Already showing comparison — skip
    if (existing) return;
    // Create before/after comparison from the existing perceive screenshot
    const preSrc = preImg.src;
    const wrapper = document.createElement("div");
    wrapper.className = "screenshot-compare";
    wrapper.innerHTML =
        `<div class="screenshot-side"><span class="screenshot-label before">操作前</span><img class="step-screenshot" src="${preSrc}" alt="操作前" onclick="openLightbox(this.src)" loading="lazy" /></div>` +
        `<div class="screenshot-side"><span class="screenshot-label after">操作后</span><img class="step-screenshot" src="data:image/png;base64,${postB64}" alt="操作后" onclick="openLightbox(this.src)" loading="lazy" /></div>`;
    preImg.parentElement.replaceWith(wrapper);
}

function updatePhaseContent(container, phaseClass, icon, text, showTimer) {
    let html = `<div class="step-phase ${phaseClass}">`;
    html += `<span class="phase-icon">${icon}</span>`;
    html += `<span class="phase-text">${escapeHtml(text)}</span>`;
    if (showTimer) {
        html += `<span class="phase-timer" data-phase="${phaseClass}"></span>`;
    }
    html += `</div>`;
    container.innerHTML = html;

    if (showTimer) {
        const timerEl = container.querySelector(".phase-timer");
        if (timerEl) {
            const start = Date.now();
            const update = () => {
                if (!timerEl.parentElement) return;
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

    if (timers.verifying && !timers.verifyingDuration) {
        timers.verifyingDuration = ((Date.now() - timers.verifying) / 1000).toFixed(1);
    }

    const vt  = timers.reasoningDuration ? timers.reasoningDuration + "s" : null;
    const at  = timers.actingDuration ? timers.actingDuration + "s" : null;
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

    const bubbleEl = bubble.querySelector(".bubble");
    const phasesEl = bubble.querySelector(".step-phases");
    let detailHtml = "";
    if (msg.description) {
        detailHtml += `<div class="step-detail">${escapeHtml(msg.description)}</div>`;
    }

    // SoM annotated screenshot (debug mode)
    if (msg.annotated_screenshot) {
        detailHtml += `<div style="max-width:100%;margin-top:8px">`;
        detailHtml += `<span class="screenshot-label" style="background:rgba(176,136,247,0.12);color:#b088f7;border:1px solid rgba(176,136,247,0.25)">SoM 标注 (调试)</span>`;
        detailHtml += `<img class="step-screenshot" src="data:image/png;base64,${msg.annotated_screenshot}" alt="SoM标注截图" onclick="openLightbox(this.src)" loading="lazy" /></div>`;
    }

    // Post screenshot comparison (skip if already shown via post_action phase)
    const alreadyHasCompare = bubble.querySelector(".screenshot-compare");
    if (!alreadyHasCompare && msg.post_screenshot && msg.screenshot) {
        detailHtml += `<div class="screenshot-compare">`;
        detailHtml += `<div class="screenshot-side"><span class="screenshot-label before">操作前</span><img class="step-screenshot" src="data:image/png;base64,${msg.screenshot}" alt="操作前" onclick="openLightbox(this.src)" loading="lazy" /></div>`;
        detailHtml += `<div class="screenshot-side"><span class="screenshot-label after">操作后</span><img class="step-screenshot" src="data:image/png;base64,${msg.post_screenshot}" alt="操作后" onclick="openLightbox(this.src)" loading="lazy" /></div>`;
        detailHtml += `</div>`;
    } else if (msg.screenshot && !msg.post_screenshot) {
        detailHtml += `<img class="step-screenshot" src="data:image/png;base64,${msg.screenshot}" alt="Step ${msg.step}" onclick="openLightbox(this.src)" loading="lazy" />`;
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
    // Update task indicator in top bar
    taskIndicator.textContent = task.length > 50 ? task.substring(0, 48) + "..." : task;
    // Update browser URL
    browserUrl.textContent = url;
    scrollToBottom();
}

function addResultMessage(success, answer, totalSteps, error, answerImage) {
    const div = document.createElement("div");
    div.className = `message result ${success ? "success" : "fail"}`;

    const icon = success ? "✅" : "❌";
    const title = success ? (answer || "任务完成") : (error || "任务失败");
    const stats = totalSteps ? `共 ${totalSteps} 步` : "";

    let answerImgHtml = "";
    if (answerImage) {
        answerImgHtml = `<img class="result-image" src="data:image/png;base64,${answerImage}" alt="答案图片" onclick="openLightbox(this.src)" loading="lazy" />`;
    }
    div.innerHTML = `
        <div class="bubble">
            <div class="result-icon">${icon}</div>
            <div class="result-answer">${escapeHtml(title)}</div>
            ${answerImgHtml}
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
    // Also show captcha screenshot in browser panel
    if (msg.screenshot) {
        updateBrowserPanel(msg.screenshot, null, null, null);
    }
    scrollToBottom();
}

function continueTask(btn) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "continue" }));
    if (btn) { btn.disabled = true; btn.textContent = "⏳ 等待中..."; }
    document.querySelectorAll(".btn-continue").forEach(b => { b.disabled = true; });
}

// ============================================================================
// Sidebar (Slide-over)
// ============================================================================

function openSidebar() {
    sidebarOverlay.classList.remove("hidden");
}

function closeSidebar() {
    sidebarOverlay.classList.add("hidden");
}

// ============================================================================
// Browser Panel Resize
// ============================================================================

function initResizeHandle() {
    let isResizing = false;
    let startX = 0;
    let startWidth = 0;

    const handle = browserPanel;
    if (!handle) return;

    handle.addEventListener("mousedown", (e) => {
        // Only trigger resize when clicking on the right edge area (after pseudo-element)
        const rect = handle.getBoundingClientRect();
        const edgeX = rect.right;
        if (Math.abs(e.clientX - edgeX) > 8) return; // Not on the edge

        isResizing = true;
        startX = e.clientX;
        startWidth = rect.width;
        handle.classList.add("resizing");
        document.body.classList.add("resizing");
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";

        e.preventDefault();
    });

    document.addEventListener("mousemove", (e) => {
        if (!isResizing) return;
        const delta = e.clientX - startX;
        const newWidth = startWidth + delta;
        // Clamp between 280px and 55% of viewport
        const minW = 280;
        const maxW = window.innerWidth * 0.55;
        const clamped = Math.max(minW, Math.min(maxW, newWidth));
        browserPanel.style.width = clamped + "px";
        browserPanel.style.minWidth = "0";
        browserPanel.style.maxWidth = "none";
    });

    document.addEventListener("mouseup", () => {
        if (!isResizing) return;
        isResizing = false;
        handle.classList.remove("resizing");
        document.body.classList.remove("resizing");
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
    });

    // Also handle the cursor style when hovering over the edge
    handle.addEventListener("mousemove", (e) => {
        if (isResizing) return;
        const rect = handle.getBoundingClientRect();
        const edgeX = rect.right;
        if (Math.abs(e.clientX - edgeX) <= 8) {
            handle.style.cursor = "col-resize";
        } else {
            handle.style.cursor = "";
        }
    });
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

    // Clear old agent messages
    const oldMessages = chatMessages.querySelectorAll(
        ".message.agent, .message.result, .message.captcha"
    );
    oldMessages.forEach(m => m.remove());
    for (const key of Object.keys(stepTimers)) {
        delete stepTimers[key];
    }

    // Reset browser panel for new task
    resetBrowserPanel();
    browserUrl.textContent = url;

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

/* Sidebar toggle */
btnToggleSidebar.addEventListener("click", openSidebar);
btnCloseSidebar.addEventListener("click", closeSidebar);
sidebarOverlay.addEventListener("click", (e) => {
    if (e.target === sidebarOverlay) closeSidebar();
});

/* New task */
btnNewTask.addEventListener("click", () => {
    taskInput.value = "";
    urlInput.value = "https://example.com";
    taskInput.focus();
    for (const key of Object.keys(stepTimers)) { delete stepTimers[key]; }
    taskIndicator.textContent = "就绪";
    resetBrowserPanel();
    chatMessages.innerHTML = `
        <div class="welcome-message" id="welcome-message">
            <div class="welcome-icon">
                <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2">
                    <circle cx="12" cy="12" r="10"/>
                    <path d="M8 14s1.5 2 4 2 4-2 4-2"/>
                    <line x1="9" y1="9" x2="9.01" y2="9"/>
                    <line x1="15" y1="9" x2="15.01" y2="9"/>
                </svg>
            </div>
            <h3>欢迎使用 VisuMark Agent</h3>
            <p>输入任务描述和目标网址，AI Agent 将自动操控浏览器完成任务。<br/>左侧面板将实时展示浏览器画面。</p>
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
                <button class="example-chip" data-task="B站番剧 追番人数最多第一名是什么，返回名字和封面" data-url="https://www.bilibili.com">📺 B站番剧排行榜</button>
                <button class="example-chip" data-task="最新的特斯拉Model 3价格是多少" data-url="https://www.bing.com">🚗 特斯拉Model 3价格</button>
                <button class="example-chip" data-task="翻译 'Hello World' 到中文" data-url="https://fanyi.baidu.com">🌐 百度翻译</button>
                <button class="example-chip" data-task="搜索从深圳北到广州南的高铁，告诉我最早一班的时间" data-url="https://www.baidu.com">🚄 高铁最早班次</button>
                <button class="example-chip" data-task="搜索'周杰伦'，找到他的出生日期和代表作前3首" data-url="https://www.baidu.com">🎤 明星信息查询</button>
                <button class="example-chip" data-task="分别搜索茅台和腾讯的最新股价，比较哪个涨幅更高" data-url="https://www.bing.com">📊 股票对比查询</button>
                <button class="example-chip" data-task="搜索iPhone 16 Pro Max和Samsung Galaxy S25 Ultra的详细参数，告诉我哪一个屏幕更大" data-url="https://www.bing.com">📱 手机参数对比</button>
                <button class="example-chip" data-task="打开bilibili首页，点击第一个视频，返回作者的头像" data-url="https://www.bilibili.com">📺 B站作者头像</button>
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
    $$(".example-chip").forEach(bindExampleChip);
    toast("新任务已就绪", "info");
});

/* Browser screenshot click → lightbox */
browserScreenshot.addEventListener("click", () => {
    if (browserScreenshot.src) openLightbox(browserScreenshot.src);
});

/* Lightbox */
lightboxClose.addEventListener("click", closeLightbox);
lightbox.addEventListener("click", (e) => {
    if (e.target === lightbox) closeLightbox();
});

document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
        closeLightbox();
        closeSidebar();
    }

    // Ctrl+K to focus task input
    if (e.key === "k" && e.ctrlKey && !isRunning) {
        e.preventDefault();
        taskInput.focus();
    }

    // Ctrl+B to toggle sidebar
    if (e.key === "b" && e.ctrlKey) {
        e.preventDefault();
        if (sidebarOverlay.classList.contains("hidden")) {
            openSidebar();
        } else {
            closeSidebar();
        }
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
[settingProvider, settingModel, settingApiKey, settingBaseUrl, settingMaxSteps, settingHeadless, settingMode, settingDebug].forEach((el) => {
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
connectWebSocket();
initResizeHandle();
initBrowserInputCapture();
