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
│   └── utils/                # 工具函数（配置加载、日志）
├── scripts/
│   ├── run_agent.py          # 单任务执行入口
│   └── evaluate.py           # 批量评测脚本
└── data/                     # 截图、评测结果、任务文件
```

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 设置 API Key
export OPENAI_API_KEY="sk-..."

# 运行单条任务
python scripts/run_agent.py \
  --task "在 Google Flights 上搜索去巴黎的机票" \
  --url "https://www.google.com/travel/flights"

# 使用自定义模型或代理
python scripts/run_agent.py \
  --task "找到 Hacker News 今天的头条" \
  --url "https://news.ycombinator.com" \
  --model gpt-4o \
  --base-url "https://your-api-proxy.com/v1"

# 批量评测
python scripts/evaluate.py --tasks data/tasks_example.json
```

## 配置说明

编辑 `config/config.yaml` 或通过命令行参数覆盖：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `vlm.model` | 模型名称 | `gpt-4o` |
| `vlm.base_url` | API 代理地址 | `null`（直连 OpenAI） |
| `environment.headless` | 浏览器无头模式 | `true` |
| `som.max_elements` | 每页最多标注元素数 | `50` |
| `agent.max_steps` | 每个任务最大步数 | `30` |

## 关键参考文献

- [Set-of-Mark Prompting — 用视觉标记释放 GPT-4V 的定位能力 (Yang et al., 2023)](https://arxiv.org/abs/2310.11441)
- [Mind2Web — 迈向通用 Web Agent (Deng et al., 2023)](https://arxiv.org/abs/2306.06070)
- [WebVoyager — 基于多模态大模型的端到端 Web Agent (He et al., 2024)](https://arxiv.org/abs/2401.13919)

## License

MIT
