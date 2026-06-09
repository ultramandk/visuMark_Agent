"""FastAPI backend for VisuMark Agent Web UI.

Provides:
    - WebSocket /ws/agent for real-time step streaming
    - Static file serving for the frontend (unchanged)
    - REST API endpoints: health, som-tree

The frontend (static/index.html, app.js, style.css) is preserved as-is.
This backend maintains the same WebSocket protocol expected by the frontend.
"""

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
    headless: bool = True,
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

    task_desc = config.get("task", "")
    url = config.get("url", "https://www.google.com")
    provider = config.get("provider", "qwen")
    model = config.get("model", "gpt-4o")
    api_key = config.get("api_key") or os.getenv("OPENAI_API_KEY")
    base_url = config.get("base_url") or None
    max_steps = config.get("max_steps", 30)
    headless = config.get("headless", True)

    if not task_desc:
        await ws.send_json({"type": "error", "message": "Task description is required"})
        await ws.close()
        return

    # Build components
    from visumark.environment.live_env import LiveEnvironment
    from visumark.perception.som_perceptor import SoMPerceptor
    from visumark.reasoning.factory import ReasonerFactory
    from visumark.core.agent import Agent, StepCallbacks
    from visumark.dataset.base import TaskInstance
    from visumark.action.executor import build_target_label
    from visumark.utils.config import load_config

    env = LiveEnvironment(headless=headless, viewport=(1280, 720))
    perceptor = SoMPerceptor({
        "max_elements": 200,
        "font_size": 14,
        "use_accessibility_tree": True,
    })
    reas_cfg = load_config().get("reasoning", {})
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

    class WSCallbacks(StepCallbacks):
        async def on_step(self, record: StepRecord, bridge: DOMBridge) -> None:
            action_data = {}
            target_label = ""
            if record.action is not None:
                action_data = record.action.to_dict()
                target_label = build_target_label(record.action, bridge)

            b64_screenshot = None
            if record.perception.screenshot:
                b64_screenshot = base64.b64encode(record.perception.screenshot).decode("utf-8")

            # Target bbox for frontend highlight overlay
            target_bbox = None
            if record.action and record.action.element_id:
                for elem in record.perception.elements:
                    if elem.id == record.action.element_id:
                        target_bbox = list(elem.bbox)
                        break

            await ws.send_json({
                "type": "step",
                "step": record.step,
                "action": action_data.get("action"),
                "element_id": action_data.get("element_id"),
                "value": action_data.get("value"),
                "description": target_label,
                "vlm_output": record.reasoner_output.raw_text[:2000],
                "screenshot": b64_screenshot,
                "target_bbox": target_bbox,
                "target_label": target_label,
                "success": record.success,
            })

        async def on_done(self, record) -> None:
            await ws.send_json({
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

    try:
        await agent.run(task, callbacks=WSCallbacks())
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
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
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
