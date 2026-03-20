# MeetingEZ API 文档

## 概述

当前 Web 应用已经收敛为单一路径：

- 前端采集音频
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

主页面。

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

请求体：

```json
{
  "language": "zh",
  "prompt": ""
}
```

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
          "model": "gpt-4o-transcribe",
          "language": "zh"
        },
        "turn_detection": {
          "type": "semantic_vad",
          "eagerness": "high"
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

### `POST /api/translate`

后端代理翻译请求，避免前端暴露标准 API Key。

请求体：

```json
{
  "text": "こんにちは",
  "primaryLanguage": "zh",
  "secondaryLanguage": "ja",
  "originalLanguageHint": "ja",
  "context": "[1] (zh) 上一条上下文",
  "model": "gpt-5.4-mini-2026-03-17",
  "reasoningEffort": "low"
}
```

当前行为：

- 默认模型来自环境变量 `TRANSLATION_MODEL`
- 默认为 `gpt-5.4-mini-2026-03-17`
- 可选 reasoning effort 来自环境变量 `TRANSLATION_REASONING_EFFORT`
- 默认为 `low`
- 当前代码仅在翻译模型名以 `gpt-5` 开头时发送 `reasoning.effort`
- 输出严格 JSON
- 后端会做结果清洗，防止“同语种翻译”

返回体：

```json
{
  "originalLanguage": "ja",
  "primaryTranslation": "你好",
  "secondaryTranslation": null
}
```

规则：

- 原文是第一语言：`primaryTranslation = null`
- 原文是第二语言：`secondaryTranslation = null`
- 原文是其他语言：只翻到第一语言

## 前端与 OpenAI Realtime

### 连接流程

1. 前端请求 `/api/realtime-session`
2. 后端向 OpenAI 创建 `client secret`
3. 前端创建 `RTCPeerConnection`
4. 前端添加音频轨道并创建 `oai-events` data channel
5. 前端生成 offer
6. 前端将 SDP POST 到 `https://api.openai.com/v1/realtime/calls`
7. OpenAI 返回 answer SDP
8. DataChannel 打开后开始接收转写事件

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

- 全屏字幕区
- 底部吸附工具栏
- 设置浮层

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
