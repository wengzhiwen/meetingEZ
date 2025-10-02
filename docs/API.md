# MeetingEZ API 文档

## 概述

MeetingEZ 通过浏览器端将音频分段上传至 OpenAI `gpt-4o-transcribe`（接口：`/v1/audio/transcriptions`）进行转写；随后在浏览器端调用文本模型对结果进行“后置处理”（纠错与按需翻译），不依赖后端中转。本文档描述分段策略、请求参数与字段。

## OpenAI /v1/audio/transcriptions 集成

### 单通道

- 仅进行一路分段并发上传转写。
- 语言来源：由“使用语言”选择器决定（主要语言或第二语言）。
- 渲染：单栏顺序展示结果。

### 请求示例（浏览器端 FormData）

```javascript
const form = new FormData();
form.append('model', 'gpt-4o-transcribe');
form.append('language', 'ja'); // 例：主要语言
form.append('response_format', 'json');
form.append('prompt', tailText); // 例：上一段尾部上下文（可选）
form.append('file', wavBlob, 'segment.wav');

await fetch('https://api.openai.com/v1/audio/transcriptions', {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${apiKey}` },
  body: form
});
```

### 分段与上下文策略

- 分段时长：8 秒
- 重叠长度：1 秒（滑动步长 7 秒）
- 上下文：将上一段的文本尾巴（约 200 字符）作为 `prompt` 传入，帮助模型延续上下文
- 并发：允许多个分段同时上传，降低等待时间

### 会议结束处理

当用户点击"结束会议"时：
1. 立即设置关闭标志 `isShuttingDown = true`，停止产生新的音频分段
2. 停止麦克风录音和音频上下文
3. **保留**所有在途的转写请求继续处理（不执行 `abort()`）
4. 允许后续的翻译和后置处理正常完成
5. 这确保了最后一段内容不会因为过早终止而丢失

```javascript
// 停止录音时的处理
function stopRecording() {
    isShuttingDown = true;  // 标记关闭，不再产生新分段
    isRecording = false;
    
    // 停止录音设备
    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
    }
    
    // 不中断在途请求，让它们自然完成
    // activeUploadControllers 中的请求会继续执行
    console.log(`保留 ${activeUploadControllers.size} 个在途请求继续处理`);
}
```

## （提示）已移除 Realtime API / WebRTC

本项目已不再使用 OpenAI Realtime API 与 WebRTC。所有语音数据通过浏览器端分段编码后，使用 REST 接口 `/v1/audio/transcriptions` 上传并获取转写结果。

## 音频处理

## 音频捕获与编码（会议主流程 48kHz）
```javascript
// 获取麦克风权限
navigator.mediaDevices.getUserMedia({
  audio: {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false,
    sampleRate: 48000,
    channelCount: 1,
    sampleSize: 16,
    latency: 0.01
  }
})
.then(stream => {
  const audioContext = new AudioContext({ sampleRate: 48000 });
  const source = audioContext.createMediaStreamSource(stream);
  const processor = audioContext.createScriptProcessor(2048, 1, 1);
  
  processor.onaudioprocess = (event) => {
    const audioData = event.inputBuffer.getChannelData(0);
    // 转换为 Int16Array 格式
    const int16Data = new Int16Array(audioData.length);
    for (let i = 0; i < audioData.length; i++) {
      int16Data[i] = Math.max(-32768, Math.min(32767, audioData[i] * 32768));
    }
  // 使用浏览器端 WAV 封装后通过 REST 上传至 OpenAI
  };
  
  source.connect(processor);
  processor.connect(audioContext.destination);
});
```

### 音频格式转换
```javascript
function convertFloat32ToInt16(float32Array) {
  const int16Array = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const sample = Math.max(-1, Math.min(1, float32Array[i]));
    int16Array[i] = sample < 0 ? sample * 0x8000 : sample * 0x7FFF;
  }
  return int16Array;
}
```

## 翻译与后置处理（结构化 JSON，浏览器直连）

浏览器在收到 `/v1/audio/transcriptions` 的文本后，会调用 OpenAI Responses 接口 `/v1/responses`，使用 `gpt-4.1-mini-2025-04-14` 做最小化的结构化处理：

- 语言判定：`originalLanguage`
- 是否需要第一语言翻译（非第一语言时为 true）：`isNotPrimaryLanguage`
- 第一语言翻译（需要时提供，否则为 null）：`primaryTranslation`

### 请求（浏览器 -> OpenAI /v1/responses）
```javascript
const payload = {
  model: 'gpt-4.1-mini-2025-04-14',
  input: [
    { role: 'system', content: '...严格输出 JSON 的说明 ...' },
    { role: 'user', content: JSON.stringify({
        task: 'translate_transcript',
        primary_language: 'zh',
        secondary_language: 'ja',
        original_language_hint: 'ja',
        text: '原始转写文本'
    }) }
  ],
  text: {
    format: {
      type: 'json_schema',
      name: 'TranslateTranscript',
      schema: {
        $schema: 'http://json-schema.org/draft-07/schema#',
        type: 'object',
        additionalProperties: false,
        required: ['originalLanguage', 'isNotPrimaryLanguage', 'primaryTranslation'],
        properties: {
          originalLanguage: { type: 'string' },
          isNotPrimaryLanguage: { type: 'boolean' },
          primaryTranslation: { anyOf: [{ type: 'string' }, { type: 'null' }] }
        }
      },
      strict: true
    }
  }
};
```

### 渲染与存储策略
- 首先以“原始转写”即时显示一行临时记录
- 收到结构化结果后：保留原文；若需要翻译则紧随其后插入一行翻译（标记为 `isTranslation: true`）
- 所有记录写入浏览器 Local Storage（键：`meetingEZ_transcripts`，含 `version` 与 `items`）

## 本地存储

### 数据存储结构
```javascript
const storageKey = 'meetingEZ_transcripts';
const transcriptData = {
  timestamp: Date.now(),
  original: '原始文本',
  translated: '翻译文本',
  language: 'en',
  confidence: 0.95
};

// 存储数据
function saveTranscript(data) {
  const existing = JSON.parse(localStorage.getItem(storageKey) || '[]');
  existing.push(data);
  localStorage.setItem(storageKey, JSON.stringify(existing));
}

// 读取数据
function loadTranscripts() {
  return JSON.parse(localStorage.getItem(storageKey) || '[]');
}

// 清空数据
function clearTranscripts() {
  localStorage.removeItem(storageKey);
}
```

## 错误处理

### 连接错误
```javascript
client.on('error', (error) => {
  switch (error.type) {
    case 'connection_failed':
      showError('连接失败，请检查网络连接');
      break;
    case 'authentication_failed':
      showError('API Key 无效，请重新输入');
      break;
    case 'rate_limit_exceeded':
      showError('请求频率过高，请稍后重试');
      break;
    default:
      showError('未知错误: ' + error.message);
  }
});
```

### 音频错误
```javascript
navigator.mediaDevices.getUserMedia({ audio: true })
  .catch(error => {
    switch (error.name) {
      case 'NotAllowedError':
        showError('麦克风权限被拒绝，请在浏览器设置中允许麦克风访问');
        break;
      case 'NotFoundError':
        showError('未找到麦克风设备');
        break;
      case 'NotReadableError':
        showError('麦克风被其他应用占用');
        break;
      default:
        showError('获取麦克风失败: ' + error.message);
    }
  });
```

## 性能优化

### 音频缓冲
```javascript
class AudioBuffer {
  constructor(bufferSize = 24000) {
    this.buffer = new Int16Array(bufferSize);
    this.index = 0;
    this.bufferSize = bufferSize;
  }
  
  append(data) {
    const remaining = this.bufferSize - this.index;
    const toCopy = Math.min(data.length, remaining);
    
    this.buffer.set(data.subarray(0, toCopy), this.index);
    this.index += toCopy;
    
    if (this.index >= this.bufferSize) {
      this.flush();
    }
  }
  
  flush() {
    if (this.index > 0) {
      client.appendInputAudio(this.buffer.subarray(0, this.index));
      this.index = 0;
    }
  }
}
```

### 节流处理
```javascript
function throttle(func, delay) {
  let timeoutId;
  let lastExecTime = 0;
  
  return function (...args) {
    const currentTime = Date.now();
    
    if (currentTime - lastExecTime > delay) {
      func.apply(this, args);
      lastExecTime = currentTime;
    } else {
      clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        func.apply(this, args);
        lastExecTime = Date.now();
      }, delay - (currentTime - lastExecTime));
    }
  };
}
```

## 测试

### API 连接测试
```javascript
async function testAPIConnection(apiKey) {
  try {
    const client = new RealtimeClient({ apiKey });
    await client.connect();
    client.disconnect();
    return { success: true, message: '连接成功' };
  } catch (error) {
    return { success: false, message: error.message };
  }
}
```

### 音频设备测试
```javascript
async function testAudioDevices() {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const audioInputs = devices.filter(device => device.kind === 'audioinput');
    
    if (audioInputs.length === 0) {
      throw new Error('未找到音频输入设备');
    }
    
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach(track => track.stop());
    
    return { success: true, devices: audioInputs };
  } catch (error) {
    return { success: false, message: error.message };
  }
}
```

## 配置参数

### 音频参数（会议主流程）
- **采样率**: 48000 Hz
- **位深度**: 16-bit
- **声道数**: 单声道
- **格式**: PCM

### 音频参数（麦克风测试）
- **采样率**: 24000 Hz
- **位深度**: 16-bit
- **声道数**: 单声道
- **格式**: PCM

### 网络参数
- **HTTP 超时**: 由浏览器与目标服务决定（内置重试：429/5xx 指数退避次数 2）

### 性能参数（前端实现）
- **分段窗口**: 8 秒
- **重叠**: 1 秒
- **ScriptProcessor 缓冲**: 2048 样本
- **VAD 阈值（RMS）**: 0.02（启发式）
- **持续静音判定**: 连续 30 帧（约 600ms）

---

*最后更新: 2025年10月*
