# VisuMark Agent

基于 **VLM（视觉大语言模型）+ Set-of-Mark（SoM）视觉标记** 的 Web 自动化智能体。

## 工作原理

```
浏览器截图 → SoM 标注器（给可交互元素画编号边框）→ VLM 推理（看图决定下一步动作）→ 执行动作 → 循环
```

每步 5 个阶段：**标记 DOM → 提取元素+绘制标注 → VLM 推理 → 解析动作 → 执行+回调**

| 阶段 | 关键文件 | 核心函数 |
|------|---------|---------|
| 1. 标记 DOM | `environment/browser.py` | `tag_elements()` |
| 2. 提取+标注 | `som/extractor.py` `som/marker.py` | `extract()` `annotate()` |
| 3. VLM 推理 | `agent/visumark.py` | `vlm.generate(prompt, images)` |
| 4. 解析动作 | `parser/action_parser.py` | `parse(raw_text)` |
| 5. 执行动作 | `environment/browser.py` | `execute(action)` |

## 项目结构

```
├── config/config.yaml
├── src/visumark_agent/
│   ├── agent/        # Agent 核心循环 + 提示词
│   ├── vlm/          # VLM 接口（base.py + openai.py）
│   ├── environment/  # Playwright 浏览器 + 动作定义
│   ├── som/          # SoM 元素提取 + 标注绘制
│   ├── parser/       # VLM 输出 → Action 解析
│   ├── web/          # FastAPI + WebSocket + 前端
│   └── utils/        # 配置加载、日志
├── scripts/          # CLI / Web / 评测 入口
└── data/             # 截图、任务文件
```

## 快速开始

```bash
pip install -r requirements.txt
playwright install chromium

# Linux / macOS
export OPENAI_API_KEY="sk-..."

# Windows (PowerShell)
$env:OPENAI_API_KEY = "sk-..."

python scripts/run_web.py
# 打开 http://localhost:8000
```

## 使用方式

### Web UI

| 快捷键 | 操作 |
|--------|------|
| `Enter` | 发送任务 |
| `Ctrl` + `B` | 切换侧边栏 |
| `Ctrl` + `M` | 切换截图面板 |
| `Escape` | 关闭截图灯箱 |

### SoM 独立 API

```bash
GET /api/som-tree?url=<网址>&annotate=true&max_elements=50&headless=true
```

返回元素列表（id, tag, text, bbox）+ 标注截图 base64。

### 命令行

```bash
python scripts/run_agent.py -t "搜索去巴黎的机票" -u "https://www.google.com/travel/flights"
python scripts/evaluate.py --tasks data/tasks_example.json   # 批量评测
```

## 配置

`config/config.yaml` 支持 `${ENV}` 环境变量替换。CLI 参数覆盖配置文件，Web UI 设置优先级最高。

---

## 待完成功能

### VLM 多供应商支持
> 目前只实现了 `OpenAIVLM`，配置中 `qwen`/`local` 等选项未生效。

- [ ] **VLM 工厂模式** — 根据 `provider` 字段自动选择适配器
- [ ] **Qwen-VL** — 通义千问视觉模型
- [ ] **Claude** — Anthropic Claude 3.5/4 Sonnet/Opus
- [ ] **Gemini** — Google Gemini 2.5 Pro/Flash
- [ ] **本地模型** — Ollama / vLLM
- [ ] **前端供应商选择器** — 目前只有一个模型名输入框

### Agent 智能

- [ ] **步骤间记忆** — 每一步只传当前截图，VLM 不知道之前做过什么
- [ ] **思维链 (CoT)** — 让 VLM 先分析再输出动作，而非直接输出 JSON
- [ ] **自我纠错** — 动作失败后将错误反馈给 VLM 调整策略
- [ ] **失败兜底策略** — click 失败后尝试滚动再点击、坐标兜底等

### 浏览器环境

- [ ] **Cookie / 登录态持久化** — 跨任务保持浏览器会话
- [ ] **iframe 支持** — 提取和操作 iframe 内元素
- [ ] **弹窗处理** — 自动关闭 alert/confirm/cookie 弹窗
- [ ] **视口外元素发现** — 当前 SoM 只能看到视口内的元素

### 前端

- [ ] **历史记录** — 侧边栏历史列表目前是空壳
- [ ] **任务停止机制** — `stopTask()` 仅断开 WebSocket，浏览器进程可能残留
- [ ] **多轮对话** — 完成任务后可继续下达指令

### 测试与部署

- [ ] **单元测试** — `pytest` 已在依赖中但无测试文件
- [ ] **Docker 支持**
- [ ] **多会话管理**

## 参考文献

- [Set-of-Mark Prompting (Yang et al., 2023)](https://arxiv.org/abs/2310.11441)
- [Mind2Web (Deng et al., 2023)](https://arxiv.org/abs/2306.06070)
- [WebVoyager (He et al., 2024)](https://arxiv.org/abs/2401.13919)

## License

MIT
