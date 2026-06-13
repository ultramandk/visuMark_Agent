"""VisuMark Agent — main ReAct loop: observe → reason → act → verify → repeat.

Orchestrates the full pipeline:
    1. Perception: screenshot → SoM annotation → element list → DOM bridge
    2. Reasoning: annotated screenshot + task → VLM → structured action
    3. Action: parse → execute in browser (live) or record (offline eval)
    4. Verification: compare before/after screenshots via VLM to check effect
    5. Loop: continue until ANSWER, FAIL, or max_steps

This is the single entry point for both live task execution and
Mind2Web offline evaluation. The only difference is the environment
(LiveEnvironment vs OfflineEnvironment) and whether a comparator
callback is attached.
"""

import asyncio
import time
from pathlib import Path

from loguru import logger

from visumark.core.types import (
    Action,
    ActionType,
    ReasonerOutput,
    StepRecord,
    TaskRecord,
    VerificationResult,
)
from visumark.environment.base import BaseEnvironment
from visumark.perception.base import BasePerceptor
from visumark.perception.dom_bridge import DOMBridge
from visumark.reasoning.base import BaseReasoner
from visumark.action.parser import ActionParser, ParseError
from visumark.action.executor import ActionExecutor, build_target_label
from visumark.dataset.base import TaskInstance

# ---------------------------------------------------------------------------
# Callback protocol
# ---------------------------------------------------------------------------

class StepCallbacks:
    """Hooks invoked at each phase of an agent step.

    Phases (in order):
        1. on_perceive   — screenshot + elements ready
        2. on_reasoning  — VLM call started
        3. on_acting     — action decided, about to execute
        4. on_verifying  — post-action verification started
        5. on_step       — step fully complete (final)

    The frontend uses these to progressively render each step.
    """

    async def on_perceive(self, step: int, screenshot: bytes, elements_count: int) -> None:
        """Screenshot ready — frontend can show it immediately."""
        pass

    async def on_reasoning(self, step: int) -> None:
        """VLM call started — frontend can show thinking animation."""
        pass

    async def on_acting(self, step: int, action: Action, label: str, highlighted_screenshot: bytes | None = None) -> None:
        """Action decided — frontend can show what will be executed, with target highlight."""
        pass

    async def on_verifying(self, step: int) -> None:
        """Verification started — frontend can show verifying animation."""
        pass

    async def on_step(
        self,
        record: StepRecord,
        bridge: DOMBridge,
    ) -> None:
        """Step complete — frontend shows final result."""
        pass

    async def on_captcha(self, screenshot: bytes, variant: str = "captcha") -> None:
        """CAPTCHA detected — agent pauses for manual intervention.

        variant: 'captcha' (VLM-detected CAPTCHA) or 'login' (programmatic login detection)
        """
        pass

    async def on_done(self, record: TaskRecord) -> None:
        """Task finished."""
        pass


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent:
    """ReAct-mode web agent with SoM visual grounding.

    Usage:
        agent = Agent(config)
        result = await agent.run(task)
    """

    def __init__(
        self,
        perceptor: BasePerceptor,
        reasoner: BaseReasoner,
        env: BaseEnvironment,
        *,
        max_steps: int = 30,
        retry_on_error: bool = True,
        max_retries: int = 3,
        screenshot_dir: str | Path = "./data/screenshots",
        verify_actions: bool = True,
        max_verify_retries: int = 1,
    ):
        self.perceptor = perceptor
        self.reasoner = reasoner
        self.env = env
        self.max_steps = max_steps
        self.retry_on_error = retry_on_error
        self.max_retries = max_retries
        self.screenshot_dir = Path(screenshot_dir)
        self.verify_actions = verify_actions
        self.max_verify_retries = max_verify_retries

        self.parser = ActionParser()
        self.executor = ActionExecutor()

        # CAPTCHA pause/resume
        self._paused = asyncio.Event()
        self._paused.set()  # Initial: not paused

        # ── Smart login detection state ──
        # Track domains where the user has already dismissed the captcha dialog.
        # Once dismissed, we skip programmatic detection for that domain for
        # the rest of the task — the VLM can still request CAPTCHA if needed.
        self._captcha_dismissed_domains: set[str] = set()
        # Also track exact URLs to avoid re-triggering on the same page
        self._captcha_dismissed_urls: set[str] = set()

    def resume(self) -> None:
        """Resume agent after manual CAPTCHA completion."""
        self._paused.set()
        logger.info("Agent resumed after CAPTCHA")

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        task: TaskInstance,
        callbacks: StepCallbacks | None = None,
    ) -> TaskRecord:
        """Execute a task from start to finish.

        Args:
            task: Task description and metadata.
            callbacks: Optional hooks for streaming, saving, evaluation.

        Returns:
            TaskRecord with full step history and outcome.
        """
        callbacks = callbacks or StepCallbacks()
        result = TaskRecord(
            task_id=task.task_id,
            task_description=task.description,
            success=False,
        )

        await self.env.start(task.start_url)
        logger.info(f"Agent started — task: {task.description[:80]}")

        try:
            for step_num in range(1, self.max_steps + 1):
                logger.info(f"--- Step {step_num}/{self.max_steps} ---")

                record = await self._execute_step(
                    step=step_num,
                    task_description=task.description,
                    history=result.steps,
                    callbacks=callbacks,
                )
                result.steps.append(record)

                # Callback
                bridge = DOMBridge().build_from_elements(
                    record.perception.elements
                )
                await callbacks.on_step(record, bridge)

                # Terminal check
                if record.action is None:
                    result.error = "Failed to produce a valid action"
                    break

                if record.action.action_type == ActionType.ANSWER:
                    result.success = True
                    result.answer = record.action.value
                    logger.success(f"Task completed: {result.answer}")
                    break

                if record.action.action_type == ActionType.FAIL:
                    result.error = record.action.value or "Agent declared failure"
                    logger.error(f"Agent failed: {result.error}")
                    break

                if record.action.action_type == ActionType.CAPTCHA:
                    vlm_url = record.perception.page_url
                    vlm_domain = self._extract_domain(vlm_url)
                    logger.info(f"CAPTCHA detected by VLM — pausing ({vlm_domain})")
                    await callbacks.on_captcha(record.perception.screenshot, variant="captcha")
                    self._paused.clear()
                    await self._paused.wait()
                    # Dismissal tracking — avoid re-triggering
                    if vlm_domain:
                        self._captcha_dismissed_domains.add(vlm_domain)
                    if vlm_url:
                        self._captcha_dismissed_urls.add(vlm_url)
                    logger.info(
                        f"CAPTCHA dismissed — domain '{vlm_domain}' will not re-trigger"
                    )
                    continue  # Re-perceive the page on next iteration

            else:
                result.error = (
                    f"Reached max steps ({self.max_steps}) without completing the task"
                )
                logger.warning(result.error)

        except Exception as exc:
            logger.exception(f"Unexpected error: {exc}")
            result.error = str(exc)

        finally:
            await self.env.stop()
            # Clean up screenshots after task completes
            self._cleanup_screenshots()

        result.total_steps = len(result.steps)
        await callbacks.on_done(result)
        return result

    def _cleanup_screenshots(self) -> None:
        """Clear all screenshots from the screenshot directory after task completion."""
        if not self.screenshot_dir.exists():
            return
        try:
            for f in self.screenshot_dir.iterdir():
                if f.is_file():
                    f.unlink()
            logger.debug(f"Cleared screenshots in: {self.screenshot_dir}")
        except Exception as exc:
            logger.debug(f"Failed to clear screenshots: {exc}")

    # ------------------------------------------------------------------
    # Login page detection
    # ------------------------------------------------------------------

    def _extract_domain(self, url: str) -> str:
        """Extract the domain from a URL for dimissal tracking."""
        import re
        m = re.search(r"https?://([^/]+)", url)
        return m.group(1) if m else url

    async def _detect_login_page(self, perception) -> bool:
        """Check whether the current page REQUIRES manual login.

        Two‑phase check designed to avoid false positives on pages where
        the user is ALREADY logged in or where login elements are merely
        present in the navigation chrome.

        Phase 1 — Already‑logged‑in exclusion (returns False immediately):
            • Visible user avatar / account menu icon
            • Logout / sign‑out button or link
            • User‑name display (e.g. "Welcome, 张三")
            • Inbox / account‑specific navigation (mailboxes, dashboards)

        Phase 2 — Positive login‑page signals (MUST have ≥2):
            • A VISIBLE password field (not hidden / off‑screen)
            • QR‑code login prompt text (e.g. "扫码登录")
            • A login‑form container (form with both text input + password)
            • Multiple login‑method buttons (QQ / WeChat / phone SMS)
            • Strong login‑page heading (e.g. "登录你的QQ邮箱")

        Additionally, if the user has already dismissed a captcha for this
        domain or URL, the check is skipped entirely.
        """
        if not self.env.is_live:
            return False

        page = self.env.page if hasattr(self.env, "page") else None
        if page is None:
            return False

        try:
            current_url = await self.env.get_page_url()
        except Exception:
            current_url = ""

        domain = self._extract_domain(current_url)

        # ── Already dismissed for this domain / URL? skip ──
        if domain and domain in self._captcha_dismissed_domains:
            logger.debug(f"Login check skipped — domain '{domain}' was already dismissed")
            return False
        if current_url and current_url in self._captcha_dismissed_urls:
            logger.debug(f"Login check skipped — URL already dismissed")
            return False

        try:
            # Also pass the current URL into the JS context so it can be used
            # as a hint for known login domains (mail.qq.com, passport.*, etc.)
            page_url_for_js = current_url
            result = await page.evaluate("""(pageUrl) => {
                const bodyText = (document.body ? document.body.innerText : '') || '';
                const lowerBody = bodyText.toLowerCase();

                // ═══════════════════════════════════════════════════
                // Phase 1 — Already logged‑in?  Exit immediately.
                // ═══════════════════════════════════════════════════

                // 1a) User avatar / account icon
                const avatarImgs = document.querySelectorAll(
                    'img[src*="avatar"], img[src*="head"], img[src*="photo"], ' +
                    'img[class*="avatar"], img[class*="headimg"], img[class*="face"], ' +
                    'img[class*="portrait"], img[class*="user-img"], ' +
                    'img[class*="txd-avatar"]'
                );
                if (avatarImgs.length > 0) return {result: false, reason: 'avatar'};

                // 1b) Logout / sign-out links or buttons
                const limitedScan = document.querySelectorAll(
                    'a, button, span, div[class*="user"], div[class*="account"], ' +
                    'div[class*="profile"], div[class*="header"], div[class*="top"]'
                );
                const MAX_SCAN = 300;
                let scanCount = 0;
                for (const el of limitedScan) {
                    if (scanCount++ > MAX_SCAN) break;
                    const t = (el.textContent || '').trim();
                    if (t.length > 20) continue;
                    if (/^(退出|注销|登出|退出登录|安全退出|log\\s*out|sign\\s*out)$/i.test(t)) {
                        if (el.offsetParent !== null) return {result: false, reason: 'logout-btn'};
                    }
                    if (el.className && typeof el.className === 'string') {
                        const c = el.className.toLowerCase();
                        if (/nickname|username|displayname|account-name/i.test(c)) {
                            if (el.offsetParent !== null && t.length > 0)
                                return {result: false, reason: 'username-display'};
                        }
                    }
                }

                // 1c) Inbox / dashboard indicators — user IS inside their account
                const inboxPatterns = [/收件箱/, /inbox/i, /写邮件/, /compose/i, /已登录/,
                                        /我的订单/, /个人中心/, /账号管理/, /account settings/i,
                                        /\\d+封未读/, /\\d+封邮件/];
                for (const p of inboxPatterns) {
                    if (p.test(bodyText)) return {result: false, reason: 'inbox'};
                }

                // ═══════════════════════════════════════════════════
                // Phase 2 — Positive login‑page signals
                // ═══════════════════════════════════════════════════
                let strongSignals = 0;   // high-confidence signals
                let weakSignals = 0;     // supporting signals
                const details = [];

                // ── 2a) VISIBLE password field (STRONG) ──
                const pwFields = document.querySelectorAll('input[type="password"]');
                let visiblePw = false;
                for (const el of pwFields) {
                    if (el.offsetParent !== null) { visiblePw = true; break; }
                }
                if (visiblePw) { strongSignals += 1; details.push('visible-password'); }

                // ── 2b) QR code login (STRONG) ──
                // Text patterns — expanded for real Chinese login pages
                const qrTextPatterns = [
                    '扫码登录', '二维码登录', '扫一扫登录', '扫描二维码',
                    '扫一扫', '扫码', '扫描登录', '扫码验证',
                    'scan qr code', 'scan to login', 'qr code login',
                    'scan with', 'mobile scan', '手机扫码',
                ];
                let hasQrText = false;
                for (const p of qrTextPatterns) {
                    if (lowerBody.includes(p)) { hasQrText = true; break; }
                }
                // QR image — check for <img> or <canvas> that LOOKS like a QR code
                // (QR codes are typically square images with "qr" in src/class/id/alt)
                let hasQrImage = false;
                const qrImgCandidates = document.querySelectorAll(
                    'img[src*="qr"], img[class*="qr"], img[id*="qr"], img[alt*="qr"], ' +
                    'img[src*="QR"], img[class*="QR"], img[id*="QR"], ' +
                    'canvas[class*="qr"], canvas[id*="qr"], ' +
                    'img[src*="code"], img[class*="qrcode"], img[id*="qrcode"]'
                );
                for (const el of qrImgCandidates) {
                    if (el.offsetParent !== null) { hasQrImage = true; break; }
                }
                if (hasQrText || hasQrImage) {
                    strongSignals += 1;
                    details.push(hasQrText ? 'qr-text' : 'qr-image');
                }

                // ── 2c) Login form container (STRONG) ──
                const forms = document.querySelectorAll('form');
                let hasLoginForm = false;
                for (const f of forms) {
                    if (f.offsetParent === null) continue;
                    const hasText = f.querySelector(
                        'input[type="text"], input[type="email"], ' +
                        'input:not([type]), input[name*="user"], input[name*="account"]'
                    );
                    const hasPw = f.querySelector('input[type="password"]');
                    if (hasText && hasPw) { hasLoginForm = true; break; }
                }
                if (!hasLoginForm) {
                    const panels = document.querySelectorAll(
                        '[class*="login-panel"], [class*="loginPanel"], ' +
                        '[class*="login-box"], [class*="loginBox"], ' +
                        '[class*="login-form"], [class*="loginForm"], ' +
                        '[class*="login-wrap"], [class*="loginWrap"]'
                    );
                    for (const p of panels) {
                        if (p.offsetParent !== null) { hasLoginForm = true; break; }
                    }
                }
                if (hasLoginForm) { strongSignals += 1; details.push('login-form'); }

                // ── 2d) Multiple login‑method buttons / tabs (WEAK) ──
                // QQ, WeChat, phone, email, etc. — typical of login hubs
                const methodPatterns = [
                    /微信登录/, /QQ登录/, /手机号登录/, /邮箱登录/, /账号密码登录/,
                    /短信登录/, /WeChat.*log/i, /phone.*log/i, /email.*log/i,
                    /微信/, /QQ/, /手机号/, /账号登录/, /密码登录/,
                    /快捷登录/, /快速登录/, /安全登录/, /扫码登录/,
                    /免密登录/, /验证码登录/,
                ];
                let methodCount = 0;
                for (const p of methodPatterns) {
                    if (p.test(bodyText)) methodCount++;
                    if (methodCount >= 2) break;
                }
                if (methodCount >= 2) { weakSignals += 1; details.push('multi-method'); }

                // ── 2e) Strong login heading (WEAK) ──
                const headings = document.querySelectorAll(
                    'h1, h2, h3, [class*="title"], [class*="heading"], ' +
                    '[class*="header-text"], [class*="headerText"]'
                );
                let strongHeading = false;
                for (const h of headings) {
                    if (h.offsetParent === null) continue;
                    const t = (h.textContent || '').trim();
                    if (/登录你的|登录您的|登录QQ|登录微信|Sign in to|Log in to|欢迎登录|安全登录|账号登录|密码登录|立即登录|马上登录/.test(t)) {
                        strongHeading = true; break;
                    }
                }
                if (strongHeading) { weakSignals += 1; details.push('strong-heading'); }

                // ── 2f) "快捷登录" / "快速登录" interface (WEAK) ──
                // QQ Mail specifically labels its login page as "快捷登录"
                if (/快捷登录|快速登录/.test(bodyText)) {
                    weakSignals += 1;
                    details.push('quick-login');
                }

                // ── 2g) Page title contains login keywords (WEAK) ──
                const pageTitle = (document.title || '').toLowerCase();
                if (/登录|登入|login|sign\\s*in|log\\s*in/.test(pageTitle)) {
                    weakSignals += 1;
                    details.push('login-title');
                }

                // ── 2h) URL-based hint — known login domains (WEAK) ──
                const urlLower = (pageUrl || window.location.href || '').toLowerCase();
                const knownLoginDomains = [
                    'mail.qq.com', 'passport.', 'login.', 'account.',
                    'signin', 'sign_in', 'signup', 'auth.',
                    'open.weixin.', 'open.wechat.', 'accounts.',
                    'id.qq.com', 'ptlogin', 'xui.ptlogin',
                ];
                let urlHint = false;
                for (const d of knownLoginDomains) {
                    if (urlLower.includes(d)) { urlHint = true; break; }
                }
                if (urlHint) { weakSignals += 1; details.push('login-url'); }

                // ── 2i) Anti-bot verification (WEAK) ──
                // Slider verification, puzzle, click-the-X CAPTCHA
                if (/滑块验证|拼图验证|点击验证|拖动滑块|请完成安全验证|验证码|captcha/i.test(bodyText)) {
                    weakSignals += 1;
                    details.push('captcha-challenge');
                }

                // ═══════════════════════════════════════════════════
                // Decision logic
                // ═══════════════════════════════════════════════════
                // 1. Any STRONG signal + any other signal (strong OR weak) → login page
                // 2. ≥3 weak signals → login page
                // 3. ≥2 weak signals + URL hint → login page
                const totalStrong = strongSignals;
                const totalWeak = weakSignals;
                const totalSignals = totalStrong + totalWeak;

                let isLogin = false;

                if (totalStrong >= 1 && totalSignals >= 2) {
                    isLogin = true;  // strong + anything else
                } else if (totalWeak >= 3) {
                    isLogin = true;  // many weak signals
                } else if (totalWeak >= 2 && urlHint) {
                    isLogin = true;  // weak signals on known login URL
                }

                return {
                    result: isLogin,
                    strong: totalStrong,
                    weak: totalWeak,
                    total: totalSignals,
                    details: details,
                };
            }""", page_url_for_js)
            is_login = bool(result.get("result", False)) if isinstance(result, dict) else False
            strong = result.get("strong", 0) if isinstance(result, dict) else 0
            weak = result.get("weak", 0) if isinstance(result, dict) else 0
            total = result.get("total", 0) if isinstance(result, dict) else 0
            details = result.get("details", []) if isinstance(result, dict) else []

            if is_login:
                logger.info(
                    f"Login page detected: {strong}S+{weak}W signals — {details}"
                )
            else:
                logger.debug(
                    f"Not a login page: {strong}S+{weak}W signals — {details}, "
                    f"reason={result.get('reason', 'none') if isinstance(result, dict) else 'n/a'}"
                )
            return is_login
        except Exception as exc:
            logger.debug(f"Login detection JS failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # DOM stability detection
    # ------------------------------------------------------------------

    async def _wait_for_dom_stable(
        self,
        page,
        poll_interval_ms: int = 400,
        max_polls: int = 8,
    ) -> None:
        """Wait for the DOM to stop changing after an action.

        Compares body.innerHTML.length across consecutive polls.  When
        two consecutive samples are equal, the DOM has stabilized.
        This catches async rendering (React dialogs, validation messages)
        that appear after the initial page load.
        """
        prev_len = -1
        for i in range(max_polls):
            try:
                curr_len = await page.evaluate(
                    "() => (document.body ? document.body.innerHTML.length : 0)"
                )
            except Exception:
                break

            if curr_len == prev_len and prev_len >= 0:
                logger.debug(f"DOM stable after {(i + 1) * poll_interval_ms}ms ({curr_len} bytes)")
                return

            prev_len = curr_len
            await asyncio.sleep(poll_interval_ms / 1000)

        logger.debug(f"DOM did not stabilize after {max_polls * poll_interval_ms}ms — proceeding anyway")

    # ------------------------------------------------------------------
    # Page error detection
    # ------------------------------------------------------------------

    async def _detect_page_error(self, page) -> str | None:
        """Scan the DOM for visible error dialogs, alerts, or validation
        messages that indicate the last action failed.

        Handles QQ Mail's xmail-ui-dialog, generic ARIA alerts, and
        common error toast/card patterns.
        """
        try:
            error_text = await page.evaluate("""() => {
                // 1) Visible dialogs with error-related text
                const dialogSel = [
                    '.xmail-ui-dialog', '[class*="dialog"]', '[class*="modal"]',
                    '[class*="popup"]', '[class*="toast"]', '[class*="alert"]',
                    '[class*="message"]', '[role="dialog"]', '[role="alertdialog"]',
                    '[role="alert"]',
                ];
                for (const sel of dialogSel) {
                    const el = document.querySelector(sel);
                    if (!el || el.offsetParent === null) continue;  // hidden
                    const text = (el.innerText || el.textContent || '').trim();
                    if (!text || text.length > 500) continue;
                    // Check for error-related keywords
                    const lower = text.toLowerCase();
                    const isError =
                        lower.includes('错误') || lower.includes('失败') ||
                        lower.includes('格式') || lower.includes('无效') ||
                        lower.includes('不存在') || lower.includes('无法') ||
                        lower.includes('error') || lower.includes('fail') ||
                        lower.includes('invalid') || lower.includes('wrong');
                    if (isError) return text.substring(0, 300);
                }

                // 2) QQ Mail specific: ui-dialog-title-text
                const titleEl = document.querySelector('.ui-dialog-title-text');
                if (titleEl && titleEl.offsetParent !== null) {
                    const text = titleEl.innerText.trim();
                    if (text) return text;
                }

                // 3) Form validation messages near focused/red inputs
                const errMsgs = document.querySelectorAll(
                    '[class*="error"], [class*="err-msg"], [class*="err_msg"], ' +
                    '[class*="form-error"], [class*="validate-msg"], ' +
                    '.xmail-ui-form-item-explain-error'
                );
                for (const el of errMsgs) {
                    if (el.offsetParent === null) continue;
                    const text = (el.innerText || '').trim();
                    if (text && text.length < 300) return text;
                }

                return null;
            }""")
            return error_text if error_text else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Action verification — post-action before/after comparison
    # ------------------------------------------------------------------

    async def _verify_action(
        self,
        action: Action,
        thought: str,
        pre_screenshot: bytes,
        task: str,
        page_url: str = "",
        pre_clean_screenshot: bytes | None = None,
    ) -> tuple[VerificationResult | None, bytes]:
        """Verify whether the executed action achieved its intended effect.

        Takes a post-action screenshot and asks the VLM to compare
        before (pre_screenshot, SoM-annotated) vs after (raw).

        Returns:
            (verification_result, post_screenshot) tuple.
            post_screenshot is always returned (even if verification fails)
            so the frontend can display before/after comparison.
        """
        empty = b""
        if not self.env.is_live:
            return None, empty

        page = self.env.page if hasattr(self.env, "page") else None
        if page is None:
            return None, empty

        # Wait for DOM to stabilize before taking the post-action screenshot.
        # Actions like CLICK can trigger async rendering (React dialogs,
        # form validation messages) that appear AFTER the initial page load.
        # We poll body.innerHTML.length — when two consecutive samples match,
        # the DOM has finished changing.
        await self._wait_for_dom_stable(page)

        # Take post-action screenshot
        try:
            post_screenshot = await self.env.screenshot()
        except Exception as exc:
            logger.warning(f"Failed to take post-action screenshot: {exc}")
            return None, empty

        # Fast pixel-diff: if screenshots are identical, the action had
        # zero visible effect.  Skip VLM call — the answer is obvious.
        from visumark.utils.image import are_screenshots_identical

        pre_clean = pre_clean_screenshot or pre_screenshot
        if are_screenshots_identical(pre_clean, post_screenshot):
            return VerificationResult(
                effect_achieved=False,
                observation="Screenshots are identical — action had no visible effect",
                should_retry=False,
            ), post_screenshot

        # Check DOM for error dialogs / validation messages BEFORE VLM.
        # Error dialogs (like QQ Mail "收件人地址格式错误") are persistent
        # DOM elements that the VLM might miss because the screenshot shows
        # the page BEHIND the semi-transparent mask.
        error_text = await self._detect_page_error(page)
        if error_text:
            logger.warning(f"DOM error detected: {error_text[:120]}")
            return VerificationResult(
                effect_achieved=False,
                observation=f"Page error: {error_text}",
                should_retry=True,
            ), post_screenshot

        try:
            result = await self.reasoner.verify(
                action=action,
                thought=thought,
                pre_screenshot=pre_screenshot,
                post_screenshot=post_screenshot,
                task=task,
                page_url=page_url,
            )
            return result, post_screenshot
        except Exception as exc:
            logger.warning(f"Verification VLM call failed: {exc}")
            return None, post_screenshot

    # ------------------------------------------------------------------
    # Single step
    # ------------------------------------------------------------------

    async def _execute_step(
        self,
        step: int,
        task_description: str,
        history: list,
        callbacks: StepCallbacks | None = None,
    ) -> StepRecord:
        """Run a single observe → reason → act cycle.

        Calls phase callbacks for progressive frontend rendering.
        """
        t0 = time.time()
        cb = callbacks or StepCallbacks()

        # 1. PERCEIVE
        perception, bridge = await self.perceptor.perceive(self.env)

        # Save screenshots for debugging
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        if perception.screenshot:
            (self.screenshot_dir / f"step_{step:03d}_clean.jpg").write_bytes(perception.screenshot)
        if perception.annotated_screenshot:
            (self.screenshot_dir / f"step_{step:03d}_som.jpg").write_bytes(perception.annotated_screenshot)

        # Phase callback: screenshot ready
        await cb.on_perceive(
            step,
            perception.screenshot or b"",
            len(perception.elements),
        )

        # ── Programmatic login page detection ──
        # Check the page for login indicators.  Only fires when the user
        # hasn't already dismissed a captcha for this domain/URL, and when
        # the page genuinely requires authentication (not just has a login
        # link somewhere in the nav).
        is_login_page = await self._detect_login_page(perception)
        if is_login_page:
            # Record URL + domain BEFORE pausing so the dismissal is sticky
            login_url = perception.page_url
            login_domain = self._extract_domain(login_url)

            logger.info(f"Login page detected programmatically — pausing ({login_domain})")
            await cb.on_captcha(perception.screenshot, variant="login")
            self._paused.clear()
            await self._paused.wait()

            # User dismissed the captcha — remember this domain & URL so we
            # don't re-trigger on the next step
            if login_domain:
                self._captcha_dismissed_domains.add(login_domain)
            if login_url:
                self._captcha_dismissed_urls.add(login_url)
            logger.info(
                f"CAPTCHA dismissed — domain '{login_domain}' will not re-trigger"
            )

            # Re-perceive after manual login
            perception, bridge = await self.perceptor.perceive(self.env)
            await cb.on_perceive(step, perception.screenshot or b"", len(perception.elements))
            # Fall through to reasoning — let VLM decide next action on logged-in page

        # 2. REASON (with retry)
        await cb.on_reasoning(step)
        reasoner_output = None
        for retry in range(self.max_retries if self.retry_on_error else 1):
            try:
                reasoner_output = await self.reasoner.reason(
                    perception,
                    task_description,
                    history,
                )
                break
            except ParseError as e:
                logger.warning(f"Parse error (retry {retry + 1}): {e}")
            except Exception as e:
                logger.warning(f"Reasoner error (retry {retry + 1}): {e}")
                if not self.retry_on_error:
                    break

        if reasoner_output is None:
            return StepRecord(
                step=step,
                perception=perception,
                reasoner_output=reasoner_output or ReasonerOutput(raw_text=""),
                action=None,
                success=False,
            )

        action = reasoner_output.action

        # Phase callback: action decided — with target highlight
        action_label = build_target_label(action, bridge) if action else ""
        highlighted_screenshot = None
        if action and action.element_id and perception.annotated_screenshot:
            target_bbox = bridge.get_bbox(action.element_id)
            if target_bbox:
                from visumark.utils.image import highlight_element
                highlighted_screenshot = highlight_element(
                    perception.annotated_screenshot, target_bbox
                )
        await cb.on_acting(step, action, action_label, highlighted_screenshot)

        # 3. ACT
        success = False
        if self.env.is_live and action is not None:
            success = await self.executor.execute(action, self.env, bridge)
        elif action is not None:
            success = True  # Offline mode: always "succeeds" (no real execution)

        # 4. VERIFY — compare before/after to check if the action worked
        verification = None
        post_screenshot = None
        verify_needed = (
            self.verify_actions
            and action is not None
            and not action.is_terminal
            and self.env.is_live
            and perception.annotated_screenshot
        )
        if verify_needed:
            await cb.on_verifying(step)
            pre_img = perception.annotated_screenshot
            for v_retry in range(self.max_verify_retries + 1):
                verification, post_screenshot = await self._verify_action(
                    action=action,
                    thought=reasoner_output.thought,
                    pre_screenshot=pre_img,
                    task=task_description,
                    page_url=perception.page_url,
                    pre_clean_screenshot=perception.screenshot,
                )
                if verification is None:
                    break  # Technical failure — skip verification

                if verification.effect_achieved:
                    logger.debug(f"Verification OK: {verification.observation[:120]}")
                    break

                logger.warning(
                    f"Verification FAILED [{v_retry + 1}]: {verification.observation[:120]}"
                )

                # 4a. ROLLBACK — undo the failed action's side effects
                if verification.rollback_action is not None:
                    logger.info(
                        f"Rolling back: {verification.rollback_action.to_dict()}"
                    )
                    rollback_ok = await self.executor.execute(
                        verification.rollback_action, self.env, bridge
                    )
                    if rollback_ok:
                        logger.debug("Rollback OK")
                        # Brief settle after rollback
                        try:
                            page = self.env.page if hasattr(self.env, "page") else None
                            if page:
                                await page.wait_for_timeout(400)
                        except Exception:
                            pass

                # 4b. RETRY — try the alternative action
                if verification.should_retry and verification.retry_action:
                    retry_action = verification.retry_action

                    # ── Stale element guard: if the page navigated, element IDs
                    #     from the BEFORE screenshot no longer point to the same
                    #     elements.  Replace element-based retries with press Enter
                    #     which is the safest non-element action after navigation.
                    try:
                        current_url = await self.env.get_page_url()
                    except Exception:
                        current_url = ""
                    url_changed = current_url and current_url != perception.page_url

                    if url_changed and retry_action.element_id is not None:
                        logger.warning(
                            f"URL changed ({perception.page_url} → {current_url}), "
                            f"element #{retry_action.element_id} is stale — "
                            f"replacing retry with press Enter"
                        )
                        retry_action = Action(
                            action_type=ActionType.PRESS, value="Enter"
                        )

                    logger.info(
                        f"Retrying with: {retry_action.to_dict()}"
                    )
                    retry_ok = await self.executor.execute(
                        retry_action, self.env, bridge
                    )
                    if retry_ok:
                        action = retry_action
                        success = True
                else:
                    break  # No retry suggested — move on

        # Build target label for display
        target_label = ""
        if action:
            target_label = build_target_label(action, bridge)

        elapsed = time.time() - t0
        logger.debug(
            f"Step {step}: {target_label} "
            f"(success={success}, {elapsed:.1f}s)"
        )

        return StepRecord(
            step=step,
            perception=perception,
            reasoner_output=reasoner_output,
            action=action,
            success=success,
            element_correct=None,   # Filled by evaluation callback
            operation_correct=None,  # Filled by evaluation callback
            verification=verification,
            post_screenshot=post_screenshot,
        )
