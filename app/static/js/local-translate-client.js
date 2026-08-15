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

    const TRANSLATE_SYSTEM_PROMPT = [
        '你是专业的实时会议字幕译校员。输入来自流式语音识别，可能包含错误断句、',
        '相邻片段重叠、同音错字、口头停顿和不完整短语。结合提供的上文理解当前字幕，',
        '先在内部还原连贯语义，再翻译成指定目标语言。不要逐个碎片机械直译。',
        '无论输入是哪种语言，输出语言必须且只能是指定的目标语言，严禁输出英语或源语言。',
        '不得添加输入中没有的事实，不得总结或扩写；人名、地名、数字和专有名词要保持准确。',
        '只输出最终译文，不要解释、不要原文、不要引号、不要标题或任何前后缀。',
    ].join('');

    const POLISH_SYSTEM_PROMPT = [
        '你是专业的实时语音识别字幕译校员。保持原语言不变，把流式 ASR 文本整理成自然、',
        '完整、易读的字幕。修复明显的错误断句、相邻片段重叠、重复字词、同音错字和标点，',
        '并结合上文消除歧义。不得翻译，不得总结、扩写或添加原文没有的事实；不确定时保留原意。',
        '只输出整理后的字幕，不要解释、不要标题、不要引号或任何前后缀。',
    ].join('');

    // 目标语言的输出特征校验：translate 模式下译文必须呈现目标语言特征。
    // 本地小模型偶发指令跟随失败，把原文（如日语假名句）原样复述进目标语言栏。
    // 中日共用汉字，故 zh 用「禁止假名」而不是「必须含汉字」。
    const TARGET_LANGUAGE_CHECKS = {
        zh: { forbidden: /[\u3040-\u309f\u30a0-\u30ff\u30fc]/u },
        'zh-TW': { forbidden: /[\u3040-\u309f\u30a0-\u30ff\u30fc]/u },
        ja: { required: /[\u3040-\u309f\u30a0-\u30ff]/u },
        ko: { required: /[\uac00-\ud7af]/u },
        ru: { required: /[\u0400-\u04ff]/u },
        en: { required: /[A-Za-z]/ },
        es: { required: /[A-Za-z]/ },
        fr: { required: /[A-Za-z]/ },
        de: { required: /[A-Za-z]/ },
        pt: { required: /[A-Za-z]/ },
    };

    function failsTargetLanguageCheck(text, targetLanguage) {
        const check = TARGET_LANGUAGE_CHECKS[targetLanguage];
        if (!check) return false;
        if (check.required && !check.required.test(text)) return true;
        if (check.forbidden && check.forbidden.test(text)) return true;
        return false;
    }

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

        async _httpError(resp, fallback) {
            let detail = '';
            try {
                const data = await resp.clone().json();
                detail = data?.error?.message || data?.error || data?.message || '';
            } catch (_err) {
                // 非 JSON 错误响应只显示状态码，避免把 HTML 错误页带到 UI。
            }
            const suffix = detail ? `: ${detail}` : '';
            return new Error(`${fallback} HTTP ${resp.status}${suffix}`);
        }

        /**
         * 翻译一段文本。
         * @param {string} text
         * @param {Object} opts
         * @param {string} opts.targetLanguage     目标语言码（zh/en/...）
         * @param {string} [opts.sourceLanguageHint] 源语言提示（可为空）
         * @param {string} [opts.context]          会议上下文（术语/背景，可为空）
         * @param {'translate'|'polish'} [opts.mode] 翻译或保持原语言译校
         * @returns {Promise<string>} 译文
         */
        async translate(text, opts = {}) {
            if (!this.ready) {
                throw new Error('本地翻译配置不完整（需端点与模型名）');
            }
            const targetName = LANGUAGE_NAMES[opts.targetLanguage] || opts.targetLanguage;
            const mode = opts.mode === 'polish' ? 'polish' : 'translate';
            const systemPrompt = mode === 'polish'
                ? POLISH_SYSTEM_PROMPT
                : TRANSLATE_SYSTEM_PROMPT;
            const buildUserLines = (warning) => {
                const lines = [
                    `任务：${mode === 'polish' ? '保持原语言并整理 ASR 字幕' : '翻译字幕'}`,
                    `目标语言：${targetName}`,
                ];
                if (opts.sourceLanguageHint) {
                    lines.push(`源语言提示：${opts.sourceLanguageHint}`);
                }
                if (opts.context) {
                    lines.push('仅供理解、禁止复述的上文：', opts.context.slice(0, 1200));
                }
                lines.push('需要处理的当前字幕：', text);
                if (warning) lines.push(warning);
                return lines;
            };

            // translate 模式做输出语言校验：失败带强化警告重试一次，仍失败则抛错，
            // 该条译文不上屏（原文不受影响），避免原文直通进目标语言栏。
            let warning = '';
            for (let attempt = 0; attempt < 2; attempt++) {
                const content = await this._chatCompletion(systemPrompt, buildUserLines(warning).join('\n'));
                if (mode !== 'translate' || !failsTargetLanguageCheck(content, opts.targetLanguage)) {
                    return content;
                }
                console.warn('[local-translate] 译文未呈现目标语言特征，强化重试: target=%s, output=%s...',
                    opts.targetLanguage, content.slice(0, 24));
                warning = `警告：上一次输出未使用目标语言。必须输出${targetName}，严禁复述原文或输出其他语言。`;
            }
            throw new Error(`本地翻译输出语言校验失败（目标 ${targetName}，输出疑似源语言复述）`);
        }

        /** 单次 chat/completions 请求，去除 think 区块与成对引号后返回纯文本。 */
        async _chatCompletion(systemPrompt, userContent) {
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
                            { role: 'system', content: systemPrompt },
                            { role: 'user', content: userContent },
                        ],
                    }),
                });
                if (!resp.ok) {
                    throw await this._httpError(resp, '本地翻译');
                }
                const data = await resp.json();
                const content = data?.choices?.[0]?.message?.content;
                if (typeof content !== 'string' || !content.trim()) {
                    throw new Error('本地翻译返回空内容');
                }
                return content
                    .replace(/<think>[\s\S]*?<\/think>/gi, '')
                    .trim()
                    .replace(/^["“](.*)["”]$/s, '$1')
                    .trim();
            } finally {
                clearTimeout(timer);
            }
        }

        /** 探测端点连通性：GET {base}/models，返回模型名列表。 */
        async testConnection() {
            let resp;
            try {
                resp = await fetch(`${this.baseUrl}/models`, {
                    headers: this._headers(),
                    signal: AbortSignal.timeout(10000),
                });
            } catch (error) {
                if (error?.name === 'TimeoutError' || error?.name === 'AbortError') {
                    throw new Error('连接超时（10 秒）');
                }
                throw error;
            }
            if (!resp.ok) {
                throw await this._httpError(resp, '连接测试');
            }
            const data = await resp.json();
            return (data?.data || []).map((m) => m.id).filter(Boolean);
        }
    }

    window.LocalTranslateClient = LocalTranslateClient;
})();
