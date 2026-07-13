# CLAUDE.md

本文件为 Claude Code 在本仓库中工作时提供指引。

**沟通语言约定：始终使用简体中文与用户交流。**

## 项目现状

MeetingEZ 是一个轻量的会议实时转写、按需翻译和会后纪要工具。当前 Web 应用已经收敛为“控制台 + 实时页”双入口：

- 控制台 `/`：项目选择、会议创建、会议文件/音频/术语/背景管理。
- 实时页 `/realtime`：采集麦克风或标签页音频，通过 OpenAI Realtime API WebRTC transcription session 做实时转写。
- 后端只签发短期 Realtime `client secret`，浏览器不保存标准 API Key。
- Web 实时页只使用 `gpt-realtime-translate`，不提供翻译方式选择。
- 仓库仍保留 CLI 会议纪要 Agent，用于离线音频转写、纪要生成和项目记忆维护。

## 常用命令

```bash
# 开发启动
source venv/bin/activate
python run.py

# 生产启动
gunicorn -w 2 -b 0.0.0.0:5090 run:app

# CLI Agent
python -m meeting_agent status --project <项目名>
python -m meeting_agent run --project <项目名> --meeting <会议目录名>

# 格式化与检查
yapf -ir meeting_agent/ app/
pylint meeting_agent/ app/

# 基础语法校验
python3 -m py_compile app/routes.py
```

默认 Web 地址：`http://localhost:5090`。虚拟环境通常位于 `venv/`。

## 运行时关键配置

配置来自 `.env`，主要入口在 `meeting_agent/config.py` 和 `app/routes.py`。

- `OPENAI_API_KEY`：Realtime transcription/translation client secret 签发、纪要生成。
- `TRANSCRIPTION_MODEL`：Web 实时转写模型，默认 `gpt-realtime-whisper`。
- `REALTIME_TRANSLATION_MODEL`：Web 实时翻译模型，默认 `gpt-realtime-translate`。
- `REALTIME_TRANSLATION_INPUT_MODEL`：实时翻译的输入转写模型，默认 `gpt-realtime-whisper`。
- `ACCESS_CODE`：可选访问码；为空时不启用登录保护。
- `OPENROUTER_API_KEY`：离线文件 ASR 的首选 OpenRouter Chirp 3。
- `ZHIPU_API_KEY`：OpenRouter ASR 不可用时的降级 ASR。

## 架构概览

```
Web GUI
  app/routes.py
  app/workspace_service.py
  templates/
  app/static/js/app.js
  app/static/js/realtime-transcription.js
  app/static/js/workspace/*.js

CLI Agent
  meeting_agent/__main__.py
  meeting_agent/asr/router.py
  meeting_agent/llm/client.py
  meeting_agent/memory/*.py

共享模型与配置
  meeting_agent/models.py
  meeting_agent/config.py
  meeting_agent/glossary/*.py
```

## Web 实时转写链路

当前实时页固定使用 Realtime Translation WebRTC：

1. 前端为两种目标语言分别请求 `POST /api/realtime-translation-session`。
2. 后端使用 `OPENAI_API_KEY` 签发两个 translation client secret。
3. 前端通过 `/v1/realtime/translations/calls` 建立两路 WebRTC 连接。
4. 第 0 路 `session.input_transcript.*` 作为权威混合原文。
5. 两路 `session.output_transcript.*` 分别输出第一语言和第二语言译文。
6. input/output delta 不保证携带 `item_id`，前端按当前流累积并以 done 或静音超时收尾。

不要把标准 API Key 放入浏览器端代码。不要回退到旧的浏览器 WebSocket + Base64 PCM 推流实现。

### Realtime Translation

- 调用 `POST /api/realtime-translation-session` 签发 translation client secret。
- 前端类为 `app/static/js/realtime-translation.js`。
- WebRTC endpoint 为 `/v1/realtime/translations/calls`。
- 模型默认 `gpt-realtime-translate`，输入转写模型默认 `gpt-realtime-whisper`。
- 为第一语言和第二语言各开一路 translation session，用于混合会议音频的互译效果对比。
- 第 0 路兼任权威源转写：其 `session.input_transcript` 自带源语言转写并自动检测语言，喂给原文 pane，省去额外转写 session。
- input/output transcript delta 不保证携带 `item_id`；前端按当前流累积，以 `.done` 或 1.5 秒静音兜底定格。
- translation session 不支持 `instructions`/`prompt`，实时页不提供术语表或提示词注入。

## 离线会议处理链路

CLI Agent 处理项目/会议目录中的录音文件：

```
音频文件
  -> ASRRouter
  -> transcript.json
  -> GPT 纪要生成
  -> minutes.md / actions.md / timeline.md / context.md
```

当前 ASR 路由器 `meeting_agent/asr/router.py`：

- 首选 OpenRouter Chirp 3：`meeting_agent/asr/openrouter_engine.py`
- 降级智谱 ASR：`meeting_agent/asr/engine.py`
- VibeVoice 文件仍保留，但当前不实例化、不路由调用。

## 数据与状态文件

项目和会议状态以文件形式持久化：

- `_project.json`：项目配置。
- `_people.json`：项目成员。
- `_meeting.json`：会议元数据。
- `transcript.json`：正式转写稿。
- `transcript.json.progress`：ASR 中间进度。
- `_asr_state.json`：ASR provider、状态、错误。
- `_processing.lock` / `_processing.error` / `_processing.progress`：后台处理状态。
- `_glossary.json` / `_glossary.pending.json`：术语表。
- `minutes.md` / `actions.md` / `timeline.md` / `context.md`：纪要与项目记忆。

术语约定见 `docs/TERMINOLOGY.md`：`transcription` 是转写，`minutes` 是 AI 生成的纪要。

## 前端约定

- 无构建步骤，直接使用原生 JS 和 ES modules。
- 实时页主逻辑：`app/static/js/app.js`。
- Realtime WebRTC 封装：`app/static/js/realtime-transcription.js`。
- 控制台 SPA：`app/static/js/workspace/`。
- 控制台样式：`app/static/css/workspace-spa.css`。
- 实时页样式：`app/static/css/style.css`。

修改前端时保持现有原生 JS 模块风格，不引入打包器或框架。

## 文档维护约定

优先更新这些当前文档：

- `README.md`
- `docs/API.md`
- `docs/AUDIO_ARCHITECTURE.md`
- `docs/USAGE.md`
- `docs/realtime-transcription-best-practices.md`
- `docs/meeting_minutes_agent.md`

`docs/FEATURE_*.md` 和旧设计文档可能包含历史方案，除非用户明确要求，不要把一次运行时改动扩大为大规模历史文档重写。

## 代码风格

- Python：遵循 `pyproject.toml` 中 yapf 配置，88 列，4 空格。
- JavaScript：保持当前原生模块风格，少做全局状态扩散。
- 变更运行时模型时，同时检查 `app/routes.py`、前端默认值、`env.example` 和当前文档。
- 不要删除或覆盖用户数据目录、会议目录、项目目录和生成的纪要文件，除非用户明确要求。
