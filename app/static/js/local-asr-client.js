/**
 * LocalAsrClient — 直连 Qwen3-ASR 流式转写服务的轻量客户端。
 *
 * 与 app.js 解耦：只负责 start/chunk/finish 三个端点的 HTTP 往返，
 * 把每次 chunk 返回的累积全文通过 onText 回调交给调用方。
 *
 * 协议（对应 qwen_asr/cli/demo_streaming.py）：
 *   POST {baseUrl}/api/start                     -> { session_id }
 *   POST {baseUrl}/api/chunk?session_id=xxx      -> { language, text }
 *        body = float32 LE PCM 二进制, Content-Type: application/octet-stream
 *   POST {baseUrl}/api/finish?session_id=xxx     -> { language, text }
 *
 * Qwen3-ASR 的 text 是「从头到现在的累积全文」，因此 chunk 必须串行：
 * 上一个未返回时丢弃新到的音频块（实时性优先于完整性）。
 */
(function () {
    'use strict';

    class LocalAsrClient {
        /**
         * @param {Object}   opts
         * @param {string}   opts.baseUrl      本地 ASR 服务地址，如 http://192.168.1.10:8000
         * @param {Function} [opts.onText]     每次 chunk/finish 返回时触发：(text, language) => void
         * @param {Function} [opts.onStatus]   状态变化：(kind, detail) => void
         *                                      kind: 'connected' | 'disconnected' | 'error'
         */
        constructor(opts = {}) {
            this.baseUrl = (opts.baseUrl || '').replace(/\/+$/, '');
            this.onText = typeof opts.onText === 'function' ? opts.onText : null;
            this.onStatus = typeof opts.onStatus === 'function' ? opts.onStatus : null;

            this.sessionId = null;
            this._inFlight = false;   // 当前是否有 chunk 请求未返回
            this._stopped = false;    // finish 之后不再接受 feedAudio
            this._chunkBuffer = null; // 攒满一帧之前的待发样本
            // 目标一帧样本数：500ms @16kHz。WebAudio 回调块（如 4096）会拼到这里。
            this._targetChunkSize = (opts.chunkSizeSamples || 8000) | 0;
        }

        /** 创建会话，拿 session_id */
        async start() {
            this._stopped = false;
            const resp = await fetch(`${this.baseUrl}/api/start`, { method: 'POST' });
            if (!resp.ok) {
                throw new Error(`本地 ASR 启动失败: HTTP ${resp.status}`);
            }
            const data = await resp.json();
            this.sessionId = data.session_id;
            if (!this.sessionId) {
                throw new Error('本地 ASR 未返回 session_id');
            }
            if (this.onStatus) this.onStatus('connected', this.sessionId);
        }

        /**
         * 送入一段 16kHz 单声道 float32 PCM（任意长度）。
         * 内部按 targetChunkSize（默认 8000 样本 = 500ms @16kHz）攒满后再发送。
         * 串行保证：若上一个 chunk 未返回，本次直接丢弃。
         * @param {Float32Array} float32Array
         */
        feedAudio(float32Array) {
            if (this._stopped || !this.sessionId || this._inFlight) return;
            if (!float32Array || float32Array.length === 0) return;

            // 攒到目标长度再发送。WebAudio 的 onaudioprocess 回调块大小受限于
            // createScriptProcessor 的合法 bufferSize（2 的幂），不一定是目标长度，
            // 所以这里用一个可变长 buffer 拼接。
            if (this._chunkBuffer && this._chunkBuffer.length > 0) {
                const merged = new Float32Array(this._chunkBuffer.length + float32Array.length);
                merged.set(this._chunkBuffer, 0);
                merged.set(float32Array, this._chunkBuffer.length);
                this._chunkBuffer = merged;
            } else {
                this._chunkBuffer = new Float32Array(float32Array);
            }
            if (this._chunkBuffer.length < this._targetChunkSize) return;

            const toSend = this._chunkBuffer.subarray(0, this._targetChunkSize);
            this._chunkBuffer = new Float32Array(this._chunkBuffer.subarray(this._targetChunkSize));
            this._sendChunk(new Float32Array(toSend));
        }

        async _sendChunk(float32Array) {
            this._inFlight = true;
            // 30s 超时兜底：服务端若卡死，宁可丢这一帧并报错，也不能让
            // inFlight 永久挂起冻结整个转写流水线。
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 30000);
            try {
                const url = `${this.baseUrl}/api/chunk?session_id=${encodeURIComponent(this.sessionId)}`;
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/octet-stream' },
                    body: float32Array.buffer.slice(0, float32Array.byteLength),
                    signal: controller.signal,
                });
                if (!resp.ok) {
                    if (this.onStatus) this.onStatus('error', `chunk HTTP ${resp.status}`);
                    return;
                }
                const data = await resp.json();
                if (data && this.onText) {
                    this.onText(data.text || "", data.language || "", data);
                }
            } catch (err) {
                if (this.onStatus) this.onStatus('error', String(err.message || err));
            } finally {
                clearTimeout(timer);
                this._inFlight = false;
            }
        }

        /** 结束会话，flush 尾部音频，返回最终全文 */
        async finish() {
            this._stopped = true;
            if (!this.sessionId) return;
            // 先把攒在 buffer 里不足一帧的尾部样本发掉，避免丢失最后半句。
            if (this._chunkBuffer && this._chunkBuffer.length > 0 && !this._inFlight) {
                await this._sendChunk(new Float32Array(this._chunkBuffer));
                this._chunkBuffer = null;
            }
            try {
                const url = `${this.baseUrl}/api/finish?session_id=${encodeURIComponent(this.sessionId)}`;
                const resp = await fetch(url, { method: 'POST' });
                if (resp.ok) {
                    const data = await resp.json();
                    if (data && this.onText) {
                        this.onText(data.text || "", data.language || "", data);
                    }
                }
            } catch (err) {
                if (this.onStatus) this.onStatus('error', `finish: ${err.message || err}`);
            } finally {
                this.sessionId = null;
                if (this.onStatus) this.onStatus('disconnected');
            }
        }
    }

    window.LocalAsrClient = LocalAsrClient;
})();
