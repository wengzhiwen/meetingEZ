# MeetingEZ API 文档

## 概述

当前 Web 应用已经收敛为“控制台 + 实时页”双入口：

- 控制台负责项目选择、会议创建和流程分流
- 实时页负责采集音频
- 后端签发 OpenAI Realtime `client secret`
- 前端通过 WebRTC 与 OpenAI 建立 transcription session
- 后端代理翻译请求

不再使用：
- 浏览器端保存 API Key
- 分段上传 `/v1/audio/transcriptions`
- 浏览器直连翻译接口
- 前端 WebSocket + Base64 PCM 推流

## 页面与登录

### `GET /`

控制台首页。

默认入口。

提供两条流程：

- 项目模式：先选项目、创建会议，再进入实时页
- 快速模式：不关联项目，直接进入实时页

### `GET /realtime`

实时转写页面。

常见 query 参数：

- `mode=project|quick`
- `project=<project_id>`
- `meeting=<meeting_dir>`
- `meetingTitle=<meeting_title>`
- `primaryLanguage=<lang>`
- `secondaryLanguage=<lang>`
- `languageMode=single_primary|bilingual`

### `GET|POST /login`

访问码登录页。

- 当 `ACCESS_CODE` 为空时，登录保护自动关闭。
- 当 `ACCESS_CODE` 已配置时，所有页面与 API 都受 session 保护。

### `GET /logout`

清除 session 并回到登录页。

### `GET /health`

健康检查。

返回示例：

```json
{
  "status": "healthy",
  "service": "MeetingEZ",
  "version": "0.2.0"
}
```

## 后端 API

### `POST /workspace/launch-project-meeting`

从控制台创建会议目录并跳转到实时页。

表单字段：

- `project_id`
- `meeting_title`
- `meeting_date`
- `meeting_type`
- `primary_language`
- `secondary_language`
- `language_mode`
- `notes`

效果：

- 在项目目录下创建会议文件夹
- 写入 `_meeting.json`
- 重定向到 `/realtime?mode=project...`

### `POST /api/test-connection`

测试后端 `OPENAI_API_KEY` 是否可用。

成功返回：

```json
{
  "ok": true
}
```

失败返回：

```json
{
  "error": "..."
}
```

### `POST /api/realtime-session`

创建 OpenAI Realtime transcription session 的 `client secret`。

**当前实时页默认链路不调用它**：原文来自第 0 路 translation session 的 input transcript，
术语表由 `/api/refine-transcript` 后置校正补。本接口保留为完整可用的备选路径——它是唯一
能在"转写发生时"就注入术语表的入口。

请求体：

```json
{
  "languages": ["zh", "en"],
  "prompt": "这是「MeetingEZ - 会议转写工具」的项目会议录音。项目背景：……",
  "keywords": ["翁志文", "MeetingEZ", "WebRTC"]
}
```

- `languages`：预期输入语言（ISO-639-1 短码数组），是提示不是限制，最多 4 个。
- `prompt`：自由文本的录音场景描述，不是指令。
- `keywords`：希望模型写对的字面词（人名、产品名、缩写），最多 100 个。
- 兼容旧字段 `language`（单个短码）。

后端当前创建的 session 配置：

```json
{
  "session": {
    "type": "transcription",
    "audio": {
      "input": {
        "format": {
          "type": "audio/pcm",
          "rate": 24000
        },
        "noise_reduction": {
          "type": "near_field"
        },
        "transcription": {
          "model": "gpt-live-transcribe",
          "languages": ["zh", "en"],
          "prompt": "……",
          "keywords": ["翁志文", "MeetingEZ"],
          "delay": "low"
        }
      }
    },
    "include": ["item.input_audio_transcription.logprobs"]
  }
}
```

返回体：

```json
{
  "clientSecret": "rt_...",
  "expiresAt": 1234567890,
  "session": {}
}
```

说明：

- 当前代码兼容两种返回格式：
  - 顶层 `value` / `expires_at`
  - 嵌套 `client_secret.value` / `client_secret.expires_at`
- 不发送 `turn_detection`；默认依赖 `delay: "low"` 的低延迟流式 transcript deltas。
  `delay` 支持 `minimal` / `low` / `medium` / `high` / `xhigh`，值越低首个 delta 越快、准确率略降。
- `prompt` / `keywords` / `languages` 只有 `gpt-live-transcribe` 支持。后端按
  `_supports_transcription_context()` 做能力门控：换回上一代 `gpt-realtime-whisper` 时
  这三个字段不会下发（发了会被 400 `unknown_parameter` 拒绝），只发 `model` / `language` / `delay`。

### `POST /api/realtime-translation-session`

创建 OpenAI Realtime Translation session 的 `client secret`。

该接口是当前实时页的唯一翻译 session 签发入口；前端会为两种目标语言各调用一次。

请求体：

```json
{
  "targetLanguage": "ja"
}
```

后端当前创建的 session 配置：

```json
{
  "session": {
    "model": "gpt-realtime-translate",
    "audio": {
      "input": {
        "transcription": {
          "model": "gpt-live-transcribe"
        },
        "noise_reduction": {
          "type": "near_field"
        }
      },
      "output": {
        "language": "ja"
      }
    }
  }
}
```

返回体：

```json
{
  "clientSecret": "ek_...",
  "expiresAt": 1234567890,
  "targetLanguage": "ja",
  "model": "gpt-realtime-translate",
  "session": {}
}
```

说明：

- 支持的目标语言按 OpenAI demo 白名单约束：`es`、`pt`、`fr`、`ja`、`ru`、`zh`、`de`、`ko`、`hi`、`id`、`vi`、`it`、`en`。
- **translation session 的 `audio.input.transcription` 只接受 `model` 一个字段。**
  `prompt` / `keywords` / `languages` / `delay` 都会被以 400 `unknown_parameter` 拒绝（已实测）。
  需要术语表注入就必须走独立的 `/api/realtime-session`。
- 实时页创建两路 translation session，分别译入第一语言和第二语言；第 0 路 session 的
  input transcript 同时作为权威原文（translation 内部本来就要转写，这份原文不额外计费）。
  前端显示原文与两栏目标语言字幕，原文栏可隐藏。
- transcript delta 不保证携带 `item_id`；前端按当前 input/output 流累积，并由 done 事件或短暂停顿完成分段。
- 混合会议音频没有说话人音轨边界，因此当前链路不做说话人分离。

### `POST /api/refine-transcript`

按项目术语表批量校正已定格的字幕片段。原文和译文都走这个接口。

存在的理由：Realtime Translation session 的 `audio.input.transcription` 只接受 `model`，
`keywords` 会被 400 拒绝，所以术语准确性没法在转写/翻译时解决，只能在文本层补。
前端的用法是"先直接给、再更新优化结果"——realtime 字幕立刻上屏，本接口返回后原位替换。

请求体：

```json
{
  "segments": [
    { "id": "rt-input-abc-1", "lang": "zh", "text": "我们这次在 Meeting EZ 里接入了实时转写。" },
    { "id": "rt-translate-en-...", "lang": "en", "text": "We shipped the Meeting E Z pipeline with web RTC." }
  ],
  "keywords": ["MeetingEZ", "WebRTC", "翁志文"],
  "context": "项目摘要: ...\n背景说明: ..."
}
```

返回体（**只回传真正被改动的片段**，没改的不出现）：

```json
{
  "segments": [
    { "id": "rt-input-abc-1", "text": "我们这次在 MeetingEZ 里接入了实时转写。" },
    { "id": "rt-translate-en-...", "text": "We shipped the MeetingEZ pipeline with WebRTC." }
  ]
}
```

说明：

- 模型由 `REFINE_MODEL` 决定，默认 `gpt-5.6-luna`；reasoning effort 由
  `REFINE_REASONING_EFFORT` 决定，默认 `high`。
- 单次最多 `REFINE_MAX_SEGMENTS`（12）个片段、`REFINE_MAX_CHARS`（4000）字符，超出直接截断。
- `keywords` 为空时直接返回 `{"segments": []}`，不调用模型——没有术语表就没有校正依据。
- 模型偶尔会把 `changed` 标反，后端以文本比对为准，只回传 `text != 原文` 的片段。
- 前端批大小 8、静默 1.2 秒 flush、最多 2 个并发请求；失败静默保留 realtime 原文。

### `GET /api/workspace/projects`

返回当前工作区可见项目列表。

返回示例：

```json
{
  "projects": [
    {
      "id": "__default__",
      "name": "meetings",
      "isDefault": true
    }
  ]
}
```

### `GET /api/workspace/context-pack`

返回项目增强包，供实时页增强 prompt 和后置处理。

查询参数：

- `project`
- `primaryLanguage`
- `secondaryLanguage`
- `languageMode`

说明：

- 当 `project=__none__` 时，表示快速模式，返回空增强包
- 当传入真实项目时，会返回项目摘要、背景说明、术语、近期会议和待办摘要

返回字段示例：

```json
{
  "projectId": "__default__",
  "projectName": "示例项目",
  "languageMode": "single_primary",
  "primaryLanguage": "zh",
  "secondaryLanguage": "",
  "confirmedTermsCount": 12,
  "glossaryLines": ["MeetingEZ | 米听易"],
  "pendingActions": ["整理评审反馈"],
  "recentMeetings": ["2026-03-23 需求评审"],
  "realtimePrompt": ""
}
```

### `POST /api/translate`

兼容保留的后置翻译代理。它可调用 Responses API 完成智能修正、术语表、会议上下文和双向翻译，但当前实时页固定使用 Realtime Translation，不调用此接口。

## 前端与 OpenAI Realtime

### 连接流程

1. 前端为两种目标语言分别请求 `/api/realtime-translation-session`
2. 后端向 OpenAI 创建两个 translation `client secret`
3. 前端创建两路 `RTCPeerConnection`
4. 前端把同一音频轨道加入两路连接并创建 data channel
5. 前端分别生成 offer
6. 前端将 SDP POST 到 `https://api.openai.com/v1/realtime/translations/calls`
7. OpenAI 返回 answer SDP
8. DataChannel 打开后开始接收 input/output transcript 事件

### 前端处理的关键事件

| 事件 | 用途 |
|------|------|
| `input_audio_buffer.speech_started` | 开始显示“正在识别...” |
| `conversation.item.input_audio_transcription.delta` | 更新 live transcript |
| `conversation.item.input_audio_transcription.completed` | 写入最终字幕 |
| `error` | 展示连接或会话错误 |

### 状态建模

前端按 `item_id` 维护两层文本：

- `live`
  增量渲染中的文本
- `final`
  完整提交后的文本

## 前端 UI 状态

当前页面结构：

- 控制台首页
- 实时页：全屏字幕区 + 底部吸附工具栏 + 设置浮层

底部工具栏包含：

- 音量条
- 计时/状态文本
- 麦克风测试
- 自动滚动
- 下载
- 清空
- 设置
- 单一 `开始/结束` 切换按钮

设置浮层包含：

- API 测试按钮
- 连接状态
- 项目工作区选择
- 语言模式
- 音频输入源
- 麦克风设备选择
- 主要语言 / 第二语言
- 字体大小

## 性能日志

当前实现已经加入性能日志，便于排查：

### 后端日志

统一前缀：

```text
[perf]
```

关键阶段：

- `realtime_session_created`
- `realtime_session_failed`
- `translate_request_received`
- `translate_openai_response`
- `translate_request_completed`
- `translate_request_failed`

### 前端日志

浏览器 console 关键前缀：

- `Realtime [perf]`
- `UI [perf]`

用于观察：

- `speech_started -> first delta`
- `first delta -> completed`
- 翻译请求总耗时

## 认证与错误约定

- 未登录访问 API：返回 `401 {"error":"Unauthorized"}`
- 前端在收到 `401` 后跳转 `/login`
- 后端所有 OpenAI 代理错误统一返回 JSON `error` 字段
