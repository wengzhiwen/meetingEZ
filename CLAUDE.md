# CLAUDE.md

本文件为 Claude Code 在本仓库中工作时提供指引。

**沟通语言约定：始终使用简体中文与用户交流。**

## 项目现状

MeetingEZ 是一个轻量的会议实时转写、按需翻译和会后纪要工具。当前 Web 应用已经收敛为“控制台 + 实时页”双入口：

- 控制台 `/`：项目选择、会议创建、会议文件/音频/术语/背景管理。
- 实时页 `/realtime`：采集麦克风、标签页音频或 macOS 应用/系统音频（经本地采集器），通过 OpenAI Realtime API WebRTC session 做实时转写和实时翻译。
- 后端只签发短期 Realtime `client secret`，浏览器不保存标准 API Key。
- Web 实时页翻译只使用 `gpt-realtime-translate`，不提供翻译方式选择。
- 仓库仍保留 CLI 会议纪要 Agent，用于离线音频转写、纪要生成和项目记忆维护。

**全系统只用 OpenAI 一家模型供应商。** 不要重新引入 OpenRouter、智谱或 VibeVoice——
这些供应商的代码已在 2026-07-31 全部删除，不是"暂时屏蔽"。

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

- `OPENAI_API_KEY`：唯一凭据。Realtime client secret 签发、离线 ASR、纪要生成都用它。
- `TRANSCRIPTION_MODEL`：Web 实时转写模型，默认 `gpt-live-transcribe`。
- `TRANSCRIPTION_DELAY`：实时转写延迟档位 minimal/low/medium/high/xhigh，默认 `low`。
- `REALTIME_TRANSLATION_MODEL`：Web 实时翻译模型，默认 `gpt-realtime-translate`。
- `REALTIME_TRANSLATION_INPUT_MODEL`：实时翻译的输入转写模型，默认 `gpt-live-transcribe`。
- `OPENAI_ASR_MODEL`：离线文件 ASR 模型，默认 `gpt-transcribe`。
- `OPENAI_ASR_LANGUAGES`：离线 ASR 的预期输入语言（逗号分隔短码），留空自动检测。
- `ASR_CHUNK_SECONDS`：离线 ASR 分块时长，默认 120 秒。
- `ACCESS_CODE`：可选访问码；为空时不启用登录保护。

## 架构概览

```
Web GUI
  app/routes.py
  app/workspace_service.py
  templates/
  app/static/js/app.js
  app/static/js/realtime-transcription.js
  app/static/js/workspace/*.js

macOS 本地采集器（第三音源：其他应用/系统音频）
  native-capture/                     SwiftPM，无第三方依赖，详见其 README
  app/static/js/local-collector-client.js    浏览器侧 WS 客户端 + PCM→MediaStream 管线
  app/static/js/collector-pcm-worklet.js      AudioWorklet 环形缓冲

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

一场会议开 2 路 translation WebRTC session：

1. `POST /api/realtime-translation-session` 为两种目标语言各签发一个 client secret，
   通过 `/v1/realtime/translations/calls` 建连。
2. 第 0 路的 `session.input_transcript.*` 作为权威混合原文（translation session 内部
   本来就要转写才能翻译，这份原文是白拿的，不额外花钱）。
3. 两路 `session.output_transcript.*` 分别输出第一语言和第二语言译文。
4. input/output delta 不保证携带 `item_id`，前端按当前流累积并以 done 或静音超时收尾。

不要把标准 API Key 放入浏览器端代码。不要回退到旧的浏览器 WebSocket + Base64 PCM 推流实现。

### 第三音源：macOS 本地采集器（应用/系统音频）

`native-capture/` 是独立的 Swift 菜单栏程序（ScreenCaptureKit 纯音频采集），在
`127.0.0.1:17642` 提供 WebSocket：文本帧 JSON 控制协议 + 二进制帧 16kHz 单声道
Float32 PCM。浏览器端 `local-collector-client.js` 经 AudioWorklet 把 PCM 重建成
MediaStream，喂给与麦克风/标签页完全相同的下游链路。

- 采集器只解决采集，无业务 GUI——应用选择、权限引导等 UI 全在 web 端，
  web 端改版不需要更新采集器。
- WS 握手校验 Origin（默认只放行 localhost/127.0.0.1/[::1] 任意端口），
  Origin 缺失即拒绝，防恶意网页连本机端口窃听音频。
- 应用选择合同是 bundleId（不是 pid），容忍应用重启；连接模型 last-wins；
  无客户端时立即停止 SCK 采集。
- 协议 v1 已冻结（见 `native-capture/README.md`），改动需两侧同步并 bump 版本。
- 浏览器 WebSocket 的 `binaryType` 必须设为 `arraybuffer`（默认 Blob 会导致 PCM 静默丢弃）。

### 术语表注入：只能走后置文本链路

**realtime 这一层注入不了术语表，这是 API 硬限制，不是配置问题：**

- translation session 的 `audio.input.transcription` 只接受 `model` 一个字段，
  `prompt` / `keywords` / `languages` / `delay` 一律 400 `unknown_parameter`（已实测）。
- 独立的 transcription session（`gpt-live-transcribe`）倒是接受这些字段，但那要多开
  一路 WebRTC，每分钟多花 $0.017，且原文与译文来自不同模型实例会产生断句漂移。

所以术语准确性由 `POST /api/refine-transcript` 补：

- realtime 结果**先直接上屏**，条目定格后进 `refineQueue`（`app/static/js/app.js`）。
- 攒够 8 条或静默 1.2 秒 flush 一批，交给 `REFINE_MODEL`（默认 `gpt-5.6-luna`，
  `effort=low`）按 keywords 纠错，返回后**原位替换**并重绘。
- 原文和译文都走这条路——两路 translation 产出的译文同样带术语错误。
- 校正失败/超时不影响已显示的字幕，静默保留原文。
- keywords 来自 `/api/workspace/context-pack` 的 `realtimeKeywords`（术语表 canonical +
  人员标准名）加上手填术语框，**不含别名**——别名是已知的识别错误。
- 没有术语表时前端根本不入队，后端也会短路返回，不产生调用。

`REFINE_REASONING_EFFORT=none` 快约一倍（2s vs 4s），但实测会漏掉音译类错误
（片假名写的产品名改不回来），默认 `low`。

### Realtime transcription session（当前默认链路不使用）

`POST /api/realtime-session` 和 `app/static/js/realtime-transcription.js` 仍然是完整
可用的实现，支持 `gpt-live-transcribe` 的 `prompt` / `keywords` / `languages` / `delay`，
按 `_supports_transcription_context()` 做模型能力门控。实时页当前不加载它。
如果以后要把原文改回"转写时就注入术语表"，从这里接。

## 离线会议处理链路

CLI Agent 处理项目/会议目录中的录音文件：

```
音频文件
  -> ASRRouter -> OpenAIASREngine (gpt-transcribe)
  -> transcript.json
  -> GPT 纪要生成
  -> minutes.md / actions.md / timeline.md / context.md
```

`meeting_agent/asr/engine.py`（`OpenAIASREngine`）的几个约束来自模型能力：

- `gpt-transcribe` 只支持 `response_format=json|text`，**不返回时间戳**
  （`verbose_json` 会被 400 拒绝）。时间戳由分块边界推导，块内再按字符数把
  时长摊到句子上——这是估算值，够纪要和 timeline 用，不能做逐词对齐。
- 分块**不重叠**：没有时间戳就无法对重叠文本去重。跨块连贯性靠把上一块结尾
  文本放进下一块的 `prompt`。
- `prompt` / `keywords` / `languages` 从 `meeting_dir.parent` 推导项目目录后，
  自动读 `_context.json`、`_glossary.json`、`_people.json` 构造。
  CLI 多项目模式下 ASRRouter 拿到的是全局 Config，不能直接用 `config.meetings_dir`。
- `transcript.json.progress` 的每条记录带 `signature`（模型@分块长度），
  换模型或改分块长度后旧进度会被丢弃，避免复用出错位的转写稿。
- `/v1/audio/transcriptions` 是 multipart 端点，**不做未知字段校验**——参数拼错
  会静默忽略而不是报错。改这里的参数要用真实音频 A/B 验证，不能只看 HTTP 200。

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
