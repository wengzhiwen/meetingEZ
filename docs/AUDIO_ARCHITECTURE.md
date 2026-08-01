# MeetingEZ 当前架构

## 总览

当前 Web 端已经不是旧的“分段上传 + Worker WAV 编码”方案，也不再提供后置翻译与 Realtime 的模式切换，而是：

```text
浏览器音频采集
  -> 两路 WebRTC
  -> OpenAI Realtime Translation
  -> 第 0 路 input transcript 渲染原文
  -> 两路 output transcript 渲染两种目标语言
```

## 当前数据流

### 1. 音频采集

前端支持两种输入：

- 麦克风：`navigator.mediaDevices.getUserMedia`
- 标签页音频：`navigator.mediaDevices.getDisplayMedia`

特点：

- 单声道
- 麦克风尽量关闭浏览器侧增强：
  - `echoCancellation: false`
  - `noiseSuppression: false`
  - `autoGainControl: false`

### 2. 音量监测

前端单独创建一个 `AudioContext + AnalyserNode` 用于：

- 音量条显示
- 麦克风测试

这条链路只负责 UI，不参与转写上行。

### 3. Realtime Translation 连接

前端类：[`app/static/js/realtime-translation.js`](/home/wengzhiwen/meetingEZ/app/static/js/realtime-translation.js)

流程：

1. 为第一语言和第二语言分别请求后端 `/api/realtime-translation-session`
2. 获取两个 `client secret`
3. 创建两路 `RTCPeerConnection`
4. 将音频轨道加入 PeerConnection
5. 创建 `oai-events` DataChannel
6. 发送 SDP offer 到 OpenAI `/v1/realtime/translations/calls`
7. 设置 answer SDP
8. 等待 DataChannel 打开

## OpenAI Translation Session 配置

当前后端 session 创建逻辑在 [`app/routes.py`](/home/wengzhiwen/meetingEZ/app/routes.py)。

关键配置是目标语言、`gpt-realtime-translate` 和内部输入转写模型 `gpt-live-transcribe`。

Translation session 的 `audio.input.transcription` **只接受 `model` 一个字段**：
`prompt`、`keywords`、`languages`、`delay` 都会被以 400 `unknown_parameter` 拒绝（已实测）。
术语表因此不在 realtime 层解决，改由 `/api/refine-transcript` 的后置文本校正补（见下）。

## 前端状态机

前端分别维护 input/output 当前流；事件可能没有 `item_id`，因此使用 `item_id`、`response_id` 或本地流序号聚合，以 done/completed 或 1.5 秒静音超时完成分段。

## 翻译链路

- 页面固定创建两个 `RealtimeTranslation` 会话，分别以第一语言和第二语言为目标语言。
- 第 0 路会话的 `session.input_transcript.*` 同时作为权威混合原文，进入左栏。
- 两路 `session.output_transcript.*` 分别进入右侧两个目标语言栏。
- 后端通过 `/api/realtime-translation-session` 签发 translation client secret。
- 前端通过 `/v1/realtime/translations/calls` 建立 WebRTC 连接。
- 消费 `session.input_transcript.delta`、`session.output_transcript.delta` 及其 done/completed 变体。
- 事件不保证有 `item_id`，因此按当前流累积，以 done 或 1.5 秒静音兜底完成分段。
- 三栏独立分段、独立打时间标签、独立滚动；原文栏可隐藏。由于会议音频是混合流，没有说话人音轨边界，该模式不做说话人分离，也不在前端按脚本文字过滤原文。

## 当前 UI 架构

页面由三部分组成：

### 1. 全屏字幕区

- 占满主视口
- 支持单栏和双栏
- 支持大字体显示

### 2. 底部吸附工具栏

包含：

- 音量条
- 状态/计时文本
- 麦克风测试
- 自动滚动
- 下载
- 清空
- 设置入口
- 单一开始/结束按钮

### 3. 设置浮层

包含：

- API 测试
- 连接状态
- 音频源选择
- 麦克风设备
- 语言设置
- 字体大小

## 性能与调试

### 前端日志

当前会记录：

- `speech started`
- `first delta`
- `transcript completed`
- `translate completed`

### 后端日志

当前会记录：

- session 创建耗时
- 翻译请求收到时间
- OpenAI 响应耗时
- 翻译总耗时

统一前缀为：

```text
[perf]
```

## 已移除的旧设计

下列内容已不再是当前 Web 端实现：

- AudioWorklet 主转写链路
- Web Worker WAV 编码
- `/v1/audio/transcriptions` 分段上传
- 浏览器保存 OpenAI API Key
- 前端直连翻译模型
- 旧的顶部控制面板 + 侧边设置栏布局

这些文件或文档可能仍保留历史记录，但不代表当前运行路径。

## 术语校正后置链路

realtime 层注入不了术语表，所以三栏字幕（原文 + 两栏译文）统一走一条后置文本校正：

```
realtime 条目定格
  -> enqueueRefine(entry, text)      前端队列，攒够 8 条或静默 1.2 秒
  -> POST /api/refine-transcript     批量提交，最多 2 个并发
  -> gpt-5.6-luna (effort=low)       按 keywords 纠错，只返回改动过的片段
  -> applyRefineResults()            按 entry.id 原位替换 + 重绘
```

设计取舍：

- **先显示后修正。** realtime 字幕不等校正，立刻上屏；校正是"追加改进"，
  失败或超时只是保持原样，不产生任何用户可见的错误。
- **只改术语，不改语义。** system prompt 明确禁止改写、增删、调整语序和翻译，
  拿不准就原样返回。后端再以文本比对二次过滤，避免模型自作主张的润色被写回。
- **原文条目和译文条目的落点不同。** 原文是结构化条目，写入 `correctedTranscript`
  （`getDisplayTranscriptText()` 优先取它）；译文是普通条目，直接改 `text`。
- **入队只做一次。** 条目上打 `_refineQueued` 标记，避免重复提交和重复计费。
