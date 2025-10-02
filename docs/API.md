# MeetingEZ API 文档

## 概述

MeetingEZ 使用 OpenAI Realtime API 进行实时语音识别和翻译。本文档详细说明了 API 的使用方法、参数配置和事件处理。

## OpenAI Realtime API 集成 (WebRTC)

### WebRTC 连接配置

```javascript
const client = new RealtimeAPIClient();
client.setApiKey('your-openai-api-key');

// 请求WebRTC会话
const sessionInfo = await client.requestWebRTCSession({
  model: 'gpt-4o-realtime-preview-2024-10-01',
  voice: 'alloy',
  instructions: '你是一个专业的会议助手，负责实时转录会议内容。',
  turn_detection: {
    type: 'server_vad',
    threshold: 0.5,
    prefix_padding_ms: 300,
    silence_duration_ms: 200
  }
});

// 创建WebRTC PeerConnection
await client.createPeerConnection(sessionInfo);
```

### 音频配置

```javascript
// 音频格式配置
const audioConfig = {
  input_audio_format: 'pcm16',
  input_audio_transcription: {
    model: 'whisper-1'
  },
  output_audio_format: 'pcm16',
  output_audio_transcription: {
    model: 'whisper-1'
  }
};
```

## WebRTC 事件处理

### 客户端事件

#### 连接事件
```javascript
client.on('error', (error) => {
  console.error('连接错误:', error);
});

client.on('userTranscript', (data) => {
  console.log('用户转录:', data.text);
  // 处理用户语音转录
});

client.on('assistantTranscript', (data) => {
  console.log('AI转录:', data.text);
  // 处理AI响应转录
});
```

#### WebRTC消息处理
```javascript
// WebRTC消息通过数据通道接收
client.dataChannel.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  switch (data.type) {
    case 'conversation.item.delta':
      if (data.item.type === 'message' && data.item.role === 'user') {
        // 处理用户消息增量
        handleUserMessageDelta(data.delta);
      }
      break;
    case 'session.updated':
      // 处理会话状态更新
      handleSessionUpdate(data.session);
      break;
  }
};
```

#### 项目完成事件
```javascript
client.on('conversation.item.completed', ({ item }) => {
  if (item.type === 'message' && item.role === 'user') {
    // 用户消息完成，可以开始翻译
    translateMessage(item.content);
  }
});
```

## 音频处理

### WebRTC音频捕获
```javascript
// 获取麦克风权限
navigator.mediaDevices.getUserMedia({ 
  audio: {
    sampleRate: 24000,
    channelCount: 1,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true
  }
})
.then(stream => {
  const audioContext = new AudioContext({ sampleRate: 24000 });
  const source = audioContext.createMediaStreamSource(stream);
  const processor = audioContext.createScriptProcessor(4096, 1, 1);
  
  processor.onaudioprocess = (event) => {
    const audioData = event.inputBuffer.getChannelData(0);
    // 转换为 Int16Array 格式
    const int16Data = new Int16Array(audioData.length);
    for (let i = 0; i < audioData.length; i++) {
      int16Data[i] = Math.max(-32768, Math.min(32767, audioData[i] * 32768));
    }
    // 通过WebRTC数据通道发送到 OpenAI API
    client.sendAudio(int16Data);
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

## 翻译功能

### 语言检测
```javascript
async function detectLanguage(text) {
  const response = await fetch('/api/detect-language', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text })
  });
  return response.json();
}
```

### 翻译请求
```javascript
async function translateText(text, targetLang = 'zh-CN') {
  const response = await fetch('/api/translate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, target_lang: targetLang })
  });
  return response.json();
}
```

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

### 音频参数
- **采样率**: 24000 Hz
- **位深度**: 16-bit
- **声道数**: 单声道
- **格式**: PCM

### 网络参数
- **连接超时**: 30秒
- **重连间隔**: 5秒
- **最大重连次数**: 5次

### 性能参数
- **音频缓冲区大小**: 4096 samples
- **发送间隔**: 100ms
- **VAD 阈值**: 0.5
- **静音检测**: 200ms

---

*最后更新: 2025年1月*
