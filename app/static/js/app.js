// 使用分段上传调用 OpenAI Audio Transcriptions API
// 全局变量
let isConnected = false;
let isRecording = false;
let isShuttingDown = false;
let audioContext = null;
let mediaStream = null;
let transcripts = [];
let sessionId = null;
let volumeAnimationFrame = null;
let selectedAudioDevice = null;
let selectedAudioSource = 'microphone'; // 'microphone' 或 'tab'
let testStream = null;
let testAudioContext = null;
let isTestingMicrophone = false;
let currentStreamingTextMap = { primary: '', secondary: '' };
let currentTranscriptIdMap = { primary: null, secondary: null };
const STORAGE_KEY = 'meetingEZ_transcripts';
const STORAGE_VERSION = 2;
const HIDE_BEFORE_KEY = 'meetingEZ_hideBefore';

// 后置处理模型（结构化纠错与翻译）
const POST_PROCESS_MODEL = 'gpt-4.1-mini-2025-04-14';

// VAD 和回填更正相关变量
let vadThreshold = 0.02;  // 音量阈值（提高以减少误触发）
let isSpeaking = false;
let speechBuffer = [];
let correctionWindow = 800;  // 800ms 回填更正窗口
let lastSpeechTime = 0;
let silenceFrames = 0;  // 连续静音帧计数
const SILENCE_THRESHOLD = 30;  // 连续30帧静音才认为是真正静音（约600ms）

// 分段上传参数（8秒分段，1秒重叠）
const SEGMENT_DURATION_SEC = 8;
const OVERLAP_DURATION_SEC = 1;
let segmentSamples = 0;
let overlapSamples = 0;
let stepSamples = 0;
let aggregatedBuffer = new Float32Array(0); // 累积的采样缓冲
let segmentStartIndex = 0; // 下一段窗口的起始采样索引
let activeUploadControllers = new Set(); // 追踪在途请求以便停止时中断
let channelContextTail = { primary: '', secondary: '' }; // 每路保留上一段文本尾部上下文
let lastAcceptedTextMap = { primary: '', secondary: '' }; // 每路最近一次接受的文本
let lastAcceptedAtMap = { primary: 0, secondary: 0 }; // 每路最近一次接受的时间戳

// AudioWorklet 与 Worker 相关
let audioWorkletNode = null;
let wavEncoderWorker = null;
let pendingEncodings = new Map(); // 待完成的编码任务

const apiKeyInput = document.getElementById('apiKey');
const toggleBtn = document.getElementById('toggleApiKey');
const testBtn = document.getElementById('testConnection');
const saveBtn = document.getElementById('saveApiKey');
const startBtn = document.getElementById('startMeeting');
const stopBtn = document.getElementById('stopMeeting');
const statusDiv = document.getElementById('connectionStatus');
const transcriptContent = document.getElementById('transcriptContent');
let transcriptSplit = null;
let transcriptLeft = null;
let transcriptRight = null;

// 初始化
async function init() {
    loadSettings();
    setupEventListeners();
    await loadAudioDevices();
    updateControls();
    initializeAutoScroll();
}

// 加载设置
function loadSettings() {
    const savedApiKey = localStorage.getItem('meetingEZ_apiKey');
    if (savedApiKey) {
        apiKeyInput.value = savedApiKey;
    }
    
    // 加载音频源设置
    const savedAudioSource = localStorage.getItem('meetingEZ_audioSource') || 'microphone';
    selectedAudioSource = savedAudioSource;
    const audioSourceMic = document.getElementById('audioSourceMic');
    const audioSourceTab = document.getElementById('audioSourceTab');
    if (savedAudioSource === 'tab') {
        audioSourceTab.checked = true;
    } else {
        audioSourceMic.checked = true;
    }
    updateAudioInputVisibility();
    
    // 加载语言设置
    const primaryLang = localStorage.getItem('meetingEZ_primaryLanguage');
    if (primaryLang) {
        document.getElementById('primaryLanguage').value = primaryLang;
    }
    
    // 第二语言设置
    const secondaryLang = localStorage.getItem('meetingEZ_secondaryLanguage') || '';
    const secSelect = document.getElementById('secondaryLanguage');
    if (secSelect) {
        secSelect.value = secondaryLang;
    }
    // 使用语言模式
    const activeMode = localStorage.getItem('meetingEZ_activeLanguageMode') || 'primary';
    const activeModeSelect = document.getElementById('activeLanguageMode');
    if (activeModeSelect) {
        activeModeSelect.value = activeMode;
    }
    // 固定单栏显示
    enableSplitView(false);
}

// 设置事件监听器
function setupEventListeners() {
    toggleBtn.addEventListener('click', () => {
        if (apiKeyInput.type === 'password') {
            apiKeyInput.type = 'text';
            toggleBtn.textContent = '🙈';
        } else {
            apiKeyInput.type = 'password';
            toggleBtn.textContent = '👁️';
        }
    });

    testBtn.addEventListener('click', testConnection);

    saveBtn.addEventListener('click', saveApiKey);

    startBtn.addEventListener('click', startMeeting);
    stopBtn.addEventListener('click', stopMeeting);

    document.getElementById('downloadTranscript').addEventListener('click', downloadTranscript);
    document.getElementById('clearTranscript').addEventListener('click', clearTranscript);

    document.getElementById('autoScroll').addEventListener('click', toggleAutoScroll);

    document.getElementById('testMicrophone').addEventListener('click', toggleMicrophoneTest);

    // 音频源选择监听器
    document.getElementById('audioSourceMic').addEventListener('change', () => {
        selectedAudioSource = 'microphone';
        localStorage.setItem('meetingEZ_audioSource', 'microphone');
        updateAudioInputVisibility();
    });
    
    document.getElementById('audioSourceTab').addEventListener('change', () => {
        selectedAudioSource = 'tab';
        localStorage.setItem('meetingEZ_audioSource', 'tab');
        updateAudioInputVisibility();
    });

    if (navigator.mediaDevices) {
        if (typeof navigator.mediaDevices.addEventListener === 'function') {
            navigator.mediaDevices.addEventListener('devicechange', async () => {
                console.log('🔁 检测到音频设备变更，重新加载列表');
                await loadAudioDevices();
            });
        } else if ('ondevicechange' in navigator.mediaDevices) {
            navigator.mediaDevices.ondevicechange = async () => {
                console.log('🔁 检测到音频设备变更，重新加载列表');
                await loadAudioDevices();
            };
        }
    }

    document.getElementById('primaryLanguage').addEventListener('change', (e) => {
        localStorage.setItem('meetingEZ_primaryLanguage', e.target.value);
    });

    const secSelect = document.getElementById('secondaryLanguage');
    if (secSelect) {
        secSelect.addEventListener('change', (e) => {
            const value = (e.target.value || '').trim();
            localStorage.setItem('meetingEZ_secondaryLanguage', value);
        });
    }

    const activeModeSelect = document.getElementById('activeLanguageMode');
    if (activeModeSelect) {
        activeModeSelect.addEventListener('change', (e) => {
            const value = (e.target.value || 'primary');
            localStorage.setItem('meetingEZ_activeLanguageMode', value);
        });
    }

    document.getElementById('fontSize').addEventListener('change', (e) => {
        localStorage.setItem('meetingEZ_fontSize', e.target.value);
        updateFontSize();
    });

    document.querySelector('.close').addEventListener('click', () => {
        document.getElementById('errorModal').style.display = 'none';
    });

    apiKeyInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            saveApiKey();
        }
    });
}

// 测试连接
async function testConnection() {
    const apiKey = apiKeyInput.value.trim();
    if (!apiKey) {
        showStatus('请输入 API Key', 'error');
        return;
    }

    testBtn.disabled = true;
    testBtn.textContent = '测试中...';
    showStatus('正在测试连接...', 'info');

    try {
        // 使用 HTTP API 测试连接
        const response = await fetch('https://api.openai.com/v1/models', {
            headers: {
                'Authorization': `Bearer ${apiKey}`,
                'Content-Type': 'application/json'
            }
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        showStatus('连接成功！', 'success');
    } catch (error) {
        console.error('连接测试失败:', error);
        showStatus('连接失败: ' + error.message, 'error');
    } finally {
        testBtn.disabled = false;
        testBtn.textContent = '测试连接';
    }
}

// 保存 API Key
function saveApiKey() {
    const apiKey = apiKeyInput.value.trim();
    if (!apiKey) {
        showStatus('请输入 API Key', 'error');
        return;
    }

    localStorage.setItem('meetingEZ_apiKey', apiKey);
    showStatus('API Key 已保存', 'success');
    updateControls();
}

// 开始会议
async function startMeeting() {
    const apiKey = apiKeyInput.value.trim();
    if (!apiKey) {
        showError('请先配置并保存 API Key');
        return;
    }

    // 如果正在测试麦克风，先停止测试
    if (isTestingMicrophone) {
        stopMicrophoneTest();
        const testBtn = document.getElementById('testMicrophone');
        testBtn.textContent = '测试麦克风';
        testBtn.classList.remove('btn-danger');
        testBtn.classList.add('btn-outline');
    }

    try {
        showLoading('正在初始化会议...');

        // 根据选择的音频源获取音频流
        if (selectedAudioSource === 'tab') {
            // 使用标签页音频捕获
            try {
                const displayStream = await navigator.mediaDevices.getDisplayMedia({
                    video: true,  // 需要视频轨才能触发标签页选项
                    audio: true   // 关键：让用户勾选"共享标签页音频"
                });

                // 提取音频轨道
                const tabAudioTrack = displayStream.getAudioTracks()[0];

                // 检查是否成功获取音频
                if (!tabAudioTrack) {
                    // 停止视频轨道
                    displayStream.getTracks().forEach(track => track.stop());
                    throw new Error('未能获取标签页音频。请确保在弹窗中选择了"Chrome 标签页"并勾选了"共享标签页音频"选项。');
                }

                // 停止视频轨道（只需要音频）
                displayStream.getVideoTracks().forEach(track => track.stop());

                // 创建仅包含音频的 MediaStream
                mediaStream = new MediaStream([tabAudioTrack]);
                console.log('🎵 获取标签页音频成功');
            } catch (error) {
                if (error.name === 'NotAllowedError') {
                    throw new Error('用户取消了标签页共享。请重试并选择要捕获音频的标签页。');
                }
                throw error;
            }
        } else {
            // 使用麦克风输入（尽量保持原始音频）
            const audioConstraints = {
                echoCancellation: false,
                noiseSuppression: false,
                autoGainControl: false,
                sampleRate: 48000,
                channelCount: 1,
                sampleSize: 16,
                latency: 0.01,
                volume: 1.0,
                googEchoCancellation: false,
                googAutoGainControl: false,
                googNoiseSuppression: false,
                googHighpassFilter: false,
                googTypingNoiseDetection: false,
                googAudioMirroring: false
            };

            if (selectedAudioDevice) {
                audioConstraints.deviceId = { exact: selectedAudioDevice };
            }

            mediaStream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
            console.log('🎤 获取麦克风权限成功');
        }

        // 启动录音与分段上传流水线
        await startRecording();

        // UI 状态更新
        isConnected = true;
        updateControls();
        updateMeetingStatus('进行中', 'active');
        updateAudioStatus('已连接', 'active');
        disableSettings();
        hideLoading();
        showStatus('会议已开始', 'success');

        // 固定单栏显示
        enableSplitView(false);
    } catch (error) {
        console.error('开始会议失败:', error);
        hideLoading();
        showError('开始会议失败: ' + error.message);
        stopMeeting();
    }
}

// 停止会议
async function stopMeeting() {
    try {
        showLoading('正在结束会议...');

        // 停止录音
        stopRecording();

        // 无 WebRTC 连接，略

        isConnected = false;
        updateControls();
        updateMeetingStatus('已结束', '');
        updateAudioStatus('未连接', '');
        enableSettings(); // 解锁设置区

        // 保持当前视图状态（不强制切回单栏）

        hideLoading();
        showStatus('会议已结束', 'info');

    } catch (error) {
        console.error('停止会议失败:', error);
        hideLoading();
        showError('停止会议失败: ' + error.message);
    }
}

// （已移除）Realtime 消息处理：不再使用 WebRTC

// 开始录音
async function startRecording() {
    try {
        // 重置关闭标志
        isShuttingDown = false;
        
        // 创建音频上下文 - 使用48kHz采样率
        audioContext = new AudioContext({ sampleRate: 48000 });
        const source = audioContext.createMediaStreamSource(mediaStream);

        // 初始化分段参数
        segmentSamples = Math.round(SEGMENT_DURATION_SEC * audioContext.sampleRate);
        overlapSamples = Math.round(OVERLAP_DURATION_SEC * audioContext.sampleRate);
        stepSamples = segmentSamples - overlapSamples;
        aggregatedBuffer = new Float32Array(0);
        segmentStartIndex = 0;
        channelContextTail = { primary: '', secondary: '' };

        // 初始化 WAV 编码 Worker
        if (!wavEncoderWorker) {
            wavEncoderWorker = new Worker('/static/js/wav-encoder-worker.js');
            wavEncoderWorker.onmessage = handleWorkerMessage;
            wavEncoderWorker.onerror = (error) => {
                console.error('❌ Worker 错误:', error);
            };
        }

        // 加载并创建 AudioWorklet
        await audioContext.audioWorklet.addModule('/static/js/audio-processor.js');
        audioWorkletNode = new AudioWorkletNode(audioContext, 'audio-capture-processor');

        // 连接音频流
        source.connect(audioWorkletNode);
        audioWorkletNode.connect(audioContext.destination);

        // 监听来自 AudioWorklet 的消息
        audioWorkletNode.port.onmessage = (event) => {
            if (!isRecording) return;

            const { type, data, rms } = event.data;

            if (type === 'audio') {
                // 更新音量指示器
                const volume = Math.min(1, rms * 10);
                updateVolumeIndicator(volume);

                // 如果正在关闭，不再产生新的上传
                if (isShuttingDown) {
                    return;
                }

                // VAD：检测是否有语音活动
                const hasVoice = rms > vadThreshold;
                if (hasVoice) {
                    silenceFrames = 0;
                    isSpeaking = true;
                } else {
                    silenceFrames++;
                }

                // 只在检测到语音或最近有语音活动时才追加音频
                if (isSpeaking || silenceFrames < SILENCE_THRESHOLD) {
                    // 追加到累积缓冲
                    aggregatedBuffer = concatFloat32(aggregatedBuffer, data);

                    // 生成尽可能多的窗口（允许并发上传）
                    while (aggregatedBuffer.length >= segmentStartIndex + segmentSamples) {
                        const windowData = aggregatedBuffer.slice(segmentStartIndex, segmentStartIndex + segmentSamples);
                        queueSegmentUpload(windowData);
                        segmentStartIndex += stepSamples;

                        // 适度清理缓冲，避免无限增长
                        if (segmentStartIndex > segmentSamples * 2) {
                            const pruneAt = segmentStartIndex - overlapSamples;
                            aggregatedBuffer = aggregatedBuffer.slice(pruneAt);
                            segmentStartIndex -= pruneAt;
                        }
                    }
                } else if (silenceFrames === SILENCE_THRESHOLD) {
                    // 刚检测到持续静音，标记为非说话状态
                    isSpeaking = false;
                    console.log('🔇 检测到持续静音，停止上传音频段');
                }
            }
        };

        isRecording = true;
        console.log('🎙️ 开始录音并启动分段流水线（AudioWorklet + Worker 架构）');
    } catch (error) {
        console.error('❌ 开始录音失败:', error);
        throw error;
    }
}

// 拼接 Float32Array
function concatFloat32(a, b) {
    if (a.length === 0) return new Float32Array(b);
    const out = new Float32Array(a.length + b.length);
    out.set(a, 0);
    out.set(b, a.length);
    return out;
}

// 注意：encodeWav 函数已移至 wav-encoder-worker.js
// WAV 编码现在在独立的 Worker 线程中进行，不再阻塞主线程

// 队列化上传当前窗口：按"使用语言"一路上传
function queueSegmentUpload(float32Window) {
    try {
        // 生成唯一 ID
        const encodingId = Date.now() + Math.random();
        
        // 获取配置
        const apiKey = localStorage.getItem('meetingEZ_apiKey') || apiKeyInput.value.trim();
        const activeMode = (document.getElementById('activeLanguageMode')?.value || 'primary');
        const primaryLang = document.getElementById('primaryLanguage').value || 'en';
        const secondaryLang = (document.getElementById('secondaryLanguage')?.value || '').trim();
        const chosenLang = activeMode === 'secondary' ? (secondaryLang || primaryLang) : primaryLang;
        const promptTail = (activeMode === 'secondary')
          ? (channelContextTail.secondary || '')
          : (channelContextTail.primary || '');

        // 保存待编码任务信息
        pendingEncodings.set(encodingId, {
            apiKey,
            language: chosenLang,
            channel: 'single',
            promptTail
        });

        // 发送到 Worker 进行编码（主线程立即返回，不阻塞）
        wavEncoderWorker.postMessage({
            id: encodingId,
            float32Array: float32Window,
            sampleRate: audioContext.sampleRate
        });

        console.log('📦 音频窗口已发送到 Worker 编码:', { 
            id: encodingId, 
            samples: float32Window.length,
            pendingCount: pendingEncodings.size 
        });
    } catch (e) {
        console.error('❌ 队列分段上传失败:', e);
    }
}

// 处理 Worker 返回的编码结果
function handleWorkerMessage(event) {
    const { id, success, blob, error } = event.data;

    if (!success) {
        console.error('❌ Worker 编码失败:', error);
        pendingEncodings.delete(id);
        return;
    }

    // 获取待上传任务信息
    const task = pendingEncodings.get(id);
    if (!task) {
        console.warn('⚠️ 未找到对应的编码任务:', id);
        return;
    }

    // 清理任务
    pendingEncodings.delete(id);

    console.log('✅ Worker 编码完成，开始上传:', { 
        id, 
        blobSize: blob.size,
        remainingTasks: pendingEncodings.size 
    });

    // 上传 Blob
    transcribeBlob(blob, task.apiKey, task.language, task.channel, task.promptTail);
}

async function transcribeBlob(blob, apiKey, language, channel, promptTail) {
    const form = new FormData();
    form.append('model', 'gpt-4o-transcribe');
    if (language) form.append('language', language);
    if (promptTail) form.append('prompt', promptTail);
    form.append('response_format', 'json');
    form.append('file', blob, `segment_${Date.now()}_${channel}.wav`);

    const controller = new AbortController();
    activeUploadControllers.add(controller);

    try {
        console.log('📤 上传分段(单通道):', { language, sizeKB: Math.round(blob.size / 1024), inflight: activeUploadControllers.size });
        const resp = await fetch('https://api.openai.com/v1/audio/transcriptions', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${apiKey}` },
            body: form,
            signal: controller.signal
        });
        if (!resp.ok) {
            const errTxt = await resp.text().catch(() => '');
            throw new Error(`HTTP ${resp.status}: ${resp.statusText} ${errTxt}`);
        }
        const data = await resp.json();
        const text = (data && (data.text || data.transcript || data.result)) || '';
        console.log('📥 收到转写(单通道):', { language, length: text.length });
        if (text && text.trim()) {
            const primaryLang = document.getElementById('primaryLanguage')?.value || 'zh';
            const secondaryLang = (document.getElementById('secondaryLanguage')?.value || '').trim();
            // 将单通道结果映射到可视通道（便于分屏）：根据所用 language 判断归属
            const visualChannel = (secondaryLang && language === secondaryLang) ? 'secondary' : 'primary';

            // 先以原始转写创建一个临时记录（provisional），立即显示
            const provisionalId = Date.now() + Math.random();
            const provisional = {
                id: provisionalId,
                timestamp: new Date().toISOString(),
                text: normalizeText(text),
                language: language,
                channel: visualChannel,
                provisional: true
            };
            transcripts.push(provisional);
            saveTranscripts();
            updateDisplay(visualChannel);

            // 启动异步后置处理：合理化 + 若为第二语言则生成第一语言翻译
            try {
                const structured = await postProcessText(text, {
                    primaryLanguage: primaryLang || 'zh',
                    secondaryLanguage: secondaryLang || 'ja',
                    originalLanguageHint: language
                });
                applyPostProcessToTranscript(provisionalId, structured);
            } catch (ppErr) {
                console.warn('⚠️ 后置处理失败，保留原文:', ppErr);
            }

            // 更新上下文尾巴（截取最后200字符）
            const tail = text.trim();
            channelContextTail[channel] = tail.length > 200 ? tail.slice(-200) : tail;
        }
    } catch (e) {
        if (controller.signal.aborted) {
            console.warn('上传已中断');
        } else {
            console.error('❌ 转写请求失败:', { language, error: e });
        }
    } finally {
        activeUploadControllers.delete(controller);
        console.log('📉 在途请求减少:', { inflight: activeUploadControllers.size });
    }
}

// 停止录音
function stopRecording() {
    // 设置关闭标志，防止产生新的音频分段
    isShuttingDown = true;
    isRecording = false;

    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
    }

    // 断开 AudioWorklet
    if (audioWorkletNode) {
        audioWorkletNode.disconnect();
        audioWorkletNode = null;
    }

    if (audioContext) {
        audioContext.close();
        audioContext = null;
    }
    
    // 不再中断在途上传和编码，让它们自然完成
    const inflightCount = activeUploadControllers.size;
    const pendingCount = pendingEncodings.size;
    if (inflightCount > 0 || pendingCount > 0) {
        console.log(`📊 停止录音，但保留处理任务继续完成:`, {
            上传中: inflightCount,
            编码中: pendingCount
        });
    }
    
    // 重置音量指示器
    updateVolumeIndicator(0);

    console.log('🛑 停止录音（后续处理将继续完成）');
}

// 开始新的流式转录
function startNewStreamingTranscript(channel = 'primary') {
    currentStreamingTextMap[channel] = '';
    currentTranscriptIdMap[channel] = Date.now() + Math.random();
    console.log('🆕 开始新的流式转录:', channel, currentTranscriptIdMap[channel]);
}

// 检查是否为幻觉内容
function isHallucinationText(text) {
    // 常见的 Whisper 幻觉模式
    const hallucinationPatterns = [
        // 英文欢迎语
        /^(hi|hello|hey|welcome).*(channel|video|subscribe|youtube|like|comment)/i,
        /^thanks?\s+for\s+(watching|listening|subscribing)/i,
        /^(please|don't forget to).*(subscribe|like|comment|share)/i,
        /^(if you|when you).*(like|enjoy).*(this video|this content)/i,
        
        // 字幕相关
        /字幕|subtitle|caption|transcript/i,
        
        // 重复字符模式
        /^(\s*[a-z]\s*){8,}$/i,  // 重复单字母
        /^([a-z]-){4,}/i,  // 重复字母加横线
        /^(K-){4,}/i,  // K-K-K-K
        /^(o-){4,}/i,  // o-o-o-o
        /^(a-){4,}/i,  // a-a-a-a
        /^(e-){4,}/i,  // e-e-e-e
        /^(i-){4,}/i,  // i-i-i-i
        /^(u-){4,}/i,  // u-u-u-u
        
        // 无意义字符
        /^[\s\-\.]{8,}$/,  // 只有空格、横线、点
        /^[aeiou]{10,}$/i,  // 只有元音字母
        /^[bcdfghjklmnpqrstvwxyz]{10,}$/i,  // 只有辅音字母
        
        // 数字和符号混合
        /^[0-9\s\-\.]{10,}$/,  // 只有数字、空格、横线、点
        /^[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]{5,}$/,  // 只有特殊符号
        
        // 无意义的重复
        /^(.)\1{8,}$/,  // 同一字符重复9次以上
        /^(.{2})\1{4,}$/,  // 两个字符重复5次以上
        /^(.{3})\1{3,}$/,  // 三个字符重复4次以上
        
        // 常见的无意义短语
        /^(um|uh|ah|eh|oh)\s*$/i,  // 只有语气词
        /^(yeah|yes|no|ok|okay)\s*$/i,  // 只有简单回应
        /^(so|well|now|then)\s*$/i,  // 只有连接词
        
        // 检测到明显的语音识别错误
        /^[a-z]{1,3}\s+[a-z]{1,3}\s+[a-z]{1,3}$/i,  // 三个很短的单词
        /^[a-z]{15,}$/i,  // 一个很长的单词（可能是识别错误）
    ];
    
    // 检查文本长度，太短或太长的都可能是幻觉
    if (text.length < 3 || text.length > 200) {
        return true;
    }
    
    // 如果选择了日语作为主要语言，严格过滤英文内容
    const primaryLang = document.getElementById('primaryLanguage')?.value || 'en';
    if (primaryLang === 'ja' || primaryLang === 'zh') {
        // 检测是否包含大量英文单词
        const englishWords = text.match(/\b[a-zA-Z]{3,}\b/g) || [];
        if (englishWords.length > 2) {
            console.log('⚠️ 检测到英文幻觉内容，跳过:', text);
            return true;
        }
        
        // 检测常见的英文幻觉模式
        const englishHallucinationPatterns = [
            /how\s+do\s+you/i,
            /undermine/i,
            /meeeee/i,
            /what\s+are\s+you/i,
            /can\s+you\s+help/i,
            /i\s+am\s+a/i,
            /this\s+is\s+a/i,
            /let\s+me\s+know/i,
            /thank\s+you/i,
            /you\s+are\s+welcome/i
        ];
        
        if (englishHallucinationPatterns.some(pattern => pattern.test(text))) {
            console.log('⚠️ 检测到英文幻觉模式，跳过:', text);
            return true;
        }
    }
    
    // 检查是否包含太多重复字符
    const charCounts = {};
    for (let char of text.toLowerCase()) {
        if (char.match(/[a-z]/)) {
            charCounts[char] = (charCounts[char] || 0) + 1;
        }
    }
    
    const maxCharCount = Math.max(...Object.values(charCounts));
    if (maxCharCount > text.length * 0.6) {
        return true;  // 如果某个字符占比超过60%，可能是幻觉
    }
    
    return hallucinationPatterns.some(pattern => pattern.test(text));
}

// 不再进行前端语言检测，每个通道的结果直接显示在对应位置
// 左边通道 (primary) 的结果显示在左边
// 右边通道 (secondary) 的结果显示在右边
// OpenAI 的语言配置会确保各自只处理指定语言，无需前端过滤

// 更新流式转录（单通道）
function updateStreamingTranscript(delta, channel = 'single') {
    if (!delta) return;
    
    // 累积文本
    currentStreamingTextMap[channel] = (currentStreamingTextMap[channel] || '') + delta;
    console.log('📝 流式累积文本:', channel, currentStreamingTextMap[channel]);
    
    // 检查是否为幻觉内容（只检查完整文本，不阻止流式显示）
    if (isHallucinationText(currentStreamingTextMap[channel])) {
        console.log('⚠️ 检测到幻觉内容，但继续流式显示:', currentStreamingTextMap[channel]);
        // 不返回，继续显示，让用户看到流式效果
    }
    
    // 立即更新显示（实时流式效果）
    updateStreamingDisplay(channel);
    
    // 自动滚动
    if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
        scrollToBottom();
    }
}

// 提交当前的流式转录（单通道）
function commitCurrentTranscript(channel = 'single') {
    const text = currentStreamingTextMap[channel];
    if (text && text.trim() !== '') {
        console.log('✅ 提交转录:', channel, text);
        
        // 最后检查是否为幻觉内容
        if (isHallucinationText(text)) {
            console.log('⚠️ 提交时检测到幻觉内容，跳过保存');
            currentStreamingTextMap[channel] = '';
            currentTranscriptIdMap[channel] = null;
            updateDisplay(channel);
            return;
        }
        
        // 检查是否与最后一条记录重复
        const lastTranscript = transcripts[transcripts.length - 1];
        if (lastTranscript && lastTranscript.text === text.trim()) {
            console.log('⚠️ 检测到重复的转录，跳过保存');
            currentStreamingTextMap[channel] = '';
            currentTranscriptIdMap[channel] = null;
            updateDisplay(channel);
            return;
        }
        
        const normalized = normalizeText(text);
        if (shouldSkipByLastAccepted(normalized, channel)) {
            console.log('🚫 提交阶段检测：与最近一次结果重复/包含，跳过:', { channel, text: normalized });
            currentStreamingTextMap[channel] = '';
            currentTranscriptIdMap[channel] = null;
            updateDisplay(channel);
            return;
        }
        const transcript = {
            id: currentTranscriptIdMap[channel] || Date.now() + Math.random(),
            timestamp: new Date().toISOString(),
            text: normalized,
            language: detectLanguage(normalized),
            channel
        };
        
        transcripts.push(transcript);
        lastAcceptedTextMap[channel] = normalized;
        lastAcceptedAtMap[channel] = Date.now();
        saveTranscripts();
        
        // 重置流式状态
        currentStreamingTextMap[channel] = '';
        currentTranscriptIdMap[channel] = null;
        
        updateDisplay(channel);
        
        // 自动滚动
        if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
            scrollToBottom();
        }
    }
}

// 添加字幕（用于完整的转录结果）
function addTranscript(text, isComplete = false, channel = 'primary') {
    if (!text || text.trim() === '') return;

    console.log('➕ 添加字幕:', text.trim(), isComplete ? '(完整)' : '(增量)');

    if (isComplete) {
        // 规范化文本
        const normalized = normalizeText(text);

        // 近期去重（同一通道，最近若干条内存在相同或相似文本则跳过）
        if (isRecentDuplicate(normalized, channel, 12)) {
            console.log('♻️ 近期重复，跳过保存:', { channel, text: normalized });
            return;
        }

        // 若新文本是对最后一条的扩展（包含关系），在短时间窗内合并更新最后一条
        if (mergeWithLastIfExpanding(normalized, channel, 15000)) {
            console.log('🔁 与上一条合并更新:', { channel, text: normalized });
            return;
        }
        
        // 横切过滤：若与最近一次接受的文本相同或包含关系，直接跳过
        if (shouldSkipByLastAccepted(normalized, channel)) {
            console.log('🚫 与最近一次结果重复/包含，跳过:', { channel, text: normalized });
            return;
        }

        // 完整的转录结果，直接保存
        const transcript = {
            id: Date.now() + Math.random(),
            timestamp: new Date().toISOString(),
            text: normalized,
            language: detectLanguage(text.trim()),
            channel
        };

        transcripts.push(transcript);
        lastAcceptedTextMap[channel] = normalized;
        lastAcceptedAtMap[channel] = Date.now();
        saveTranscripts();
        updateDisplay(channel);
    } else {
        // 增量更新，累积到当前流式转录
        updateStreamingTranscript(text, channel);
    }

    // 自动滚动
    if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
        scrollToBottom();
    }
}

// 检测语言（改进的启发式方法）
function detectLanguage(text) {
    // 先检测日文（平假名或片假名）- 日文优先，因为日文也包含汉字
    const hasHiragana = /[\u3040-\u309f]/.test(text);
    const hasKatakana = /[\u30a0-\u30ff]/.test(text);
    const hasKanji = /[\u4e00-\u9fa5]/.test(text);
    
    // 如果有假名（平假名或片假名），则是日文
    if (hasHiragana || hasKatakana) {
        return 'ja';
    }
    
    // 检测韩文
    if (/[\uac00-\ud7af]/.test(text)) {
        return 'ko';
    }
    
    // 如果只有汉字且没有假名，检查是否为中文
    if (hasKanji && !hasHiragana && !hasKatakana) {
        // 简单区分简繁体
        const traditionalChars = /[繁體覽擇檢測]/;
        return traditionalChars.test(text) ? 'zh-TW' : 'zh';
    }
    
    // 检测西里尔字母（俄文）
    if (/[\u0400-\u04FF]/.test(text)) {
        return 'ru';
    }
    
    // 获取用户选择的主要语言作为默认
    const primaryLang = document.getElementById('primaryLanguage')?.value || 'en';
    return primaryLang;
}

// 更新流式显示（实时显示当前正在累积的文本）
function updateStreamingDisplay(channel = 'primary') {
    const transcriptContent = document.getElementById('transcriptContent');
    transcriptSplit = transcriptSplit || document.getElementById('transcriptSplit');
    transcriptLeft = transcriptLeft || document.getElementById('transcriptLeft');
    transcriptRight = transcriptRight || document.getElementById('transcriptRight');
    
    // 清除之前的流式显示
    const existingStreaming = document.getElementById('streaming-transcript');
    if (existingStreaming) {
        existingStreaming.remove();
    }
    
    // 显示当前流式文本
    const text = currentStreamingTextMap[channel];
    if (text && text.trim()) {
        // 创建流式显示元素
        const streamingElement = document.createElement('div');
        streamingElement.className = 'streaming-text';
        // 按通道使用独立 ID，防止互相覆盖
        streamingElement.id = `streaming-transcript-${channel}`;
        
        // 添加时间戳
        const timestamp = new Date().toLocaleTimeString();
        streamingElement.textContent = `${text} [${timestamp}]`;
        
    const container = transcriptSplit && transcriptSplit.style.display !== 'none'
        ? (channel === 'secondary' ? transcriptRight : transcriptLeft)
        : transcriptContent;
    // 清除当前通道的旧流式元素
    const old = container.querySelector(`#streaming-transcript-${channel}`);
    if (old) old.remove();
    container.appendChild(streamingElement);
        
        // 确保自动滚动到底部
        if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
            scrollToBottom();
        }
        
        console.log('🔄 流式显示更新:', channel, text);
    }
}

// 更新显示
function updateDisplay(channel = 'primary') {
    const transcriptContent = document.getElementById('transcriptContent');
    transcriptSplit = transcriptSplit || document.getElementById('transcriptSplit');
    transcriptLeft = transcriptLeft || document.getElementById('transcriptLeft');
    transcriptRight = transcriptRight || document.getElementById('transcriptRight');
    
    if (transcripts.length === 0 && !currentStreamingTextMap.primary && !currentStreamingTextMap.secondary && !currentStreamingTextMap.single) {
        transcriptContent.innerHTML = `
            <div class="welcome-message">
                <p>欢迎使用 MeetingEZ！</p>
                <p>请先配置 OpenAI API Key，然后点击"开始会议"开始使用。</p>
            </div>
        `;
        return;
    }

    const hideBefore = localStorage.getItem(HIDE_BEFORE_KEY);
    const displayTranscripts = transcripts
      .filter(t => !hideBefore || t.timestamp > hideBefore)
      .slice(-50); // 只显示最近50条（过滤隐藏阈值前的记录）
    
    // 显示已保存的记录，使用简洁格式
    const activeChannel = channel;
    const contentHtml = displayTranscripts
      .filter(t => (transcriptSplit && transcriptSplit.style.display !== 'none') ? t.channel === activeChannel : true)
      .map(transcript => {
        const time = new Date(transcript.timestamp).toLocaleTimeString();
        const textClass = transcript.isTranslation ? 'translation-text' : '';
        return `<div class="${textClass}">${escapeHtml(transcript.text)} [${time}]</div>`;
      }).join('');
    if (transcriptSplit && transcriptSplit.style.display !== 'none') {
        if (channel === 'secondary') {
            transcriptRight.innerHTML = contentHtml;
        } else {
            transcriptLeft.innerHTML = contentHtml;
        }
    } else {
        transcriptContent.innerHTML = contentHtml;
        if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
            scrollToBottom();
        }
    }
}

// 获取语言标签
function getLanguageLabel(language) {
    const labels = {
        'zh': '中文',
        'zh-TW': '中文',
        'ja': '日本語',
        'ko': '한국어',
        'en': 'EN',
        'es': 'ES',
        'fr': 'FR',
        'de': 'DE',
        'ru': 'RU',
        'pt': 'PT'
    };
    return labels[language] || language.toUpperCase();
}

// HTML 转义
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 滚动到底部
function scrollToBottom() {
    const container = (transcriptSplit && transcriptSplit.style.display !== 'none')
        ? transcriptLeft
        : transcriptContent;
    if (!container) return;
    requestAnimationFrame(() => {
        container.scrollTop = container.scrollHeight;
    });
}

// 保存字幕
function saveTranscripts() {
    const payload = { version: STORAGE_VERSION, items: transcripts };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

// 加载字幕
function loadTranscripts() {
    try {
        const stored = localStorage.getItem(STORAGE_KEY) || localStorage.getItem('meetingEZ_transcripts');
        if (stored) {
            const parsed = JSON.parse(stored);
            if (parsed && parsed.version === STORAGE_VERSION && Array.isArray(parsed.items)) {
                transcripts = parsed.items;
            } else if (Array.isArray(parsed)) {
                // 旧版本迁移：无channel信息，默认归为primary
                transcripts = parsed.map(t => ({ ...t, channel: t.channel || 'primary' }));
                saveTranscripts();
                // 移除旧key
                try { localStorage.removeItem('meetingEZ_transcripts'); } catch (e) {}
            } else {
                transcripts = [];
            }
            updateDisplay();
        }
    } catch (error) {
        console.error('加载字幕失败:', error);
        transcripts = [];
    }
}

// 下载记录
function downloadTranscript() {
    if (transcripts.length === 0) {
        alert('没有可导出的记录');
        return;
    }

    let content = 'MeetingEZ 会议记录\n';
    content += `导出时间: ${new Date().toLocaleString()}\n`;
    content += `总记录数: ${transcripts.length}\n`;
    content += '='.repeat(50) + '\n\n';

    transcripts.forEach((transcript, index) => {
        const time = new Date(transcript.timestamp).toLocaleString();
        content += `[${index + 1}] ${time}\n`;
        content += `内容: ${transcript.text}\n\n`;
    });

    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `meetingEZ_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// 清空记录
function clearTranscript() {
    if (confirm('确定要清空所有记录吗？此操作不可撤销。')) {
        console.time('⏱️ 清空记录耗时');
        
        // 清空本地存储的所有记录
        transcripts = [];
        
        // 清空流式缓冲
        currentStreamingTextMap.primary = '';
        currentStreamingTextMap.secondary = '';
        currentTranscriptIdMap.primary = null;
        currentTranscriptIdMap.secondary = null;
        
        // 设置隐藏阈值
        const nowIso = new Date().toISOString();
        localStorage.setItem(HIDE_BEFORE_KEY, nowIso);
        
        // 批量 DOM 操作：先清空 UI，再保存到 localStorage（避免阻塞）
        const transcriptContent = document.getElementById('transcriptContent');
        const transcriptLeft = document.getElementById('transcriptLeft');
        const transcriptRight = document.getElementById('transcriptRight');
        const transcriptSplit = document.getElementById('transcriptSplit');
        
        // 直接清空 DOM，不调用 updateDisplay（避免重复渲染）
        const secondaryLang = (document.getElementById('secondaryLanguage')?.value || '').trim();
        if (secondaryLang && transcriptSplit && transcriptSplit.style.display !== 'none') {
            // 分屏模式：清空左右两侧
            if (transcriptLeft) transcriptLeft.textContent = '';
            if (transcriptRight) transcriptRight.textContent = '';
        } else {
            // 单屏模式：显示欢迎消息
            if (transcriptContent) {
                transcriptContent.innerHTML = `
                    <div class="welcome-message">
                        <p>欢迎使用 MeetingEZ！</p>
                        <p>请先配置 OpenAI API Key，然后点击"开始会议"开始使用。</p>
                    </div>
                `;
            }
        }
        
        // 异步保存到 localStorage（不阻塞 UI）
        setTimeout(() => {
            saveTranscripts();
            console.timeEnd('⏱️ 清空记录耗时');
            console.log('✅ 已清空所有记录（包括本地存储和显示）');
        }, 0);
    }
}

// 更新控制按钮状态
function updateControls() {
    const hasApiKey = apiKeyInput.value.trim() !== '';
    startBtn.disabled = !hasApiKey || isConnected || isTestingMicrophone;
    stopBtn.disabled = !isConnected;
    
    const downloadBtn = document.getElementById('downloadTranscript');
    const clearBtn = document.getElementById('clearTranscript');
    downloadBtn.disabled = transcripts.length === 0;
    clearBtn.disabled = transcripts.length === 0;
    
    // 麦克风测试按钮状态
    const testBtn = document.getElementById('testMicrophone');
    testBtn.disabled = isConnected;
}

// 更新音频输入设备选择器的可见性
function updateAudioInputVisibility() {
    const audioInputContainer = document.getElementById('audioInputContainer');
    const tabAudioHint = document.getElementById('tabAudioHint');
    
    if (selectedAudioSource === 'microphone') {
        audioInputContainer.style.display = 'flex';
        if (tabAudioHint) {
            tabAudioHint.style.display = 'none';
        }
    } else {
        audioInputContainer.style.display = 'none';
        if (tabAudioHint) {
            tabAudioHint.style.display = 'block';
        }
    }
}

// 更新会议状态
function updateMeetingStatus(status, className) {
    const statusElement = document.getElementById('meetingStatus');
    statusElement.textContent = status;
    statusElement.className = `status-indicator ${className}`;
}

// 更新音频状态
function updateAudioStatus(status, className) {
    const statusElement = document.getElementById('audioStatus');
    statusElement.textContent = status;
    statusElement.className = `status-indicator ${className}`;
}

// 更新字体大小
function updateFontSize() {
    const fontSize = localStorage.getItem('meetingEZ_fontSize') || 'medium';
    transcriptContent.className = `transcript-content font-${fontSize}`;
}

// 禁用设置区（会议进行中）
function disableSettings() {
    const settingsInputs = [
        document.getElementById('apiKey'),
        document.getElementById('audioInput'),
        document.getElementById('primaryLanguage'),
        document.getElementById('secondaryLanguage'),
        document.getElementById('activeLanguageMode'),
        document.getElementById('fontSize')
    ];
    
    settingsInputs.forEach(input => {
        if (input) input.disabled = true;
    });
    
    // 禁用API Key相关按钮
    const testBtn = document.getElementById('testConnection');
    const saveBtn = document.getElementById('saveApiKey');
    const toggleBtn = document.getElementById('toggleApiKey');
    if (testBtn) testBtn.disabled = true;
    if (saveBtn) saveBtn.disabled = true;
    if (toggleBtn) toggleBtn.disabled = true;
    
    console.log('🔒 设置区已锁定');
}

// 启用设置区（会议结束后）
function enableSettings() {
    const settingsInputs = [
        document.getElementById('apiKey'),
        document.getElementById('audioInput'),
        document.getElementById('primaryLanguage'),
        document.getElementById('secondaryLanguage'),
        document.getElementById('activeLanguageMode'),
        document.getElementById('fontSize')
    ];
    
    settingsInputs.forEach(input => {
        if (input) input.disabled = false;
    });
    
    // 启用API Key相关按钮
    const testBtn = document.getElementById('testConnection');
    const saveBtn = document.getElementById('saveApiKey');
    const toggleBtn = document.getElementById('toggleApiKey');
    if (testBtn) testBtn.disabled = false;
    if (saveBtn) saveBtn.disabled = false;
    if (toggleBtn) toggleBtn.disabled = false;
    
    console.log('🔓 设置区已解锁');
}

// 辅助：启用/禁用左右分屏
function enableSplitView(enabled) {
    const content = document.getElementById('transcriptContent');
    transcriptSplit = transcriptSplit || document.getElementById('transcriptSplit');
    transcriptLeft = transcriptLeft || document.getElementById('transcriptLeft');
    transcriptRight = transcriptRight || document.getElementById('transcriptRight');
    if (!content || !transcriptSplit) {
        console.warn('⚠️ enableSplitView: 容器未就绪，跳过');
        return;
    }
    if (enabled) {
        content.style.display = 'none';
        transcriptSplit.style.display = 'grid';
        // 初始化时不清空内容，保留已有数据
        console.log('✅ 已启用左右分屏模式');
    } else {
        transcriptSplit.style.display = 'none';
        content.style.display = 'block';
        console.log('✅ 已切换到单栏模式');
    }
}

// （已移除）第二通道逻辑：改为双语言并行上传 REST 调用

// 显示状态消息
function showStatus(message, type) {
    statusDiv.textContent = message;
    statusDiv.className = `status-message ${type}`;
    statusDiv.style.display = 'block';

    setTimeout(() => {
        statusDiv.style.display = 'none';
    }, 3000);
}

// 显示错误消息
function showError(message) {
    const modal = document.getElementById('errorModal');
    const messageElement = document.getElementById('errorMessage');
    messageElement.textContent = message;
    modal.style.display = 'block';
}

// 显示加载状态
function showLoading(message) {
    const overlay = document.getElementById('loadingOverlay');
    overlay.querySelector('p').textContent = message;
    overlay.style.display = 'flex';
}

// 隐藏加载状态
function hideLoading() {
    document.getElementById('loadingOverlay').style.display = 'none';
}

// 加载音频设备列表
async function loadAudioDevices() {
    try {
        const audioInputSelect = document.getElementById('audioInput');

        // 非安全上下文（HTTP 非 localhost）在 Android 上会禁止麦克风权限与设备枚举
        if (!window.isSecureContext) {
            console.warn('⚠️ 非安全上下文：Android 浏览器需要 HTTPS 或 localhost 才能访问麦克风');
            if (audioInputSelect) {
                audioInputSelect.innerHTML = '<option value="">需要在 HTTPS 或 localhost 下使用</option>';
            }
            showStatus('Android 需 HTTPS/localhost 才能加载输入设备与弹出权限框', 'error');
            return;
        }

        let devices = await navigator.mediaDevices.enumerateDevices();
        let audioInputs = devices.filter(device => device.kind === 'audioinput');
        const labelsMissing = audioInputs.some(d => !d.label);
        audioInputSelect.innerHTML = '';
        
        // 权限预热：若没有设备或设备 label 为空，多数是未授权，需要先请求一次最小权限
        if (audioInputs.length === 0 || labelsMissing) {
            try {
                console.log('🟡 权限预热：请求最小麦克风权限以解锁设备列表与标签');
                const prewarm = await navigator.mediaDevices.getUserMedia({ audio: true });
                // 立即释放
                prewarm.getTracks().forEach(t => t.stop());
                // 重新枚举
                devices = await navigator.mediaDevices.enumerateDevices();
                audioInputs = devices.filter(device => device.kind === 'audioinput');
            } catch (e) {
                console.warn('🔒 权限预热失败：', e);
                if (audioInputSelect) {
                    audioInputSelect.innerHTML = '<option value="">未授权麦克风或被浏览器阻止</option>';
                }
                showStatus('请允许麦克风权限后重试', 'error');
                return;
            }
        }

        if (audioInputs.length === 0) {
            audioInputSelect.innerHTML = '<option value="">无可用设备</option>';
            return;
        }
        
        audioInputs.forEach((device, index) => {
            const option = document.createElement('option');
            option.value = device.deviceId;
            option.text = device.label || `麦克风 ${index + 1}`;
            audioInputSelect.appendChild(option);
        });
        
        // 加载保存的设备选择
        const savedDevice = localStorage.getItem('meetingEZ_audioDevice');
        if (savedDevice && audioInputs.some(d => d.deviceId === savedDevice)) {
            audioInputSelect.value = savedDevice;
            selectedAudioDevice = savedDevice;
        } else {
            selectedAudioDevice = audioInputs[0].deviceId;
            audioInputSelect.value = selectedAudioDevice;
        }
        
        // 监听设备变化
        audioInputSelect.addEventListener('change', (e) => {
            selectedAudioDevice = e.target.value;
            localStorage.setItem('meetingEZ_audioDevice', selectedAudioDevice);
            console.log('🎤 选择音频设备:', selectedAudioDevice);
        });
        
        console.log('🎤 加载了', audioInputs.length, '个音频输入设备');
    } catch (error) {
        console.error('❌ 加载音频设备失败:', error);
    }
}

// 更新音量指示器
function updateVolumeIndicator(volume) {
    const volumeBar = document.getElementById('volumeBar');
    if (!volumeBar) return;
    
    const percentage = Math.min(100, volume * 100);
    volumeBar.style.width = `${percentage}%`;
    
    // 根据音量大小改变颜色
    if (percentage > 70) {
        volumeBar.classList.add('high');
    } else {
        volumeBar.classList.remove('high');
    }
}

// 切换麦克风测试
async function toggleMicrophoneTest() {
    const testBtn = document.getElementById('testMicrophone');
    
    if (isTestingMicrophone) {
        // 停止测试
        stopMicrophoneTest();
        testBtn.textContent = '测试麦克风';
        testBtn.classList.remove('btn-danger');
        testBtn.classList.add('btn-outline');
    } else {
        // 开始测试
        try {
            await startMicrophoneTest();
            testBtn.textContent = '停止测试';
            testBtn.classList.remove('btn-outline');
            testBtn.classList.add('btn-danger');
        } catch (error) {
            console.error('❌ 麦克风测试失败:', error);
            showError('麦克风测试失败: ' + error.message);
        }
    }
}

// 开始麦克风测试
async function startMicrophoneTest() {
    console.log('🎤 开始麦克风测试');
    
    const audioConstraints = {
        sampleRate: 24000,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
    };
    
    // 如果选择了特定设备，使用该设备
    if (selectedAudioDevice) {
        audioConstraints.deviceId = { exact: selectedAudioDevice };
    }
    
    testStream = await navigator.mediaDevices.getUserMedia({
        audio: audioConstraints
    });
    
    testAudioContext = new AudioContext({ sampleRate: 24000 });
    const source = testAudioContext.createMediaStreamSource(testStream);
    const analyser = testAudioContext.createAnalyser();
    analyser.fftSize = 256;
    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    
    source.connect(analyser);
    
    isTestingMicrophone = true;
    
    // 开始音量监测循环
    function monitorVolume() {
        if (!isTestingMicrophone) return;
        
        analyser.getByteFrequencyData(dataArray);
        
        // 计算平均音量
        let sum = 0;
        for (let i = 0; i < bufferLength; i++) {
            sum += dataArray[i];
        }
        const average = sum / bufferLength;
        const volume = average / 255; // 归一化到 0-1
        
        updateVolumeIndicator(volume);
        
        volumeAnimationFrame = requestAnimationFrame(monitorVolume);
    }
    
    monitorVolume();
    console.log('✅ 麦克风测试已开始');
}

// 停止麦克风测试
function stopMicrophoneTest() {
    console.log('🛑 停止麦克风测试');
    
    isTestingMicrophone = false;
    
    if (volumeAnimationFrame) {
        cancelAnimationFrame(volumeAnimationFrame);
        volumeAnimationFrame = null;
    }
    
    if (testStream) {
        testStream.getTracks().forEach(track => track.stop());
        testStream = null;
    }
    
    if (testAudioContext) {
        testAudioContext.close();
        testAudioContext = null;
    }
    
    // 重置音量指示器
    updateVolumeIndicator(0);
}

// 切换自动滚动
function toggleAutoScroll() {
    const autoScrollBtn = document.getElementById('autoScroll');
    const isActive = autoScrollBtn.classList.contains('btn-primary');
    
    if (isActive) {
        // 关闭自动滚动
        autoScrollBtn.classList.remove('btn-primary');
        autoScrollBtn.classList.add('btn-outline');
        autoScrollBtn.textContent = '滚';
        localStorage.setItem('meetingEZ_autoScroll', 'false');
        console.log('📜 自动滚动已关闭');
    } else {
        // 开启自动滚动
        autoScrollBtn.classList.remove('btn-outline');
        autoScrollBtn.classList.add('btn-primary');
        autoScrollBtn.textContent = '滚✓';
        localStorage.setItem('meetingEZ_autoScroll', 'true');
        // 立即滚动到底部
        scrollToBottom();
        console.log('📜 自动滚动已开启');
    }
}


// 初始化自动滚动状态
function initializeAutoScroll() {
    const autoScrollBtn = document.getElementById('autoScroll');
    const savedAutoScroll = localStorage.getItem('meetingEZ_autoScroll');
    
    // 默认开启自动滚动
    const shouldAutoScroll = savedAutoScroll !== 'false';
    
    if (shouldAutoScroll) {
        autoScrollBtn.classList.remove('btn-outline');
        autoScrollBtn.classList.add('btn-primary');
        autoScrollBtn.textContent = '滚✓';
    } else {
        autoScrollBtn.classList.remove('btn-primary');
        autoScrollBtn.classList.add('btn-outline');
        autoScrollBtn.textContent = '滚';
    }
    
    console.log('📜 自动滚动状态已初始化:', shouldAutoScroll ? '开启' : '关闭');
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    // 先准备分屏容器（懒创建，若模板未渲染则动态创建）
    transcriptSplit = document.getElementById('transcriptSplit');
    if (!transcriptSplit) {
        transcriptSplit = document.createElement('div');
        transcriptSplit.id = 'transcriptSplit';
        transcriptSplit.className = 'transcript-split';
        transcriptSplit.style.display = 'none';
        transcriptLeft = document.createElement('div');
        transcriptLeft.id = 'transcriptLeft';
        transcriptLeft.className = 'transcript-pane';
        transcriptRight = document.createElement('div');
        transcriptRight.id = 'transcriptRight';
        transcriptRight.className = 'transcript-pane';
        transcriptSplit.appendChild(transcriptLeft);
        transcriptSplit.appendChild(transcriptRight);
        const container = document.querySelector('.transcript-container');
        if (container) {
            container.appendChild(transcriptSplit);
        }
    }
    
    // 然后初始化（这样 loadSettings 中的 enableSplitView 才能正常工作）
    init();
    loadTranscripts();
    
    // 加载完历史记录后，如果是分屏模式，需要渲染左右两侧
    const secondaryLang = (localStorage.getItem('meetingEZ_secondaryLanguage') || '').trim();
    if (secondaryLang && transcriptSplit && transcriptSplit.style.display !== 'none') {
        updateDisplay('primary');
        updateDisplay('secondary');
    }
});

function normalizeText(text) {
    const t = (text || '').trim();
    // 统一句末标点（去除重复句号，保留一个）
    return t.replace(/[。\.]{2,}$/u, (m) => m[0]);
}

function isRecentDuplicate(normalized, channel, lookbackCount = 12) {
    const recent = transcripts
        .filter(t => t.channel === channel)
        .slice(-lookbackCount)
        .map(t => normalizeText(t.text));
    const last = recent[recent.length - 1] || '';
    if (recent.includes(normalized)) return true;
    // 与最后一条几乎相等（去空格）
    if (last && stripSpaces(last) === stripSpaces(normalized)) return true;
    return false;
}

function mergeWithLastIfExpanding(normalized, channel, windowMs = 15000) {
    const last = transcripts.slice(-1)[0];
    if (!last || last.channel !== channel) return false;
    const lastTs = new Date(last.timestamp).getTime();
    if (Date.now() - lastTs > windowMs) return false;
    // 新文本是旧文本的扩展（包含关系且长度更长）
    if (normalized.length > last.text.length && normalized.startsWith(last.text)) {
        last.text = normalized;
        last.timestamp = new Date().toISOString();
        saveTranscripts();
        updateDisplay(channel);
        return true;
    }
    return false;
}

function shouldSkipByLastAccepted(normalized, channel) {
    const lastText = lastAcceptedTextMap[channel] || '';
    if (!lastText) return false;
    if (normalized === lastText) return true;
    // 若完全包含（例如因 1 秒重叠导致同一句被重复识别）
    if (normalized.includes(lastText) || lastText.includes(normalized)) return true;
    return false;
}

function stripSpaces(s) {
    return (s || '').replace(/\s+/g, '');
}

// ---------------------------
// 后置处理：结构化纠错与翻译
// ---------------------------

async function postProcessText(originalText, opts = {}) {
    const apiKey = localStorage.getItem('meetingEZ_apiKey') || apiKeyInput.value.trim();
    if (!apiKey) throw new Error('缺少 API Key');
    const primaryLanguage = opts.primaryLanguage || 'zh';
    const secondaryLanguage = opts.secondaryLanguage || 'ja';
    const originalLanguageHint = opts.originalLanguageHint || primaryLanguage;
    
    console.log('🔄 开始翻译处理:', { 
        originalText, 
        primaryLanguage, 
        secondaryLanguage, 
        originalLanguageHint 
    });

    const system = [
        '你是实时字幕的翻译助手：',
        '1) 判断文本语言。',
        '2) 若文本语言不是第一语言（primary_language），则提供第一语言的准确翻译；否则翻译字段为 null。',
        '3) 输出严格的 JSON（不要包含多余说明）。',
    ].join('\n');

    const user = JSON.stringify({
        task: 'translate_transcript',
        primary_language: primaryLanguage,
        secondary_language: secondaryLanguage,
        original_language_hint: originalLanguageHint,
        text: originalText
    });

    const jsonSchema = {
        name: 'TranslateTranscript',
        schema: {
            $schema: 'http://json-schema.org/draft-07/schema#',
            type: 'object',
            additionalProperties: false,
            required: ['originalLanguage', 'isNotPrimaryLanguage', 'primaryTranslation'],
            properties: {
                originalLanguage: { type: 'string', description: '判定的文本语言，ISO 简写，如 zh/ja/en' },
                isNotPrimaryLanguage: { type: 'boolean', description: '文本语言是否不是第一语言（primary_language，不是的话需要翻译）' },
                primaryTranslation: {
                    description: '若不是第一语言，这里为第一语言（primary_language）的翻译；否则为 null',
                    anyOf: [ { type: 'string' }, { type: 'null' } ]
                }
            }
        }
    };

    const payload = {
        model: POST_PROCESS_MODEL,
        input: [
            { role: 'system', content: system },
            { role: 'user', content: user }
        ],
        text: {
            format: {
                type: 'json_schema',
                name: jsonSchema.name,
                schema: jsonSchema.schema,
                strict: true
            }
        }
    };

    console.log('📤 发送翻译请求:', payload);
    
    const resp = await fetchWithRetry('https://api.openai.com/v1/responses', {
        method: 'POST',
        headers: {
            'Authorization': `Bearer ${apiKey}`,
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
    });
    if (!resp.ok) {
        const errTxt = await resp.text().catch(() => '');
        console.error('❌ 翻译请求失败:', { status: resp.status, error: errTxt });
        throw new Error(`HTTP ${resp.status}: ${resp.statusText} ${errTxt}`);
    }
    const result = await resp.json();
    console.log('📥 收到翻译响应:', result);
    console.log('📥 响应详细结构:', JSON.stringify(result, null, 2));
    
    // 优先使用 output_parsed，其次文本
    let structured = result && result.output_parsed ? result.output_parsed : null;
    console.log('🔍 output_parsed:', structured);
    
    if (!structured) {
        // Responses API 返回格式：output 是数组，找到 type='message' 的项
        let textOut = '';
        if (result && result.output && Array.isArray(result.output)) {
            const messageOutput = result.output.find(o => o.type === 'message');
            if (messageOutput && messageOutput.content && messageOutput.content[0]) {
                textOut = messageOutput.content[0].text || '';
            }
        }
        console.log('🔍 textOut:', textOut);
        try {
            if (typeof textOut === 'string' && textOut) {
                structured = JSON.parse(textOut);
            } else if (typeof textOut === 'object' && textOut) {
                structured = textOut;
            }
        } catch (e) {
            console.error('🔍 JSON解析失败:', e);
            structured = null;
        }
    }

    if (!structured || !structured.originalLanguage) {
        // 回退：若解析失败，按最小结构返回
        console.warn('⚠️ 解析失败，使用回退结构');
        return {
            originalLanguage: originalLanguageHint,
            isNotPrimaryLanguage: originalLanguageHint !== primaryLanguage,
            primaryTranslation: null
        };
    }
    // 兜底字段
    structured.isNotPrimaryLanguage = !!structured.isNotPrimaryLanguage;
    structured.primaryTranslation = structured.primaryTranslation || null;
    
    console.log('✅ 翻译处理完成:', {
        originalLanguage: structured.originalLanguage,
        isNotPrimaryLanguage: structured.isNotPrimaryLanguage,
        primaryTranslation: structured.primaryTranslation
    });
    
    return structured;
}

// 简单的重试封装（对429/5xx退避重试）
async function fetchWithRetry(url, options, retries = 2, backoffMs = 800) {
    let attempt = 0;
    while (true) {
        const resp = await fetch(url, options);
        if (resp.ok) return resp;
        const status = resp.status;
        if (attempt >= retries || ![429, 500, 502, 503, 504].includes(status)) {
            return resp; // 由上层抛错并显示信息
        }
        await new Promise(r => setTimeout(r, backoffMs * Math.pow(2, attempt)));
        attempt += 1;
    }
}

function applyPostProcessToTranscript(provisionalId, structured) {
    const idx = transcripts.findIndex(t => t.id === provisionalId);
    if (idx === -1) {
        console.warn('⚠️ 未找到临时记录:', provisionalId);
        return;
    }
    const entry = transcripts[idx];
    
    console.log('🔧 应用翻译结果到记录:', {
        provisionalId,
        originalText: entry.text,
        detectedLanguage: structured.originalLanguage,
        isNotPrimaryLanguage: structured.isNotPrimaryLanguage,
        translation: structured.primaryTranslation
    });
    
    // 不再覆盖文本，保留原转写结果
    entry.language = structured.originalLanguage || entry.language;
    delete entry.provisional;
    entry.timestamp = new Date().toISOString();

    // 若不是第一语言，插入第一语言翻译（插入在其后方）
    if (structured.isNotPrimaryLanguage && structured.primaryTranslation) {
        console.log('✅ 插入翻译:', structured.primaryTranslation);
        const translationEntry = {
            id: Date.now() + Math.random(),
            timestamp: new Date().toISOString(),
            text: normalizeText(structured.primaryTranslation),
            language: 'zh', // 第一语言
            channel: entry.channel, // 插入同一可视通道的下一行
            meta: { translationOf: provisionalId, primaryTranslation: true },
            isTranslation: true // 标记为翻译行
        };
        transcripts.splice(idx + 1, 0, translationEntry);
    } else {
        console.log('ℹ️ 不需要翻译（已是第一语言或无翻译结果）');
    }

    saveTranscripts();
    // 根据通道最小刷新
    updateDisplay(entry.channel || 'primary');
}