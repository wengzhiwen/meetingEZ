# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指引。

**沟通语言约定：始终使用简体中文与用户交流。**

## 常用命令

```bash
# 启动 Web 应用（开发模式）
python run.py                          # Flask 开发服务器，监听 0.0.0.0:5090

# 运行 CLI Agent
python -m meeting_agent run --project <项目名> --meeting <会议目录名>
python -m meeting_agent status --project <项目名>

# 生产服务器
gunicorn -w 2 -b 0.0.0.0:5090 run:app

# 代码格式化
yapf -ir meeting_agent/ app/

# 代码检查
pylint meeting_agent/ app/
```

虚拟环境：`venv/bin/python`（激活命令：`source venv/bin/activate`）。

## 架构概览

MeetingEZ 是一个双模式会议智能系统，Web 端和 CLI 共享数据层：

```
┌─────────────────────────────────────────────────────────┐
│  Web GUI（Flask SPA）                                    │
│  app/routes.py → app/workspace_service.py → templates/  │
│  app/static/js/workspace/（原生 JS 模块）                │
│  ├── 实时转写：OpenAI Realtime API via WebRTC            │
│  └── 工作台：项目/会议增删改查、术语表、文件管理          │
├─────────────────────────────────────────────────────────┤
│  CLI Agent（meeting_agent/）                              │
│  __main__.py → cmd_run() 编排处理管线：                  │
│  ASR 路由器 → LLM 客户端 → 记忆写入器 → 术语管理        │
├─────────────────────────────────────────────────────────┤
│  共享层：meeting_agent/models.py, meeting_agent/config   │
└─────────────────────────────────────────────────────────┘
```

### ASR 管线

`meeting_agent/asr/router.py`（`ASRRouter`）编排两个 ASR 引擎：
1. **VibeVoice**（首选，`vibevoice_engine.py`）— 本地 vLLM 部署，支持说话人分离
2. **智谱 ASR**（降级，`engine.py`）— 云端 API，无说话人信息

VibeVoice 失败时，路由器将 `_asr_state.json` 写入会议目录，进入指数退避。用户可通过 Web GUI 立即重试（`/asr/retry`）或降级到智谱（`/asr/fallback`）。

### 会议处理流程

通过 Web GUI 或 CLI 触发。在后台线程中运行（`routes.py` 中的 `_run_meeting_process_async`），该线程以子进程方式执行 `python -m meeting_agent run`：

```
音频文件 → ASR（VibeVoice/智谱） → transcript.json
                                       ↓
transcript.json + 上下文 → LLM（GPT） → GPTAnalysisResult
                                       ↓
                          MemoryWriter → minutes.md, actions.md, timeline.md, context.md
```

### 状态与持久化

所有状态以文件形式存储在项目/会议目录中：
- `_meeting.json` — 会议元数据（日期、类型、参会人、语言）
- `transcript.json` — ASR 输出，每个片段含可选的 `speaker` 字段
- `_asr_state.json` — ASR 重试/降级状态
- `_processing.lock` / `_processing.error` — 处理状态（供 Web GUI 轮询）
- `context.md`, `actions.md`, `timeline.md` — 跨会议累积的项目记忆
- `_glossary.json` / `_glossary.pending.json` — 术语表（已确认/待审核/已拒绝三种状态）
- `_project.json`, `_people.json` — 项目配置

### 配置

`meeting_agent/config.py` — `Settings(BaseSettings)` 从 `.env` 加载配置。所有设置项都有合理默认值。通过 `Config()` 包装 `Settings` 实例化。

### 前端

原生 JS SPA，位于 `app/static/js/workspace/`。组件为 ES 模块。`api.js` 封装所有 `/api/workspace/` 调用。无需构建步骤。

## 关键约定

- **会议目录命名**：`projects/<项目>/` 下采用 `YYYY-MM-DD_标题` 格式
- **元数据文件**：以 `_` 为前缀（如 `_meeting.json`、`_project.json`）
- **术语辨析**："转写"（transcription）= ASR 输出；"纪要"（minutes）= AI 生成的会议摘要
- **语言模式**：`single_primary`（单语言）vs `bilingual`（双语，含翻译）
- **代码风格**：yapf + pep8 基准，88 列宽限制，4 空格缩进（详见 `pyproject.toml`）
- **LLM 提示词**：`meeting_agent/llm/prompts.py` — `PromptBuilder` 类提供模板方法。VibeVoice 的说话人信息以 `[HH:MM:SS] Speaker X: 文本` 格式注入，但提示词中明确标注仅供参考、不完全可靠
