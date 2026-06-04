#!/usr/bin/env python3
"""Launch the VisuMark Agent Web UI server."""

import argparse
import os
import sys
from pathlib import Path

# Allow importing from repo root
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

import uvicorn


def main():
    parser = argparse.ArgumentParser(
        description="VisuMark Agent Web UI — chat interface for the VLM-powered web agent",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    # uvicorn expects the module path as "package.module:app"
    # The server module lives at src/web/server.py
    uvicorn.run(
        "web.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
