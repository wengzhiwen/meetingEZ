/**
 * Realtime Translation - OpenAI Realtime Translation WebRTC sidecar.
 *
 * Experimental path used alongside the existing transcription flow. One client
 * translates the mixed input stream to one fixed target language. Each session
 * also emits a source-language transcript (session.input_transcript.*) which the
 * Beta UI can surface as the original caption.
 */
console.log('realtime-translation.js loaded, build: 20260509b');

class RealtimeTranslation {
    constructor(options = {}) {
        this.targetLanguage = options.targetLanguage;
        this.label = options.label || this.targetLanguage;
        this.pc = null;
        this.dc = null;
        this.remoteAudio = null;
        this.localStream = null;
        this._sourceStream = null;
        this.isConnected = false;
        this.isConnecting = false;
        // Reconnect (mirrors RealtimeTranscription): exponential backoff. Every
        // connect() re-fetches a client secret, so reconnection naturally rotates
        // the secret before the previous one expires on long meetings.
        this.maxReconnectAttempts = 3;
        this.reconnectDelay = 2000;
        this.reconnectAttempts = 0;
        this._intentionalClose = false;
        this._reconnectTimer = null;
        this.options = {
            onConnected: options.onConnected || null,
            onDisconnected: options.onDisconnected || null,
            onInputDelta: options.onInputDelta || null,
            onInputDone: options.onInputDone || null,
            onOutputDelta: options.onOutputDelta || null,
            onOutputDone: options.onOutputDone || null,
            onError: options.onError || null
        };
    }

    async connect(mediaStream) {
        if (!this.targetLanguage) {
            throw new Error('Realtime Translation targetLanguage is required');
        }
        if (this.isConnecting) {
            throw new Error('Realtime Translation 正在连接中，请勿重复调用');
        }

        this.isConnecting = true;
        this._intentionalClose = false;
        this._sourceStream = mediaStream;
        // Tear down any leftover peer from a previous failed/disconnected attempt
        // (without treating it as an intentional close).
        this._teardownPeer();
        try {
            const clientSecret = await this._fetchClientSecret();

            this.pc = new RTCPeerConnection();
            this.dc = this.pc.createDataChannel('oai-events');
            this._setupDataChannel();

            this.pc.onconnectionstatechange = () => {
                const state = this.pc ? this.pc.connectionState : 'unknown';
                console.log(`RealtimeTranslation [${this.label}] connectionState:`, state);
                if (['failed', 'disconnected', 'closed'].includes(state)) {
                    this.isConnected = false;
                    if (this.options.onDisconnected) {
                        this.options.onDisconnected(this);
                    }
                    this._attemptReconnect();
                }
            };
            this.pc.ontrack = ({ streams }) => {
                // Keep remote translated audio available but muted by default to avoid
                // feedback in meeting rooms. The current Beta surfaces subtitles first.
                this.remoteAudio = new Audio();
                this.remoteAudio.autoplay = false;
                this.remoteAudio.muted = true;
                this.remoteAudio.srcObject = streams[0];
                console.log(`RealtimeTranslation [${this.label}] remote audio track received`);
            };

            this.localStream = new MediaStream(
                mediaStream.getAudioTracks().map(track => track.clone()));
            this.localStream.getAudioTracks().forEach(track => {
                this.pc.addTrack(track, this.localStream);
            });

            const offer = await this.pc.createOffer();
            await this.pc.setLocalDescription(offer);
            await this._waitForIceGatheringComplete();

            const sdpResp = await fetch('/api/realtime-translation-call', {
                method: 'POST',
                headers: {
                    'X-OpenAI-Client-Secret': clientSecret,
                    'Content-Type': 'application/sdp'
                },
                body: this.pc.localDescription?.sdp || offer.sdp
            });

            const answerSdp = await sdpResp.text();
            if (!sdpResp.ok) {
                throw new Error(`Realtime Translation SDP 交换失败: ${sdpResp.status} ${answerSdp}`);
            }

            await this.pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
            await this._waitForDataChannelOpen();
        } finally {
            this.isConnecting = false;
        }
    }

    async _fetchClientSecret() {
        const sessionResp = await fetch('/api/realtime-translation-session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ targetLanguage: this.targetLanguage })
        });

        if (sessionResp.status === 401) {
            window.location.href = '/login';
            throw new Error('登录已失效，请重新登录');
        }
        if (!sessionResp.ok) {
            const errText = await sessionResp.text();
            throw new Error(`获取 translation client secret 失败: ${sessionResp.status} ${errText}`);
        }

        const session = await sessionResp.json();
        const clientSecret = session.clientSecret;
        if (!clientSecret) {
            throw new Error('后端未返回有效的 translation client secret');
        }
        return clientSecret;
    }

    _attemptReconnect() {
        if (this._intentionalClose) return;
        // Skip if already back online, mid-connect, or with a reconnect pending.
        if (this.isConnected || this.isConnecting || this._reconnectTimer) return;
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.warn(`RealtimeTranslation [${this.label}] 达到最大重连次数，放弃`);
            if (this.options.onError) {
                this.options.onError(
                    new Error('Realtime Translation 连接已断开，自动重连次数已用尽'), this);
            }
            return;
        }
        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
        console.log(`RealtimeTranslation [${this.label}] 第 ${this.reconnectAttempts} 次重连，${delay}ms 后尝试`);
        this._reconnectTimer = setTimeout(async () => {
            this._reconnectTimer = null;
            if (this._intentionalClose || !this._sourceStream) return;
            try {
                await this.connect(this._sourceStream);
            } catch (err) {
                console.warn(`RealtimeTranslation [${this.label}] 重连失败:`, err.message);
                this._attemptReconnect();
            }
        }, delay);
    }

    _setupDataChannel() {
        this.dc.onopen = () => {
            this.isConnected = true;
            this.reconnectAttempts = 0;
            console.log(`RealtimeTranslation [${this.label}] DataChannel opened`);
            if (this.options.onConnected) {
                this.options.onConnected(this);
            }
        };
        this.dc.onclose = () => {
            this.isConnected = false;
            console.log(`RealtimeTranslation [${this.label}] DataChannel closed`);
            if (this.options.onDisconnected) {
                this.options.onDisconnected(this);
            }
            this._attemptReconnect();
        };
        this.dc.onerror = (event) => {
            console.error(`RealtimeTranslation [${this.label}] DataChannel error`, event);
            if (this.options.onError) {
                this.options.onError(new Error('Realtime Translation DataChannel 发生错误'), this);
            }
        };
        this.dc.onmessage = (message) => {
            this._handleMessage(message);
        };
    }

    _handleMessage(message) {
        let event;
        try {
            event = JSON.parse(message.data);
        } catch (error) {
            console.warn(`RealtimeTranslation [${this.label}] non-JSON message`, error);
            return;
        }

        const type = event.type;
        const isDelta = type === 'session.input_transcript.delta'
            || type === 'session.output_transcript.delta';
        // delta 事件高频，仅在非常规事件时打印，避免控制台刷屏。
        if (!isDelta) {
            console.log(`RealtimeTranslation [${this.label}] recv:`, type, event);
        }

        if (type === 'error') {
            if (this.options.onError) {
                this.options.onError(event.error || event, this);
            }
            return;
        }

        if (type === 'session.input_transcript.delta' && typeof event.delta === 'string') {
            if (this.options.onInputDelta) {
                this.options.onInputDelta(event.delta, this);
            }
            return;
        }

        // input 转写完成（事件名以实测为准，兼容 .done / .completed）
        if (type === 'session.input_transcript.done'
            || type === 'session.input_transcript.completed') {
            if (this.options.onInputDone) {
                this.options.onInputDone(
                    { itemId: event.item_id, transcript: event.transcript || event.text || '' },
                    this);
            }
            return;
        }

        if (type === 'session.output_transcript.delta' && typeof event.delta === 'string') {
            if (this.options.onOutputDelta) {
                this.options.onOutputDelta(event.delta, this);
            }
            return;
        }

        // 译文完成
        if (type === 'session.output_transcript.done'
            || type === 'session.output_transcript.completed') {
            if (this.options.onOutputDone) {
                this.options.onOutputDone(
                    { itemId: event.item_id, transcript: event.transcript || event.text || '' },
                    this);
            }
        }
    }

    _teardownPeer() {
        if (this.dc) {
            // Null handlers before close so teardown doesn't trigger reconnect.
            this.dc.onmessage = null;
            this.dc.onopen = null;
            this.dc.onclose = null;
            this.dc.onerror = null;
            try { this.dc.close(); } catch (e) {}
            this.dc = null;
        }
        if (this.pc) {
            this.pc.onconnectionstatechange = null;
            this.pc.ontrack = null;
            try { this.pc.close(); } catch (e) {}
            this.pc = null;
        }
        if (this.localStream) {
            this.localStream.getTracks().forEach(track => {
                try { track.stop(); } catch (e) {}
            });
            this.localStream = null;
        }
    }

    disconnect() {
        this._intentionalClose = true;
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        this._teardownPeer();
        if (this.remoteAudio) {
            this.remoteAudio.pause();
            this.remoteAudio.srcObject = null;
            this.remoteAudio = null;
        }
        this.isConnected = false;
        this.isConnecting = false;
        this.reconnectAttempts = 0;
    }

    _waitForIceGatheringComplete(timeoutMs = 5000) {
        if (!this.pc || this.pc.iceGatheringState === 'complete') {
            return Promise.resolve();
        }
        return new Promise((resolve) => {
            let settled = false;
            const finish = () => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                this.pc.removeEventListener('icegatheringstatechange', onStateChange);
                resolve();
            };
            const timer = setTimeout(finish, timeoutMs);
            const onStateChange = () => {
                if (this.pc && this.pc.iceGatheringState === 'complete') finish();
            };
            this.pc.addEventListener('icegatheringstatechange', onStateChange);
        });
    }

    _waitForDataChannelOpen(timeoutMs = 10000) {
        if (this.dc?.readyState === 'open') {
            return Promise.resolve();
        }
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(new Error('Realtime Translation DataChannel 打开超时')), timeoutMs);
            const cleanup = () => {
                clearTimeout(timer);
                this.dc.removeEventListener('open', onOpen);
                this.dc.removeEventListener('error', onError);
            };
            const onOpen = () => {
                cleanup();
                resolve();
            };
            const onError = () => {
                cleanup();
                reject(new Error('Realtime Translation DataChannel 打开失败'));
            };
            this.dc.addEventListener('open', onOpen);
            this.dc.addEventListener('error', onError);
        });
    }
}

window.RealtimeTranslation = RealtimeTranslation;
