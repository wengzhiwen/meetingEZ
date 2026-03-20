# MeetingEZ 当前架构

## 总览

当前 Web 端已经不是旧的“分段上传 + Worker WAV 编码”方案，而是：

```text
浏览器音频采集
  -> WebRTC
  -> OpenAI Realtime transcription
  -> 前端渲染 live / final transcript
  -> 后端代理翻译
  -> 前端回填翻译
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

### 3. Realtime 连接

前端类：[`app/static/js/realtime-transcription.js`](/home/wengzhiwen/meetingEZ/app/static/js/realtime-transcription.js)

流程：

1. 请求后端 `/api/realtime-session`
2. 获取 `client secret`
3. 创建 `RTCPeerConnection`
4. 将音频轨道加入 PeerConnection
5. 创建 `oai-events` DataChannel
6. 发送 SDP offer 到 OpenAI `/v1/realtime/calls`
7. 设置 answer SDP
8. 等待 DataChannel 打开

## OpenAI Session 配置

当前后端 session 创建逻辑在 [`app/routes.py`](/home/wengzhiwen/meetingEZ/app/routes.py)。

关键配置：

- `type: "transcription"`
- `audio.input.format.type: "audio/pcm"`
- `audio.input.format.rate: 24000`
- `audio.input.noise_reduction.type: "near_field"`
- `audio.input.transcription.model: "gpt-4o-transcribe"`
- `audio.input.turn_detection.type: "semantic_vad"`
- `audio.input.turn_detection.eagerness: "high"`
- `include: ["item.input_audio_transcription.logprobs"]`

## 前端状态机

前端按 `item_id` 管理转写条目：

- `speech_started`
  创建或刷新当前识别状态
- `delta`
  更新 live text
- `completed`
  生成 final text 并写入记录

这避免了简单按消息到达顺序拼接导致的错乱。

## 翻译链路

转写完成后，前端调用后端 `/api/translate`。

后端职责：

- 调用 OpenAI Responses API
- 按翻译模型能力有条件附带 `reasoning.effort`
- 使用 JSON schema 约束输出
- 做语言归一化和结果收口
- 避免把原文同语种文本当作“翻译”

前端职责：

- 先显示原文
- 翻译完成后在原文后插入翻译行
- 维护少量翻译上下文 `translationContext`

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
