/**
 * LocalAsrClient — 直连 Qwen3-ASR 流式转写服务的轻量客户端。
 *
 * 与 app.js 解耦：只负责 start/chunk/finish 三个端点的 HTTP 往返，
 * 把每次 chunk 返回的累积全文通过 onText 回调交给调用方。
 *
 * 协议（对应 qwen_asr/cli/demo_streaming.py）：
 *   POST {baseUrl}/api/start?language=Japanese&secondary_language=Chinese
 *                                                -> { session_id }
 *   POST {baseUrl}/api/chunk?session_id=xxx      -> { language, text }
 *        body = float32 LE PCM 二进制, Content-Type: application/octet-stream
 *   POST {baseUrl}/api/finish?session_id=xxx     -> { language, text }
 *
 * start 的 language / secondary_language（Qwen3-ASR 英文语言名）用于让服务端
 * 锁定转写语言；纯自动检测在日语等输入下容易整段错检成英语。
 * 旧版服务端不认识这两个参数会直接忽略，不影响会话创建。
 *
 * Qwen3-ASR 的 text 是「从头到现在的累积全文」，因此 chunk 必须串行。
 * 请求未返回期间继续缓存新音频；积压时合并成较大的 chunk 追赶实时进度，
 * 不再静默丢弃正在讲话的内容。
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
            // 会话语言提示 { primary, secondary }，Qwen3-ASR 英文语言名（如 'Japanese'）。
            this.languages = opts.languages || null;

            this.sessionId = null;
            this._inFlight = false;    // 当前是否有 chunk 请求未返回
            this._stopped = false;     // finish 之后不再接受 feedAudio
            this._audioQueue = [];     // 请求期间继续接收的音频块
            this._queuedSamples = 0;
            this._drainPromise = null;
            // 目标一帧样本数：500ms @16kHz。WebAudio 回调块（如 4096）会拼到这里。
            this._targetChunkSize = (opts.chunkSizeSamples || 8000) | 0;
            // 有积压时最多合并为 2 秒一包，减少模型调用次数并追赶实时进度。
            this._catchUpChunkSize = (opts.catchUpChunkSizeSamples || 32000) | 0;
            // 极端故障只保留最近 15 秒，防止本地服务卡死导致浏览器内存无限增长。
            this._maxQueuedSamples = (opts.maxQueuedSamples || 240000) | 0;
            this._overflowWarned = false;
        }

        /** 创建会话，拿 session_id。languages 随 query 参数下发（见文件头协议说明）。 */
        async start() {
            this._stopped = false;
            this._audioQueue = [];
            this._queuedSamples = 0;
            this._drainPromise = null;
            this._overflowWarned = false;
            const params = new URLSearchParams();
            if (this.languages && this.languages.primary) {
                params.set('language', this.languages.primary);
            }
            if (this.languages && this.languages.secondary) {
                params.set('secondary_language', this.languages.secondary);
            }
            const query = params.toString();
            const resp = await fetch(`${this.baseUrl}/api/start${query ? '?' + query : ''}`, { method: 'POST' });
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
         * 串行保证：若上一个 chunk 未返回，本次进入队列，返回后自动续发。
         * @param {Float32Array} float32Array
         */
        feedAudio(float32Array) {
            if (this._stopped || !this.sessionId) return;
            if (!float32Array || float32Array.length === 0) return;

            // WebAudio 的内部 input buffer 会复用，因此必须复制后再入队。
            const chunk = new Float32Array(float32Array);
            this._audioQueue.push(chunk);
            this._queuedSamples += chunk.length;
            this._trimOverflow();
            void this._drainAudio();
        }

        _trimOverflow() {
            let excess = this._queuedSamples - this._maxQueuedSamples;
            if (excess <= 0) {
                if (this._queuedSamples < this._targetChunkSize) this._overflowWarned = false;
                return;
            }

            const dropped = excess;
            while (excess > 0 && this._audioQueue.length) {
                const first = this._audioQueue[0];
                if (first.length <= excess) {
                    this._audioQueue.shift();
                    this._queuedSamples -= first.length;
                    excess -= first.length;
                } else {
                    this._audioQueue[0] = first.subarray(excess);
                    this._queuedSamples -= excess;
                    excess = 0;
                }
            }
            if (!this._overflowWarned) {
                this._overflowWarned = true;
                const seconds = (dropped / 16000).toFixed(1);
                if (this.onStatus) {
                    this.onStatus('warning', `本地 ASR 严重积压，已跳过最旧 ${seconds} 秒音频`);
                }
            }
        }

        _takeQueuedSamples(count) {
            const size = Math.min(count, this._queuedSamples);
            const output = new Float32Array(size);
            let offset = 0;
            while (offset < size && this._audioQueue.length) {
                const first = this._audioQueue[0];
                const take = Math.min(first.length, size - offset);
                output.set(first.subarray(0, take), offset);
                offset += take;
                this._queuedSamples -= take;
                if (take === first.length) {
                    this._audioQueue.shift();
                } else {
                    this._audioQueue[0] = first.subarray(take);
                }
            }
            return output;
        }

        _nextChunkSize(flushTail) {
            if (this._queuedSamples < this._targetChunkSize) {
                return flushTail ? this._queuedSamples : 0;
            }
            if (this._queuedSamples >= this._targetChunkSize * 2) {
                return Math.min(this._queuedSamples, this._catchUpChunkSize);
            }
            return this._targetChunkSize;
        }

        async _drainAudio({ flushTail = false } = {}) {
            // 已有排空任务时复用它；finish 会在其后补一次尾部冲刷。
            if (this._drainPromise) {
                if (!flushTail) return this._drainPromise;
                await this._drainPromise;
                return this._drainAudio({ flushTail: true });
            }

            let trackedPromise;
            const drain = async () => {
                while (this.sessionId) {
                    const size = this._nextChunkSize(flushTail);
                    if (size <= 0) break;
                    await this._sendChunk(this._takeQueuedSamples(size));
                }
            };
            trackedPromise = drain().finally(() => {
                if (this._drainPromise === trackedPromise) this._drainPromise = null;
            });
            this._drainPromise = trackedPromise;
            return trackedPromise;
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
            // 等待正在发送的 chunk，并把队列及不足 500ms 的尾音全部送完。
            await this._drainAudio({ flushTail: true });
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
                this._audioQueue = [];
                this._queuedSamples = 0;
                this._drainPromise = null;
                if (this.onStatus) this.onStatus('disconnected');
            }
        }
    }

    window.LocalAsrClient = LocalAsrClient;
})();
