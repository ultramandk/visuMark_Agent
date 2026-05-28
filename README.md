# VisuMark Agent

VLM-based web automation agent with **Set-of-Mark (SoM)** visual grounding.

## Overview

VisuMark Agent combines large vision-language models (GPT-4V/GPT-4o, Qwen-VL) with Set-of-Mark prompting to build a generalist web agent. Interactive elements on a page are annotated with labeled bounding boxes in the screenshot before being sent to the VLM, allowing the model to precisely reference elements by ID.

```
┌──────────┐     ┌──────────────┐     ┌──────────┐     ┌──────────┐
│  Browser  │────▶│  SoM Marker  │────▶│   VLM    │────▶│  Action  │
│ Screenshot│     │ (labeled bboxes)  │  Reason  │     │  Execute │
└──────────┘     └──────────────┘     └──────────┘     └────┬─────┘
      ▲                                                      │
      └──────────────────────────────────────────────────────┘
                        loop until done
```

## Architecture

```
visuMark_Agent/
├── config/config.yaml        # YAML config with env-var interpolation
├── src/visumark_agent/
│   ├── agent/                # Agent core loop
│   │   ├── visumark.py       # VisuMarkAgent: observe→reason→act
│   │   └── prompts.py        # VLM prompt templates
│   ├── vlm/                  # Vision-language model interface
│   │   ├── base.py           # Abstract BaseVLM
│   │   └── openai.py         # OpenAI / compatible API backend
│   ├── environment/          # Browser automation
│   │   ├── browser.py        # Playwright wrapper
│   │   └── actions.py        # Action types (click, type, scroll, ...)
│   ├── som/                  # Set-of-Mark visual grounding
│   │   ├── extractor.py      # Extract interactive DOM elements
│   │   └── marker.py         # Draw labeled bounding boxes on screenshots
│   ├── parser/               # VLM output → structured Action
│   │   └── action_parser.py  # JSON + line-based action parser
│   └── utils/                # Config loader, logging
├── scripts/
│   ├── run_agent.py          # Single-task CLI entry point
│   └── evaluate.py           # Batch evaluation on task suites
└── data/                     # Screenshots, results, task files
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Set your API key
export OPENAI_API_KEY="sk-..."

# Run a single task
python scripts/run_agent.py \
  --task "Search for flights to Paris on Google Flights" \
  --url "https://www.google.com/travel/flights"

# Run with custom model
python scripts/run_agent.py \
  --task "Find the top HN post" \
  --url "https://news.ycombinator.com" \
  --model gpt-4o

# Batch evaluation
python scripts/evaluate.py --tasks data/tasks_example.json
```

## Configuration

Edit `config/config.yaml` or override via CLI flags:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `vlm.model` | Model name | `gpt-4o` |
| `vlm.base_url` | API proxy URL | `null` (OpenAI default) |
| `environment.headless` | Run Chromium headless | `true` |
| `som.max_elements` | Max annotated elements per page | `50` |
| `agent.max_steps` | Max actions per task | `30` |

## Key Papers

- [Set-of-Mark Prompting (Yang et al., 2023)](https://arxiv.org/abs/2310.11441)
- [Mind2Web (Deng et al., 2023)](https://arxiv.org/abs/2306.06070)
- [WebVoyager (He et al., 2024)](https://arxiv.org/abs/2401.13919)

## License

MIT
