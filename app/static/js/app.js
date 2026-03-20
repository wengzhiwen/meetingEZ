console.log('app.js loaded, build: 83156');
// MeetingEZ - 基于 OpenAI Realtime API (WebRTC) 的实时转写
// API Key 由后端从环境变量读取，前端不接触

let isConnected = false;
let isRecording = false;
let mediaStream = null;
let transcripts = [];
let volumeAnimationFrame = null;
let selectedAudioDevice = null;
let selectedAudioSource = 'microphone';
let testStream = null;
let testAudioContext = null;
let isTestingMicrophone = false;
let currentStreamingTextMap = { primary: '' };
let currentTranscriptIdMap = { primary: null };
const STORAGE_KEY = 'meetingEZ_transcripts';
const STORAGE_VERSION = 2;
const HIDE_BEFORE_KEY = 'meetingEZ_hideBefore';

let realtimeClient = null;

const TRANSLATION_CONTEXT_SIZE = 20;
let translationContext = [];

let volumeAudioContext = null;
let volumeAnalyser = null;
let meetingStartedAt = null;

function addToTranslationContext(text, language) {
    if (!text || !text.trim()) return;
    translationContext.push({ text: text.trim(), language: language || 'unknown', timestamp: Date.now() });
    if (translationContext.length > TRANSLATION_CONTEXT_SIZE) {
        translationContext = translationContext.slice(-TRANSLATION_CONTEXT_SIZE);
    }
}

function clearTranslationContext() {
    translationContext = [];
}

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

function loadSettings() {
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

    const primaryLang = localStorage.getItem('meetingEZ_primaryLanguage');
    if (primaryLang) {
        document.getElementById('primaryLanguage').value = primaryLang;
    }

    const secondaryLang = localStorage.getItem('meetingEZ_secondaryLanguage') || '';
    const secSelect = document.getElementById('secondaryLanguage');
    if (secSelect) secSelect.value = secondaryLang;

    enableSplitView(false);
}

function setupEventListeners() {
    const testConnBtn = document.getElementById('testConnection');
    if (testConnBtn) testConnBtn.addEventListener('click', testConnection);

    startBtn.addEventListener('click', startMeeting);
    stopBtn.addEventListener('click', stopMeeting);

    document.getElementById('downloadTranscript').addEventListener('click', downloadTranscript);
    document.getElementById('clearTranscript').addEventListener('click', clearTranscript);
    document.getElementById('autoScroll').addEventListener('click', toggleAutoScroll);
    document.getElementById('testMicrophone').addEventListener('click', toggleMicrophoneTest);

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
                await loadAudioDevices();
            });
        } else if ('ondevicechange' in navigator.mediaDevices) {
            navigator.mediaDevices.ondevicechange = async () => {
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
            localStorage.setItem('meetingEZ_secondaryLanguage', (e.target.value || '').trim());
        });
    }

    document.getElementById('fontSize').addEventListener('change', (e) => {
        localStorage.setItem('meetingEZ_fontSize', e.target.value);
        updateFontSize();
    });

    document.querySelector('.close').addEventListener('click', () => {
        document.getElementById('errorModal').style.display = 'none';
    });
}

// 测试连接（通过后端代理）
async function testConnection() {
    const testConnBtn = document.getElementById('testConnection');
    testConnBtn.disabled = true;
    showStatus('正在测试连接...', 'info');

    try {
        const resp = await fetch('/api/test-connection', { method: 'POST' });
        if (resp.status === 401) { window.location.href = '/login'; return; }
        const data = await resp.json();
        if (resp.ok) {
            showStatus('连接成功！', 'success');
        } else {
            showStatus('连接失败: ' + (data.error || resp.statusText), 'error');
        }
    } catch (error) {
        showStatus('连接失败: ' + error.message, 'error');
    } finally {
        testConnBtn.disabled = false;
    }
}

// 开始会议
async function startMeeting() {
    if (isTestingMicrophone) {
        stopMicrophoneTest();
        const testMicBtn = document.getElementById('testMicrophone');
        testMicBtn.textContent = '测';
        testMicBtn.classList.remove('btn-danger');
        testMicBtn.classList.add('btn-outline');
    }

    try {
        showLoading('正在初始化会议...');

        if (selectedAudioSource === 'tab') {
            try {
                const displayStream = await navigator.mediaDevices.getDisplayMedia({
                    video: true,
                    audio: true
                });

                const tabAudioTrack = displayStream.getAudioTracks()[0];
                if (!tabAudioTrack) {
                    displayStream.getTracks().forEach(track => track.stop());
                    throw new Error('未能获取标签页音频。请确保选择了"Chrome 标签页"并勾选了"共享标签页音频"。');
                }

                displayStream.getVideoTracks().forEach(track => track.stop());
                mediaStream = new MediaStream([tabAudioTrack]);
            } catch (error) {
                if (error.name === 'NotAllowedError') {
                    throw new Error('用户取消了标签页共享。');
                }
                throw error;
            }
        } else {
            const audioConstraints = {
                echoCancellation: false,
                noiseSuppression: false,
                autoGainControl: false,
                channelCount: 1
            };

            if (selectedAudioDevice) {
                audioConstraints.deviceId = { exact: selectedAudioDevice };
            }

            mediaStream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
        }

        startVolumeMonitor(mediaStream);
        meetingStartedAt = performance.now();
        await initRealtimeConnection();

        isConnected = true;
        isRecording = true;
        updateControls();
        updateMeetingStatus('进行中', 'active');
        updateAudioStatus('已连接', 'active');
        disableSettings();
        hideLoading();
        showStatus('会议已开始', 'success');

        enableSplitView(false);
    } catch (error) {
        console.error('开始会议失败:', error);
        hideLoading();
        showError('开始会议失败: ' + error.message);
        await stopMeeting({ showStoppedMessage: false, showLoadingOverlay: false });
    }
}

// 初始化 Realtime WebRTC 连接（不再需要 API Key）
async function initRealtimeConnection() {
    const primaryLang = document.getElementById('primaryLanguage').value || null;
    const RealtimeTranscriptionClass = window.RealtimeTranscription;
    if (typeof RealtimeTranscriptionClass !== 'function') {
        throw new Error('RealtimeTranscription 未正确加载');
    }

    realtimeClient = new RealtimeTranscriptionClass({
        model: 'gpt-4o-transcribe',
        language: primaryLang,
        prompt: '',

        onConnected: () => {
            console.log('Realtime 连接成功');
            showStatus('实时转写已连接', 'success');
            updateMeetingStatus('进行中', 'active');
            updateAudioStatus('已连接', 'active');
        },

        onDisconnected: () => {
            if (isRecording) {
                showStatus('连接断开，尝试重连...', 'error');
                updateAudioStatus('重连中', '');
            }
        },

        onSpeechStarted: (itemId) => {
            currentStreamingTextMap.primary = '正在识别...';
            currentTranscriptIdMap.primary = itemId;
            console.log('UI [perf] speech started', {
                itemId,
                msFromMeetingStart: meetingStartedAt ? Math.round(performance.now() - meetingStartedAt) : null
            });
            updateStreamingDisplay('primary');
        },

        onSpeechStopped: () => {},

        onTranscriptDelta: (delta, itemId, liveText) => {
            if (!delta) return;
            currentStreamingTextMap.primary = liveText;
            currentTranscriptIdMap.primary = itemId;
            console.log('UI [perf] delta render', {
                itemId,
                deltaChars: delta.length,
                liveChars: liveText.length
            });
            updateStreamingDisplay('primary');
        },

        onTranscriptComplete: async (transcript, itemId) => {
            if (!transcript || !transcript.trim()) return;

            if (isHallucinationText(transcript)) {
                currentStreamingTextMap.primary = '';
                currentTranscriptIdMap.primary = null;
                return;
            }

            const channel = 'primary';
            const normalized = normalizeText(transcript);

            const newTranscript = {
                id: itemId || Date.now() + Math.random(),
                timestamp: new Date().toISOString(),
                text: normalized,
                language: detectLanguage(normalized),
                channel
            };
            transcripts.push(newTranscript);
            saveTranscripts();

            currentStreamingTextMap.primary = '';
            currentTranscriptIdMap.primary = null;
            console.log('UI [perf] transcript committed', {
                itemId: newTranscript.id,
                chars: normalized.length
            });
            updateDisplay(channel);

            if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
                scrollToBottom();
            }

            // 后置翻译（通过后端代理）
            try {
                const primaryLang = document.getElementById('primaryLanguage')?.value || 'zh';
                const secondaryLang = (document.getElementById('secondaryLanguage')?.value || '').trim();
                if (secondaryLang) {
                    const structured = await postProcessText(transcript, {
                        primaryLanguage: primaryLang,
                        secondaryLanguage: secondaryLang,
                        originalLanguageHint: newTranscript.language
                    });
                    applyPostProcessToTranscript(newTranscript.id, structured);
                }
            } catch (ppErr) {
                console.warn('后置处理失败，保留原文:', ppErr);
            }
        },

        onError: (error) => {
            console.error('Realtime 错误:', error);
            showStatus('Realtime 错误: ' + (error.message || JSON.stringify(error)), 'error');
        }
    });

    // 不再传 apiKey，后端从环境变量读取
    await realtimeClient.connect(mediaStream);
}

// 停止会议
async function stopMeeting(options = {}) {
    const showStoppedMessage = options.showStoppedMessage !== false;
    const showLoadingOverlay = options.showLoadingOverlay !== false;

    try {
        if (showLoadingOverlay) {
            showLoading('正在结束会议...');
        }

        isRecording = false;

        if (realtimeClient) {
            realtimeClient.disconnect();
            realtimeClient = null;
        }

        stopVolumeMonitor();

        if (mediaStream) {
            mediaStream.getTracks().forEach(track => track.stop());
            mediaStream = null;
        }

        clearTranslationContext();

        isConnected = false;
        currentStreamingTextMap.primary = '';
        currentTranscriptIdMap.primary = null;
        updateDisplay('primary');
        updateControls();
        updateMeetingStatus('已结束', '');
        updateAudioStatus('未连接', '');
        enableSettings();

        hideLoading();
        if (showStoppedMessage) {
            showStatus('会议已结束', 'info');
        }
    } catch (error) {
        console.error('停止会议失败:', error);
        hideLoading();
        showError('停止会议失败: ' + error.message);
    }
}

// ---- 音量监测 ----

function startVolumeMonitor(stream) {
    try {
        volumeAudioContext = new AudioContext();
        const source = volumeAudioContext.createMediaStreamSource(stream);
        volumeAnalyser = volumeAudioContext.createAnalyser();
        volumeAnalyser.fftSize = 256;
        source.connect(volumeAnalyser);

        const dataArray = new Uint8Array(volumeAnalyser.frequencyBinCount);

        function tick() {
            if (!volumeAnalyser) return;
            volumeAnalyser.getByteFrequencyData(dataArray);
            let sum = 0;
            for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
            const volume = (sum / dataArray.length) / 255;
            updateVolumeIndicator(volume);
            volumeAnimationFrame = requestAnimationFrame(tick);
        }
        tick();
    } catch (e) {
        console.warn('音量监测初始化失败:', e);
    }
}

function stopVolumeMonitor() {
    if (volumeAnimationFrame) {
        cancelAnimationFrame(volumeAnimationFrame);
        volumeAnimationFrame = null;
    }
    if (volumeAudioContext) {
        volumeAudioContext.close();
        volumeAudioContext = null;
    }
    volumeAnalyser = null;
    updateVolumeIndicator(0);
}

// ---- 幻觉检测 ----

function isHallucinationText(text) {
    const hallucinationPatterns = [
        /^(hi|hello|hey|welcome).*(channel|video|subscribe|youtube|like|comment)/i,
        /^thanks?\s+for\s+(watching|listening|subscribing)/i,
        /^(please|don't forget to).*(subscribe|like|comment|share)/i,
        /字幕|subtitle|caption|transcript/i,
        /^(\s*[a-z]\s*){8,}$/i,
        /^([a-z]-){4,}/i,
        /^[\s\-\.]{8,}$/,
        /^[aeiou]{10,}$/i,
        /^(.)\1{8,}$/,
        /^(.{2})\1{4,}$/,
        /^(.{3})\1{3,}$/,
        /^(um|uh|ah|eh|oh)\s*$/i,
        /^[0-9\s\-\.]{10,}$/,
        /^[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]{5,}$/,
    ];

    if (text.length < 2 || text.length > 500) return true;

    const primaryLang = document.getElementById('primaryLanguage')?.value || 'en';
    if (primaryLang === 'ja' || primaryLang === 'zh') {
        const englishWords = text.match(/\b[a-zA-Z]{3,}\b/g) || [];
        if (englishWords.length > 3) return true;
    }

    const charCounts = {};
    for (let char of text.toLowerCase()) {
        if (char.match(/[a-z]/)) charCounts[char] = (charCounts[char] || 0) + 1;
    }
    const counts = Object.values(charCounts);
    if (counts.length > 0 && Math.max(...counts) > text.length * 0.6) return true;

    return hallucinationPatterns.some(pattern => pattern.test(text));
}

// ---- 显示相关 ----

function updateStreamingDisplay(channel = 'primary') {
    const tc = document.getElementById('transcriptContent');
    transcriptSplit = transcriptSplit || document.getElementById('transcriptSplit');
    transcriptLeft = transcriptLeft || document.getElementById('transcriptLeft');
    transcriptRight = transcriptRight || document.getElementById('transcriptRight');

    const text = currentStreamingTextMap[channel];
    const container = transcriptSplit && transcriptSplit.style.display !== 'none'
        ? (channel === 'secondary' ? transcriptRight : transcriptLeft)
        : tc;

    const old = container.querySelector(`#streaming-transcript-${channel}`);
    if (old) old.remove();

    if (text && text.trim()) {
        const el = document.createElement('div');
        el.className = 'streaming-text';
        el.id = `streaming-transcript-${channel}`;
        el.textContent = `${text} [${new Date().toLocaleTimeString()}]`;
        container.appendChild(el);

        if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
            scrollToBottom();
        }
    }
}

function updateDisplay(channel = 'primary') {
    const tc = document.getElementById('transcriptContent');
    transcriptSplit = transcriptSplit || document.getElementById('transcriptSplit');
    transcriptLeft = transcriptLeft || document.getElementById('transcriptLeft');
    transcriptRight = transcriptRight || document.getElementById('transcriptRight');

    if (transcripts.length === 0 && !currentStreamingTextMap.primary) {
        tc.innerHTML = `
            <div class="welcome-message">
                <p>欢迎使用 MeetingEZ！</p>
                <p>点击"开始会议"开始实时转写。</p>
            </div>
        `;
        return;
    }

    const hideBefore = localStorage.getItem(HIDE_BEFORE_KEY);
    const displayTranscripts = transcripts
        .filter(t => !hideBefore || t.timestamp > hideBefore)
        .slice(-50);

    const contentHtml = displayTranscripts
        .filter(t => (transcriptSplit && transcriptSplit.style.display !== 'none') ? t.channel === channel : true)
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
        tc.innerHTML = contentHtml;
        updateStreamingDisplay(channel);
        if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
            scrollToBottom();
        }
    }
}

// ---- 辅助函数 ----

function detectLanguage(text) {
    if (/[\u3040-\u309f]/.test(text) || /[\u30a0-\u30ff]/.test(text)) return 'ja';
    if (/[\uac00-\ud7af]/.test(text)) return 'ko';
    if (/[\u4e00-\u9fa5]/.test(text)) {
        return /[繁體覽擇檢測]/.test(text) ? 'zh-TW' : 'zh';
    }
    if (/[\u0400-\u04FF]/.test(text)) return 'ru';
    return document.getElementById('primaryLanguage')?.value || 'en';
}

function normalizeText(text) {
    return (text || '').trim().replace(/[。\.]{2,}$/u, (m) => m[0]);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function scrollToBottom() {
    const container = (transcriptSplit && transcriptSplit.style.display !== 'none')
        ? transcriptLeft : transcriptContent;
    if (!container) return;
    requestAnimationFrame(() => { container.scrollTop = container.scrollHeight; });
}

function saveTranscripts() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: STORAGE_VERSION, items: transcripts }));
}

function loadTranscripts() {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored) {
            const parsed = JSON.parse(stored);
            if (parsed && parsed.version === STORAGE_VERSION && Array.isArray(parsed.items)) {
                transcripts = parsed.items;
            } else if (Array.isArray(parsed)) {
                transcripts = parsed.map(t => ({ ...t, channel: t.channel || 'primary' }));
                saveTranscripts();
            } else {
                transcripts = [];
            }
            updateDisplay();
        }
    } catch (error) {
        transcripts = [];
    }
}

function downloadTranscript() {
    if (transcripts.length === 0) { alert('没有可导出的记录'); return; }

    let content = 'MeetingEZ 会议记录\n';
    content += `导出时间: ${new Date().toLocaleString()}\n`;
    content += `总记录数: ${transcripts.length}\n`;
    content += '='.repeat(50) + '\n\n';

    transcripts.forEach((t, i) => {
        content += `[${i + 1}] ${new Date(t.timestamp).toLocaleString()}\n`;
        content += `内容: ${t.text}\n\n`;
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

function clearTranscript() {
    if (confirm('确定要清空所有记录吗？')) {
        transcripts = [];
        currentStreamingTextMap.primary = '';
        currentTranscriptIdMap.primary = null;
        localStorage.setItem(HIDE_BEFORE_KEY, new Date().toISOString());

        document.getElementById('transcriptContent').innerHTML = `
            <div class="welcome-message">
                <p>欢迎使用 MeetingEZ！</p>
                <p>点击"开始会议"开始实时转写。</p>
            </div>
        `;
        setTimeout(() => saveTranscripts(), 0);
    }
}

// ---- 控制 UI ----

function updateControls() {
    startBtn.disabled = isConnected || isTestingMicrophone;
    stopBtn.disabled = !isConnected;
    document.getElementById('downloadTranscript').disabled = transcripts.length === 0;
    document.getElementById('clearTranscript').disabled = transcripts.length === 0;
    document.getElementById('testMicrophone').disabled = isConnected;
}

function updateAudioInputVisibility() {
    const audioInputContainer = document.getElementById('audioInputContainer');
    const tabAudioHint = document.getElementById('tabAudioHint');
    if (selectedAudioSource === 'microphone') {
        audioInputContainer.style.display = 'flex';
        if (tabAudioHint) tabAudioHint.style.display = 'none';
    } else {
        audioInputContainer.style.display = 'none';
        if (tabAudioHint) tabAudioHint.style.display = 'block';
    }
}

function updateMeetingStatus(status, className) {
    const el = document.getElementById('meetingStatus');
    el.textContent = status;
    el.className = `status-indicator ${className}`;
}

function updateAudioStatus(status, className) {
    const el = document.getElementById('audioStatus');
    el.textContent = status;
    el.className = `status-indicator ${className}`;
}

function updateFontSize() {
    transcriptContent.className = `transcript-content font-${localStorage.getItem('meetingEZ_fontSize') || 'medium'}`;
    const fontSize = localStorage.getItem('meetingEZ_fontSize') || 'medium';
    if (transcriptLeft) transcriptLeft.className = `transcript-pane font-${fontSize}`;
    if (transcriptRight) transcriptRight.className = `transcript-pane font-${fontSize}`;
}

function disableSettings() {
    ['audioInput', 'primaryLanguage', 'secondaryLanguage', 'fontSize'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = true;
    });
    ['testConnection'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = true;
    });
}

function enableSettings() {
    ['audioInput', 'primaryLanguage', 'secondaryLanguage', 'fontSize'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = false;
    });
    ['testConnection'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = false;
    });
}

function enableSplitView(enabled) {
    const content = document.getElementById('transcriptContent');
    transcriptSplit = transcriptSplit || document.getElementById('transcriptSplit');
    if (!content || !transcriptSplit) return;
    if (enabled) {
        content.style.display = 'none';
        transcriptSplit.style.display = 'grid';
    } else {
        transcriptSplit.style.display = 'none';
        content.style.display = 'block';
    }
}

function showStatus(message, type) {
    statusDiv.textContent = message;
    statusDiv.className = `status-message ${type}`;
    statusDiv.style.display = 'block';
    setTimeout(() => { statusDiv.style.display = 'none'; }, 3000);
}

function showError(message) {
    document.getElementById('errorMessage').textContent = message;
    document.getElementById('errorModal').style.display = 'block';
}

function showLoading(message) {
    const overlay = document.getElementById('loadingOverlay');
    overlay.querySelector('p').textContent = message;
    overlay.style.display = 'flex';
}

function hideLoading() {
    document.getElementById('loadingOverlay').style.display = 'none';
}

function updateVolumeIndicator(volume) {
    const volumeBar = document.getElementById('volumeBar');
    if (!volumeBar) return;
    const pct = Math.min(100, volume * 100);
    volumeBar.style.width = `${pct}%`;
    volumeBar.classList.toggle('high', pct > 70);
}

// ---- 音频设备 ----

async function loadAudioDevices() {
    try {
        const audioInputSelect = document.getElementById('audioInput');
        if (!window.isSecureContext) {
            if (audioInputSelect) audioInputSelect.innerHTML = '<option value="">需要 HTTPS 或 localhost</option>';
            return;
        }

        let devices = await navigator.mediaDevices.enumerateDevices();
        let audioInputs = devices.filter(d => d.kind === 'audioinput');
        audioInputSelect.innerHTML = '';

        if (audioInputs.length === 0 || audioInputs.some(d => !d.label)) {
            try {
                const prewarm = await navigator.mediaDevices.getUserMedia({ audio: true });
                prewarm.getTracks().forEach(t => t.stop());
                devices = await navigator.mediaDevices.enumerateDevices();
                audioInputs = devices.filter(d => d.kind === 'audioinput');
            } catch (e) {
                audioInputSelect.innerHTML = '<option value="">未授权麦克风</option>';
                return;
            }
        }

        if (audioInputs.length === 0) {
            audioInputSelect.innerHTML = '<option value="">无可用设备</option>';
            return;
        }

        audioInputs.forEach((device, i) => {
            const option = document.createElement('option');
            option.value = device.deviceId;
            option.text = device.label || `麦克风 ${i + 1}`;
            audioInputSelect.appendChild(option);
        });

        const saved = localStorage.getItem('meetingEZ_audioDevice');
        if (saved && audioInputs.some(d => d.deviceId === saved)) {
            audioInputSelect.value = saved;
            selectedAudioDevice = saved;
        } else {
            selectedAudioDevice = audioInputs[0].deviceId;
            audioInputSelect.value = selectedAudioDevice;
        }

        audioInputSelect.onchange = (e) => {
            selectedAudioDevice = e.target.value;
            localStorage.setItem('meetingEZ_audioDevice', selectedAudioDevice);
        };
    } catch (error) {
        console.error('加载音频设备失败:', error);
    }
}

// ---- 麦克风测试 ----

async function toggleMicrophoneTest() {
    const btn = document.getElementById('testMicrophone');
    if (isTestingMicrophone) {
        stopMicrophoneTest();
        btn.textContent = '测';
        btn.classList.remove('btn-danger');
        btn.classList.add('btn-outline');
    } else {
        try {
            await startMicrophoneTest();
            btn.textContent = '停';
            btn.classList.remove('btn-outline');
            btn.classList.add('btn-danger');
        } catch (error) {
            showError('麦克风测试失败: ' + error.message);
        }
    }
}

async function startMicrophoneTest() {
    const constraints = { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    if (selectedAudioDevice) constraints.deviceId = { exact: selectedAudioDevice };

    testStream = await navigator.mediaDevices.getUserMedia({ audio: constraints });
    testAudioContext = new AudioContext();
    const source = testAudioContext.createMediaStreamSource(testStream);
    const analyser = testAudioContext.createAnalyser();
    analyser.fftSize = 256;
    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    source.connect(analyser);

    isTestingMicrophone = true;
    updateControls();

    function monitor() {
        if (!isTestingMicrophone) return;
        analyser.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
        updateVolumeIndicator((sum / dataArray.length) / 255);
        volumeAnimationFrame = requestAnimationFrame(monitor);
    }
    monitor();
}

function stopMicrophoneTest() {
    isTestingMicrophone = false;
    if (volumeAnimationFrame) { cancelAnimationFrame(volumeAnimationFrame); volumeAnimationFrame = null; }
    if (testStream) { testStream.getTracks().forEach(t => t.stop()); testStream = null; }
    if (testAudioContext) { testAudioContext.close(); testAudioContext = null; }
    updateVolumeIndicator(0);
    updateControls();
}

// ---- 自动滚动 ----

function toggleAutoScroll() {
    const btn = document.getElementById('autoScroll');
    if (btn.classList.contains('btn-primary')) {
        btn.classList.replace('btn-primary', 'btn-outline');
        btn.textContent = '滚';
        localStorage.setItem('meetingEZ_autoScroll', 'false');
    } else {
        btn.classList.replace('btn-outline', 'btn-primary');
        btn.textContent = '滚\u2713';
        localStorage.setItem('meetingEZ_autoScroll', 'true');
        scrollToBottom();
    }
}

function initializeAutoScroll() {
    const btn = document.getElementById('autoScroll');
    if (localStorage.getItem('meetingEZ_autoScroll') !== 'false') {
        btn.classList.replace('btn-outline', 'btn-primary');
        btn.textContent = '滚\u2713';
    } else {
        btn.classList.replace('btn-primary', 'btn-outline');
        btn.textContent = '滚';
    }
}

// ---- 后置翻译（通过后端 /api/translate） ----

async function postProcessText(originalText, opts = {}) {
    const primaryLanguage = opts.primaryLanguage || 'zh';
    const secondaryLanguage = opts.secondaryLanguage || 'ja';
    const originalLanguageHint = opts.originalLanguageHint || primaryLanguage;
    const translateStartedAt = performance.now();

    const contextInfo = translationContext.length > 0
        ? translationContext.map((item, idx) => `[${idx + 1}] (${item.language}) ${item.text}`).join('\n')
        : '';

    const resp = await fetch('/api/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            text: originalText,
            primaryLanguage,
            secondaryLanguage,
            originalLanguageHint,
            context: contextInfo
        })
    });

    if (resp.status === 401) { window.location.href = '/login'; return; }

    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        console.warn('UI [perf] translate failed', {
            elapsedMs: Math.round(performance.now() - translateStartedAt),
            error: err.error || `HTTP ${resp.status}`
        });
        throw new Error(err.error || `HTTP ${resp.status}`);
    }

    const structured = await resp.json();
    console.log('UI [perf] translate completed', {
        elapsedMs: Math.round(performance.now() - translateStartedAt),
        textChars: originalText.length,
        originalLanguage: structured.originalLanguage,
        hasPrimaryTranslation: !!structured.primaryTranslation,
        hasSecondaryTranslation: !!structured.secondaryTranslation
    });
    structured.primaryTranslation = structured.primaryTranslation || null;
    structured.secondaryTranslation = structured.secondaryTranslation || null;
    return structured;
}

function applyPostProcessToTranscript(provisionalId, structured) {
    const idx = transcripts.findIndex(t => t.id === provisionalId);
    if (idx === -1) return;
    const entry = transcripts[idx];

    const primaryLang = document.getElementById('primaryLanguage')?.value || 'zh';
    const secondaryLang = document.getElementById('secondaryLanguage')?.value || 'ja';

    entry.language = structured.originalLanguage || entry.language;
    entry.timestamp = new Date().toISOString();
    addToTranslationContext(entry.text, entry.language);

    let offset = 0;

    if (structured.primaryTranslation) {
        addToTranslationContext(structured.primaryTranslation, primaryLang);
        transcripts.splice(idx + 1 + offset, 0, {
            id: Date.now() + Math.random(),
            timestamp: new Date().toISOString(),
            text: normalizeText(structured.primaryTranslation),
            language: primaryLang,
            channel: entry.channel,
            isTranslation: true
        });
        offset++;
    }

    if (structured.secondaryTranslation) {
        addToTranslationContext(structured.secondaryTranslation, secondaryLang);
        transcripts.splice(idx + 1 + offset, 0, {
            id: Date.now() + Math.random() + 1,
            timestamp: new Date().toISOString(),
            text: normalizeText(structured.secondaryTranslation),
            language: secondaryLang,
            channel: entry.channel,
            isTranslation: true
        });
        offset++;
    }

    saveTranscripts();
    updateDisplay(entry.channel || 'primary');
}

// ---- 页面初始化 ----

document.addEventListener('DOMContentLoaded', () => {
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
        if (container) container.appendChild(transcriptSplit);
    }

    init();
    loadTranscripts();
    updateFontSize();

    const secondaryLang = (localStorage.getItem('meetingEZ_secondaryLanguage') || '').trim();
    if (secondaryLang && transcriptSplit && transcriptSplit.style.display !== 'none') {
        updateDisplay('primary');
        updateDisplay('secondary');
    }
});
