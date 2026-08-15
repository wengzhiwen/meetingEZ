/**
 * LocalTranslateClient — 直连本地 OpenAI 兼容 API 的轻量翻译客户端。
 *
 * 与 LocalAsrClient 同一先例：浏览器直连本地端点，不经 meetingEZ 后端中转。
 * 端点需自行开启 CORS（如 vLLM 启动加 --allowed-origins '["*"]'）。
 *
 * 协议：标准 OpenAI Chat Completions
 *   POST {baseUrl}/chat/completions
 *   Authorization: Bearer {apiKey}（可选）
 *
 * 这是普通（非实时）API：每次翻译一段已定格的字幕文本，延迟取决于
 * 本地模型的生成速度，体验与 Realtime Translation 有别，但零云端成本。
 */
(function () {
    'use strict';

    // 字幕语言码 → 提示词用的语言名。realtimeTranslationTargetLanguages
    // 归一化后只会出现这些码。
    const LANGUAGE_NAMES = {
        zh: '简体中文',
        'zh-TW': '繁體中文',
        en: 'English',
        ja: '日本語',
        ko: '한국어',
        es: 'Español',
        fr: 'Français',
        de: 'Deutsch',
        ru: 'Русский',
        pt: 'Português',
    };

    const SYSTEM_PROMPT = [
        '你是会议字幕翻译。',
        '把用户提供的文本翻译成指定的目标语言。',
        '只输出译文本身：不要解释、不要原文、不要引号、不要任何前后缀。',
        '保持口语风格与标点，专有名词按目标语言的惯用写法处理。',
    ].join('');

    class LocalTranslateClient {
        /**
         * @param {Object} opts
         * @param {string} opts.baseUrl  形如 http://host:port/v1（须含 /v1）
         * @param {string} opts.model    模型名，如 qwen3-8b
         * @param {string} [opts.apiKey] 可选；为空则不发 Authorization 头
         */
        constructor(opts = {}) {
            this.baseUrl = (opts.baseUrl || '').trim().replace(/\/+$/, '');
            this.model = (opts.model || '').trim();
            this.apiKey = (opts.apiKey || '').trim();
        }

        /** 配置是否齐全可用 */
        get ready() {
            return Boolean(this.baseUrl && this.model);
        }

        _headers() {
            const headers = { 'Content-Type': 'application/json' };
            if (this.apiKey) headers['Authorization'] = `Bearer ${this.apiKey}`;
            return headers;
        }

        /**
         * 翻译一段文本。
         * @param {string} text
         * @param {Object} opts
         * @param {string} opts.targetLanguage     目标语言码（zh/en/...）
         * @param {string} [opts.sourceLanguageHint] 源语言提示（可为空）
         * @param {string} [opts.context]          会议上下文（术语/背景，可为空）
         * @returns {Promise<string>} 译文
         */
        async translate(text, opts = {}) {
            if (!this.ready) {
                throw new Error('本地翻译配置不完整（需端点与模型名）');
            }
            const targetName = LANGUAGE_NAMES[opts.targetLanguage] || opts.targetLanguage;
            const userPayload = {
                text,
                target_language: targetName,
            };
            if (opts.sourceLanguageHint) userPayload.source_language_hint = opts.sourceLanguageHint;
            if (opts.context) userPayload.context = opts.context.slice(0, 800);

            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 40000);
            try {
                const resp = await fetch(`${this.baseUrl}/chat/completions`, {
                    method: 'POST',
                    headers: this._headers(),
                    signal: controller.signal,
                    body: JSON.stringify({
                        model: this.model,
                        temperature: 0,
                        messages: [
                            { role: 'system', content: SYSTEM_PROMPT },
                            { role: 'user', content: JSON.stringify(userPayload, null, 0) },
                        ],
                    }),
                });
                if (!resp.ok) {
                    throw new Error(`本地翻译 HTTP ${resp.status}`);
                }
                const data = await resp.json();
                const content = data?.choices?.[0]?.message?.content;
                if (typeof content !== 'string' || !content.trim()) {
                    throw new Error('本地翻译返回空内容');
                }
                // 部分本地模型爱加引号包裹，去掉首尾成对的引号。
                return content.trim().replace(/^["“](.*)["”]$/s, '$1').trim();
            } finally {
                clearTimeout(timer);
            }
        }

        /** 探测端点连通性：GET {base}/models，返回模型名列表。 */
        async testConnection() {
            const resp = await fetch(`${this.baseUrl}/models`, {
                headers: this._headers(),
            });
            if (!resp.ok) {
                throw new Error(`HTTP ${resp.status}`);
            }
            const data = await resp.json();
            return (data?.data || []).map((m) => m.id).filter(Boolean);
        }
    }

    window.LocalTranslateClient = LocalTranslateClient;
})();
