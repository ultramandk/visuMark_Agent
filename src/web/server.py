"""FastAPI backend for VisuMark Agent Web UI.

Provides:
    - WebSocket /ws/agent for real-time step streaming
    - Static file serving for the frontend (unchanged)
    - REST API endpoints: health, som-tree

The frontend (static/index.html, app.js, style.css) is preserved as-is.
This backend maintains the same WebSocket protocol expected by the frontend.
"""

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

# Ensure visumark package is importable
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from loguru import logger

from visumark.core.types import StepRecord, ActionType
from visumark.perception.dom_bridge import DOMBridge
from visumark.utils.logging import setup_logger

setup_logger(level="INFO")

app = FastAPI(title="VisuMark Agent Web UI", version="0.2.0")

# ---------------------------------------------------------------------------
# Static files (frontend — PRESERVED AS-IS)
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/")
async def index():
    """Serve the main frontend page."""
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/static/{filename:path}")
async def static_file(filename: str):
    """Serve static assets (CSS, JS)."""
    file_path = _STATIC_DIR / filename
    if not file_path.resolve().is_relative_to(_STATIC_DIR.resolve()):
        return FileResponse(_STATIC_DIR / "index.html", status_code=403)
    return FileResponse(file_path)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "visumark-web"}


# ---------------------------------------------------------------------------
# SoM Tree — standalone element extraction API
# ---------------------------------------------------------------------------
from pydantic import BaseModel


class SoMElementOut(BaseModel):
    id: int
    tag: str
    text: str
    bbox: tuple[float, float, float, float]


@app.get("/api/som-tree")
async def som_tree(
    url: str = "https://example.com",
    annotate: bool = True,
    max_elements: int = 50,
    headless: bool = False,
):
    """Extract Set-of-Mark element tree for any web page.

    Returns elements + optional annotated screenshot.
    """
    from visumark.environment.live_env import LiveEnvironment
    from visumark.perception.element_extractor import ElementExtractor
    from visumark.perception.som_marker import SoMMarker

    browser = LiveEnvironment(headless=headless, viewport=(1280, 720))
    extractor = ElementExtractor(max_elements=min(max_elements, 100))
    marker = SoMMarker()

    try:
        await browser.start(url)
        page = browser.page
        title = await page.title() if page else url

        elements = await extractor.extract(page)

        annotated_b64: str | None = None
        if annotate:
            screenshot_bytes = await browser.screenshot()
            vp = browser.get_viewport()
            annotated_bytes = marker.annotate(screenshot_bytes, elements, vp["width"], vp["height"])
            annotated_b64 = base64.b64encode(annotated_bytes).decode("utf-8")

        return {
            "url": url,
            "title": title,
            "viewport": browser.get_viewport(),
            "elements": [
                {"id": int(e.id), "tag": e.tag, "text": e.text, "bbox": e.bbox}
                for e in elements
            ],
            "total_elements": len(elements),
            "annotated_screenshot": annotated_b64,
        }

    finally:
        await browser.stop()


# ---------------------------------------------------------------------------
# WebSocket — Agent Runner
# ---------------------------------------------------------------------------
@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket):
    """Main WebSocket endpoint for agent task execution.

    Protocol (matches existing frontend app.js):
        Client → Server: { task, url, model, api_key, base_url, max_steps, headless }
        Server → Client: { type: "step", step, action, element_id, value, ... }
        Server → Client: { type: "done", success, answer, total_steps, error }
        Server → Client: { type: "error", message }
    """
    await ws.accept()

    # Wait for start message
    try:
        raw = await ws.receive_text()
        config: dict[str, Any] = json.loads(raw)
    except (WebSocketDisconnect, json.JSONDecodeError) as exc:
        await ws.send_json({"type": "error", "message": f"Invalid start message: {exc}"})
        await ws.close()
        return

    from visumark.utils.config import load_config

    task_desc = config.get("task", "")
    url = config.get("url", "https://www.bing.com")
    # 从 YAML 配置读取默认值，前端可覆盖
    reas_cfg = load_config().get("reasoning", {})
    provider = config.get("provider") or reas_cfg.get("provider", "local")
    model = config.get("model") or reas_cfg.get("model", "")
    api_key = config.get("api_key") or reas_cfg.get("api_key") or os.getenv("DASHSCOPE_API_KEY")
    base_url = config.get("base_url") or reas_cfg.get("base_url")
    max_steps = config.get("max_steps", 30)
    headless = config.get("headless", False)
    perception_mode = config.get("mode", "som")  # "som" or "html"

    if not task_desc:
        await ws.send_json({"type": "error", "message": "Task description is required"})
        await ws.close()
        return

    # Build components
    from visumark.environment.live_env import LiveEnvironment
    from visumark.perception.base import PerceptorFactory
    from visumark.reasoning.factory import ReasonerFactory
    from visumark.core.agent import Agent, StepCallbacks
    from visumark.dataset.base import TaskInstance
    from visumark.action.executor import build_target_label

    # reas_cfg already loaded above

    env = LiveEnvironment(headless=headless, viewport=(1280, 720))
    perc_config = load_config().get("perception", {})
    perceptor = PerceptorFactory.create(perception_mode, perc_config)
    reasoner = ReasonerFactory.create(
        provider=provider,
        model=model,
        api_key=api_key or reas_cfg.get("api_key"),
        base_url=base_url or reas_cfg.get("base_url"),
        temperature=reas_cfg.get("temperature", 0.0),
        max_tokens=reas_cfg.get("max_tokens", 4096),
        timeout=reas_cfg.get("timeout", 120),
        max_retries=reas_cfg.get("max_retries", 3),
    )
    agent = Agent(
        perceptor=perceptor,
        reasoner=reasoner,
        env=env,
        max_steps=max_steps,
    )

    # Background listener for "continue" messages during CAPTCHA pause
    agent_task: asyncio.Task | None = None

    # Signal set when the WebSocket connection is lost — callbacks
    # check this before sending and the agent loop exits cleanly.
    ws_disconnected = asyncio.Event()

    class WSCallbacks(StepCallbacks):
        async def _safe_send(self, data: dict) -> bool:
            """Send JSON over WS, return False if the connection is dead."""
            if ws_disconnected.is_set():
                return False
            try:
                await ws.send_json(data)
                return True
            except Exception:
                ws_disconnected.set()
                return False

        async def on_captcha(self, screenshot: bytes, variant: str = "captcha") -> None:
            b64 = base64.b64encode(screenshot).decode("utf-8") if screenshot else None
            msg = (
                "检测到登录页面，请在浏览器窗口中手动登录后点击继续"
                if variant == "login" else
                "检测到验证码，请在浏览器窗口中手动完成操作后点击继续"
            )
            await self._safe_send({
                "type": "captcha_required",
                "screenshot": b64,
                "message": msg,
                "variant": variant,
            })

        async def on_perceive(self, step: int, screenshot: bytes, elements_count: int) -> None:
            b64 = base64.b64encode(screenshot).decode("utf-8") if screenshot else None
            await self._safe_send({
                "type": "step_phase",
                "step": step,
                "phase": "perceive",
                "screenshot": b64,
                "elements": elements_count,
            })

        async def on_reasoning(self, step: int) -> None:
            await self._safe_send({
                "type": "step_phase",
                "step": step,
                "phase": "reasoning",
            })

        async def on_acting(self, step: int, action, label: str, highlighted_screenshot: bytes | None = None) -> None:
            b64_hs = None
            if highlighted_screenshot:
                b64_hs = base64.b64encode(highlighted_screenshot).decode("utf-8")
            await self._safe_send({
                "type": "step_phase",
                "step": step,
                "phase": "acting",
                "action": action.action_type.value if action else None,
                "element_id": action.element_id if action else None,
                "value": action.value if action else None,
                "label": label,
                "highlighted_screenshot": b64_hs,
            })

        async def on_verifying(self, step: int) -> None:
            await self._safe_send({
                "type": "step_phase",
                "step": step,
                "phase": "verifying",
            })

        async def on_step(self, record: StepRecord, bridge: DOMBridge) -> None:
            action_data = {}
            target_label = ""
            if record.action is not None:
                action_data = record.action.to_dict()
                target_label = build_target_label(record.action, bridge)

            b64_screenshot = None
            if record.perception.screenshot:
                b64_screenshot = base64.b64encode(record.perception.screenshot).decode("utf-8")

            b64_post_screenshot = None
            if record.post_screenshot:
                b64_post_screenshot = base64.b64encode(record.post_screenshot).decode("utf-8")

            # Target bbox for frontend highlight overlay
            target_bbox = None
            if record.action and record.action.element_id:
                for elem in record.perception.elements:
                    if elem.id == record.action.element_id:
                        target_bbox = list(elem.bbox)
                        break

            # Verification result (if available)
            verify_data = None
            if record.verification is not None:
                verify_data = {
                    "effect_achieved": record.verification.effect_achieved,
                    "observation": record.verification.observation,
                    "should_retry": record.verification.should_retry,
                }
                if record.verification.rollback_action is not None:
                    verify_data["rollback_action"] = record.verification.rollback_action.to_dict()
                if record.verification.retry_action is not None:
                    verify_data["retry_action"] = record.verification.retry_action.to_dict()

            await self._safe_send({
                "type": "step",
                "step": record.step,
                "action": action_data.get("action"),
                "element_id": action_data.get("element_id"),
                "value": action_data.get("value"),
                "description": target_label,
                "vlm_output": record.reasoner_output.raw_text[:2000],
                "screenshot": b64_screenshot,
                "post_screenshot": b64_post_screenshot,
                "target_bbox": target_bbox,
                "target_label": target_label,
                "success": record.success,
                "verification": verify_data,
            })

        async def on_done(self, record) -> None:
            await self._safe_send({
                "type": "done",
                "success": record.success,
                "answer": record.answer,
                "total_steps": record.total_steps,
                "error": record.error,
            })

    task = TaskInstance(
        task_id="webui-task",
        description=task_desc,
        start_url=url,
    )

    # ── CDP screencast state (shared between tasks below) ──
    screencast_cdp = None
    screencast_task_ref: asyncio.Task | None = None

    async def setup_screencast():
        """Wait for browser to start, then begin screencast streaming."""
        nonlocal screencast_cdp
        # Poll until browser page is available (agent.run calls env.start internally)
        while not ws_disconnected.is_set() and env.page is None:
            await asyncio.sleep(0.3)
        if ws_disconnected.is_set() or env.page is None:
            return

        try:
            screencast_cdp = await env.get_cdp_session()
            await screencast_cdp.send("Page.startScreencast", {
                "format": "jpeg",
                "quality": 65,
                "maxWidth": 960,
                "maxHeight": 540,
                "everyNthFrame": 2,
            })
            logger.info("CDP screencast started (960x540, q65, every 2nd frame)")

            frame_queue: asyncio.Queue = asyncio.Queue(maxsize=4)
            ack_failures = 0
            MAX_ACK_FAILURES = 5

            def _on_screencast_frame(data: dict):
                try:
                    frame_queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass  # drop oldest-like frame if queue is full

            screencast_cdp.on("Page.screencastFrame", _on_screencast_frame)

            # Stream frames to frontend
            while not ws_disconnected.is_set():
                try:
                    frame = await asyncio.wait_for(frame_queue.get(), timeout=1.0)
                    if ws_disconnected.is_set():
                        break
                    await ws.send_json({
                        "type": "screencast_frame",
                        "data": frame["data"],
                        "metadata": {
                            "deviceWidth": frame["metadata"]["deviceWidth"],
                            "deviceHeight": frame["metadata"]["deviceHeight"],
                            "pageScaleFactor": frame["metadata"].get("pageScaleFactor", 1),
                        },
                    })
                    # Acknowledge frame to receive next one
                    try:
                        await screencast_cdp.send(
                            "Page.screencastFrameAck",
                            {"sessionId": frame["sessionId"]},
                        )
                        ack_failures = 0  # reset on success
                    except Exception as ack_exc:
                        ack_failures += 1
                        logger.warning(
                            f"Screencast ack failed ({ack_failures}/{MAX_ACK_FAILURES}): {ack_exc}"
                        )
                        if ack_failures >= MAX_ACK_FAILURES:
                            logger.error("Too many screencast ack failures — stopping stream")
                            break
                except asyncio.TimeoutError:
                    # No frame received — check if CDP is still alive
                    try:
                        await screencast_cdp.send("Page.stopScreencast")
                        await screencast_cdp.send("Page.startScreencast", {
                            "format": "jpeg",
                            "quality": 65,
                            "maxWidth": 960,
                            "maxHeight": 540,
                            "everyNthFrame": 2,
                        })
                        logger.info("CDP screencast restarted after idle period")
                        ack_failures = 0
                    except Exception:
                        logger.warning("Screencast restart failed — stream may be dead")
                        break
                except (WebSocketDisconnect, Exception):
                    break
        except Exception as exc:
            logger.warning(f"Screencast setup failed: {exc}")
        finally:
            if screencast_cdp:
                try:
                    await screencast_cdp.send("Page.stopScreencast")
                except Exception:
                    pass
            logger.info("CDP screencast stopped")

    async def forward_browser_input(inp: dict) -> None:
        """Forward user input from frontend to the browser via CDP."""
        if screencast_cdp is None:
            return
        try:
            kind = inp.get("kind", "")
            if kind == "mouse":
                await screencast_cdp.send("Input.dispatchMouseEvent", {
                    "type": inp.get("mouseType", "mouseMoved"),
                    "x": float(inp.get("x", 0)),
                    "y": float(inp.get("y", 0)),
                    "button": inp.get("button", "left"),
                    "clickCount": int(inp.get("clickCount", 1)),
                    "modifiers": int(inp.get("modifiers", 0)),
                })
            elif kind == "key":
                await screencast_cdp.send("Input.dispatchKeyEvent", {
                    "type": inp.get("keyType", "keyDown"),
                    "key": str(inp.get("key", "")),
                    "code": str(inp.get("code", "")),
                    "text": str(inp.get("text", "")),
                    "modifiers": int(inp.get("modifiers", 0)),
                    "windowsVirtualKeyCode": int(inp.get("windowsVirtualKeyCode", 0)),
                })
            elif kind == "wheel":
                await screencast_cdp.send("Input.dispatchMouseEvent", {
                    "type": "mouseWheel",
                    "x": float(inp.get("x", 0)),
                    "y": float(inp.get("y", 0)),
                    "deltaX": float(inp.get("deltaX", 0)),
                    "deltaY": float(inp.get("deltaY", 0)),
                })
        except Exception as exc:
            logger.debug(f"Browser input forward failed: {exc}")

    async def listen_for_messages():
        """Background task: listen for 'continue' and 'browser_input' from client."""
        while not ws_disconnected.is_set():
            try:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                msg_type = msg.get("type", "")
                if msg_type == "continue":
                    agent.resume()
                elif msg_type == "browser_input":
                    await forward_browser_input(msg.get("input", {}))
            except WebSocketDisconnect:
                ws_disconnected.set()
                if agent_task and not agent_task.done():
                    agent_task.cancel()
                break
            except Exception:
                pass

    agent_task = asyncio.create_task(agent.run(task, callbacks=WSCallbacks()))
    screencast_task_ref = asyncio.create_task(setup_screencast())
    listener_task = asyncio.create_task(listen_for_messages())

    # Wait for agent to finish, then cancel listeners.
    try:
        await agent_task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    finally:
        ws_disconnected.set()
        for t in [screencast_task_ref, listener_task]:
            if t and not t.done():
                t.cancel()
        try:
            await ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main():
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="VisuMark Agent Web UI Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(
        "web.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
