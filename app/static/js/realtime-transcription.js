/**
 * Realtime Transcription - OpenAI Realtime API WebSocket 客户端
 * 用于实时音频转写，支持 gpt-realtime 模型
 */
class RealtimeTranscription {
    constructor(apiKey, options = {}) {
        this.apiKey = apiKey;
        this.ws = null;
        this.isConnected = false;
        this.isConnecting = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 3;
        this.reconnectDelay = 1000;

        // 配置选项
        this.options = {
            model: options.model || 'gpt-realtime-1.5',
            language: options.language || 'zh',
            sampleRate: options.sampleRate || 24000,
            vadThreshold: options.vadThreshold || 0.5,
            silenceDurationMs: options.silenceDurationMs || 500,

            // 事件回调
            onTranscriptDelta: options.onTranscriptDelta || null,
            onTranscriptComplete: options.onTranscriptComplete || null,
            onSpeechStarted: options.onSpeechStarted || null,
            onSpeechStopped: options.onSpeechStopped || null,
            onError: options.onError || null,
            onConnected: options.onConnected || null,
            onDisconnected: options.onDisconnected || null
        };

        // 当前转录会话状态
        this.currentItemId = null;
        this.currentTranscript = '';
    }

    /**
     * 建立 WebSocket 连接
     */
    async connect() {
        if (this.isConnected || this.isConnecting) {
            console.log('🔄 Realtime: 已连接或正在连接中');
            return;
        }

        this.isConnecting = true;
        const url = `wss://api.openai.com/v1/realtime?model=${this.options.model}`;

        console.log('🔌 Realtime: 正在连接...', { url, model: this.options.model });

        return new Promise((resolve, reject) => {
            try {
                this.ws = new WebSocket(url, {
                    headers: {
                        'Authorization': `Bearer ${this.apiKey}`,
                        'OpenAI-Beta': 'realtime=v1'
                    }
                });

                this.ws.onopen = () => {
                    console.log('✅ Realtime: WebSocket 连接成功');
                    this.isConnected = true;
                    this.isConnecting = false;
                    this.reconnectAttempts = 0;

                    // 发送 session 配置
                    this._configureSession();

                    if (this.options.onConnected) {
                        this.options.onConnected();
                    }
                    resolve();
                };

                this.ws.onmessage = (event) => {
                    this._handleMessage(event);
                };

                this.ws.onerror = (error) => {
                    console.error('❌ Realtime: WebSocket 错误', error);
                    this.isConnecting = false;

                    if (this.options.onError) {
                        this.options.onError(error);
                    }
                    reject(error);
                };

                this.ws.onclose = (event) => {
                    console.log('🔌 Realtime: WebSocket 关闭', { code: event.code, reason: event.reason });
                    this.isConnected = false;
                    this.isConnecting = false;

                    if (this.options.onDisconnected) {
                        this.options.onDisconnected(event);
                    }

                    // 尝试重连
                    this._attemptReconnect();
                };

            } catch (error) {
                console.error('❌ Realtime: 创建 WebSocket 失败', error);
                this.isConnecting = false;
                reject(error);
            }
        });
    }

    /**
     * 配置 transcription session
     */
    _configureSession() {
        const sessionConfig = {
            type: 'session.update',
            session: {
                type: 'transcription',
                audio: {
                    input: {
                        format: {
                            type: 'audio/pcm',
                            rate: this.options.sampleRate
                        },
                        noise_reduction: {
                            type: 'near_field'
                        },
                        transcription: {
                            model: this.options.model,
                            language: this.options.language
                        },
                        turn_detection: {
                            type: 'server_vad',
                            threshold: this.options.vadThreshold,
                            prefix_padding_ms: 300,
                            silence_duration_ms: this.options.silenceDurationMs
                        }
                    }
                }
            }
        };

        console.log('📤 Realtime: 发送 session 配置', sessionConfig);
        this.ws.send(JSON.stringify(sessionConfig));
    }

    /**
     * 处理 WebSocket 消息
     */
    _handleMessage(event) {
        try {
            const data = JSON.parse(event.data);
            const type = data.type;

            // 调试日志
            if (type !== 'input_audio_buffer.speech_started' && type !== 'input_audio_buffer.speech_stopped') {
                console.log('📥 Realtime: 收到消息', type, data);
            }

            switch (type) {
                case 'session.created':
                    console.log('✅ Realtime: Session 创建成功', data.session);
                    break;

                case 'session.updated':
                    console.log('✅ Realtime: Session 更新成功', data.session);
                    break;

                case 'input_audio_buffer.speech_started':
                    console.log('🎤 Realtime: 检测到语音开始');
                    this.currentTranscript = '';
                    if (this.options.onSpeechStarted) {
                        this.options.onSpeechStarted();
                    }
                    break;

                case 'input_audio_buffer.speech_stopped':
                    console.log('🔇 Realtime: 检测到语音停止');
                    if (this.options.onSpeechStopped) {
                        this.options.onSpeechStopped();
                    }
                    break;

                case 'conversation.item.input_audio_transcription.delta':
                    // 增量转录
                    this.currentItemId = data.item_id;
                    this.currentTranscript += data.delta || '';
                    if (this.options.onTranscriptDelta) {
                        this.options.onTranscriptDelta(data.delta, data.item_id);
                    }
                    break;

                case 'conversation.item.input_audio_transcription.completed':
                    // 完整转录
                    const transcript = data.transcript || this.currentTranscript;
                    console.log('✅ Realtime: 转录完成', { item_id: data.item_id, transcript });
                    if (this.options.onTranscriptComplete) {
                        this.options.onTranscriptComplete(transcript, data.item_id);
                    }
                    this.currentTranscript = '';
                    this.currentItemId = null;
                    break;

                case 'error':
                    console.error('❌ Realtime: API 错误', data.error);
                    if (this.options.onError) {
                        this.options.onError(data.error);
                    }
                    break;

                default:
                    // 其他事件类型，如 rate_limits_updated 等
                    break;
            }
        } catch (error) {
            console.error('❌ Realtime: 解析消息失败', error, event.data);
        }
    }

    /**
     * 发送音频数据
     * @param {Float32Array} float32Data - 音频数据
     */
    sendAudio(float32Data) {
        if (!this.isConnected || !this.ws) {
            console.warn('⚠️ Realtime: 未连接，无法发送音频');
            return;
        }

        // Float32 -> Int16 PCM
        const pcm16 = this._float32ToPcm16(float32Data);

        // Base64 编码
        const base64 = this._arrayBufferToBase64(pcm16.buffer);

        // 发送
        const message = {
            type: 'input_audio_buffer.append',
            audio: base64
        };

        this.ws.send(JSON.stringify(message));
    }

    /**
     * 手动提交音频缓冲区（可选，用于禁用 VAD 时）
     */
    commitAudio() {
        if (!this.isConnected || !this.ws) {
            return;
        }

        this.ws.send(JSON.stringify({
            type: 'input_audio_buffer.commit'
        }));
    }

    /**
     * 清空音频缓冲区
     */
    clearAudio() {
        if (!this.isConnected || !this.ws) {
            return;
        }

        this.ws.send(JSON.stringify({
            type: 'input_audio_buffer.clear'
        }));
    }

    /**
     * 断开连接
     */
    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.isConnected = false;
        this.isConnecting = false;
        console.log('🔌 Realtime: 已断开连接');
    }

    /**
     * 尝试重连
     */
    _attemptReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.log('❌ Realtime: 达到最大重连次数，停止重连');
            return;
        }

        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

        console.log(`🔄 Realtime: 将在 ${delay}ms 后尝试重连 (${this.reconnectAttempts}/${this.maxReconnectAttempts})`);

        setTimeout(() => {
            if (!this.isConnected) {
                this.connect().catch(err => {
                    console.error('❌ Realtime: 重连失败', err);
                });
            }
        }, delay);
    }

    /**
     * Float32 转 Int16 PCM
     */
    _float32ToPcm16(float32Array) {
        const bufferLength = float32Array.length;
        const pcm16 = new Int16Array(bufferLength);
        for (let i = 0; i < bufferLength; i++) {
            const s = Math.max(-1, Math.min(1, float32Array[i]));
            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return pcm16;
    }

    /**
     * ArrayBuffer 转 Base64
     */
    _arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    /**
     * 更新语言设置
     */
    updateLanguage(language) {
        this.options.language = language;
        if (this.isConnected) {
            this._configureSession();
        }
    }

    /**
     * 获取连接状态
     */
    getConnectionState() {
        if (!this.ws) return 'disconnected';
        switch (this.ws.readyState) {
            case WebSocket.CONNECTING: return 'connecting';
            case WebSocket.OPEN: return 'connected';
            case WebSocket.CLOSING: return 'closing';
            case WebSocket.CLOSED: return 'closed';
            default: return 'unknown';
        }
    }
}

// 导出（兼容模块和非模块环境）
if (typeof module !== 'undefined' && module.exports) {
    module.exports = RealtimeTranscription;
}
