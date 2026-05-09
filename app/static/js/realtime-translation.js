/**
 * Realtime Translation - OpenAI Realtime Translation WebRTC sidecar.
 *
 * Experimental path used alongside the existing transcription flow. One client
 * translates the mixed input stream to one fixed target language.
 */
console.log('realtime-translation.js loaded, build: 20260509a');

class RealtimeTranslation {
    constructor(options = {}) {
        this.targetLanguage = options.targetLanguage;
        this.label = options.label || this.targetLanguage;
        this.pc = null;
        this.dc = null;
        this.remoteAudio = null;
        this.localStream = null;
        this.isConnected = false;
        this.options = {
            onConnected: options.onConnected || null,
            onDisconnected: options.onDisconnected || null,
            onInputDelta: options.onInputDelta || null,
            onOutputDelta: options.onOutputDelta || null,
            onError: options.onError || null
        };
    }

    async connect(mediaStream) {
        if (!this.targetLanguage) {
            throw new Error('Realtime Translation targetLanguage is required');
        }

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

        this.pc = new RTCPeerConnection();
        this.dc = this.pc.createDataChannel('oai-events');
        this._setupDataChannel();

        this.pc.onconnectionstatechange = () => {
            console.log(`RealtimeTranslation [${this.label}] connectionState:`, this.pc.connectionState);
            if (['failed', 'disconnected', 'closed'].includes(this.pc.connectionState)) {
                this.isConnected = false;
                if (this.options.onDisconnected) {
                    this.options.onDisconnected(this);
                }
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

        this.localStream = new MediaStream(mediaStream.getAudioTracks().map(track => track.clone()));
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
    }

    _setupDataChannel() {
        this.dc.onopen = () => {
            this.isConnected = true;
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

        console.log(`RealtimeTranslation [${this.label}] recv:`, event.type, event);
        if (event.type === 'error') {
            if (this.options.onError) {
                this.options.onError(event.error || event, this);
            }
            return;
        }

        if (event.type === 'session.input_transcript.delta' && typeof event.delta === 'string') {
            if (this.options.onInputDelta) {
                this.options.onInputDelta(event.delta, this);
            }
            return;
        }

        if (event.type === 'session.output_transcript.delta' && typeof event.delta === 'string') {
            if (this.options.onOutputDelta) {
                this.options.onOutputDelta(event.delta, this);
            }
        }
    }

    disconnect() {
        if (this.dc) {
            try { this.dc.close(); } catch (e) {}
            this.dc = null;
        }
        if (this.pc) {
            try { this.pc.close(); } catch (e) {}
            this.pc = null;
        }
        if (this.remoteAudio) {
            this.remoteAudio.pause();
            this.remoteAudio.srcObject = null;
            this.remoteAudio = null;
        }
        if (this.localStream) {
            this.localStream.getTracks().forEach(track => {
                try { track.stop(); } catch (e) {}
            });
            this.localStream = null;
        }
        this.isConnected = false;
    }

    _waitForIceGatheringComplete(timeoutMs = 5000) {
        if (!this.pc || this.pc.iceGatheringState === 'complete') {
            return Promise.resolve();
        }
        return new Promise((resolve) => {
            const timer = setTimeout(resolve, timeoutMs);
            const onStateChange = () => {
                if (this.pc.iceGatheringState === 'complete') {
                    clearTimeout(timer);
                    this.pc.removeEventListener('icegatheringstatechange', onStateChange);
                    resolve();
                }
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
