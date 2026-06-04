"""FastAPI backend for VisuMark Agent Web UI.

Provides:
- WebSocket /ws/agent for real-time step streaming
- Static file serving for the frontend
- Health check endpoint
"""

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Allow importing visumark_agent from repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from visumark_agent import VisuMarkAgent, OpenAIVLM, BrowserEnv, SoMMarker, load_config
from visumark_agent.agent.visumark import StepResult
from visumark_agent.environment.actions import ActionType
from visumark_agent.utils.logging import setup_logger

app = FastAPI(title="VisuMark Agent Web UI", version="0.1.0")

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/")
async def index():
    """Serve the main frontend page."""
    return FileResponse(_STATIC_DIR / "index.html")


# Mount static assets (CSS, JS) at /static/...
@app.get("/static/{filename:path}")
async def static_file(filename: str):
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
# WebSocket — Agent Runner
# ---------------------------------------------------------------------------
@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket):
    await ws.accept()

    # Wait for the start message
    try:
        raw = await ws.receive_text()
        config: dict[str, Any] = json.loads(raw)
    except (WebSocketDisconnect, json.JSONDecodeError) as exc:
        await ws.send_json({"type": "error", "message": f"Invalid start message: {exc}"})
        await ws.close()
        return

    task: str = config.get("task", "")
    url: str = config.get("url", "https://www.google.com")
    model: str = config.get("model", "gpt-4o")
    api_key: str | None = config.get("api_key") or os.getenv("OPENAI_API_KEY")
    base_url: str | None = config.get("base_url") or None
    max_steps: int = config.get("max_steps", 30)
    headless: bool = config.get("headless", True)

    if not task:
        await ws.send_json({"type": "error", "message": "Task description is required"})
        await ws.close()
        return

    # Build components
    vlm = OpenAIVLM(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=60,
    )
    browser = BrowserEnv(
        headless=headless,
        viewport=(1280, 720),
    )
    marker = SoMMarker()
    agent = VisuMarkAgent(
        vlm=vlm,
        browser=browser,
        marker=marker,
        max_steps=max_steps,
    )

    # Step callback — fires after each agent step
    async def on_step(step_result: StepResult, screenshot: bytes) -> None:
        action_data = {}
        if step_result.action is not None:
            action_data = step_result.action.to_dict()
            action_data["description"] = step_result.action.description

        b64_screenshot = base64.b64encode(screenshot).decode("utf-8") if screenshot else None

        await ws.send_json({
            "type": "step",
            "step": step_result.step,
            "action": action_data.get("action"),
            "element_id": action_data.get("element_id"),
            "value": action_data.get("value"),
            "description": action_data.get("description", ""),
            "vlm_output": step_result.vlm_output[:2000],  # truncate long outputs
            "screenshot": b64_screenshot,
            "success": step_result.success,
        })

    try:
        result = await agent.run_task(
            task=task,
            start_url=url,
            step_callback=on_step,
        )

        await ws.send_json({
            "type": "done",
            "success": result.success,
            "answer": result.answer,
            "total_steps": result.total_steps,
            "error": result.error,
        })

    except Exception as exc:
        await ws.send_json({"type": "error", "message": str(exc)})
    finally:
        try:
            await ws.close()
        except Exception:
            pass  # already closed


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main():
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="VisuMark Agent Web UI Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev)")
    args = parser.parse_args()

    uvicorn.run(
        "visumark_web.server:app" if __package__ else "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
