# VisuMark Agent

基于 **VLM（视觉大语言模型）+ Set-of-Mark（SoM）视觉标记** 的 Web 自动化智能体。

## 项目简介

VisuMark Agent 将大视觉语言模型（GPT-4o、Qwen-VL 等）与 Set-of-Mark 提示技术相结合，构建一个通用的 Web Agent。核心思路是：先将网页截图中所有可交互元素用编号的边界框标注出来，再将这张标注后的截图发给 VLM 进行推理，模型通过引用元素编号来精确地定位和操作界面元素。

```
┌──────────┐     ┌──────────────┐     ┌──────────┐     ┌──────────┐
│  浏览器   │────▶│  SoM 标注器   │────▶│   VLM    │────▶│  执行动作  │
│  截图     │     │ (编号边界框)  │     │  推理决策  │     │          │
└──────────┘     └──────────────┘     └──────────┘     └────┬─────┘
      ▲                                                      │
      └──────────────────────────────────────────────────────┘
                        循环直到任务完成
```

## 项目结构

```
visuMark_Agent/
├── config/config.yaml        # YAML 配置文件（支持 ${ENV} 环境变量替换）
├── src/visumark_agent/
│   ├── agent/                # Agent 核心循环
│   │   ├── visumark.py       # VisuMarkAgent：观察→推理→执行 主循环
│   │   └── prompts.py        # VLM 提示词模板
│   ├── vlm/                  # 视觉语言模型接口
│   │   ├── base.py           # 抽象基类 BaseVLM
│   │   └── openai.py         # OpenAI 兼容 API（支持 GPT-4o、代理转发等）
│   ├── environment/          # 浏览器自动化
│   │   ├── browser.py        # Playwright 封装（启动、截图、执行动作）
│   │   └── actions.py        # 动作类型定义（点击、输入、滚动、回答等）
│   ├── som/                  # Set-of-Mark 视觉标记
│   │   ├── extractor.py      # 从 DOM 提取所有可交互元素
│   │   └── marker.py         # 在截图上绘制带编号的边界框
│   ├── parser/               # 动作解析器
│   │   └── action_parser.py  # 将 VLM 文本输出解析为结构化 Action
│   ├── web/                  # Web UI 界面
│   │   ├── server.py         # FastAPI 后端 + WebSocket 实时推送
│   │   └── static/           # 前端静态资源（HTML/CSS/JS）
│   └── utils/                # 工具函数（配置加载、日志）
├── scripts/
│   ├── run_agent.py          # CLI 单任务执行入口
│   ├── run_web.py            # Web UI 启动入口
│   └── evaluate.py           # 批量评测脚本
└── data/                     # 截图、评测结果、任务文件
```

## 环境准备

### 1. 安装依赖

```bash
# 创建虚拟环境（推荐）
python -m venv .venv
source .venv/bin/activate   # Linux / macOS
# .venv\Scripts\activate    # Windows

# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Chromium 浏览器（Playwright 需要）
playwright install chromium
```

### 2. 配置 API Key

```bash
# Linux / macOS
export OPENAI_API_KEY="sk-..."

# Windows (PowerShell)
$env:OPENAI_API_KEY = "sk-..."

# Windows (CMD)
set OPENAI_API_KEY=sk-...
```

也可以编辑 `config/config.yaml` 直接写入 `api_key`，或将 Key 填入 Web UI 的高级设置面板。

---

## 使用方式一：Web UI（推荐）

提供**玻璃态暗色主题**聊天式交互界面，可实时查看 Agent 每一步的截图、动作与推理过程。前端支持 WebSocket 自动重连、Toast 通知、打字指示器、键盘快捷键等交互增强。

### 启动 Web 服务

```bash
python scripts/run_web.py

# 可选参数
python scripts/run_web.py --port 8080 --host 127.0.0.1 --reload
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--host` | 绑定地址 | `0.0.0.0` |
| `--port` / `-p` | 绑定端口 | `8000` |
| `--reload` | 开发模式自动重载 | 关闭 |

启动后访问 `http://localhost:8000`，API 文档见 `http://localhost:8000/docs`。

### 使用界面

1. 浏览器打开 `http://localhost:8000`
2. 在底部输入框中描述你想完成的任务，例如：「搜索从北京到上海的航班」
3. 输入目标网址，例如：`https://www.google.com/travel/flights`
4. 点击发送按钮 **▶**（或按 `Enter`），Agent 开始执行
5. 每一步都会实时展示：
   - 📍 步骤编号 + 🎯 动作标签（点击/输入/滚动等）
   - 🖼️ 带 SoM 标注的截图（点击可放大）
   - 💬 VLM 原始输出（折叠面板）
6. 任务完成后显示最终结果 + 总步数

### 高级设置

点击输入框旁的 **⚙** 齿轮图标，可展开配置面板：

| 设置项 | 说明 | 默认值 |
|--------|------|--------|
| 模型 | VLM 模型名称 | `gpt-4o` |
| API Key | API 密钥（留空使用环境变量） | — |
| API Base URL | 自定义 API 代理地址 | — |
| 最大步数 | 单次任务最多执行步数 | `30` |
| 无头模式 | 后台运行浏览器（不显示窗口） | ✅ |

> 所有高级设置会自动保存到浏览器 `localStorage`，侧边栏折叠状态也会持久化。

### 键盘快捷键

| 快捷键 | 操作 |
|--------|------|
| `Enter` | 发送任务 |
| `Ctrl` + `Enter` | 发送任务 |
| `Ctrl` + `K` | 聚焦任务输入框 |
| `Ctrl` + `B` | 切换侧边栏 |
| `Escape` | 关闭截图灯箱 |

---

## SoM 树独立 API

除了通过 Agent 任务流程使用 SoM 之外，也可以**直接调用 API 提取任意网页的 SoM 元素树和标注截图**，无需启动完整 Agent。

### 端点

```
GET /api/som-tree?url=<网址>&annotate=<true|false>&max_elements=<N>&headless=<true|false>
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `url` | string | `https://example.com` | 目标网页地址 |
| `annotate` | bool | `true` | 是否返回带编号边界框的标注截图 |
| `max_elements` | int | `50` | 最多提取的可交互元素数量（上限 100） |
| `headless` | bool | `true` | 是否使用无头浏览器 |

### 返回格式

```json
{
  "url": "https://example.com",
  "title": "Example Domain",
  "viewport": { "width": 1280, "height": 720 },
  "elements": [
    {
      "id": 1,
      "tag": "a",
      "text": "More information...",
      "bbox": [0.15, 0.42, 0.18, 0.04]
    }
  ],
  "total_elements": 12,
  "annotated_screenshot": "iVBORw0KGgo... (base64 PNG)"
}
```

每个元素字段说明：

| 字段 | 说明 |
|------|------|
| `id` | 元素编号（对应 SoM 截图中标注的数字） |
| `tag` | HTML 标签（button, a, input, select, textarea 等） |
| `text` | 可见文本或 aria-label / placeholder（最多 80 字符） |
| `bbox` | 归一化边界框 `(x, y, width, height)`，值域 0~1 |

### 使用示例

```bash
# 获取 HN 首页的 SoM 树 + 标注截图
curl "http://localhost:8000/api/som-tree?url=https://news.ycombinator.com" | python -m json.tool

# 仅获取元素列表，不返回截图
curl "http://localhost:8000/api/som-tree?url=https://example.com&annotate=false"

# 限制最多 10 个元素
curl "http://localhost:8000/api/som-tree?url=https://github.com&max_elements=10"
```

也可以在浏览器中直接打开 Swagger 文档进行交互式测试：`http://localhost:8000/docs`

---

## 使用方式二：命令行（CLI）

适合脚本调用和批量评测场景。

### 单任务执行

```bash
python scripts/run_agent.py \
  --task "在 Google Flights 上搜索去巴黎的机票" \
  --url "https://www.google.com/travel/flights"

# 使用自定义模型或代理
python scripts/run_agent.py \
  --task "找到 Hacker News 今天的头条" \
  --url "https://news.ycombinator.com" \
  --model gpt-4o \
  --base-url "https://your-api-proxy.com/v1"

# 显示浏览器窗口（非无头模式）
python scripts/run_agent.py \
  --task "..." --url "..." \
  --show-browser

# 输出详细日志
python scripts/run_agent.py \
  --task "..." --url "..." \
  --verbose
```

| 参数 | 简写 | 说明 | 默认值 |
|------|------|------|--------|
| `--task` | `-t` | 任务描述（必填） | — |
| `--url` | `-u` | 起始 URL（必填） | — |
| `--config` | `-c` | YAML 配置文件路径 | `config/config.yaml` |
| `--model` | `-m` | VLM 模型名称 | `gpt-4o` |
| `--api-key` | — | API Key 覆盖 | 环境变量 |
| `--base-url` | — | API 代理地址覆盖 | `null` |
| `--max-steps` | — | 最大步数覆盖 | `30` |
| `--screenshot-dir` | — | 截图保存目录 | `./data/screenshots` |
| `--output-dir` | `-o` | 运行结果输出目录 | — |
| `--verbose` | `-v` | 启用 DEBUG 日志 | 关闭 |

### 批量评测

```bash
python scripts/evaluate.py --tasks data/tasks_example.json
```

---

## 配置文件

编辑 `config/config.yaml` 修改默认行为：

```yaml
vlm:
  provider: openai
  model: gpt-4o
  api_key: ${OPENAI_API_KEY}   # 支持环境变量替换
  base_url: null
  max_tokens: 4096
  temperature: 0.0
  timeout: 60

environment:
  headless: true
  viewport_width: 1280
  viewport_height: 720
  timeout: 30000

som:
  enabled: true
  label_font_size: 14
  bounding_box_color: "#FF0000"
  show_labels: true
  max_elements: 50

agent:
  max_steps: 30
  step_timeout: 60
  retry_on_error: true
  max_retries: 3
  screenshot_dir: ./data/screenshots
```

CLI 参数会覆盖配置文件中的对应值，Web UI 高级设置面板中的值优先级最高。

---

## 关键参考文献

- [Set-of-Mark Prompting — 用视觉标记释放 GPT-4V 的定位能力 (Yang et al., 2023)](https://arxiv.org/abs/2310.11441)
- [Mind2Web — 迈向通用 Web Agent (Deng et al., 2023)](https://arxiv.org/abs/2306.06070)
- [WebVoyager — 基于多模态大模型的端到端 Web Agent (He et al., 2024)](https://arxiv.org/abs/2401.13919)

## License

MIT
