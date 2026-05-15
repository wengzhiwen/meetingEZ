# AGENTS.md

本文件是本仓库给 Codex/通用代码 Agent 使用的工作规则。若与用户当前明确要求冲突，以用户要求为准。

## 基本协作

- 始终使用简体中文回复。
- 先读当前代码和文档，再改文件；不要基于旧记忆假设项目状态。
- 优先做窄范围、可验证的修改，避免顺手重构。
- 工作区可能有用户未提交改动；不要还原、覆盖或清理与任务无关的变更。
- 不要删除会议数据、项目数据、音频、转写稿、纪要或记忆文件，除非用户明确要求。

## 项目事实

- Web 应用入口是 Flask：`run.py` -> `app/__init__.py` -> `app/routes.py`。
- 控制台入口是 `/`，实时转写页是 `/realtime`。
- Web 实时转写使用 OpenAI Realtime API WebRTC transcription-only session。
- 实时转写默认模型是 `gpt-realtime-whisper`。
- Realtime client secret 由后端 `/api/realtime-session` 签发。
- 前端不保存 OpenAI 标准 API Key。
- 离线会议处理由 `meeting_agent` CLI 完成。
- 离线 ASR 首选 OpenRouter Chirp 3，失败后降级智谱；VibeVoice 代码保留但当前不参与路由。

## 常用命令

```bash
source venv/bin/activate
python run.py
gunicorn -w 2 -b 0.0.0.0:5090 run:app
python -m meeting_agent status --project <项目名>
python -m meeting_agent run --project <项目名> --meeting <会议目录名>
yapf -ir meeting_agent/ app/
pylint meeting_agent/ app/
python3 -m py_compile app/routes.py
```

项目没有前端构建步骤。

## 关键文件

- `app/routes.py`：Flask 路由、Realtime session 签发、翻译代理、后台处理触发。
- `app/workspace_service.py`：项目、会议、上下文包、术语和文件管理服务。
- `app/static/js/app.js`：实时页主逻辑。
- `app/static/js/realtime-transcription.js`：WebRTC Realtime 封装和事件状态机。
- `app/static/js/workspace/`：控制台 SPA 原生 JS 模块。
- `meeting_agent/asr/router.py`：离线 ASR provider 路由。
- `meeting_agent/config.py`：Pydantic settings 与目录配置。
- `meeting_agent/llm/client.py` 和 `meeting_agent/llm/prompts.py`：纪要生成。
- `meeting_agent/memory/`：项目记忆写入。

## 修改实时转写时

- 同步检查 `TRANSCRIPTION_MODEL` 默认值、前端默认 model、`env.example` 和当前文档。
- 保持 session 类型为 `transcription`，不要改成对话模式。
- 保持后端签发 client secret，不要把 API Key 暴露给浏览器。
- 当前 Realtime 关键配置是 24kHz PCM、`near_field` noise reduction、`gpt-realtime-whisper` 的 `delay: "low"`。
- `gpt-realtime-whisper` 当前不接受 `turn_detection` 字段；不要发送 `server_vad` 或对话模式的 `semantic_vad`。
- 事件处理围绕 `conversation.item.input_audio_transcription.delta` 和 `completed`。
- Realtime Translation Beta 是旁路能力，入口为 `/api/realtime-translation-session` 和 `app/static/js/realtime-translation.js`；不要把它改成默认主链路，除非用户明确要求。

## 修改离线 Agent 时

- 区分“转写”与“纪要”：`transcript.json` 是转写，`minutes.md` 是纪要。
- 不要把 Web 实时转写模型误用于离线文件 ASR，除非用户明确要求迁移。
- ASR 状态写在 `_asr_state.json`，中间进度写在 `transcript.json.progress`。
- 处理流程锁和错误文件用于 Web 轮询，修改时要保持兼容。

## 文档规则

- 当前事实优先更新 `README.md`、`docs/API.md`、`docs/AUDIO_ARCHITECTURE.md`、`docs/USAGE.md`、`docs/realtime-transcription-best-practices.md`。
- `docs/FEATURE_*.md` 和旧设计文档可能记录历史方案，不要因为局部代码变更而大规模重写。
- 修改模型或 API 用法时，如果信息可能过期，先核对官方文档。

## 验证建议

- Python 路由改动：至少运行 `python3 -m py_compile app/routes.py`。
- CLI Agent 改动：优先运行相关 `python -m meeting_agent ...` 命令做烟测。
- 前端改动：检查浏览器控制台相关路径、事件名、DOM id 是否一致；本项目没有自动前端测试。
- 文档改动：至少用 `rg` 搜索旧模型名、旧 endpoint 或旧流程描述，确认当前文档没有明显矛盾。
