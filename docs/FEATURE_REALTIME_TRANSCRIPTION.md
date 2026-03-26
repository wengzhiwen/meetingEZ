# 实时转写功能详细设计

## 1. 功能概述

### 1.1 产品定位

实时转写是 MeetingEZ 的核心功能，提供浏览器端的实时会议语音转文字能力。基于 OpenAI Realtime API 的 WebRTC transcription session，实现低延迟、高质量的实时字幕显示。

### 1.2 核心价值

- **低延迟字幕**：讲话后快速出字，延迟通常在 500ms-2s
- **双语翻译**：支持配置第二语言，自动进行双向翻译
- **智能修正**：可选的 ASR 后置修正，提升专业术语准确性
- **项目增强**：可选加载术语表和上下文，提升识别精度

### 1.3 目标用户

- 参加在线会议需要实时字幕的用户
- 需要跨语言会议沟通的团队
- 需要高质量会议记录的项目团队

---

## 2. 功能边界

### 2.1 职责范围

| 功能 | 是否负责 | 说明 |
|------|----------|------|
| 音频采集 | ✅ | 麦克风/标签页音频 |
| 实时转写 | ✅ | OpenAI Realtime API |
| 字幕显示 | ✅ | 增量更新 + 完成确认 |
| 翻译后置处理 | ✅ | 双向翻译 + 智能修正 |
| 会后纪要生成 | ❌ | 由离线 Agent 负责 |
| 项目记忆维护 | ❌ | 由离线 Agent 负责 |
| 完整录音保存 | ❌ | 当前版本未实现 |

### 2.2 与其他功能的关系

```
┌─────────────────────────────────────────────────────────────┐
│                      实时转写功能                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   输入                         输出                         │
│   ┌─────────────┐             ┌─────────────┐              │
│   │ 音频流      │ ──────────▶ │ 实时字幕    │              │
│   │ 术语表      │             │ 翻译结果    │              │
│   │ 项目上下文  │             │ 本地记录    │              │
│   └─────────────┘             └─────────────┘              │
│                                                             │
│   关联功能                                                   │
│   ┌─────────────┐             ┌─────────────┐              │
│   │ 术语表系统  │◀── 增强识别─│             │              │
│   │ 项目记忆    │◀── 加载上下文             │              │
│   │ Web工作台   │◀── 创建会议入口           │              │
│   └─────────────┘                          │              │
│                                             │              │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 核心流程

### 3.1 整体流程图

```
用户点击"开始"
    │
    ▼
┌─────────────────────────────────────┐
│ 1. 获取音频权限                      │
│    - 麦克风: getUserMedia            │
│    - 标签页: getDisplayMedia         │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 2. 加载项目增强包（可选）            │
│    - 术语表                          │
│    - 项目上下文                      │
│    - 近期待办                        │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 3. 获取 Realtime Session Secret     │
│    - POST /api/realtime-session     │
│    - 后端签发 client secret         │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 4. 建立 WebRTC 连接                  │
│    - 创建 RTCPeerConnection         │
│    - 添加音频轨道                    │
│    - 创建 DataChannel               │
│    - SDP 交换                       │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 5. 接收转写事件                      │
│    - speech_started: 显示"识别中"   │
│    - delta: 更新增量文本             │
│    - completed: 确认最终文本         │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 6. 后置翻译（可选）                  │
│    - POST /api/translate            │
│    - 智能修正 + 双向翻译             │
└─────────────────────────────────────┘
    │
    ▼
更新字幕显示，保存本地记录
```

### 3.2 WebRTC 连接详细流程

```
前端                                    OpenAI
 │                                         │
 │  1. POST /api/realtime-session         │
 │  ─────────────────────────────────────▶│
 │                                         │
 │  2. 返回 client secret                 │
 │  ◀─────────────────────────────────────│
 │                                         │
 │  3. 创建 RTCPeerConnection             │
 │  4. 添加音频轨道                        │
 │  5. 创建 oai-events DataChannel        │
 │  6. createOffer()                      │
 │  7. setLocalDescription(offer)         │
 │                                         │
 │  8. POST /v1/realtime/calls            │
 │     (Authorization: Bearer secret)     │
 │  ─────────────────────────────────────▶│
 │                                         │
 │  9. 返回 answer SDP                    │
 │  ◀─────────────────────────────────────│
 │                                         │
 │  10. setRemoteDescription(answer)      │
 │                                         │
 │  11. DataChannel open                  │
 │  ◀═════════════════════════════════════│
 │                                         │
 │  12. 接收转写事件 (oai-events)         │
 │  ◀─────────────────────────────────────│
```

---

## 4. 数据结构

### 4.1 转写条目结构

```javascript
// 结构化转写条目（当前版本）
{
  id: "item_abc123",                    // item_id，来自 Realtime API
  timestamp: "2026-03-26T10:30:00.000Z", // ISO 时间戳
  channel: "primary",                   // primary | secondary
  originalLanguage: "zh",               // 检测到的原始语言

  // 转写文本
  rawTranscript: "我们今天讨论一下项目进度",  // ASR 原始输出
  correctedTranscript: "我们今天讨论一下项目进度", // 智能修正后（可选）
  correctionApplied: false,             // 是否应用了修正

  // 翻译结果（可选）
  primaryTranslation: null,             // 翻译到主语言
  secondaryTranslation: "今日はプロジェクトの進捗について話し合います", // 翻译到第二语言

  // 状态标记
  postProcessing: false,                // 是否正在后置处理
  pendingCorrection: false,             // 是否等待修正
  pendingTranslation: false             // 是否等待翻译
}
```

### 4.2 Context Pack 结构

```javascript
// 项目增强包
{
  projectId: "meetingEZ",
  projectName: "MeetingEZ 开发",
  languageMode: "single_primary",       // single_primary | bilingual
  primaryLanguage: "zh",
  secondaryLanguage: "",

  // 上下文摘要
  projectSummary: "智能会议纪要系统...",
  backgroundSummary: "基于 OpenAI Realtime API...",

  // 术语表
  confirmedTermsCount: 12,
  glossaryLines: ["MeetingEZ | 米听易", "Realtime API | 实时 API"],

  // 近期上下文
  pendingActions: ["完成文档编写", "测试翻译功能"],
  recentMeetings: ["2026-03-25 需求评审", "2026-03-20 技术讨论"],

  // 生成的提示
  realtimePrompt: "你正在执行会议实时转写..."
}
```

### 4.3 字幕状态

```javascript
// 全局状态
let transcripts = [];                    // 已完成的转写条目列表
let currentStreamingTextMap = {          // 当前增量文本
  primary: '',
  secondary: ''
};
let currentTranscriptIdMap = {           // 当前条目 ID
  primary: null,
  secondary: null
};
let translationContext = [];             // 翻译上下文（最近 N 条）
```

---

## 5. 详细设计

### 5.1 音频采集机制

#### 5.1.1 输入源类型

| 类型 | API | 适用场景 |
|------|-----|----------|
| 麦克风 | `getUserMedia` | 本机讲话、会议室拾音 |
| 标签页音频 | `getDisplayMedia` | 转写浏览器中的远程会议 |

#### 5.1.2 音频参数

```javascript
// 麦克风采集参数
const audioConstraints = {
  echoCancellation: false,     // 关闭浏览器侧回声消除
  noiseSuppression: false,     // 关闭浏览器侧降噪
  autoGainControl: false,      // 关闭浏览器侧自动增益
  channelCount: 1              // 单声道
};

// 标签页音频
const displayStream = await navigator.mediaDevices.getDisplayMedia({
  video: true,                 // 必须请求视频（用于选择标签页）
  audio: true                  // 请求音频
});
```

#### 5.1.3 音量监测

```javascript
// 独立的音量监测链路（不参与转写上行）
function startVolumeMonitor(stream) {
  volumeAudioContext = new AudioContext();
  const source = volumeAudioContext.createMediaStreamSource(stream);
  volumeAnalyser = volumeAudioContext.createAnalyser();
  volumeAnalyser.fftSize = 256;
  source.connect(volumeAnalyser);
  // 每帧更新音量条 UI
}
```

### 5.2 Realtime 连接管理

#### 5.2.1 连接参数

```javascript
// 后端 Session 配置（由后端创建）
{
  type: "transcription",
  audio: {
    input: {
      format: {
        type: "audio/pcm",
        rate: 24000
      },
      noise_reduction: {
        type: "near_field"      // 近场降噪
      },
      transcription: {
        model: "gpt-4o-transcribe",
        language: "zh"          // 可选，显式指定语言
      },
      turn_detection: {
        type: "semantic_vad",   // 语义 VAD
        eagerness: "high"       // 高灵敏度，快速出字
      }
    }
  },
  include: ["item.input_audio_transcription.logprobs"]
}
```

#### 5.2.2 断线重连

```javascript
// 重连策略
{
  maxReconnectAttempts: 3,      // 最大重连次数
  reconnectDelay: 2000,         // 基础延迟
  // 指数退避: 2s → 4s → 8s
}

// 重连条件
// 1. 非主动断开 (_intentionalClose = false)
// 2. 有可用的媒体流 (localStream 存在)
// 3. 未达到最大重连次数
```

### 5.3 字幕状态机

#### 5.3.1 状态定义

```
┌─────────────────────────────────────────────────────────────┐
│                     字幕条目状态机                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   [开始讲话]                                                │
│       │                                                     │
│       ▼                                                     │
│   ┌───────────────┐                                         │
│   │ speech_started│  显示 "正在识别..."                     │
│   └───────────────┘                                         │
│       │                                                     │
│       │ delta 事件                                          │
│       ▼                                                     │
│   ┌───────────────┐                                         │
│   │   streaming   │  增量更新 live text                     │
│   │   (delta)     │  按 item_id 管理多条                    │
│   └───────────────┘                                         │
│       │                                                     │
│       │ completed 事件                                      │
│       ▼                                                     │
│   ┌───────────────┐                                         │
│   │   completed   │  写入 final text                        │
│   └───────────────┘                                         │
│       │                                                     │
│       │ 触发后置处理（可选）                                │
│       ▼                                                     │
│   ┌───────────────┐                                         │
│   │ postProcessing│  智能修正 + 翻译                        │
│   └───────────────┘                                         │
│       │                                                     │
│       ▼                                                     │
│   [完成]  显示最终结果                                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 5.3.2 按 item_id 管理

**关键原则**：不同 speech turn 的 completion 事件顺序并不保证严格按到达顺序。必须以 `item_id` 为主键管理转写条目。

```javascript
// 按 item_id 管理的数据结构
this.items = {
  "item_001": {
    live: "我们今天讨论一下...",      // 增量文本
    final: null,                      // 完成后填充
    timestamp: 1711449000000
  },
  "item_002": {
    live: "项目进度方面...",
    final: null,
    timestamp: 1711449005000
  }
};

// delta 处理
onTranscriptDelta: (delta, itemId, liveText) => {
  this.items[itemId].live = liveText;
  updateStreamingDisplay(liveText);
}

// completed 处理
onTranscriptComplete: (transcript, itemId) => {
  this.items[itemId].final = transcript;
  addToTranscriptList(itemId, transcript);
}
```

### 5.4 翻译后置处理

#### 5.4.1 触发条件

```javascript
// 后置处理触发判断
const processingSettings = getProcessingSettings();
if (processingSettings.enableCorrection || secondaryLanguage) {
  // 启用后置处理
  newTranscript.postProcessing = true;
  newTranscript.pendingCorrection = processingSettings.enableCorrection;
  newTranscript.pendingTranslation = !!secondaryLanguage;
}
```

#### 5.4.2 翻译请求

```javascript
// 调用后端翻译接口
const resp = await fetch('/api/translate', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    text: originalText,
    primaryLanguage: 'zh',
    secondaryLanguage: 'ja',
    languageMode: 'single_primary',
    originalLanguageHint: 'zh',
    enableCorrection: true,
    enableGlossary: true,
    glossary: "MeetingEZ | 米听易\nRealtime API",
    meetingContext: "项目摘要: 智能会议系统...",
    context: "[1] (zh) 上一条上下文"
  })
});
```

#### 5.4.3 翻译结果规则

| 原文语言 | primaryTranslation | secondaryTranslation |
|----------|-------------------|---------------------|
| 第一语言 (zh) | `null` | 翻译成第二语言 |
| 第二语言 (ja) | 翻译成第一语言 | `null` |
| 其他语言 (en) | 翻译成第一语言 | `null` |

**禁止同语种翻译**：若目标语言与原文同语种，对应字段必须是 `null`。

### 5.5 项目增强模式

#### 5.5.1 Context Pack 加载

```javascript
// 从后端加载项目增强包
async function loadWorkspaceContextPack() {
  const params = new URLSearchParams({
    project: getSelectedWorkspaceProject(),
    primaryLanguage: document.getElementById('primaryLanguage').value,
    secondaryLanguage: document.getElementById('secondaryLanguage').value,
    languageMode: getLanguageMode()
  });

  const resp = await fetch(`/api/workspace/context-pack?${params}`);
  currentContextPack = await resp.json();
}
```

#### 5.5.2 Realtime Prompt 构建

```javascript
function buildRealtimePrompt() {
  const parts = [];

  // 1. 项目预定义提示
  if (currentContextPack?.realtimePrompt) {
    parts.push(currentContextPack.realtimePrompt);
  }

  // 2. 术语表（合并项目术语 + 手动输入）
  const mergedGlossary = buildMergedGlossary();
  if (mergedGlossary) {
    const condensed = mergedGlossary
      .split('\n')
      .filter(Boolean)
      .slice(0, 16)      // 限制条目数
      .join('；');
    parts.push(`术语参考：${condensed}`);
  }

  return parts.join(' ');
}
```

### 5.6 幻觉检测

```javascript
// 过滤 ASR 幻觉文本
function isHallucinationText(text) {
  const hallucinationPatterns = [
    /^(hi|hello|hey|welcome).*(channel|video|subscribe)/i,
    /^thanks?\s+for\s+(watching|listening|subscribing)/i,
    /字幕|subtitle|caption|transcript/i,
    /^(\s*[a-z]\s*){8,}$/i,           // 单字母重复
    /^([a-z]-){4,}/i,                  // 字母-重复
    /^(um|uh|ah|eh|oh)\s*$/i,         // 语气词
  ];

  // 长度检查
  if (text.length < 2 || text.length > 500) return true;

  // 模式匹配
  return hallucinationPatterns.some(pattern => pattern.test(text));
}
```

---

## 6. 与其他模块的交互

### 6.1 与术语表系统的交互

```
术语表系统
    │
    │ 提供: confirmedTerms
    ▼
┌─────────────────────────────────────┐
│ Context Pack 构建                   │
│ - glossaryLines: 术语列表           │
│ - realtimePrompt: 包含术语          │
└─────────────────────────────────────┘
    │
    │ 注入
    ▼
Realtime Session (prompt 参数)
    │
    │ 识别增强
    ▼
翻译后置处理 (glossary 参数)
    │
    │ 术语修正
    ▼
最终字幕输出
```

### 6.2 与 Web 工作台的交互

```
Web 工作台
    │
    │ 1. 用户选择项目 → 创建会议
    │ 2. 跳转到 /realtime?mode=project&project=xxx
    ▼
实时转写页面
    │
    │ 3. 读取 URL 参数
    │    - project
    │    - meeting
    │    - primaryLanguage
    │    - languageMode
    ▼
加载 Context Pack
    │
    │ 4. 开始会议
    ▼
转写过程...
```

### 6.3 与离线 Agent 的关系

当前版本实时转写与离线 Agent **独立运行**：

- 实时转写：产生 `live_transcript`（本地存储）
- 离线 Agent：处理完整录音 → 生成 `transcript.json` + `minutes.md`

未来版本将实现：
- 实时转写保存完整录音
- 离线 Agent 基于完整录音生成正式转写
- 实时结果与正式结果的差异对比

---

## 7. 配置项

### 7.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAI_API_KEY` | 必需 | OpenAI API Key |
| `TRANSLATION_MODEL` | `gpt-5.4-mini-2026-03-17` | 翻译模型 |
| `TRANSLATION_REASONING_EFFORT` | `low` | 推理强度（仅 gpt-5 系列） |
| `ACCESS_CODE` | 空 | 访问码（空则关闭登录保护） |

### 7.2 前端设置

| 设置 | 存储 | 说明 |
|------|------|------|
| 音频输入源 | `meetingEZ_audioSource` | microphone / tab |
| 麦克风设备 | `meetingEZ_audioDevice` | 设备 ID |
| 主要语言 | `meetingEZ_primaryLanguage` | zh, en, ja 等 |
| 第二语言 | `meetingEZ_secondaryLanguage` | 可选 |
| 语言模式 | `meetingEZ_languageMode` | single_primary / bilingual |
| 智能修正 | `meetingEZ_enableCorrection` | true / false |
| 术语增强 | `meetingEZ_enableGlossary` | true / false |
| 字体大小 | `meetingEZ_fontSize` | small / medium / large |
| 自动滚动 | `meetingEZ_autoScroll` | true / false |

### 7.3 模型配置

```javascript
// 转写模型
const TRANSCRIPTION_MODEL = 'gpt-4o-transcribe';

// 翻译模型
const TRANSLATION_MODEL = 'gpt-5.4-mini-2026-03-17';

// 推理配置（仅 gpt-5 系列支持）
const reasoning = {
  effort: 'low'    // low | medium | high
};
```

---

## 8. 错误处理

### 8.1 常见错误

| 错误类型 | 原因 | 处理方式 |
|----------|------|----------|
| 麦克风权限被拒绝 | 用户拒绝授权 | 提示用户手动授权 |
| 标签页音频为空 | 未勾选"共享音频" | 提示重新选择并勾选 |
| Session 创建失败 | API Key 无效/配额不足 | 显示错误信息 |
| WebRTC 连接失败 | 网络问题 | 自动重连（最多3次） |
| 翻译超时 | 后端响应慢 | 保留原文，标记失败 |

### 8.2 断线恢复

```javascript
// 断线检测
pc.onconnectionstatechange = () => {
  if (['failed', 'disconnected', 'closed'].includes(pc.connectionState)) {
    if (!this._intentionalClose) {
      this._attemptReconnect();
    }
  }
};

// 重连逻辑
_attemptReconnect() {
  if (this.reconnectAttempts >= this.maxReconnectAttempts) {
    console.log('达到最大重连次数');
    return;
  }

  const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
  setTimeout(async () => {
    await this.connect(this.localStream);
  }, delay);
}
```

---

## 9. 性能考量

### 9.1 性能目标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 讲话开始 → 首字显示 | < 1s | speech_started → first delta |
| 首字 → 完成确认 | < 3s | first delta → completed |
| 翻译延迟 | < 2s | completed → 翻译完成 |
| UI 渲染 | 60fps | 增量更新不卡顿 |

### 9.2 性能日志

```javascript
// 前端性能日志（console）
console.log('Realtime [perf] first delta', {
  itemId,
  msFromSpeechStart: 800
});

console.log('Realtime [perf] transcript completed', {
  itemId,
  chars: 150,
  msFromSpeechStart: 2500,
  msFromFirstDelta: 1700
});

console.log('UI [perf] translate completed', {
  elapsedMs: 1200,
  originalLanguage: 'zh',
  correctionApplied: true,
  hasPrimaryTranslation: false,
  hasSecondaryTranslation: true
});
```

### 9.3 后端性能日志

```python
# 后端统一前缀 [perf]
_log_timing('realtime_session_created',
            elapsed_ms=350,
            language='zh',
            has_prompt=True)

_log_timing('translate_request_completed',
            elapsed_ms=1200,
            original_language='zh',
            correction_applied=True)
```

---

## 10. 未来规划

### 10.1 已知限制

1. **无完整录音保存**：当前版本不保存会议完整录音
2. **无说话人分离**：无法区分不同发言人
3. **无书签功能**：无法在会中标记关键点
4. **无实时回放**：无法跳转到历史位置

### 10.2 待优化项

1. **完整录音保存**：支持 WebM 格式录制
2. **说话人分离**：集成 diarization 能力
3. **会中书签**：标记决策点、待办候选
4. **与离线 Agent 融合**：实时稿 → 正式稿的流转

### 10.3 扩展方向

1. **离线转写支持**：支持上传音频文件进行转写
2. **多语言混说**：更好的中英日混合识别
3. **自定义术语热更新**：实时添加术语无需重连
4. **协作模式**：多人同时查看实时字幕
