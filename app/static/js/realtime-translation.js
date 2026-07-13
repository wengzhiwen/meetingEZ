/**
 * Realtime Translation - OpenAI Realtime Translation WebRTC sidecar.
 *
 * Experimental path used alongside the existing transcription flow. One client
 * translates the mixed input stream to one fixed target language. Each session
 * also emits a source-language transcript (session.input_transcript.*) which the
 * Beta UI can surface as the original caption.
 */
console.log('realtime-translation.js loaded, build: 20260713a');

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
        // 用稳定 stream key 管理 input/output 的 live/final。服务端有 item_id/response_id
        // 时优先使用，否则生成本地序号；input/output 各自拥有独立命名空间。
        this.inputItems = {};
        this.outputItems = {};
        // session.created 门禁：DataChannel 打开后等首个 session.created 再置 connected。
        this._sessionReady = false;
        this._dcOpenPending = false;
        this._sessionGateTimer = null;
        // Translation transcript delta 不保证携带 item_id。按 input/output 各维护
        // 一个当前流，并用短暂停顿作为兜底完成边界（与 sokuji 的 pairing 思路一致）。
        this._transcriptSequence = 0;
        this._transcriptSilenceMs = 1500;
        this._transcriptStreams = {
            input: { current: null, timer: null, lastFinalized: null },
            output: { current: null, timer: null, lastFinalized: null }
        };
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

    _markConnected(reason) {
        if (this.isConnected) return;
        if (this._sessionGateTimer) {
            clearTimeout(this._sessionGateTimer);
            this._sessionGateTimer = null;
        }
        this._sessionReady = true;
        this._dcOpenPending = false;
        this.isConnected = true;
        this.reconnectAttempts = 0;
        console.log(`RealtimeTranslation [${this.label}] connected (${reason})`);
        if (this.options.onConnected) {
            this.options.onConnected(this);
        }
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
            console.log(`RealtimeTranslation [${this.label}] DataChannel opened`);
            this._dcOpenPending = true;
            // 等 session.created 再 onConnected；8s 超时兜底，防止 session.created 丢包时卡在 connecting。
            if (this._sessionGateTimer) clearTimeout(this._sessionGateTimer);
            this._sessionGateTimer = setTimeout(
                () => this._markConnected('session_created_timeout'), 8000);
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
            || type === 'conversation.item.input_audio_transcription.delta'
            || type === 'session.output_transcript.delta'
            || type === 'response.output_audio_transcript.delta';
        // delta 事件高频，仅在非常规事件时打印，避免控制台刷屏。
        if (!isDelta) {
            console.log(`RealtimeTranslation [${this.label}] recv:`, type, event);
        }

        if (type === 'session.created' || type === 'translation_session.created') {
            this._markConnected('session_created');
            return;
        }

        if (type === 'error') {
            if (this.options.onError) {
                this.options.onError(event.error || event, this);
            }
            return;
        }

        if ((type === 'session.input_transcript.delta'
            || type === 'conversation.item.input_audio_transcription.delta')
            && typeof event.delta === 'string') {
            this._appendTranscriptDelta('input', event);
            return;
        }

        // input 转写完成（事件名以实测为准，兼容 .done / .completed）
        if (type === 'session.input_transcript.done'
            || type === 'session.input_transcript.completed'
            || type === 'conversation.item.input_audio_transcription.completed') {
            this._finalizeTranscriptStream('input', event);
            return;
        }

        if ((type === 'session.output_transcript.delta'
            || type === 'response.output_audio_transcript.delta')
            && typeof event.delta === 'string') {
            this._appendTranscriptDelta('output', event);
            return;
        }

        // 译文完成
        if (type === 'session.output_transcript.done'
            || type === 'session.output_transcript.completed'
            || type === 'response.output_audio_transcript.done') {
            this._finalizeTranscriptStream('output', event);
        }
    }

    _eventTranscriptKey(kind, event = {}) {
        return event.item_id || event.response_id || null;
    }

    _nextTranscriptKey(kind) {
        this._transcriptSequence += 1;
        return `${kind}-stream-${this._transcriptSequence}`;
    }

    _appendTranscriptDelta(kind, event) {
        const stream = this._transcriptStreams[kind];
        const explicitKey = this._eventTranscriptKey(kind, event);
        if (stream.current && explicitKey && stream.current.explicitKey
            && stream.current.explicitKey !== explicitKey) {
            this._finalizeTranscriptStream(kind, {});
        }

        if (!stream.current) {
            stream.current = {
                key: explicitKey || this._nextTranscriptKey(kind),
                explicitKey,
                live: '',
                updatedAt: Date.now()
            };
        }

        stream.current.live += event.delta;
        stream.current.updatedAt = Date.now();
        const itemMap = kind === 'input' ? this.inputItems : this.outputItems;
        itemMap[stream.current.key] = {
            live: stream.current.live,
            final: null,
            updatedAt: stream.current.updatedAt
        };

        const callback = kind === 'input'
            ? this.options.onInputDelta
            : this.options.onOutputDelta;
        if (callback) {
            callback(event.delta, this, stream.current.key, stream.current.live);
        }
        this._resetTranscriptSilenceTimer(kind);
    }

    _resetTranscriptSilenceTimer(kind) {
        const stream = this._transcriptStreams[kind];
        if (stream.timer) clearTimeout(stream.timer);
        stream.timer = setTimeout(() => {
            stream.timer = null;
            this._finalizeTranscriptStream(kind, {});
        }, this._transcriptSilenceMs);
    }

    _finalizeTranscriptStream(kind, event = {}) {
        const stream = this._transcriptStreams[kind];
        if (stream.timer) {
            clearTimeout(stream.timer);
            stream.timer = null;
        }

        const explicitKey = this._eventTranscriptKey(kind, event);
        const eventText = event.transcript || event.text || '';
        const current = stream.current;
        const transcript = (eventText || current?.live || '').trim();
        const itemId = explicitKey || current?.key || null;
        if (!transcript || !itemId) {
            stream.current = null;
            return;
        }

        const finalizedAt = Date.now();
        const last = stream.lastFinalized;
        if (!current && last && finalizedAt - last.at < 10000
            && ((explicitKey && explicitKey === last.explicitKey)
                || (!explicitKey && transcript === last.transcript))) {
            stream.current = null;
            return;
        }

        const itemMap = kind === 'input' ? this.inputItems : this.outputItems;
        itemMap[itemId] = { live: transcript, final: transcript, updatedAt: finalizedAt };
        stream.lastFinalized = { explicitKey, transcript, at: finalizedAt };
        stream.current = null;

        const callback = kind === 'input'
            ? this.options.onInputDone
            : this.options.onOutputDone;
        if (callback) callback({ itemId, transcript }, this);
    }

    _clearTranscriptStreams() {
        Object.values(this._transcriptStreams).forEach((stream) => {
            if (stream.timer) clearTimeout(stream.timer);
            stream.timer = null;
            stream.current = null;
            stream.lastFinalized = null;
        });
    }

    getInputItems() {
        return this.inputItems;
    }

    getOutputItems() {
        return this.outputItems;
    }

    _teardownPeer() {
        if (this._sessionGateTimer) {
            clearTimeout(this._sessionGateTimer);
            this._sessionGateTimer = null;
        }
        this._sessionReady = false;
        this._dcOpenPending = false;
        this._clearTranscriptStreams();
        this.inputItems = {};
        this.outputItems = {};
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
