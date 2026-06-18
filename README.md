# VisuMark Agent

基于 VLM 视觉大模型 + Set-of-Mark（SoM）视觉标记的 Web 自动化智能体。

给定自然语言任务和起始网址，Agent 自动打开浏览器、截取页面、为可交互元素绘制编号边框、将标注截图发给 VLM 决策、执行动作、验证效果，循环直到任务完成。

```
浏览器截图 → SoM 标注（编号边框）→ VLM 推理（看图决策）→ 解析动作 → 执行 → 截图验证 → 循环
```

---

## 快速开始

```bash
# 安装依赖
pip install -e .
playwright install chromium

# 配置 API Key（默认使用通义千问，也可以直接写在 config/config.yaml 里）
# Linux / macOS
export DASHSCOPE_API_KEY="sk-..."

# Windows PowerShell
$env:DASHSCOPE_API_KEY = "sk-..."

# 启动 Web 界面
python scripts/cli.py serve --port 8000
```

浏览器打开 `http://localhost:8000`，输入任务描述和目标网址即可开始。

Web 界面支持：实时步骤流（感知 → 推理 → 执行 → 验证四阶段进度）、CDP 浏览器实时投屏、前后截图对比、CAPTCHA / 登录暂停并手动接管、供应商和模型自由切换。

| 快捷键 | 操作 |
|--------|------|
| `Enter` | 发送任务 |
| `Ctrl + B` | 切换历史侧边栏 |
| `Ctrl + M` | 切换截图面板 |
| `Escape` | 关闭截图灯箱 |

---

## 命令行使用

### 实时执行任务

```bash
# 基本用法
python scripts/cli.py run -t "搜索去巴黎的机票" -u "https://www.bing.com"

# 显示浏览器窗口
python scripts/cli.py run -t "搜索去巴黎的机票" -u "https://www.bing.com" --show-browser

# 指定供应商和模型
python scripts/cli.py run -t "订酒店" -u "https://www.booking.com" \
    --provider openai --model gpt-4o

# Claude
python scripts/cli.py run -t "查天气" -u "https://www.bing.com" \
    --provider anthropic --model claude-sonnet-4-6

# 本地模型（Ollama）
python scripts/cli.py run -t "搜索资料" -u "https://www.bing.com" \
    --provider local --model qwen3-vl:8b --base-url http://localhost:11434/v1
```

### Mind2Web 评测

```bash
# SoM 视觉模式（截图 + 编号标注 → VLM）
python scripts/cli.py evaluate --split test_cross_task --num 10 --mode som

# HTML 文本模式（候选元素列表 → VLM）
python scripts/cli.py evaluate --split test_cross_task --num 10 --mode html

# HTML + 截图（VLM 同时看文本和页面截图）
python scripts/cli.py evaluate --split test_cross_task --num 10 --mode html --html-screenshot

# 启用 BERT 语义排序
python scripts/cli.py evaluate --split test_cross_task --num 10 --mode html --rank

# 指定供应商
python scripts/cli.py evaluate --split test_cross_domain --num 50 \
    --provider openai --model gpt-4o
```

**Split 选项：**

| `--split` | 含义 | 任务数 |
|-----------|------|--------|
| `test_cross_task` | 新任务，已知网站 | 252 |
| `test_cross_website` | 新网站，已知领域 | 177 |
| `test_cross_domain` | 新领域 | 912 |

**评测指标（Mind2Web 论文标准）：**

| 指标 | 含义 |
|------|------|
| Element Accuracy | 正确选中目标元素的比例 |
| Operation F1 | 操作类型和参数值的 token 级 F1 |
| Step Success Rate | 元素和操作均正确的步骤比例 |
| Task Success Rate | 全部步骤成功的任务比例 |

评测支持**断点续跑**：中断后重新运行会自动从 checkpoint 恢复。

### SoM 独立 API

```bash
GET /api/som-tree?url=<网址>&annotate=true&max_elements=50
```

返回元素列表（id、tag、text、bbox）+ base64 标注截图。

---

## 项目结构

```
├── config/
│   ├── config.yaml              # 主配置文件
│   └── models.yaml              # 模型注册表（各供应商支持的模型列表）
├── src/
│   ├── visumark/                # ★ 核心包
│   │   ├── core/                # Agent ReAct 主循环 + 全部数据类型定义
│   │   ├── perception/          # 感知层：SoM 视觉标注 + HTML 文本候选
│   │   ├── reasoning/           # 推理层：4 个 VLM 供应商 + 工厂 + 提示词模板
│   │   ├── environment/         # 浏览器环境：Playwright 在线 + HTML 离线快照
│   │   ├── action/              # 动作层：JSON/文本解析 + SoM ID → 浏览器操作
│   │   ├── evaluation/          # Mind2Web 评测：对比器 + 指标计算 + 结果输出
│   │   ├── dataset/             # 数据集：TaskInstance 定义 + Mind2Web 加载器
│   │   └── utils/               # 工具：YAML 配置 + 图片处理 + 日志
│   ├── visumark_agent/          # 旧版包（兼容保留）
│   └── web/                     # FastAPI + WebSocket 后端 + 前端静态页面
├── scripts/
│   ├── cli.py                   # ★ 统一 CLI 入口（run / evaluate / serve）
│   ├── run_agent.py             # 旧版命令行入口
│   ├── run_web.py               # 旧版 Web 入口
│   └── evaluate.py              # 旧版评测入口
├── data/
│   ├── screenshots/             # 步骤截图（任务结束后自动清理）
│   └── results/                 # 评测结果 JSON + checkpoint
├── test/                        # Mind2Web 评测数据集
└── pyproject.toml
```

---

## 架构

### 整体流程

```
                    ┌─────────────────────────────────┐
                    │         Agent ReAct 循环          │
                    │                                  │
  ┌──────────┐     ┌───────────┐     ┌──────┐     ┌──────────┐
  │Perception│ ──→ │ Reasoning │ ──→ │Action│ ──→ │ Verify   │
  │ 感知页面  │     │ VLM 决策  │     │执行动作│     │ 验证效果  │
  └──────────┘     └───────────┘     └──────┘     └──────────┘
       ↑                                               │
       │              最近 5 步历史                       │
       └─────────── Environment 浏览器 ◀────────────────┘
                        │
                  失败 → 回退 → 重试
```

### 感知层

两种模式，通过 `config.yaml` 的 `agent.mode` 或 CLI `--mode` 切换：

- **SoM 模式** — 截图 → DOM + Accessibility Tree 融合提取可交互元素 → 绘制彩色编号边框 → VLM 看图选号
- **HTML 模式** — 解析 Mind2Web 候选元素列表 → 提取属性文本 → 编号 → VLM 文本选择（可选 BERT 语义排序）

### 推理层

工厂模式自动选择供应商，统一接口：

| 供应商 | 模型示例 | 实现 |
|--------|---------|------|
| `qwen` | qwen3-vl-8b-instruct | 继承 OpenAIReasoner，默认指向 DashScope |
| `openai` | gpt-4o | OpenAI 兼容 API |
| `anthropic` | claude-sonnet-4-6 | Anthropic 原生 Messages API |
| `local` | qwen3-vl:8b | Ollama / vLLM 等 OpenAI 兼容端点 |

每个供应商实现两个方法：`reason()` 决策下一步动作，`verify()` 对比前后截图验证效果。

### 动作层

支持 12 种动作：`CLICK` · `TYPE` · `SELECT` · `SCROLL` · `HOVER` · `PRESS` · `GOTO` · `WAIT` · `ANSWER` · `FAIL` · `CAPTCHA` · `LOGIN`

VLM 返回的 SoM 编号通过 DOMBridge 映射为 Playwright CSS 选择器后执行。点击采用三级兜底：JS click → Playwright click → 坐标点击。

### 验证层

每次非终态动作执行后自动验证：

1. 像素级 diff 相同 → 直接判定无效，跳过 VLM 调用
2. DOM 扫描可见错误弹窗 / 校验提示
3. VLM 对比前后截图判断效果

验证失败后自动回退（关闭弹窗、返回原页面）并用替代动作重试。

### 登录 / CAPTCHA 检测

两阶段程序化检测（先用 JS 判断用户是否已登录来排除误报，再统计登录页正向信号打分），检测到后暂停等待人工介入，按域名记忆避免重复触发。

---

## 配置

`config/config.yaml`，支持 `${ENV}` 环境变量替换。优先级：Web 界面设置 > CLI 参数 > 配置文件。

```yaml
agent:
  mode: som                     # som（视觉标注）| html（文本候选）
  max_steps: 30
  verify_actions: true          # 动作后自动验证

perception:
  som:
    max_elements: 200
    use_accessibility_tree: true
  html:
    max_candidates: 50

reasoning:
  provider: qwen                # openai | anthropic | qwen | local
  model: qwen3-vl-8b-instruct
  api_key: "${DASHSCOPE_API_KEY}"
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  temperature: 0.0
  max_tokens: 2048

environment:
  headless: false
  viewport_width: 1280
  viewport_height: 720
  timeout: 30000

evaluation:
  output_dir: ./data/results
```

---

## 参考文献

- [Set-of-Mark Prompting (Yang et al., 2023)](https://arxiv.org/abs/2310.11441)
- [Mind2Web (Deng et al., 2023)](https://arxiv.org/abs/2306.06070)
- [WebVoyager (He et al., 2024)](https://arxiv.org/abs/2401.13919)

## License

MIT
