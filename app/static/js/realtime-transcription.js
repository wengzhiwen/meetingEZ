/**
 * Realtime Transcription - OpenAI Realtime API WebRTC 客户端
 *
 * 基于最佳实践：
 * - 使用 WebRTC（浏览器推荐方式）连接 OpenAI Realtime API
 * - transcription-only session（不需要模型回答）
 * - 后端签发短期 client secret（不暴露 API key）
 * - server_vad 做分段检测
 * - 按 item_id 管理 live/final 状态
 */
console.log('realtime-transcription.js loaded, build: 90812');

class RealtimeTranscription {
    constructor(options = {}) {
        this.pc = null; // RTCPeerConnection
        this.dc = null; // RTCDataChannel
        this.localStream = null;
        this.isConnected = false;
        this.isConnecting = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 3;
        this.reconnectDelay = 2000;
        this._intentionalClose = false;
        this._reconnectTimer = null;

        // 配置
        this.options = {
            model: options.model || 'gpt-4o-transcribe',
            language: options.language || null,
            prompt: options.prompt || '',

            // 事件回调
            onTranscriptDelta: options.onTranscriptDelta || null,
            onTranscriptComplete: options.onTranscriptComplete || null,
            onSpeechStarted: options.onSpeechStarted || null,
            onSpeechStopped: options.onSpeechStopped || null,
            onError: options.onError || null,
            onConnected: options.onConnected || null,
            onDisconnected: options.onDisconnected || null
        };

        // 按 item_id 管理的转写条目
        // { [item_id]: { live: string, final: string|null, timestamp: number } }
        this.items = {};
        this.metrics = {};
    }

    /**
     * 使用 WebRTC 建立连接
     * @param {MediaStream} mediaStream - 已获取的音频流
     */
    async connect(mediaStream) {
        if (this.isConnected || this.isConnecting) {
            console.log('Realtime: 已连接或正在连接中');
            return;
        }

        this._intentionalClose = false;
        this.localStream = mediaStream;
        this._clearReconnectTimer();
        this._cleanup(true);
        this.isConnecting = true;

        try {
            // 1. 从后端获取 ephemeral client secret（API Key 由后端环境变量提供）
            console.log('Realtime: 正在获取 client secret...');
            const sessionResp = await fetch('/api/realtime-session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    language: this.options.language,
                    prompt: this.options.prompt
                })
            });

            if (sessionResp.status === 401) {
                window.location.href = '/login';
                return;
            }
            if (!sessionResp.ok) {
                const errText = await sessionResp.text();
                throw new Error(`获取 client secret 失败: ${sessionResp.status} ${errText}`);
            }

            const sessionData = await sessionResp.json();
            const clientSecret = sessionData.clientSecret;
            if (!clientSecret) {
                throw new Error('后端未返回有效的 client secret');
            }
            console.log('Realtime [perf] client secret ready', {
                expiresAt: sessionData.expiresAt || null
            });

            // 2. 创建 RTCPeerConnection
            this.pc = new RTCPeerConnection();

            // DEBUG: 监听连接状态变化
            this.pc.onconnectionstatechange = () => {
                console.log('Realtime [PC] connectionState:', this.pc.connectionState);
                if (!this._intentionalClose && ['failed', 'disconnected', 'closed'].includes(this.pc.connectionState)) {
                    const wasConnected = this.isConnected;
                    this.isConnected = false;
                    this.isConnecting = false;
                    this._cleanup(true);
                    if (wasConnected) {
                        this._attemptReconnect();
                    }
                }
            };
            this.pc.oniceconnectionstatechange = () => {
                console.log('Realtime [PC] iceConnectionState:', this.pc.iceConnectionState);
            };
            this.pc.ontrack = (event) => {
                console.log('Realtime [PC] ontrack:', event.track.kind, event.track.readyState);
            };

            // 3. 添加音频轨道
            this.localStream = mediaStream;
            const audioTracks = mediaStream.getAudioTracks();
            console.log('Realtime: 音频轨道数:', audioTracks.length, audioTracks.map(t => ({
                label: t.label, readyState: t.readyState, enabled: t.enabled,
                settings: t.getSettings ? t.getSettings() : {}
            })));
            audioTracks.forEach(track => {
                this.pc.addTrack(track, mediaStream);
            });

            // 4. 创建 data channel 接收转写事件
            this.dc = this.pc.createDataChannel('oai-events');
            this._setupDataChannel();

            // 5. 创建 offer
            const offer = await this.pc.createOffer();
            await this.pc.setLocalDescription(offer);
            await this._waitForIceGatheringComplete();

            // 6. 发送 offer 到 OpenAI，获取 answer
            const sdpResp = await fetch(
                'https://api.openai.com/v1/realtime/calls',
                {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${clientSecret}`,
                        'Content-Type': 'application/sdp'
                    },
                    body: this.pc.localDescription?.sdp || offer.sdp
                }
            );

            if (!sdpResp.ok) {
                const errText = await sdpResp.text();
                throw new Error(`WebRTC SDP 交换失败: ${sdpResp.status} ${errText}`);
            }

            const answerSdp = await sdpResp.text();
            console.log('Realtime: SDP answer 长度:', answerSdp.length);
            await this.pc.setRemoteDescription({
                type: 'answer',
                sdp: answerSdp
            });
            await this._waitForDataChannelOpen();

            console.log('Realtime: WebRTC 连接已就绪');
        } catch (error) {
            console.error('Realtime: 连接失败', error);
            this.isConnecting = false;
            this._cleanup(true);
            throw error;
        }
    }

    /**
     * 设置 DataChannel 事件处理
     */
    _setupDataChannel() {
        this.dc.onopen = () => {
            console.log('Realtime: DataChannel 已打开');
            this.isConnected = true;
            this.isConnecting = false;
            this.reconnectAttempts = 0;

            if (this.options.onConnected) {
                this.options.onConnected();
            }
        };

        this.dc.onclose = () => {
            console.log('Realtime: DataChannel 已关闭');
            const wasConnected = this.isConnected;
            this.isConnected = false;
            this.isConnecting = false;

            if (this.options.onDisconnected) {
                this.options.onDisconnected();
            }

            if (wasConnected && !this._intentionalClose) {
                this._cleanup(true);
                this._attemptReconnect();
            }
        };

        this.dc.onerror = (event) => {
            console.error('Realtime: DataChannel 错误', event);
        };

        this.dc.onmessage = (event) => {
            this._handleMessage(event);
        };
    }

    /**
     * 处理来自 DataChannel 的消息
     */
    _handleMessage(event) {
        try {
            const data = JSON.parse(event.data);
            const type = data.type;

            // DEBUG: 打印所有收到的消息
            console.log('Realtime [recv]:', type, data);

            switch (type) {
                case 'session.created':
                case 'transcription_session.created':
                    console.log('Realtime: Session 创建成功', JSON.stringify(data.session, null, 2));
                    break;

                case 'transcription_session.updated':
                    console.log('Realtime: Session 更新成功', JSON.stringify(data.session, null, 2));
                    break;

                case 'session.updated':
                    console.log('Realtime: Session 更新成功');
                    break;

                case 'input_audio_buffer.speech_started':
                    if (!this.metrics[data.item_id]) {
                        this.metrics[data.item_id] = {};
                    }
                    this.metrics[data.item_id].speechStartedAt = performance.now();
                    if (this.options.onSpeechStarted) {
                        this.options.onSpeechStarted(data.item_id);
                    }
                    break;

                case 'input_audio_buffer.speech_stopped':
                    if (this.options.onSpeechStopped) {
                        this.options.onSpeechStopped(data.item_id);
                    }
                    break;

                case 'conversation.item.input_audio_transcription.delta': {
                    const itemId = data.item_id;
                    const delta = data.delta || '';
                    if (!this.items[itemId]) {
                        this.items[itemId] = { live: '', final: null, timestamp: Date.now() };
                    }
                    this.items[itemId].live += delta;
                    if (!this.metrics[itemId]) {
                        this.metrics[itemId] = {};
                    }
                    if (!this.metrics[itemId].firstDeltaAt) {
                        this.metrics[itemId].firstDeltaAt = performance.now();
                        console.log('Realtime [perf] first delta', {
                            itemId,
                            msFromSpeechStart: this.metrics[itemId].speechStartedAt
                                ? Math.round(this.metrics[itemId].firstDeltaAt - this.metrics[itemId].speechStartedAt)
                                : null
                        });
                    }

                    if (this.options.onTranscriptDelta) {
                        this.options.onTranscriptDelta(delta, itemId, this.items[itemId].live);
                    }
                    break;
                }

                case 'conversation.item.input_audio_transcription.completed': {
                    const itemId = data.item_id;
                    const transcript = data.transcript || '';
                    if (!this.items[itemId]) {
                        this.items[itemId] = { live: '', final: null, timestamp: Date.now() };
                    }
                    this.items[itemId].final = transcript;
                    if (!this.metrics[itemId]) {
                        this.metrics[itemId] = {};
                    }
                    this.metrics[itemId].completedAt = performance.now();
                    console.log('Realtime [perf] transcript completed', {
                        itemId,
                        chars: transcript.length,
                        msFromSpeechStart: this.metrics[itemId].speechStartedAt
                            ? Math.round(this.metrics[itemId].completedAt - this.metrics[itemId].speechStartedAt)
                            : null,
                        msFromFirstDelta: this.metrics[itemId].firstDeltaAt
                            ? Math.round(this.metrics[itemId].completedAt - this.metrics[itemId].firstDeltaAt)
                            : null
                    });

                    if (this.options.onTranscriptComplete) {
                        this.options.onTranscriptComplete(transcript, itemId);
                    }
                    break;
                }

                case 'error':
                    console.error('Realtime: API 错误', data.error);
                    if (this.options.onError) {
                        this.options.onError(data.error);
                    }
                    break;

                default:
                    break;
            }
        } catch (error) {
            console.error('Realtime: 解析消息失败', error);
        }
    }

    /**
     * 断开连接
     */
    disconnect() {
        this._intentionalClose = true;
        this._clearReconnectTimer();
        this._cleanup(false);
        console.log('Realtime: 已断开连接');
    }

    /**
     * 清理资源（不释放外部传入的 mediaStream）
     */
    _cleanup(preserveStream = false) {
        if (this.dc) {
            this.dc.onopen = null;
            this.dc.onclose = null;
            this.dc.onmessage = null;
            this.dc.onerror = null;
            try { this.dc.close(); } catch (e) {}
            this.dc = null;
        }
        if (this.pc) {
            this.pc.onconnectionstatechange = null;
            this.pc.oniceconnectionstatechange = null;
            this.pc.ontrack = null;
            try { this.pc.getSenders().forEach(sender => this.pc.removeTrack(sender)); } catch (e) {}
            try { this.pc.close(); } catch (e) {}
            this.pc = null;
        }
        this.isConnected = false;
        this.isConnecting = false;
        if (!preserveStream) {
            this.localStream = null;
        }
    }

    /**
     * 尝试重连
     */
    _attemptReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.log('Realtime: 达到最大重连次数');
            return;
        }

        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

        console.log(`Realtime: ${delay}ms 后重连 (${this.reconnectAttempts}/${this.maxReconnectAttempts})`);

        this._reconnectTimer = setTimeout(async () => {
            if (!this.isConnected && !this._intentionalClose && this.localStream) {
                try {
                    await this.connect(this.localStream);
                } catch (err) {
                    console.error('Realtime: 重连失败', err);
                }
            }
        }, delay);
    }

    _clearReconnectTimer() {
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
    }

    async _waitForIceGatheringComplete() {
        if (!this.pc || this.pc.iceGatheringState === 'complete') {
            return;
        }

        await new Promise((resolve) => {
            const handleStateChange = () => {
                if (!this.pc || this.pc.iceGatheringState === 'complete') {
                    if (this.pc) {
                        this.pc.removeEventListener('icegatheringstatechange', handleStateChange);
                    }
                    resolve();
                }
            };

            this.pc.addEventListener('icegatheringstatechange', handleStateChange);
            setTimeout(() => {
                if (this.pc) {
                    this.pc.removeEventListener('icegatheringstatechange', handleStateChange);
                }
                resolve();
            }, 2000);
        });
    }

    async _waitForDataChannelOpen(timeoutMs = 10000) {
        if (!this.dc) {
            throw new Error('DataChannel 未创建');
        }
        if (this.dc.readyState === 'open') {
            return;
        }

        await new Promise((resolve, reject) => {
            const onOpen = () => {
                cleanup();
                resolve();
            };
            const onClose = () => {
                cleanup();
                reject(new Error('DataChannel 在就绪前关闭'));
            };
            const timer = setTimeout(() => {
                cleanup();
                reject(new Error('等待 DataChannel 就绪超时'));
            }, timeoutMs);
            const cleanup = () => {
                clearTimeout(timer);
                if (this.dc) {
                    this.dc.removeEventListener('open', onOpen);
                    this.dc.removeEventListener('close', onClose);
                }
            };

            this.dc.addEventListener('open', onOpen);
            this.dc.addEventListener('close', onClose);
        });
    }

    /**
     * 获取所有已完成的转写文本（按时间排序）
     */
    getFinalTranscripts() {
        return Object.entries(this.items)
            .filter(([, item]) => item.final !== null)
            .sort(([, a], [, b]) => a.timestamp - b.timestamp)
            .map(([itemId, item]) => ({ itemId, text: item.final, timestamp: item.timestamp }));
    }

    /**
     * 清空已管理的 items
     */
    clearItems() {
        this.items = {};
    }
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = RealtimeTranscription;
}

if (typeof window !== 'undefined') {
    window.RealtimeTranscription = RealtimeTranscription;
}
