/**
 * LocalCollectorClient / CollectorAudioPipeline — macOS 本地音频采集器的浏览器侧客户端。
 *
 * 采集器（native-capture/，Swift 菜单栏程序）在 127.0.0.1:17642 提供 WebSocket：
 *   文本帧 = JSON 控制协议（每条带 type，ref 由客户端透传、回复带回）：
 *     C→S: ping / requestPermission / listApps / start{mode,bundleIds} / stop
 *     S→C: hello{version,permission,mock} / pong / permission{granted,effective} /
 *          apps[{pid,bundleId,name}] / started{mode,sources,audioFormat{sampleRate,channels}} /
 *          stopped{reason,message} / error{code,message} / stats{framesSent,framesDropped}
 *   二进制帧 = Float32 LE PCM，单声道，started.audioFormat.sampleRate（16kHz），
 *              约 1600 样本/帧（100ms）。
 *
 * LocalCollectorClient 只管协议与连接状态机（自动重连、last-wins 被踢感知），
 * 不碰 WebAudio；CollectorAudioPipeline 把 PCM 经 AudioWorklet 环形缓冲重建成
 * MediaStream（欠载静音、超水位丢旧限延迟），管线在采集器短暂断线时保持存活
 * （RealtimeTranslation 重连会复用同一 MediaStream，不能中途销毁）。
 *
 * 术语：采集器 = collector；应用音频 = 其他 App 的输出（Zoom/Teams 等）。
 */
(function () {
    'use strict';

    const DEFAULT_WS_URL = 'ws://127.0.0.1:17642';
    const RECONNECT_BASE_MS = 1000;
    const RECONNECT_MAX_MS = 8000;

    class LocalCollectorClient {
        /**
         * @param {Object}   opts
         * @param {string}   [opts.wsUrl]
         * @param {Function} [opts.onState]   (state, detail) => void
         *      state: 'idle' | 'connecting' | 'ready' | 'denied' | 'capturing' | 'disconnected'
         *      denied = 已连接但无屏幕录制权限（或权限需重启采集器生效）
         * @param {Function} [opts.onApps]    (apps) => void  apps: [{pid, bundleId, name}]
         * @param {Function} [opts.onAudio]   (Float32Array) => void  二进制音频帧
         * @param {Function} [opts.onStopped] (reason, message) => void  采集中意外停止
         * @param {Function} [opts.onStats]   (stats) => void
         */
        constructor(opts = {}) {
            this.wsUrl = opts.wsUrl || DEFAULT_WS_URL;
            this.onState = typeof opts.onState === 'function' ? opts.onState : null;
            this.onApps = typeof opts.onApps === 'function' ? opts.onApps : null;
            this.onAudio = typeof opts.onAudio === 'function' ? opts.onAudio : null;
            this.onStopped = typeof opts.onStopped === 'function' ? opts.onStopped : null;
            this.onStats = typeof opts.onStats === 'function' ? opts.onStats : null;

            this.state = 'idle';
            this.permission = null;        // hello.permission
            this.permissionEffective = null;
            this.apps = null;
            this.lastError = null;

            this._ws = null;
            this._ref = 0;
            this._pending = new Map();     // ref -> {resolve, reject, timer}
            this._reconnectAttempts = 0;
            this._reconnectTimer = null;
            this._connectWaiters = [];
            this._destroyed = false;
        }

        /** 已连接（含未授权）时立即返回，否则发起连接并等待 hello。 */
        ensureConnected(timeoutMs = 3000) {
            if (this.state === 'ready' || this.state === 'denied' || this.state === 'capturing') {
                return Promise.resolve();
            }
            if (this._connectPromise && this.state === 'connecting') {
                return this._connectPromise;
            }
            this._destroyed = false;
            clearTimeout(this._reconnectTimer);
            this._connect();
            return this._waitForConnected(timeoutMs);
        }

        _waitForConnected(timeoutMs) {
            return new Promise((resolve, reject) => {
                const timer = setTimeout(() => {
                    const index = this._connectWaiters.indexOf(waiter);
                    if (index >= 0) this._connectWaiters.splice(index, 1);
                    reject(new Error(this.lastError
                        ? `连接本地采集器失败: ${this.lastError}`
                        : '未检测到本地采集器。请先在本机启动 MeetingEZ Capture（菜单栏应用）。'));
                }, timeoutMs);
                const waiter = { resolve, timer };
                this._connectWaiters.push(waiter);
            });
        }

        _settleWaiters(ok) {
            const waiters = this._connectWaiters.splice(0);
            for (const waiter of waiters) {
                clearTimeout(waiter.timer);
                if (ok) waiter.resolve();
            }
        }

        _connect() {
            if (this._destroyed) return;
            this._setState('connecting');
            this.lastError = null;
            let ws;
            try {
                ws = new WebSocket(this.wsUrl);
            } catch (error) {
                this._scheduleReconnect();
                return;
            }
            this._ws = ws;

            // 二进制帧默认以 Blob 投递，PCM 解析需要 ArrayBuffer。
            ws.binaryType = 'arraybuffer';

            ws.onopen = () => {
                // 等待 hello（服务端在握手通过后立即下发）。
            };
            ws.onmessage = (event) => this._handleMessage(event);
            ws.onerror = () => {
                this.lastError = 'WebSocket 错误（采集器未运行或 Origin 被拒）。';
            };
            ws.onclose = () => {
                if (this._ws !== ws) return;
                this._ws = null;
                this._failPending('disconnected', '与本地采集器的连接已断开。');
                const wasCapturing = this.state === 'capturing';
                this._setState('disconnected');
                if (wasCapturing && this.onStopped) {
                    this.onStopped('client-disconnected', '与本地采集器的连接已断开。');
                }
                this._settleWaiters(false);
                this._scheduleReconnect();
            };
        }

        _scheduleReconnect() {
            if (this._destroyed) return;
            const delay = Math.min(RECONNECT_BASE_MS * 2 ** this._reconnectAttempts, RECONNECT_MAX_MS);
            this._reconnectAttempts += 1;
            // ±20% 抖动，避免与其他页面同频重试。
            const jitter = delay * (0.8 + Math.random() * 0.4);
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = setTimeout(() => this._connect(), jitter);
        }

        _handleMessage(event) {
            if (typeof event.data === 'string') {
                let object = null;
                try {
                    object = JSON.parse(event.data);
                } catch (error) {
                    console.warn('本地采集器消息解析失败:', error);
                    return;
                }
                this._handleJson(object);
                return;
            }
            if (event.data instanceof ArrayBuffer) {
                if (event.data.byteLength < 4 || event.data.byteLength % 4 !== 0) return;
                if (this.onAudio) {
                    this.onAudio(new Float32Array(event.data));
                }
            }
        }

        _handleJson(object) {
            const type = object.type;
            if (object.ref !== undefined && this._pending.has(object.ref)) {
                const pending = this._pending.get(object.ref);
                if (type === 'error') {
                    this._pending.delete(object.ref);
                    clearTimeout(pending.timer);
                    pending.reject(new Error(object.message || object.code || '采集器返回错误。'));
                    return;
                }
                if (type === pending.expect) {
                    this._pending.delete(object.ref);
                    clearTimeout(pending.timer);
                    pending.resolve(object);
                    return;
                }
                // 其他类型落到普通分发（例如等待 started 期间收到 stats）。
            }

            switch (type) {
                case 'hello':
                    this.permission = !!object.permission;
                    this._reconnectAttempts = 0;
                    this._setState(this.permission ? 'ready' : 'denied');
                    this._settleWaiters(true);
                    // 连接后顺手拉一次应用列表。
                    if (this.permission) {
                        this.listApps().catch(() => {});
                    }
                    break;
                case 'permission':
                    this.permission = !!object.granted;
                    this.permissionEffective = !!object.effective;
                    this._setState(this.permission && this.permissionEffective ? 'ready' : 'denied');
                    if (this.permission && object.effective) {
                        this.listApps().catch(() => {});
                    }
                    break;
                case 'apps':
                    this.apps = Array.isArray(object.apps) ? object.apps : [];
                    if (this.onApps) this.onApps(this.apps);
                    break;
                case 'started':
                    this._setState('capturing');
                    break;
                case 'stopped':
                    if (object.reason !== 'request') {
                        if (this.onStopped) this.onStopped(object.reason, object.message || '');
                    }
                    if (this.state === 'capturing') this._setState('ready');
                    break;
                case 'stats':
                    if (this.onStats) this.onStats(object);
                    break;
                case 'ping':
                    this._sendJson({ type: 'pong' });
                    break;
                default:
                    break;
            }
        }

        _setState(state, detail) {
            if (this.state === state) return;
            this.state = state;
            if (this.onState) this.onState(state, detail || '');
        }

        _sendJson(object) {
            if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                this._ws.send(JSON.stringify(object));
            }
        }

        _request(payload, expect, timeoutMs) {
            return new Promise((resolve, reject) => {
                if (!this._ws || this._ws.readyState !== WebSocket.OPEN) {
                    reject(new Error('与本地采集器的连接未就绪。'));
                    return;
                }
                this._ref += 1;
                const ref = String(this._ref);
                const timer = setTimeout(() => {
                    this._pending.delete(ref);
                    reject(new Error('本地采集器响应超时。'));
                }, timeoutMs);
                this._pending.set(ref, { resolve, reject, expect, timer });
                this._sendJson(Object.assign({ ref }, payload));
            });
        }

        _failPending(code, message) {
            const pending = Array.from(this._pending.values());
            this._pending.clear();
            for (const item of pending) {
                clearTimeout(item.timer);
                item.reject(new Error(message));
            }
        }

        listApps(timeoutMs = 5000) {
            return this._request({ type: 'listApps' }, 'apps', timeoutMs)
                .then((reply) => {
                    this.apps = Array.isArray(reply.apps) ? reply.apps : [];
                    if (this.onApps) this.onApps(this.apps);
                    return this.apps;
                });
        }

        /** 返回 {granted, effective}；effective=false 表示需重启采集器进程权限才生效。 */
        requestPermission(timeoutMs = 300000) {
            return this._request({ type: 'requestPermission' }, 'permission', timeoutMs);
        }

        /**
         * @param {Object} params {mode:'system'} 或 {mode:'apps', bundleIds:[...]}
         * @returns {Promise<{sampleRate:number, channels:number, sources:string[]}>}
         */
        start(params, timeoutMs = 20000) {
            const payload = { type: 'start', mode: params.mode };
            if (params.mode === 'apps') payload.bundleIds = params.bundleIds;
            return this._request(payload, 'started', timeoutMs).then((reply) => ({
                sampleRate: (reply.audioFormat && reply.audioFormat.sampleRate) || 16000,
                channels: (reply.audioFormat && reply.audioFormat.channels) || 1,
                sources: reply.sources || []
            }));
        }

        stop(timeoutMs = 5000) {
            const payload = { type: 'stop' };
            // stop 用宽松语义：超时也视为已停（服务端断开时本来就会自动停）。
            return this._request(payload, 'stopped', timeoutMs)
                .catch(() => {
                    if (this.state === 'capturing') this._setState('ready');
                });
        }

        destroy() {
            this._destroyed = true;
            clearTimeout(this._reconnectTimer);
            this._failPending('destroyed', '客户端已销毁。');
            if (this._ws) {
                try { this._ws.close(); } catch (error) { /* 忽略 */ }
                this._ws = null;
            }
            this._setState('idle');
        }
    }

    /**
     * PCM → MediaStream：AudioWorklet 环形缓冲（见 collector-pcm-worklet.js）。
     * 断流时 suspend()（清缓冲静音）而不拆图；close() 仅在会议结束/致命错误时调用。
     */
    class CollectorAudioPipeline {
        /**
         * @param {Object} format {sampleRate, channels}（来自采集器 started.audioFormat）
         */
        constructor(format = {}) {
            this.sampleRate = format.sampleRate || 16000;
            this.ctx = null;
            this.node = null;
            this.destination = null;
            this.micSource = null;
            this.stream = null;
            this._suspended = false;
            this.closed = false;
            this.onLevels = null;  // (buffered, dropped) => void，约 500ms 一次
        }

        /** 建立 WebAudio 图并返回 MediaStream（须在用户手势之后调用）。 */
        async attach() {
            this.ctx = new AudioContext({ sampleRate: this.sampleRate });
            await this.ctx.audioWorklet.addModule('/static/js/collector-pcm-worklet.js?v=20260816a');
            this.node = new AudioWorkletNode(this.ctx, 'collector-pcm-player', {
                numberOfInputs: 0,
                numberOfOutputs: 1,
                outputChannelCount: [1]
            });
            this.node.port.onmessage = (event) => {
                if (event.data && event.data.type === 'level' && this.onLevels) {
                    this.onLevels(event.data.buffered, event.data.dropped);
                }
            };
            this.destination = this.ctx.createMediaStreamDestination();
            this.node.connect(this.destination);
            if (this.ctx.state === 'suspended') {
                await this.ctx.resume();
            }
            if (this.ctx.state === 'suspended') {
                await this.close();
                throw new Error('浏览器阻止了音频播放（AudioContext suspended）。请重新点击开始。');
            }
            this.stream = this.destination.stream;
            return this.stream;
        }

        /** 喂入一帧 Float32 PCM（零拷贝转移所有权）。 */
        pushPcm(samples) {
            if (!this.node || this.closed || this._suspended) return;
            this.node.port.postMessage({ type: 'pcm', samples }, [samples.buffer]);
        }

        /** 把本地麦克风 MediaStream 混入输出（应用音频 + 自己的声音）。 */
        mixInStream(stream) {
            if (!this.ctx || !this.destination) return;
            this.micSource = this.ctx.createMediaStreamSource(stream);
            this.micSource.connect(this.destination);
        }

        /** 采集器断流：清空缓冲并静音，保持图存活以便无缝恢复。 */
        suspend() {
            this._suspended = true;
            if (this.node) this.node.port.postMessage({ type: 'clear' });
        }

        resume() {
            this._suspended = false;
        }

        get suspended() {
            return this._suspended;
        }

        async close() {
            this.closed = true;
            try { if (this.node) this.node.disconnect(); } catch (error) { /* 忽略 */ }
            try { if (this.micSource) this.micSource.disconnect(); } catch (error) { /* 忽略 */ }
            if (this.ctx) {
                try { await this.ctx.close(); } catch (error) { /* 忽略 */ }
            }
            this.ctx = null;
            this.node = null;
            this.destination = null;
            this.stream = null;
        }
    }

    window.LocalCollectorClient = LocalCollectorClient;
    window.CollectorAudioPipeline = CollectorAudioPipeline;
})();
