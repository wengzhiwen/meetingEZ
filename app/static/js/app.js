// 直接使用 WebRTC 连接 OpenAI Realtime API
// 全局变量
let isConnected = false;
let isRecording = false;
let audioContext = null;
let mediaStream = null;
let transcripts = [];
let sessionId = null;
let peerConnection = null;
let dataChannel = null;
let volumeAnimationFrame = null;
let selectedAudioDevice = null;
let testStream = null;
let testAudioContext = null;
let isTestingMicrophone = false;
let currentStreamingText = '';
let currentTranscriptId = null;

// VAD 和回填更正相关变量
let vadThreshold = 0.01;  // 音量阈值
let isSpeaking = false;
let speechBuffer = [];
let correctionWindow = 800;  // 800ms 回填更正窗口
let lastSpeechTime = 0;

// DOM 元素
const apiKeyInput = document.getElementById('apiKey');
const toggleBtn = document.getElementById('toggleApiKey');
const testBtn = document.getElementById('testConnection');
const saveBtn = document.getElementById('saveApiKey');
const startBtn = document.getElementById('startMeeting');
const stopBtn = document.getElementById('stopMeeting');
const statusDiv = document.getElementById('connectionStatus');
const transcriptContent = document.getElementById('transcriptContent');

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
    
    // 加载语言设置
    const primaryLang = localStorage.getItem('meetingEZ_primaryLanguage');
    if (primaryLang) {
        document.getElementById('primaryLanguage').value = primaryLang;
    }
    
    // 移除二语言与自动翻译
}

// 设置事件监听器
function setupEventListeners() {
    // API Key 显示/隐藏
    toggleBtn.addEventListener('click', () => {
        if (apiKeyInput.type === 'password') {
            apiKeyInput.type = 'text';
            toggleBtn.textContent = '🙈';
        } else {
            apiKeyInput.type = 'password';
            toggleBtn.textContent = '👁️';
        }
    });

    // 测试连接
    testBtn.addEventListener('click', testConnection);

    // 保存 API Key
    saveBtn.addEventListener('click', saveApiKey);

    // 会议控制
    startBtn.addEventListener('click', startMeeting);
    stopBtn.addEventListener('click', stopMeeting);

    // 数据管理
    document.getElementById('downloadTranscript').addEventListener('click', downloadTranscript);
    document.getElementById('clearTranscript').addEventListener('click', clearTranscript);

    // 字幕控制
    document.getElementById('autoScroll').addEventListener('click', toggleAutoScroll);
    document.getElementById('clearScreen').addEventListener('click', clearScreen);

    // 麦克风测试
    document.getElementById('testMicrophone').addEventListener('click', toggleMicrophoneTest);

    // 监听系统设备变更（插拔耳机等）
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

    // 设置
    document.getElementById('primaryLanguage').addEventListener('change', (e) => {
        localStorage.setItem('meetingEZ_primaryLanguage', e.target.value);
    });

    // 已移除二语言与自动翻译事件

    document.getElementById('fontSize').addEventListener('change', (e) => {
        localStorage.setItem('meetingEZ_fontSize', e.target.value);
        updateFontSize();
    });

    // 模态框关闭
    document.querySelector('.close').addEventListener('click', () => {
        document.getElementById('errorModal').style.display = 'none';
    });

    // 回车键保存
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

        // 1. 获取麦克风权限 - 优化音频质量设置
        const audioConstraints = {
            // 关键优化：关闭浏览器默认的音频处理，保持原始音频质量
            echoCancellation: false,      // 关闭回声消除，避免细节丢失
            noiseSuppression: false,      // 关闭降噪，保持发音细节
            autoGainControl: false,       // 关闭自动增益，避免音量波动
            
            // 锁定到语音最佳参数
            sampleRate: 48000,           // 48kHz 采样率（从24kHz提升）
            channelCount: 1,             // 单声道
            sampleSize: 16,              // 16位采样
            
            // 音频格式优化
            latency: 0.01,               // 10ms 延迟
            volume: 1.0,                 // 固定音量
            
            // 高级音频约束（Chrome 特有）
            googEchoCancellation: false,
            googAutoGainControl: false,
            googNoiseSuppression: false,
            googHighpassFilter: false,
            googTypingNoiseDetection: false,
            googAudioMirroring: false
        };
        
        // 如果选择了特定设备，使用该设备
        if (selectedAudioDevice) {
            audioConstraints.deviceId = { exact: selectedAudioDevice };
        }
        
        mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: audioConstraints
        });
        
        console.log('🎤 获取麦克风权限成功');
        
        // 检查音频质量设置
        const audioTracks = mediaStream.getAudioTracks();
        if (audioTracks.length > 0) {
            const track = audioTracks[0];
            const settings = track.getSettings();
            console.log('🎵 音频设置:', {
                sampleRate: settings.sampleRate,
                channelCount: settings.channelCount,
                echoCancellation: settings.echoCancellation,
                noiseSuppression: settings.noiseSuppression,
                autoGainControl: settings.autoGainControl
            });
            
            // 检查是否使用了优化设置
            if (settings.sampleRate === 48000 && 
                settings.echoCancellation === false && 
                settings.noiseSuppression === false) {
                console.log('✅ 音频质量优化已启用');
            } else {
                console.warn('⚠️ 音频质量可能未完全优化，建议使用耳机麦克风');
            }
        }

        // 2. 创建 WebRTC 连接
        peerConnection = new RTCPeerConnection({
            iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
        });

        // 3. 添加音频轨道
        peerConnection.addTrack(mediaStream.getAudioTracks()[0]);

        // 4. 创建数据通道
        dataChannel = peerConnection.createDataChannel('oai-events');
        
        dataChannel.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                console.log('📥 收到消息:', data.type, data);
                handleRealtimeMessage(data);
            } catch (error) {
                console.error('❌ 解析消息失败:', error);
            }
        };

        dataChannel.onopen = async () => {
            console.log('✅ DataChannel 已打开');
            
            // 获取用户选择的语言
            const primaryLang = document.getElementById('primaryLanguage').value || 'en';
            const secondaryLang = '';
            
            // 构建语言提示
            const languageNames = {
                'zh': '简体中文',
                'zh-TW': '繁体中文',
                'en': '英文',
                'ja': '日文',
                'ko': '韩文',
                'es': '西班牙文',
                'fr': '法文',
                'de': '德文',
                'ru': '俄文',
                'pt': '葡萄牙文'
            };
            
            let instructions = '你是一个专业的会议助手，负责实时转录会议内容。';
            instructions += `主要语言是${languageNames[primaryLang]}。`;
            if (secondaryLang) {
                instructions += `第二语言是${languageNames[secondaryLang]}。`;
            }
            instructions += '请准确识别并转录用户说的话。';
            instructions += '只转录用户的实际语音内容，不要生成任何回复或解释。';
            instructions += '如果检测到非目标语言的内容，请忽略。';
            
            // 发送会话配置
            const configMessage = {
                type: 'session.update',
                session: {
                    model: 'gpt-4o-realtime-preview-2024-10-01',  // 指定 Realtime 模型
                    instructions: instructions,
                    voice: 'alloy',
                    modalities: ['text', 'audio'],
                    input_audio_format: 'pcm16',
                    output_audio_format: 'pcm16',
                    turn_detection: {
                        type: 'server_vad',
                        threshold: 0.2,  // 进一步降低阈值，提高灵敏度
                        prefix_padding_ms: 500,  // 增加前缀填充，捕获语音开始
                        silence_duration_ms: 800  // 增加静音时长，允许更长上下文
                    },
                    input_audio_transcription: {
                        model: 'whisper-1',
                        language: primaryLang  // 强制指定主要语言
                        // 目前 Realtime API 只支持 whisper-1
                    },
                    temperature: 0.6,  // 最低允许值，减少随机性，提高准确性
                    max_response_output_tokens: 1
                }
            };
            console.log('📤 发送会话配置:', configMessage);
            console.log('🌍 语言设置: 主要=' + primaryLang + ', 第二=' + (secondaryLang || '无'));
            dataChannel.send(JSON.stringify(configMessage));
            
            // 开始录音
            await startRecording();
            
            isConnected = true;
            updateControls();
            updateMeetingStatus('进行中', 'active');
            updateAudioStatus('已连接', 'active');
            hideLoading();
            showStatus('会议已开始', 'success');
        };

        dataChannel.onerror = (error) => {
            console.error('❌ Data channel error:', error);
            if (error.error) {
                console.error('错误详情:', error.error.message);
            }
            showError('数据通道错误: ' + (error.error?.message || error.message || '未知错误'));
        };

        dataChannel.onclose = () => {
            console.log('⚠️ Data channel closed');
            isConnected = false;
            updateControls();
            updateMeetingStatus('已结束', '');
            updateAudioStatus('未连接', '');
            
            // 停止录音
            if (isRecording) {
                stopRecording();
                showError('数据通道已关闭，会议已结束');
            }
        };

        // 5. 创建 SDP offer
        const offer = await peerConnection.createOffer();
        await peerConnection.setLocalDescription(offer);

        // 6. 发送 SDP offer 到 OpenAI
        const baseUrl = 'https://api.openai.com/v1/realtime';
        const model = 'gpt-4o-realtime-preview-2024-10-01';
        const sdpResponse = await fetch(`${baseUrl}?model=${model}`, {
            method: 'POST',
            body: offer.sdp,
            headers: {
                'Authorization': `Bearer ${apiKey}`,
                'Content-Type': 'application/sdp',
            },
        });

        if (!sdpResponse.ok) {
            throw new Error(`SDP exchange failed: ${sdpResponse.status}`);
        }

        // 7. 设置远程描述
        const answer = {
            type: 'answer',
            sdp: await sdpResponse.text(),
        };
        await peerConnection.setRemoteDescription(answer);

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

        // 关闭 WebRTC 连接
        if (dataChannel) {
            dataChannel.close();
            dataChannel = null;
        }
        
        if (peerConnection) {
            peerConnection.close();
            peerConnection = null;
        }

        isConnected = false;
        updateControls();
        updateMeetingStatus('已结束', '');
        updateAudioStatus('未连接', '');

        hideLoading();
        showStatus('会议已结束', 'info');

    } catch (error) {
        console.error('停止会议失败:', error);
        hideLoading();
        showError('停止会议失败: ' + error.message);
    }
}

// 处理 Realtime 消息
function handleRealtimeMessage(data) {
    console.log('🔍 处理消息类型:', data.type, data);
    
    // 处理所有转录相关的事件
    switch (data.type) {
        case 'error':
            console.error('❌ API 错误:', data.error);
            showError('API 错误: ' + (data.error?.message || JSON.stringify(data.error)));
            break;
            
        case 'conversation.item.input_audio_transcription.completed':
            console.log('🎤 用户语音转录完成:', data.transcript);
            if (data.transcript) {
                // 直接添加完整转录（因为 delta 事件可能不可用）
                addTranscript(data.transcript, true);
            }
            break;
            
        case 'conversation.item.input_audio_transcription.delta':
            console.log('🎤 用户语音转录增量:', data.delta);
            if (data.delta) {
                // 流式更新当前转录
                updateStreamingTranscript(data.delta);
            }
            break;
            
        case 'conversation.item.created':
            console.log('📝 会话项创建 (忽略，只处理流式增量):', data.item);
            // 忽略完整转录，只处理流式增量更新
            break;
            
        case 'response.audio_transcript.delta':
            console.log('🔊 AI 音频转录增量 (忽略):', data.delta);
            // 忽略 AI 的音频转录，只处理用户语音
            break;
            
        case 'response.audio_transcript.done':
            console.log('✅ AI 音频转录完成 (忽略):', data.transcript);
            // 忽略 AI 的音频转录，只处理用户语音
            break;
            
        case 'response.text.delta':
            console.log('💬 AI 文本增量 (忽略):', data.delta);
            // 忽略 AI 的文本回复，只处理用户语音转录
            break;
            
        case 'response.text.done':
            console.log('✅ AI 文本完成 (忽略):', data.text);
            // 忽略 AI 的文本回复，只处理用户语音转录
            break;
            
        case 'input_audio_buffer.speech_started':
            console.log('🎤 检测到语音开始');
            // 开始新的流式转录
            startNewStreamingTranscript();
            break;
            
        case 'input_audio_buffer.speech_stopped':
            console.log('🎤 检测到语音停止');
            // 语音停止后，等待完整的转录结果
            break;
            
        case 'input_audio_buffer.committed':
            console.log('✅ 音频缓冲区已提交');
            // 提交当前的流式转录
            commitCurrentTranscript();
            break;
            
        default:
            // 其他消息类型仅记录
            break;
    }
}

// 开始录音
async function startRecording() {
    try {
        // 创建音频上下文 - 使用48kHz采样率
        audioContext = new AudioContext({ sampleRate: 48000 });
        const source = audioContext.createMediaStreamSource(mediaStream);
        
        // 创建音量分析器
        const analyser = audioContext.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        
        // 注意：ScriptProcessorNode 已弃用，但目前 AudioWorkletNode 需要额外设置
        // 为了简化，继续使用 ScriptProcessorNode
        const processor = audioContext.createScriptProcessor(2048, 1, 1);
        source.connect(processor);
        processor.connect(audioContext.destination);

        let audioChunkCount = 0;
        let lastSendTime = Date.now();
        const minSendInterval = 50; // 最小发送间隔（毫秒）
        
        processor.onaudioprocess = (event) => {
            if (!isRecording || !dataChannel || dataChannel.readyState !== 'open') {
                return;
            }
            
            const now = Date.now();
            if (now - lastSendTime < minSendInterval) {
                return; // 限制发送频率
            }
            lastSendTime = now;
            
            const audioData = event.inputBuffer.getChannelData(0);
            const int16Data = new Int16Array(audioData.length);
            
            // 计算音量和VAD检测
            let sum = 0;
            for (let i = 0; i < audioData.length; i++) {
                const sample = audioData[i];
                int16Data[i] = Math.max(-32768, Math.min(32767, sample * 32768));
                sum += sample * sample;
            }
            const rms = Math.sqrt(sum / audioData.length);
            const volume = Math.min(1, rms * 10);
            
            // 更新音量指示器
            updateVolumeIndicator(volume);
            
            // 客户端VAD检测
            const vadNow = Date.now();
            const wasSpeaking = isSpeaking;
            isSpeaking = volume > vadThreshold;
            
            if (isSpeaking) {
                lastSpeechTime = vadNow;
                // 如果刚开始说话，清空之前的缓冲区
                if (!wasSpeaking) {
                    speechBuffer = [];
                    console.log('🎤 检测到语音开始');
                }
            } else {
                // 如果停止说话，启动回填更正窗口
                if (wasSpeaking && vadNow - lastSpeechTime > correctionWindow) {
                    console.log('🔇 语音结束，启动回填更正窗口');
                    // 这里可以添加回填更正逻辑
                }
            }
            
            // 转换为 base64 编码的字符串
            const uint8Array = new Uint8Array(int16Data.buffer);
            let binary = '';
            for (let i = 0; i < uint8Array.length; i++) {
                binary += String.fromCharCode(uint8Array[i]);
            }
            const base64Audio = btoa(binary);
            
            // 发送音频数据
            const message = {
                type: 'input_audio_buffer.append',
                audio: base64Audio
            };
            
            try {
                dataChannel.send(JSON.stringify(message));
                audioChunkCount++;
                
                if (audioChunkCount % 20 === 0) {
                    console.log(`🎙️ 已发送 ${audioChunkCount} 个音频块, 当前音量: ${(volume * 100).toFixed(0)}%`);
                }
            } catch (error) {
                console.error('❌ 发送音频数据失败:', error);
                // 不立即停止录音，可能只是临时错误
            }
        };

        isRecording = true;
        console.log('🎙️ 开始录音');

    } catch (error) {
        console.error('❌ 开始录音失败:', error);
        throw error;
    }
}

// 停止录音
function stopRecording() {
    isRecording = false;

    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
    }

    if (audioContext) {
        audioContext.close();
        audioContext = null;
    }
    
    // 重置音量指示器
    updateVolumeIndicator(0);

    console.log('🛑 停止录音');
}

// 开始新的流式转录
function startNewStreamingTranscript() {
    currentStreamingText = '';
    currentTranscriptId = Date.now() + Math.random();
    console.log('🆕 开始新的流式转录:', currentTranscriptId);
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

// 检查文本是否符合预期语言
function isExpectedLanguage(text) {
    const primaryLang = document.getElementById('primaryLanguage')?.value || 'en';
    const secondaryLang = '';
    
    const detectedLang = detectLanguage(text);
    
    // 如果未选择第二语言，只允许主要语言
    if (!secondaryLang) {
        return detectedLang === primaryLang;
    }
    
    // 选择了第二语言，则两种都允许
    if (detectedLang === primaryLang || detectedLang === secondaryLang) {
        return true;
    }

    // 对英文的额外过滤（未包含英文时无需放行）
    if (detectedLang === 'en' && primaryLang !== 'en' && secondaryLang !== 'en') {
        const hasEnglishWords = /\b(the|is|are|was|were|have|has|will|can|would|should|welcome|channel|video|subscribe|thank|thanks|please|sorry|hello|hi)\b/i.test(text);
        if (hasEnglishWords && text.length > 15) {
            console.log('⚠️ 检测到非预期语言（英文），跳过:', text);
            return false;
        }
    }

    return false;  // 其他情况默认不通过
}

// 更新流式转录
function updateStreamingTranscript(delta) {
    if (!delta) return;
    
    // 累积文本
    currentStreamingText += delta;
    console.log('📝 流式累积文本:', currentStreamingText);
    
    // 检查是否为幻觉内容（只检查完整文本，不阻止流式显示）
    if (isHallucinationText(currentStreamingText)) {
        console.log('⚠️ 检测到幻觉内容，但继续流式显示:', currentStreamingText);
        // 不返回，继续显示，让用户看到流式效果
    }
    
    // 检查语言（只检查完整文本，不阻止流式显示）
    if (!isExpectedLanguage(currentStreamingText)) {
        console.log('⚠️ 检测到非预期语言，但继续流式显示:', currentStreamingText);
        // 不返回，继续显示，让用户看到流式效果
    }
    
    // 立即更新显示（实时流式效果）
    updateStreamingDisplay();
    
    // 自动滚动
    if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
        scrollToBottom();
    }
}

// 提交当前的流式转录
function commitCurrentTranscript() {
    if (currentStreamingText && currentStreamingText.trim() !== '') {
        console.log('✅ 提交转录:', currentStreamingText);
        
        // 最后检查是否为幻觉内容
        if (isHallucinationText(currentStreamingText)) {
            console.log('⚠️ 提交时检测到幻觉内容，跳过保存');
            currentStreamingText = '';
            currentTranscriptId = null;
            updateDisplay();
            return;
        }
        
        // 最后检查语言
        if (!isExpectedLanguage(currentStreamingText)) {
            console.log('⚠️ 提交时检测到非预期语言，跳过保存');
            currentStreamingText = '';
            currentTranscriptId = null;
            updateDisplay();
            return;
        }
        
        // 检查是否与最后一条记录重复
        const lastTranscript = transcripts[transcripts.length - 1];
        if (lastTranscript && lastTranscript.text === currentStreamingText.trim()) {
            console.log('⚠️ 检测到重复的转录，跳过保存');
            currentStreamingText = '';
            currentTranscriptId = null;
            updateDisplay();
            return;
        }
        
        const transcript = {
            id: currentTranscriptId || Date.now() + Math.random(),
            timestamp: new Date().toISOString(),
            text: currentStreamingText.trim(),
            language: detectLanguage(currentStreamingText.trim())
        };
        
        transcripts.push(transcript);
        saveTranscripts();
        
        // 重置流式状态
        currentStreamingText = '';
        currentTranscriptId = null;
        
        updateDisplay();
        updateCount();
        
        // 自动滚动
        if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
            scrollToBottom();
        }
    }
}

// 添加字幕（用于完整的转录结果）
function addTranscript(text, isComplete = false) {
    if (!text || text.trim() === '') return;

    console.log('➕ 添加字幕:', text.trim(), isComplete ? '(完整)' : '(增量)');

    if (isComplete) {
        // 检查是否与最后一条记录或当前流式文本重复
        const lastTranscript = transcripts[transcripts.length - 1];
        if ((lastTranscript && lastTranscript.text === text.trim()) || 
            (currentStreamingText && currentStreamingText.trim() === text.trim())) {
            console.log('⚠️ 检测到重复的完整转录，跳过');
            return;
        }
        
        // 完整的转录结果，直接保存
        const transcript = {
            id: Date.now() + Math.random(),
            timestamp: new Date().toISOString(),
            text: text.trim(),
            language: detectLanguage(text.trim())
        };

        transcripts.push(transcript);
        saveTranscripts();
        updateDisplay();
        updateCount();
    } else {
        // 增量更新，累积到当前流式转录
        updateStreamingTranscript(text);
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
function updateStreamingDisplay() {
    const transcriptContent = document.getElementById('transcriptContent');
    
    // 清除之前的流式显示
    const existingStreaming = document.getElementById('streaming-transcript');
    if (existingStreaming) {
        existingStreaming.remove();
    }
    
    // 显示当前流式文本
    if (currentStreamingText && currentStreamingText.trim()) {
        // 创建流式显示元素
        const streamingElement = document.createElement('div');
        streamingElement.className = 'streaming-text';
        streamingElement.id = 'streaming-transcript';
        
        // 添加时间戳
        const timestamp = new Date().toLocaleTimeString();
        streamingElement.textContent = `${currentStreamingText} [${timestamp}]`;
        
        transcriptContent.appendChild(streamingElement);
        
        // 确保自动滚动到底部
        if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
            scrollToBottom();
        }
        
        console.log('🔄 流式显示更新:', currentStreamingText);
    }
}

// 更新显示
function updateDisplay() {
    const transcriptContent = document.getElementById('transcriptContent');
    
    if (transcripts.length === 0 && !currentStreamingText) {
        transcriptContent.innerHTML = `
            <div class="welcome-message">
                <p>欢迎使用 MeetingEZ！</p>
                <p>请先配置 OpenAI API Key，然后点击"开始会议"开始使用。</p>
            </div>
        `;
        return;
    }

    const displayTranscripts = transcripts.slice(-50); // 只显示最近50条
    
    // 显示已保存的记录，使用简洁格式
    transcriptContent.innerHTML = displayTranscripts.map(transcript => {
        const time = new Date(transcript.timestamp).toLocaleTimeString();
        return `${transcript.text} [${time}]`;
    }).join('\n');
    
    // 自动滚动到底部
    if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
        scrollToBottom();
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
    if (transcriptContent) {
        // 使用 requestAnimationFrame 确保 DOM 更新后再滚动
        requestAnimationFrame(() => {
            transcriptContent.scrollTop = transcriptContent.scrollHeight;
        });
    }
}

// 更新计数
function updateCount() {
    document.getElementById('transcriptCount').textContent = `${transcripts.length} 条记录`;
}

// 保存字幕
function saveTranscripts() {
    localStorage.setItem('meetingEZ_transcripts', JSON.stringify(transcripts));
}

// 加载字幕
function loadTranscripts() {
    try {
        const stored = localStorage.getItem('meetingEZ_transcripts');
        if (stored) {
            transcripts = JSON.parse(stored);
            updateDisplay();
            updateCount();
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
        transcripts = [];
        saveTranscripts();
        updateDisplay();
        updateCount();
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

// 清屏功能
function clearScreen() {
    const transcriptContent = document.getElementById('transcriptContent');
    
    // 只清空显示，不影响本地存储的数据
    if (transcripts.length === 0 && !currentStreamingText) {
        transcriptContent.innerHTML = `
            <div class="welcome-message">
                <p>欢迎使用 MeetingEZ！</p>
                <p>请先配置 OpenAI API Key，然后点击"开始会议"开始使用。</p>
            </div>
        `;
    } else {
        transcriptContent.innerHTML = '';
    }
    
    console.log('🧹 屏幕已清空（数据保留）');
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
    init();
    loadTranscripts();
});