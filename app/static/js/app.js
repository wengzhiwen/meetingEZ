console.log('app.js loaded, build: 20260801a');
// MeetingEZ - 基于 OpenAI Realtime API (WebRTC) 的实时转写
// API Key 由后端从环境变量读取，前端不接触

let isConnected = false;
let isRecording = false;
let mediaStream = null;
let transcripts = [];
let volumeAnimationFrame = null;
let selectedAudioDevice = null;
let selectedAudioSource = 'microphone';
let testStream = null;
let testAudioContext = null;
let isTestingMicrophone = false;
let currentStreamingTextMap = { primary: '' };
let currentTranscriptIdMap = { primary: null, secondary: null };
const STORAGE_KEY = 'meetingEZ_transcripts';
const STORAGE_VERSION = 3;
const HIDE_BEFORE_KEY = 'meetingEZ_hideBefore';
const ORIGINAL_TRANSCRIPT_VISIBLE_KEY = 'meetingEZ_originalTranscriptVisible';

let realtimeTranslationClients = [];
// 术语校正队列：realtime session（无论转写还是翻译）都注入不了术语表，所以定格后的
// 条目排队交给便宜的文本模型按术语表纠错，回来再原位替换。见 flushRefineQueue()。
let refineQueue = [];
let refineTimer = null;
let refineInFlight = 0;
let refineKeywords = [];
let betaSplitContainer = null;
let betaPaneWhisper = null;
let betaPaneLang1 = null;
let betaPaneLang2 = null;
let currentContextPack = null;
let mediaStreamExtras = null;  // 标签页+麦克风混合时的额外资源
let realtimeTranslationTargetLanguages = [];
// 按 (lang, stream_key) 维护流式译文；stream_key 可来自 item_id、response_id 或本地序号。
let realtimeTranslationLiveEntryMap = {};
let realtimePaneLiveMap = {
    primary: { original: '', translation: '' },
    secondary: { original: '', translation: '' }
};

const TRANSLATION_CONTEXT_SIZE = 10;
let translationContext = [];

let volumeAudioContext = null;
let volumeAnalyser = null;
let meetingStartedAt = null;
let meetingTimerInterval = null;
let dockResizeObserver = null;
const DEFAULT_LANGUAGE_MODE = 'single_primary';
const NO_PROJECT_ID = '__none__';
const pageLaunchContext = {
    mode: document.body?.dataset.entryMode || 'quick',
    projectId: document.body?.dataset.sessionProject || '',
    meetingDir: document.body?.dataset.sessionMeeting || '',
    meetingTitle: document.body?.dataset.sessionTitle || '',
    languageMode: document.body?.dataset.sessionLanguageMode || '',
    primaryLanguage: document.body?.dataset.sessionPrimaryLanguage || '',
    secondaryLanguage: document.body?.dataset.sessionSecondaryLanguage || ''
};
const modelInfoData = (() => {
    const el = document.getElementById('modelInfoData');
    if (!el?.textContent) return {};
    try {
        return JSON.parse(el.textContent);
    } catch (error) {
        console.warn('模型信息解析失败:', error);
        return {};
    }
})();

function getProcessingSettings() {
    return {
        enableCorrection: !!document.getElementById('enableCorrection')?.checked,
        enableGlossary: !!document.getElementById('enableGlossary')?.checked,
        glossary: document.getElementById('glossaryInput')?.value || ''
    };
}

function getLanguageMode() {
    return document.getElementById('languageMode')?.value || DEFAULT_LANGUAGE_MODE;
}

function getTranslationMode() {
    return 'realtime_beta';
}

function isOriginalTranscriptVisible() {
    return localStorage.getItem(ORIGINAL_TRANSCRIPT_VISIBLE_KEY) !== 'false';
}

function updateOriginalTranscriptVisibility() {
    const toggle = document.getElementById('toggleOriginalTranscript');
    const betaVisible = betaSplitContainer && betaSplitContainer.style.display !== 'none';
    const originalVisible = isOriginalTranscriptVisible();

    betaSplitContainer?.classList.toggle('original-transcript-hidden', !originalVisible);
    if (!toggle) return;

    toggle.hidden = !betaVisible;
    toggle.disabled = !betaVisible;
    toggle.classList.toggle('btn-primary', originalVisible);
    toggle.classList.toggle('btn-outline', !originalVisible);
    toggle.textContent = originalVisible ? '原✓' : '原';
    toggle.title = originalVisible ? '隐藏原始转写' : '显示原始转写';
    toggle.setAttribute('aria-label', toggle.title);
    toggle.setAttribute('aria-pressed', String(originalVisible));
}

function toggleOriginalTranscript() {
    localStorage.setItem(
        ORIGINAL_TRANSCRIPT_VISIBLE_KEY,
        String(!isOriginalTranscriptVisible())
    );
    updateOriginalTranscriptVisibility();
    if (document.getElementById('autoScroll')?.classList.contains('btn-primary')) {
        scrollToBottom('secondary');
    }
}

function normalizeRealtimeTranslationLanguage(value) {
    const normalized = (value || '').trim().toLowerCase().split('-')[0];
    const supported = new Set(['es', 'pt', 'fr', 'ja', 'ru', 'zh', 'de', 'ko', 'hi', 'id', 'vi', 'it', 'en']);
    return supported.has(normalized) ? normalized : '';
}

function syncRealtimeTranslationTargetLanguages() {
    const primaryLang = normalizeRealtimeTranslationLanguage(
        document.getElementById('primaryLanguage')?.value || '');
    const secondaryLang = normalizeRealtimeTranslationLanguage(
        document.getElementById('secondaryLanguage')?.value || '');
    realtimeTranslationTargetLanguages = primaryLang && secondaryLang && primaryLang !== secondaryLang
        ? [primaryLang, secondaryLang]
        : [];
    updateRealtimeTranslationSplitHeaders();
    return { primaryLang, secondaryLang };
}

function getRealtimeTranslationLanguageSelection() {
    const { primaryLang, secondaryLang } = syncRealtimeTranslationTargetLanguages();
    if (!primaryLang || !secondaryLang || primaryLang === secondaryLang) {
        throw new Error('Realtime Translation 需要选择两种不同的支持语言');
    }
    return { primaryLang, secondaryLang };
}

function getSubtitleLanguageLabel(language) {
    const normalized = normalizeRealtimeTranslationLanguage(language) || (language || '').trim().toLowerCase();
    const labels = {
        zh: 'ZH',
        ja: 'JA',
        en: 'EN',
        ko: 'KO',
        es: 'ES',
        fr: 'FR',
        de: 'DE',
        ru: 'RU',
        pt: 'PT',
        it: 'IT',
        hi: 'HI',
        id: 'ID',
        vi: 'VI'
    };
    return labels[normalized] || (normalized ? normalized.toUpperCase() : 'TEXT');
}

function getTranscriptContainerForChannel(channel = 'primary') {
    if (transcriptSplit && transcriptSplit.style.display !== 'none') {
        return channel === 'secondary' ? transcriptRight : transcriptLeft;
    }
    return transcriptContent;
}

function resetRealtimePaneLiveMap() {
    realtimePaneLiveMap = {
        primary: { original: '', translation: '' },
        secondary: { original: '', translation: '' }
    };
}

// Beta 全局单调序号：让源转写条目与两路译文条目按到达时序在共享 transcripts 中交错排列，
// 三列 pane 各自时序对齐。跨 session 不能用 item_id 配对（每路独立），改用时序软对齐。
let realtimeSegmentSeq = 0;
function nextRealtimeSegmentOrder() {
    realtimeSegmentSeq += 1;
    return realtimeSegmentSeq;
}

function clearRealtimePaneLive(role) {
    if (!role) {
        resetRealtimePaneLiveMap();
        return;
    }
    ['primary', 'secondary'].forEach((channel) => {
        realtimePaneLiveMap[channel][role] = '';
    });
}

function updateTranslationModelInfo() {
    const mode = getTranslationMode();
    const info = mode === 'realtime_beta'
        ? modelInfoData.realtime_translation
        : modelInfoData.translation;
    if (!info) return;

    const purposeEl = document.getElementById('translationModelPurpose');
    const apiEl = document.getElementById('translationModelApi');
    const nameEl = document.getElementById('translationModelName');
    const metaEl = document.getElementById('translationModelMeta');

    if (purposeEl) purposeEl.textContent = info.purpose || '';
    if (apiEl) apiEl.textContent = info.api || '';
    if (nameEl) nameEl.textContent = info.model || '';
    updateTranslationModeSettingsState();
    if (!metaEl) return;
    if (mode === 'realtime_beta') {
        metaEl.textContent = info.input_model ? `input transcription: ${info.input_model}` : '';
    } else if (info.reasoning_effort?.effort) {
        metaEl.textContent = `reasoning: ${info.reasoning_effort.effort}`;
    } else {
        metaEl.textContent = '';
    }
}

function getSelectedWorkspaceProject() {
    return document.getElementById('workspaceProject')?.value || '';
}

function isQuickModeProject(projectId) {
    return !projectId || projectId === NO_PROJECT_ID;
}

function buildEmptyContextPack() {
    return {
        projectId: NO_PROJECT_ID,
        projectName: '',
        languageMode: getLanguageMode(),
        primaryLanguage: document.getElementById('primaryLanguage')?.value || 'zh',
        secondaryLanguage: (document.getElementById('secondaryLanguage')?.value || '').trim(),
        projectSummary: '',
        backgroundSummary: '',
        confirmedTermsCount: 0,
        glossaryLines: [],
        pendingActions: [],
        recentMeetings: [],
        realtimeKeywords: [],
        realtimePrompt: ''
    };
}

function updateMeetingEntrySummary() {
    const badgeEl = document.getElementById('meetingModeBadge');
    const summaryEl = document.getElementById('meetingContextSummary');
    if (!badgeEl || !summaryEl) return;

    const projectId = getSelectedWorkspaceProject();
    const quickMode = isQuickModeProject(projectId);
    const projectName = currentContextPack?.projectName || '';
    const meetingTitle = pageLaunchContext.meetingTitle || '';

    badgeEl.textContent = quickMode ? '快速模式' : '项目模式';
    badgeEl.classList.toggle('app-badge-muted', quickMode);

    if (quickMode) {
        summaryEl.textContent = '未关联项目，可直接开始实时转写。';
        return;
    }

    const pieces = [];
    if (projectName) {
        pieces.push(`项目：${projectName}`);
    }
    if (meetingTitle) {
        pieces.push(`会议：${meetingTitle}`);
    } else if (pageLaunchContext.meetingDir) {
        pieces.push(`会议目录：${pageLaunchContext.meetingDir}`);
    }
    if (!pieces.length) {
        pieces.push('已关联项目，可加载术语和近期上下文增强。');
    }
    summaryEl.textContent = pieces.join(' · ');
}

function updateTranslationModeSettingsState() {
    const isRealtimeTranslation = getTranslationMode() === 'realtime_beta';
    const enableCorrection = document.getElementById('enableCorrection');
    const glossaryInput = document.getElementById('glossaryInput');
    const enableGlossary = document.getElementById('enableGlossary');

    if (enableCorrection) enableCorrection.disabled = isRealtimeTranslation;
    if (enableGlossary) enableGlossary.disabled = isRealtimeTranslation;
    if (glossaryInput && enableGlossary) {
        glossaryInput.disabled = isRealtimeTranslation || !enableGlossary.checked;
    }
}

function updateGlossaryInputState() {
    updateTranslationModeSettingsState();
}

function isStructuredTranscript(entry) {
    return !!entry && (
        Object.prototype.hasOwnProperty.call(entry, 'rawTranscript') ||
        Object.prototype.hasOwnProperty.call(entry, 'correctedTranscript') ||
        Object.prototype.hasOwnProperty.call(entry, 'primaryTranslation') ||
        Object.prototype.hasOwnProperty.call(entry, 'secondaryTranslation')
    );
}

function isRealtimeTranslationPaneEntry(entry) {
    return !!entry && entry.isRealtimeTranslationPaneEntry;
}

function isRealtimeTranscriptionPaneEntry(entry) {
    return !!entry && entry.isRealtimeTranscriptionPaneEntry;
}

function getDisplayTranscriptText(entry) {
    if (!entry) return '';
    if (isStructuredTranscript(entry)) {
        return (entry.correctedTranscript || entry.rawTranscript || '').trim();
    }
    return (entry.text || '').trim();
}

function rebuildTranslationContext() {
    const nextContext = [];
    transcripts.forEach((entry) => {
        if (isStructuredTranscript(entry)) {
            const sourceText = getDisplayTranscriptText(entry);
            if (sourceText) {
                nextContext.push({
                    text: sourceText,
                    language: entry.originalLanguage || entry.language || detectLanguage(sourceText),
                    timestamp: Date.parse(entry.timestamp) || Date.now()
                });
            }
            return;
        }

        if (!entry.isTranslation && entry.text) {
            nextContext.push({
                text: entry.text.trim(),
                language: entry.language || detectLanguage(entry.text),
                timestamp: Date.parse(entry.timestamp) || Date.now()
            });
        }
    });
    translationContext = nextContext.slice(-TRANSLATION_CONTEXT_SIZE);
}

function normalizeStoredTranscriptEntry(entry) {
    if (!entry || typeof entry !== 'object') return null;
    if (isRealtimeTranscriptionPaneEntry(entry)) {
        return {
            id: entry.id || Date.now() + Math.random(),
            timestamp: entry.timestamp || new Date().toISOString(),
            channel: entry.channel || 'primary',
            paneRole: 'original',
            language: entry.language || entry.originalLanguage || '',
            text: entry.text || entry.rawTranscript || '',
            rawTranscript: entry.rawTranscript || entry.text || '',
            isRealtimeTranscriptionPaneEntry: true
        };
    }
    if (isRealtimeTranslationPaneEntry(entry)) {
        return {
            id: entry.id || Date.now() + Math.random(),
            timestamp: entry.timestamp || new Date().toISOString(),
            channel: entry.channel || 'primary',
            paneRole: 'translation',
            language: entry.language || '',
            text: entry.text || '',
            isTranslation: true,
            isRealtimeTranslationPaneEntry: true,
            realtimeOrder: Number.isFinite(entry.realtimeOrder) ? entry.realtimeOrder : null
        };
    }
    if (isStructuredTranscript(entry)) {
        return {
            id: entry.id || Date.now() + Math.random(),
            timestamp: entry.timestamp || new Date().toISOString(),
            channel: entry.channel || 'primary',
            originalLanguage: entry.originalLanguage || entry.language || '',
            rawTranscript: entry.rawTranscript || '',
            correctedTranscript: entry.correctedTranscript || null,
            correctionApplied: !!entry.correctionApplied,
            primaryTranslation: entry.primaryTranslation || null,
            secondaryTranslation: entry.secondaryTranslation || null,
            postProcessing: !!entry.postProcessing,
            pendingCorrection: !!entry.pendingCorrection,
            pendingTranslation: !!entry.pendingTranslation,
            realtimeOrder: Number.isFinite(entry.realtimeOrder) ? entry.realtimeOrder : null,
            confidence: Number.isFinite(entry.confidence) ? entry.confidence : null,
            lowConfidence: !!entry.lowConfidence
        };
    }
    return { ...entry, channel: entry.channel || 'primary' };
}

function getRealtimeErrorMessage(error) {
    const code = error?.code || 'unknown';
    const messages = {
        permission_denied: '麦克风权限被拒绝，请在浏览器地址栏重新授权。',
        insecure_context: '当前页面需要 HTTPS 或 localhost 才能使用实时音频。',
        device_unavailable: '找不到可用麦克风，或设备正被其他应用占用。',
        unsupported_browser: '当前浏览器不支持实时转写所需的 WebRTC 能力。',
        auth_error: 'Realtime 鉴权失败，请检查 OPENAI_API_KEY 或登录状态。',
        network_error: 'Realtime 网络连接失败，请检查网络后重试。',
        timeout: 'Realtime 连接超时，请稍后重试。'
    };
    return messages[code] || error?.message || 'Realtime 连接异常';
}

function insertTranscriptInRealtimeOrder(entry) {
    transcripts.push(entry);
    transcripts.sort((a, b) => {
        const left = Number.isFinite(a.realtimeOrder) ? a.realtimeOrder : Number.MAX_SAFE_INTEGER;
        const right = Number.isFinite(b.realtimeOrder) ? b.realtimeOrder : Number.MAX_SAFE_INTEGER;
        if (left !== right) return left - right;
        return String(a.timestamp || '').localeCompare(String(b.timestamp || ''));
    });
}

function splitTranscriptSentences(text, { group = true } = {}) {
    const normalized = normalizeText(text);
    if (!normalized) return [];

    const sentences = [];
    let start = 0;
    const boundaryChars = new Set(['。', '！', '？', '.', '!', '?', '；', ';', '\n']);
    for (let i = 0; i < normalized.length; i++) {
        if (!boundaryChars.has(normalized[i])) continue;

        let end = i + 1;
        while (end < normalized.length && /[“’”’」』）\]\s]/u.test(normalized[end])) {
            end++;
        }

        const segment = normalized.slice(start, end).trim();
        if (segment) sentences.push(segment);
        start = end;
    }

    const tail = normalized.slice(start).trim();
    if (tail) sentences.push(tail);
    const roughSegments = (sentences.length ? sentences : [normalized]).flatMap(splitLongTranscriptSegment);
    return group ? groupSubtitleSegments(roughSegments) : roughSegments;
}

function splitLongTranscriptSegment(segment) {
    const maxChars = 110;
    if (segment.length <= maxChars) return [segment];

    const parts = [];
    let rest = segment.trim();
    while (rest.length > maxChars) {
        const windowText = rest.slice(0, maxChars);
        const softBreak = Math.max(
            windowText.lastIndexOf('，'),
            windowText.lastIndexOf(','),
            windowText.lastIndexOf('、'),
            windowText.lastIndexOf(' ')
        );
        const end = softBreak > 40 ? softBreak + 1 : maxChars;
        parts.push(rest.slice(0, end).trim());
        rest = rest.slice(end).trim();
    }
    if (rest) parts.push(rest);
    return parts.filter(Boolean);
}

function groupSubtitleSegments(segments) {
    const grouped = [];
    let current = [];
    const maxSentences = 3;
    const maxChars = 150;

    segments.forEach((segment) => {
        const normalized = (segment || '').trim();
        if (!normalized) return;
        const nextText = [...current, normalized].join(' ');
        if (current.length > 0 && (current.length >= maxSentences || nextText.length > maxChars)) {
            grouped.push(current.join(' '));
            current = [];
        }
        current.push(normalized);
    });

    if (current.length) grouped.push(current.join(' '));
    return grouped;
}

function buildTranscriptEntryFromSegment(segment, itemId, realtimeItem, index = 0, overrides = {}) {
    const baseOrder = Number.isFinite(realtimeItem.order) ? realtimeItem.order : null;
    const language = overrides.language || detectLanguage(segment);
    const channel = overrides.channel || 'primary';
    return {
        id: index === 0 ? (itemId || Date.now() + Math.random()) : `${itemId || Date.now()}-${index + 1}`,
        timestamp: new Date().toISOString(),
        channel,
        paneRole: overrides.paneRole || null,
        language,
        originalLanguage: language,
        rawTranscript: segment,
        text: overrides.text || segment,
        correctedTranscript: null,
        correctionApplied: false,
        primaryTranslation: null,
        secondaryTranslation: null,
        postProcessing: false,
        pendingCorrection: false,
        pendingTranslation: false,
        isRealtimeTranscriptionPaneEntry: !!overrides.isRealtimeTranscriptionPaneEntry,
        realtimeOrder: Number.isFinite(baseOrder) ? baseOrder + index / 1000 : null,
        confidence: Number.isFinite(realtimeItem.confidence) ? realtimeItem.confidence : null,
        lowConfidence: !!realtimeItem.lowConfidence
    };
}

function runPostProcessingForTranscript(entry, languageMode) {
    const primaryLang = document.getElementById('primaryLanguage')?.value || 'zh';
    const secondaryLang = (document.getElementById('secondaryLanguage')?.value || '').trim();
    const processingSettings = getProcessingSettings();
    const shouldTranslate = !!secondaryLang;
    if (!processingSettings.enableCorrection && !shouldTranslate) return;

    entry.postProcessing = true;
    entry.pendingCorrection = processingSettings.enableCorrection;
    entry.pendingTranslation = shouldTranslate;
    saveTranscripts();
    updateDisplay(entry.channel || 'primary');

    postProcessText(entry.rawTranscript, {
        primaryLanguage: primaryLang,
        secondaryLanguage: shouldTranslate ? secondaryLang : '',
        languageMode,
        originalLanguageHint: entry.originalLanguage,
        enableCorrection: processingSettings.enableCorrection,
        enableGlossary: processingSettings.enableGlossary,
        glossary: processingSettings.glossary
    }).then((structured) => {
        applyPostProcessToTranscript(entry.id, structured);
    }).catch((error) => {
        entry.postProcessing = false;
        entry.pendingCorrection = false;
        entry.pendingTranslation = false;
        saveTranscripts();
        updateDisplay(entry.channel || 'primary');
        console.warn('后置处理失败，保留原文:', error);
    });
}

function addToTranslationContext(text, language) {
    if (!text || !text.trim()) return;
    translationContext.push({ text: text.trim(), language: language || 'unknown', timestamp: Date.now() });
    if (translationContext.length > TRANSLATION_CONTEXT_SIZE) {
        translationContext = translationContext.slice(-TRANSLATION_CONTEXT_SIZE);
    }
}

function clearTranslationContext() {
    translationContext = [];
}

const meetingActionBtn = document.getElementById('meetingAction');
const statusDiv = document.getElementById('connectionStatus');
const transcriptContent = document.getElementById('transcriptContent');
let transcriptSplit = null;
let transcriptLeft = null;
let transcriptRight = null;
let meetingStatusText = '未开始';

function openSettingsPanel() {
    const panel = document.getElementById('settingsPanel');
    const backdrop = document.getElementById('settingsBackdrop');
    if (!panel || !backdrop) return;
    panel.classList.remove('hidden');
    backdrop.classList.remove('hidden');
    panel.setAttribute('aria-hidden', 'false');
}

function closeSettingsPanel() {
    const panel = document.getElementById('settingsPanel');
    const backdrop = document.getElementById('settingsBackdrop');
    if (!panel || !backdrop) return;
    panel.classList.add('hidden');
    backdrop.classList.add('hidden');
    panel.setAttribute('aria-hidden', 'true');
}

function updateMeetingTimer() {
    const timerEl = document.getElementById('meetingTimer');
    if (!timerEl) return;
    if (!meetingStartedAt || !isConnected) {
        timerEl.textContent = meetingStatusText;
        return;
    }

    const elapsedSeconds = Math.max(0, Math.floor((Date.now() - meetingStartedAt) / 1000));
    const hours = Math.floor(elapsedSeconds / 3600);
    const minutes = Math.floor((elapsedSeconds % 3600) / 60);
    const seconds = elapsedSeconds % 60;
    timerEl.textContent = hours > 0
        ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
        : `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function startMeetingTimer() {
    stopMeetingTimer();
    updateMeetingTimer();
    meetingTimerInterval = setInterval(updateMeetingTimer, 1000);
}

function stopMeetingTimer() {
    if (meetingTimerInterval) {
        clearInterval(meetingTimerInterval);
        meetingTimerInterval = null;
    }
    updateMeetingTimer();
}

function updateFloatingDockLayout() {
    const dock = document.getElementById('floatingDock');
    if (!dock) return;
    const dockHeight = Math.ceil(dock.getBoundingClientRect().height);
    document.documentElement.style.setProperty('--floating-dock-height', `${dockHeight}px`);
}

function initializeFloatingDockLayout() {
    updateFloatingDockLayout();
    window.addEventListener('resize', updateFloatingDockLayout);

    const dock = document.getElementById('floatingDock');
    if (!dock || typeof ResizeObserver !== 'function') return;

    dockResizeObserver = new ResizeObserver(() => {
        updateFloatingDockLayout();
    });
    dockResizeObserver.observe(dock);
}

// 初始化
async function init() {
    initializeFloatingDockLayout();
    loadSettings();
    setupEventListeners();
    await loadAudioDevices();
    await loadWorkspaceContextPack({ silent: true });
    updateControls();
    initializeAutoScroll();
}

function loadSettings() {
    const savedAudioSource = localStorage.getItem('meetingEZ_audioSource') || 'microphone';
    selectedAudioSource = savedAudioSource;
    const audioSourceMic = document.getElementById('audioSourceMic');
    const audioSourceTab = document.getElementById('audioSourceTab');
    if (savedAudioSource === 'tab') {
        audioSourceTab.checked = true;
    } else {
        audioSourceMic.checked = true;
    }
    const tabPlusMicEl = document.getElementById('tabPlusMic');
    if (tabPlusMicEl) {
        tabPlusMicEl.checked = localStorage.getItem('meetingEZ_tabPlusMic') === 'true';
    }
    updateAudioInputVisibility();

    const primaryLang = pageLaunchContext.primaryLanguage || localStorage.getItem('meetingEZ_primaryLanguage');
    if (primaryLang) {
        document.getElementById('primaryLanguage').value = primaryLang;
    }

    const selectedPrimaryLang = document.getElementById('primaryLanguage').value;
    const defaultSecondaryLang = selectedPrimaryLang === 'en' ? 'zh' : 'en';
    const secondaryLang = pageLaunchContext.secondaryLanguage ||
        localStorage.getItem('meetingEZ_secondaryLanguage') || defaultSecondaryLang;
    const secSelect = document.getElementById('secondaryLanguage');
    if (secSelect) secSelect.value = secondaryLang;
    syncRealtimeTranslationTargetLanguages();

    const savedLanguageMode = pageLaunchContext.languageMode || localStorage.getItem('meetingEZ_languageMode') ||
        (secondaryLang ? 'bilingual' : DEFAULT_LANGUAGE_MODE);
    const languageModeSelect = document.getElementById('languageMode');
    if (languageModeSelect) languageModeSelect.value = savedLanguageMode;

    const savedWorkspaceProject = pageLaunchContext.projectId || localStorage.getItem('meetingEZ_workspaceProject');
    const workspaceProject = document.getElementById('workspaceProject');
    if (workspaceProject) {
        const hasSavedOption = savedWorkspaceProject && Array.from(workspaceProject.options).some((option) => option.value === savedWorkspaceProject);
        if (hasSavedOption) {
            workspaceProject.value = savedWorkspaceProject;
        } else if (workspaceProject.options.length > 0) {
            workspaceProject.value = workspaceProject.options[0].value;
        }
    }

    updateTranslationModelInfo();

    // Realtime Translation 是唯一模式，页面加载后始终使用三栏视图；
    // 即使会议尚未开始，也要显示两个目标语言空面板和原文显隐按钮。
    enableSplitView(true);
    updateMeetingEntrySummary();
}

function setupEventListeners() {
    const testConnBtn = document.getElementById('testConnection');
    if (testConnBtn) testConnBtn.addEventListener('click', testConnection);
    document.getElementById('settingsToggle').addEventListener('click', openSettingsPanel);
    document.getElementById('closeSettings').addEventListener('click', closeSettingsPanel);
    document.getElementById('settingsBackdrop').addEventListener('click', closeSettingsPanel);

    meetingActionBtn.addEventListener('click', async () => {
        if (isConnected) {
            await stopMeeting();
            return;
        }
        await startMeeting();
    });

    document.getElementById('downloadTranscript').addEventListener('click', downloadTranscript);
    document.getElementById('clearTranscript').addEventListener('click', clearTranscript);
    document.getElementById('autoScroll').addEventListener('click', toggleAutoScroll);
    document.getElementById('toggleOriginalTranscript')?.addEventListener('click', toggleOriginalTranscript);
    document.getElementById('testMicrophone').addEventListener('click', toggleMicrophoneTest);

    document.getElementById('audioSourceMic').addEventListener('change', () => {
        selectedAudioSource = 'microphone';
        localStorage.setItem('meetingEZ_audioSource', 'microphone');
        updateAudioInputVisibility();
    });

    document.getElementById('audioSourceTab').addEventListener('change', () => {
        selectedAudioSource = 'tab';
        localStorage.setItem('meetingEZ_audioSource', 'tab');
        updateAudioInputVisibility();
    });

    document.getElementById('tabPlusMic')?.addEventListener('change', (e) => {
        localStorage.setItem('meetingEZ_tabPlusMic', String(e.target.checked));
    });

    if (navigator.mediaDevices) {
        if (typeof navigator.mediaDevices.addEventListener === 'function') {
            navigator.mediaDevices.addEventListener('devicechange', async () => {
                await loadAudioDevices();
            });
        } else if ('ondevicechange' in navigator.mediaDevices) {
            navigator.mediaDevices.ondevicechange = async () => {
                await loadAudioDevices();
            };
        }
    }

    document.getElementById('primaryLanguage').addEventListener('change', (e) => {
        localStorage.setItem('meetingEZ_primaryLanguage', e.target.value);
        syncRealtimeTranslationTargetLanguages();
        void loadWorkspaceContextPack({ silent: true });
    });

    const secSelect = document.getElementById('secondaryLanguage');
    if (secSelect) {
        secSelect.addEventListener('change', (e) => {
            localStorage.setItem('meetingEZ_secondaryLanguage', (e.target.value || '').trim());
            syncRealtimeTranslationTargetLanguages();
            void loadWorkspaceContextPack({ silent: true });
        });
    }

    document.getElementById('languageMode').addEventListener('change', (e) => {
        localStorage.setItem('meetingEZ_languageMode', e.target.value);
        void loadWorkspaceContextPack({ silent: true });
    });

    document.getElementById('workspaceProject').addEventListener('change', (e) => {
        localStorage.setItem('meetingEZ_workspaceProject', e.target.value);
        void loadWorkspaceContextPack({ silent: false });
    });

    document.getElementById('fontSize').addEventListener('change', (e) => {
        localStorage.setItem('meetingEZ_fontSize', e.target.value);
        updateFontSize();
    });

    document.querySelector('.close').addEventListener('click', () => {
        document.getElementById('errorModal').style.display = 'none';
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            closeSettingsPanel();
            document.getElementById('errorModal').style.display = 'none';
        }
    });
}

// 测试连接（通过后端代理）
async function testConnection() {
    const testConnBtn = document.getElementById('testConnection');
    testConnBtn.disabled = true;
    showStatus('正在测试连接...', 'info');

    try {
        const resp = await fetch('/api/test-connection', { method: 'POST' });
        if (resp.status === 401) { window.location.href = '/login'; return; }
        const data = await resp.json();
        if (resp.ok) {
            showStatus('连接成功！', 'success');
        } else {
            showStatus('连接失败: ' + (data.error || resp.statusText), 'error');
        }
    } catch (error) {
        showStatus('连接失败: ' + error.message, 'error');
    } finally {
        testConnBtn.disabled = false;
    }
}

// 开始会议
async function startMeeting() {
    if (isTestingMicrophone) {
        stopMicrophoneTest();
        const testMicBtn = document.getElementById('testMicrophone');
        testMicBtn.textContent = '测';
        testMicBtn.classList.remove('btn-danger');
        testMicBtn.classList.add('btn-outline');
    }

    try {
        showLoading('正在初始化会议...');

        // 先验证两个目标语言再申请音频权限，避免没有可用 session 却进入假成功状态。
        const languageSelection = getRealtimeTranslationLanguageSelection();

        if (selectedAudioSource === 'tab') {
            try {
                const displayStream = await navigator.mediaDevices.getDisplayMedia({
                    video: true,
                    audio: true
                });

                const tabAudioTrack = displayStream.getAudioTracks()[0];
                if (!tabAudioTrack) {
                    displayStream.getTracks().forEach(track => track.stop());
                    throw new Error('未能获取标签页音频。请确保选择了"Chrome 标签页"并勾选了"共享标签页音频"。');
                }

                displayStream.getVideoTracks().forEach(track => track.stop());

                const wantMic = !!document.getElementById('tabPlusMic')?.checked;
                if (wantMic) {
                    const micStream = await navigator.mediaDevices.getUserMedia({
                        audio: buildAudioConstraints({ echoCancellation: false })
                    });

                    const mixCtx = new AudioContext({ sampleRate: 24000 });
                    const tabSource = mixCtx.createMediaStreamSource(new MediaStream([tabAudioTrack]));
                    const micSource = mixCtx.createMediaStreamSource(micStream);
                    const dest = mixCtx.createMediaStreamDestination();
                    tabSource.connect(dest);
                    micSource.connect(dest);
                    if (mixCtx.state === 'suspended') {
                        await mixCtx.resume();
                    }

                    mediaStreamExtras = {
                        mixCtx,
                        micStream,
                        tabAudioTrack,
                        tabSource,
                        micSource,
                        mixDestination: dest
                    };
                    mediaStream = dest.stream;
                } else {
                    mediaStream = new MediaStream([tabAudioTrack]);
                }
            } catch (error) {
                if (error.name === 'NotAllowedError') {
                    throw new Error('用户取消了标签页共享。');
                }
                throw error;
            }
        } else {
            mediaStream = await navigator.mediaDevices.getUserMedia({ audio: buildAudioConstraints() });
        }

        startVolumeMonitor(mediaStream);
        meetingStartedAt = Date.now();
        await loadWorkspaceContextPack({ silent: true });
        resetRefineQueue();
        // 只开 2 路翻译 session：第 0 路兼任权威源转写来源（session.input_transcript
        // 自带源语言转写且自动检测语言）。术语表由定格后的文本校正兜底，见 refineQueue。
        await initRealtimeTranslationConnections(languageSelection);

        isConnected = true;
        isRecording = true;
        startMeetingTimer();
        updateControls();
        updateMeetingStatus('进行中', 'active');
        updateAudioStatus('已连接', 'active');
        disableSettings();
        hideLoading();
        showStatus('会议已开始', 'success');

        enableSplitView(true);
    } catch (error) {
        console.error('开始会议失败:', error);
        hideLoading();
        showError('开始会议失败: ' + error.message);
        await stopMeeting({ showStoppedMessage: false, showLoadingOverlay: false });
    }
}

async function initRealtimeTranslationConnections(languageSelection = null) {
    const RealtimeTranslationClass = window.RealtimeTranslation;
    if (typeof RealtimeTranslationClass !== 'function') {
        throw new Error('RealtimeTranslation 未正确加载');
    }

    const { primaryLang, secondaryLang } = languageSelection ||
        getRealtimeTranslationLanguageSelection();

    realtimeTranslationClients = [];
    realtimeTranslationLiveEntryMap = {};
    resetRealtimePaneLiveMap();
    currentStreamingTextMap.primary = '';
    currentStreamingTextMap.secondary = '';
    realtimeTranslationTargetLanguages = [primaryLang, secondaryLang];
    updateRealtimeTranslationSplitHeaders();
    enableSplitView(true);
    updateStreamingDisplay('primary');
    updateStreamingDisplay('secondary');
    const targets = realtimeTranslationTargetLanguages.map((language) => ({
        language,
        label: `译为 ${language}`
    }));

    const connectTasks = targets.map(async (target, clientIndex) => {
        // 第 0 路（目标=第一语言）兼任权威源转写来源：其 session.input_transcript 自带
        // 源语言转写且自动检测语言（处理中日语码切换），喂给原文 pane，省去额外转写 session。
        const isSourceAuthority = clientIndex === 0;
        const client = new RealtimeTranslationClass({
            targetLanguage: target.language,
            label: target.label,
            onConnected: () => {
                console.log(`Realtime Translation connected: ${target.language}`);
                renderRealtimeTranslationBeta();
            },
            onDisconnected: () => {
                console.log(`Realtime Translation disconnected: ${target.language}`);
            },
            onInputDelta: (delta, client, itemId, liveText) => {
                if (!isSourceAuthority || !delta) return;
                realtimePaneLiveMap.primary.original = liveText || '';
                scheduleDisplayUpdate('primary');
            },
            onInputDone: (payload) => {
                if (!isSourceAuthority) return;
                commitRealtimeInputTranscript(payload);
            },
            onOutputDelta: (delta, client, itemId, liveText) => {
                if (!delta) return;
                upsertRealtimeTranslationLiveEntry(target.language, itemId, liveText);
                scheduleDisplayUpdate('secondary');
            },
            onOutputDone: ({ itemId, transcript } = {}) => {
                finalizeRealtimeTranslationLiveEntry(target.language, itemId, transcript);
            },
            onError: (error) => {
                console.error(`Realtime Translation error [${target.language}]:`, error);
                showStatus(`Realtime Translation 异常: ${error.message || error}`, 'error');
            }
        });
        realtimeTranslationClients.push(client);
        await client.connect(mediaStream);
    });

    const results = await Promise.allSettled(connectTasks);
    const failures = results.filter(result => result.status === 'rejected');
    if (results[0]?.status === 'rejected') {
        // 第 0 路兼任原文来源，它连不上就没有原文了。
        throw new Error(`原文转写会话连接失败: ${results[0].reason?.message || results[0].reason}`);
    }
    if (failures.length) {
        console.warn('Realtime Translation 部分连接失败:', failures);
        if (failures.length === targets.length) {
            throw new Error('Realtime Translation 全部连接失败');
        }
        showStatus(`Realtime Translation ${failures.length} 路连接失败，其余翻译继续`, 'error');
    } else {
        showStatus('Realtime Translation 已连接', 'success');
    }
}

function renderRealtimeTranslationBeta() {
    scheduleDisplayUpdate('secondary');
}

function buildRealtimePaneLiveLines(channel = 'primary', role = 'original') {
    const text = realtimePaneLiveMap[channel]?.[role] || '';
    if (!text.trim()) return [];
    const language = role === 'translation'
        ? (realtimeTranslationTargetLanguages[0] || '')
        : detectLanguage(text);
    return [{
        language,
        label: getSubtitleLanguageLabel(language),
        text
    }];
}

function createRealtimeTranslationPaneEntry(targetLang, text) {
    return {
        id: `rt-translate-${targetLang}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        timestamp: new Date().toISOString(),
        channel: 'secondary',
        paneRole: 'translation',
        language: targetLang,
        text,
        isTranslation: true,
        isRealtimeTranslationPaneEntry: true,
        realtimeOrder: nextRealtimeSegmentOrder()
    };
}

// live 阶段：同一 (lang, item_id) 复用一条 pane 条目，原位更新 text，靠 rAF 重绘实现逐字流式。
function upsertRealtimeTranslationLiveEntry(targetLang, itemId, liveText) {
    if (!liveText) return;
    const entryKey = itemId || '__active__';
    realtimeTranslationLiveEntryMap[targetLang] = realtimeTranslationLiveEntryMap[targetLang] || {};
    let liveEntry = realtimeTranslationLiveEntryMap[targetLang][entryKey];
    if (!liveEntry) {
        liveEntry = createRealtimeTranslationPaneEntry(targetLang, '');
        liveEntry._liveItemId = entryKey;
        realtimeTranslationLiveEntryMap[targetLang][entryKey] = liveEntry;
        insertTranscriptInRealtimeOrder(liveEntry);
    }
    liveEntry.text = liveText;
}

// done 阶段：把对应 live 条目定格为最终整句；若无 live 条目（模型整句替换/补发）则新建提交。
function finalizeRealtimeTranslationLiveEntry(targetLang, itemId, transcript) {
    const finalText = (transcript && transcript.trim()) ? transcript.trim() : '';
    const entryKey = itemId || '__active__';
    realtimeTranslationLiveEntryMap[targetLang] = realtimeTranslationLiveEntryMap[targetLang] || {};
    const liveEntry = realtimeTranslationLiveEntryMap[targetLang][entryKey] || null;
    if (liveEntry) {
        if (finalText) liveEntry.text = finalText;
        delete liveEntry._liveItemId;
        delete realtimeTranslationLiveEntryMap[targetLang][entryKey];
        enqueueRefine(liveEntry, liveEntry.text);
    } else if (finalText) {
        const entry = createRealtimeTranslationPaneEntry(targetLang, finalText);
        insertTranscriptInRealtimeOrder(entry);
        enqueueRefine(entry, finalText);
    }
    saveTranscripts();
    updateControls();
    scheduleDisplayUpdate('secondary');
    if (document.getElementById('autoScroll')?.classList.contains('btn-primary')) {
        scrollToBottom('secondary');
    }
}

// 停止前兜底：把所有未 done 的 live 条目用当前 text 定格，避免丢失最后半句。
function finalizeAllRealtimeLiveEntries() {
    Object.keys(realtimeTranslationLiveEntryMap || {}).forEach((targetLang) => {
        const itemMap = realtimeTranslationLiveEntryMap[targetLang] || {};
        Object.keys(itemMap).forEach((itemId) => {
            const entry = itemMap[itemId];
            if (entry) {
                delete entry._liveItemId;
                // 这些条目不会再走 finalizeRealtimeTranslationLiveEntry，在这里补入队。
                enqueueRefine(entry, entry.text);
            }
            delete itemMap[itemId];
        });
    });
    realtimeTranslationLiveEntryMap = {};
}

function updateRealtimeTranslationSplitHeaders() {
    if (transcriptLeft) transcriptLeft.dataset.languageLabel = 'WHISPER';
    if (transcriptRight) transcriptRight.dataset.languageLabel = 'TRANSLATE';
    if (betaPaneWhisper) betaPaneWhisper.dataset.languageLabel = 'WHISPER';
    if (betaPaneLang1) {
        const l1 = realtimeTranslationTargetLanguages[0];
        betaPaneLang1.dataset.languageLabel = l1 ? getSubtitleLanguageLabel(l1) : 'LANG 1';
    }
    if (betaPaneLang2) {
        const l2 = realtimeTranslationTargetLanguages[1];
        betaPaneLang2.dataset.languageLabel = l2 ? getSubtitleLanguageLabel(l2) : 'LANG 2';
    }
}

function commitRealtimeInputTranscript({ itemId, transcript } = {}) {
    // 权威源转写完成：把最终文本切分提交为 original 条目（Whisper pane），并清空 live 文本。
    // 文本来源优先用完成事件的 transcript，缺失时回退到已累积的 live 文本。
    const finalText = (transcript && transcript.trim())
        ? transcript.trim()
        : (realtimePaneLiveMap.primary.original || '').trim();
    realtimePaneLiveMap.primary.original = '';
    scheduleDisplayUpdate('primary');
    if (!finalText) return;
    if (isHallucinationText(finalText)) return;

    const baseOrder = nextRealtimeSegmentOrder();
    const segments = splitTranscriptSentences(finalText, { group: true });
    segments.forEach((segment, index) => {
        const entry = buildTranscriptEntryFromSegment(
            segment,
            `rt-input-${itemId || `${Date.now()}-${index}`}`,
            { order: baseOrder },
            index,
            {
                language: detectLanguage(segment),
                channel: 'primary',
                paneRole: 'original',
                isRealtimeTranscriptionPaneEntry: true
            }
        );
        insertTranscriptInRealtimeOrder(entry);
        enqueueRefine(entry, segment);
    });
    saveTranscripts();
    rebuildTranslationContext();
    scheduleDisplayUpdate('primary');
    if (document.getElementById('autoScroll')?.classList.contains('btn-primary')) {
        scrollToBottom('primary');
    }
}

function commitActiveStreamingTranscriptBeforeStop() {
    const text = (currentStreamingTextMap.primary || '').trim();
    if (!text || text === '正在识别...' || isHallucinationText(text)) return;
    const itemId = currentTranscriptIdMap.primary || `streaming-${Date.now()}`;
    splitTranscriptSentences(text).forEach((segment, index) => {
        insertTranscriptInRealtimeOrder(
            buildTranscriptEntryFromSegment(segment, itemId, {}, index));
    });
    saveTranscripts();
    rebuildTranslationContext();
}

function commitRealtimePaneLiveBeforeStop() {
    ['primary', 'secondary'].forEach((channel) => {
        const originalText = (realtimePaneLiveMap[channel]?.original || '').trim();
        if (originalText && !isHallucinationText(originalText)) {
            const language = detectLanguage(originalText);
            splitTranscriptSentences(originalText).forEach((segment, index) => {
                const entry = buildTranscriptEntryFromSegment(
                    segment,
                    `streaming-original-${channel}-${Date.now()}`,
                    {},
                    index,
                    {
                        language,
                        channel,
                        paneRole: 'original',
                        isRealtimeTranscriptionPaneEntry: true
                    }
                );
                insertTranscriptInRealtimeOrder(entry);
                enqueueRefine(entry, segment);
            });
        }
    });
    saveTranscripts();
    rebuildTranslationContext();
    resetRealtimePaneLiveMap();
}

// 停止会议
async function stopMeeting(options = {}) {
    const showStoppedMessage = options.showStoppedMessage !== false;
    const showLoadingOverlay = options.showLoadingOverlay !== false;

    try {
        if (showLoadingOverlay) {
            showLoading('正在结束会议...');
        }

        isRecording = false;
        // 先定格所有未完成的译文 live 条目，再提交原文 live，避免丢失最后半句。
        finalizeAllRealtimeLiveEntries();
        commitRealtimePaneLiveBeforeStop();

        // 排干校正队列。不 await：结束会议不该卡在网络上，结果回来时条目还在，
        // applyRefineResults 照样能原位替换并重绘。
        void flushRefineQueue({ force: true });

        realtimeTranslationClients.forEach(client => client.disconnect());
        realtimeTranslationClients = [];

        stopVolumeMonitor();

        if (mediaStream) {
            mediaStream.getTracks().forEach(track => track.stop());
            mediaStream = null;
        }

        if (mediaStreamExtras) {
            if (mediaStreamExtras.micStream) {
                mediaStreamExtras.micStream.getTracks().forEach(track => track.stop());
            }
            if (mediaStreamExtras.tabAudioTrack) {
                try { mediaStreamExtras.tabAudioTrack.stop(); } catch (e) { console.warn('停止标签页音轨失败:', e); }
            }
            if (mediaStreamExtras.mixCtx) {
                try { mediaStreamExtras.mixCtx.close(); } catch (e) { console.warn('关闭混音 AudioContext 失败:', e); }
            }
            mediaStreamExtras = null;
        }

        clearTranslationContext();

        isConnected = false;
        meetingStartedAt = null;
        stopMeetingTimer();
        currentStreamingTextMap.primary = '';
        currentStreamingTextMap.secondary = '';
        currentTranscriptIdMap.primary = null;
        currentTranscriptIdMap.secondary = null;
        realtimeTranslationLiveEntryMap = {};
        resetRealtimePaneLiveMap();
        updateDisplay('primary');
        updateDisplay('secondary');
        updateControls();
        updateMeetingStatus('已结束', '');
        updateAudioStatus('未连接', '');
        enableSettings();

        hideLoading();
        if (showStoppedMessage) {
            showStatus('会议已结束', 'info');
        }
    } catch (error) {
        console.error('停止会议失败:', error);
        hideLoading();
        showError('停止会议失败: ' + error.message);
    }

    await autoSaveTranscriptsToProject();
}

// ---- 自动保存转写结果到项目 ----

async function autoSaveTranscriptsToProject() {
    const { projectId, meetingDir } = pageLaunchContext;
    if (!projectId || !meetingDir || isQuickModeProject(projectId)) return;
    if (transcripts.length === 0) return;

    const filename = 'realtime_transcript.json';
    const url = `/api/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(meetingDir)}/files/${encodeURIComponent(filename)}`;
    try {
        const resp = await fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                content: JSON.stringify({ version: STORAGE_VERSION, items: transcripts })
            })
        });
        if (resp.ok) {
            console.log(`转写结果已自动保存到项目: ${filename}`);
        } else {
            console.warn('自动保存转写结果失败:', resp.status);
        }
    } catch (err) {
        console.warn('自动保存转写结果异常:', err);
    }
}

// ---- 音量监测 ----

function buildAudioConstraints({ echoCancellation = false } = {}) {
    const c = { echoCancellation, noiseSuppression: false, autoGainControl: false, channelCount: 1 };
    if (selectedAudioDevice) c.deviceId = { exact: selectedAudioDevice };
    return c;
}

function startVolumeMonitor(stream) {
    try {
        volumeAudioContext = new AudioContext();
        const source = volumeAudioContext.createMediaStreamSource(stream);
        volumeAnalyser = volumeAudioContext.createAnalyser();
        volumeAnalyser.fftSize = 256;
        source.connect(volumeAnalyser);

        const dataArray = new Uint8Array(volumeAnalyser.frequencyBinCount);

        function tick() {
            if (!volumeAnalyser) return;
            volumeAnalyser.getByteFrequencyData(dataArray);
            let sum = 0;
            for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
            const volume = (sum / dataArray.length) / 255;
            updateVolumeIndicator(volume);
            volumeAnimationFrame = requestAnimationFrame(tick);
        }
        tick();
    } catch (e) {
        console.warn('音量监测初始化失败:', e);
    }
}

function stopVolumeMonitor() {
    if (volumeAnimationFrame) {
        cancelAnimationFrame(volumeAnimationFrame);
        volumeAnimationFrame = null;
    }
    if (volumeAudioContext) {
        volumeAudioContext.close();
        volumeAudioContext = null;
    }
    volumeAnalyser = null;
    updateVolumeIndicator(0);
}

// ---- 幻觉检测 ----

function isHallucinationText(text) {
    const hallucinationPatterns = [
        /^(hi|hello|hey|welcome).*(channel|video|subscribe|youtube|like|comment)/i,
        /^thanks?\s+for\s+(watching|listening|subscribing)/i,
        /^(please|don't forget to).*(subscribe|like|comment|share)/i,
        /字幕|subtitle|caption|transcript/i,
        /^(\s*[a-z]\s*){8,}$/i,
        /^([a-z]-){4,}/i,
        /^[\s\-\.]{8,}$/,
        /^[aeiou]{10,}$/i,
        /^(.)\1{8,}$/,
        /^(.{2})\1{4,}$/,
        /^(.{3})\1{3,}$/,
        /^(um|uh|ah|eh|oh)\s*$/i,
        /^[0-9\s\-\.]{10,}$/,
        /^[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]{5,}$/,
    ];

    if (text.length < 2) return true;

    const charCounts = {};
    for (let char of text.toLowerCase()) {
        if (char.match(/[a-z]/)) charCounts[char] = (charCounts[char] || 0) + 1;
    }
    const counts = Object.values(charCounts);
    if (counts.length > 0 && Math.max(...counts) > text.length * 0.6) return true;

    return hallucinationPatterns.some(pattern => pattern.test(text));
}

// ---- 显示相关 ----

function updateStreamingDisplay(channel = 'primary') {
    const tc = document.getElementById('transcriptContent');
    transcriptSplit = transcriptSplit || document.getElementById('transcriptSplit');
    transcriptLeft = transcriptLeft || document.getElementById('transcriptLeft');
    transcriptRight = transcriptRight || document.getElementById('transcriptRight');

    const container = transcriptSplit && transcriptSplit.style.display !== 'none'
        ? (channel === 'secondary' ? transcriptRight : transcriptLeft)
        : tc;

    const old = container.querySelector(`#streaming-transcript-${channel}`);
    if (old) old.remove();

    let html = '';
    if (getTranslationMode() === 'realtime_beta') {
        return;
    } else {
        const text = currentStreamingTextMap[channel];
        if (text && text.trim()) {
            const language = channel === 'secondary'
                ? document.getElementById('secondaryLanguage')?.value
                : document.getElementById('primaryLanguage')?.value;
            html = renderSubtitleEntry([{
                language: language || 'text',
                label: getSubtitleLanguageLabel(language || 'text'),
                text
            }], new Date().toISOString(), {
                id: `streaming-transcript-${channel}`,
                live: true
            });
        }
    }

    if (html) {
        container.insertAdjacentHTML('beforeend', html);

        if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
            scrollToBottom(channel);
        }
    }
}

function renderSubtitleEntry(lines, timestamp, options = {}) {
    const time = new Date(timestamp || Date.now()).toLocaleTimeString();
    const idAttr = options.id ? ` id="${escapeHtml(options.id)}"` : '';
    const className = ['subtitle-entry', options.live ? 'subtitle-live' : '', options.extraClass || '']
        .filter(Boolean).join(' ');
    const validLines = (lines || []).filter(line => line && (line.text || line.pending));
    const rows = validLines.map((line, idx) => {
        const role = idx === 0 ? 'is-primary' : 'is-secondary';
        const pending = line.pending ? 'is-pending' : '';
        const cls = `subtitle-line ${role} ${pending}`.trim();
        const label = idx === 0
            ? `<div class="subtitle-label">${escapeHtml(line.label || getSubtitleLanguageLabel(line.language))}</div>`
            : '';
        return `
            <div class="${cls}">
                ${label}
                <div class="subtitle-text">${escapeHtml(line.text || '...')}</div>
            </div>
        `;
    }).join('');

    return `
        <div${idAttr} class="${className}" title="${escapeHtml(time)}">
            ${rows}
        </div>
    `;
}

function formatMinuteLabel(timestamp) {
    const d = new Date(timestamp || Date.now());
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `${hh}:${mm}`;
}

function renderEntriesWithMinuteGroups(entries, renderFn) {
    let prevMinute = null;
    return (entries || []).map((entry) => {
        const minute = formatMinuteLabel(entry.timestamp);
        const header = (minute !== prevMinute)
            ? `<div class="subtitle-minute">${escapeHtml(minute)}</div>`
            : '';
        prevMinute = minute;
        return header + renderFn(entry);
    }).join('');
}

function renderLegacyTranscriptEntry(entry) {
    return renderSubtitleEntry([{
        language: entry.language || 'text',
        label: entry.isTranslation ? 'TR' : getSubtitleLanguageLabel(entry.language || 'text'),
        text: entry.text || ''
    }], entry.timestamp, {
        extraClass: entry.isTranslation ? 'subtitle-translation' : ''
    });
}

function renderRealtimeTranscriptionPaneEntry(entry) {
    return renderSubtitleEntry([{
        language: entry.language || entry.originalLanguage || 'text',
        label: getSubtitleLanguageLabel(entry.language || entry.originalLanguage || 'text'),
        text: entry.text || entry.rawTranscript || ''
    }], entry.timestamp);
}

function renderRealtimeTranslationPaneEntry(entry) {
    return renderSubtitleEntry([{
        language: entry.language || 'text',
        label: getSubtitleLanguageLabel(entry.language || 'text'),
        text: entry.text || ''
    }], entry.timestamp);
}

function renderBetaWhisperSection(entries, liveLines, liveId) {
    let prevLang = null;
    let prevMinute = null;
    const body = (entries || []).map((entry) => {
        const curLang = normalizeRealtimeTranslationLanguage(
            entry.language || entry.originalLanguage || '');
        const minute = formatMinuteLabel(entry.timestamp);
        const langChanged = prevLang !== null && curLang && prevLang !== curLang;
        const minuteChanged = minute !== prevMinute;
        prevLang = curLang || prevLang;
        prevMinute = minute;
        let header = '';
        if (langChanged) {
            header += `<div class="whisper-lang-divider" data-lang="${escapeHtml(getSubtitleLanguageLabel(curLang))}"></div>`;
        }
        if (minuteChanged) {
            header += `<div class="subtitle-minute">${escapeHtml(minute)}</div>`;
        }
        return header + renderTranscriptEntry(entry);
    }).join('');
    const live = liveLines?.length
        ? renderSubtitleEntry(liveLines, new Date().toISOString(), { id: liveId, live: true })
        : '';
    const empty = !body && !live ? '<div class="subtitle-empty">等待字幕...</div>' : '';
    return `<section class="subtitle-section">${body}${live}${empty}</section>`;
}

function renderRealtimePaneSection(title, entries, liveLines, liveId) {
    const body = renderEntriesWithMinuteGroups(entries, renderTranscriptEntry);
    const live = liveLines?.length
        ? renderSubtitleEntry(liveLines, new Date().toISOString(), { id: liveId, live: true })
        : '';
    const empty = !body && !live
        ? '<div class="subtitle-empty">等待字幕...</div>'
        : '';
    return `
        <section class="subtitle-section">
            <div class="subtitle-section-title">${escapeHtml(title)}</div>
            ${body}
            ${live}
            ${empty}
        </section>
    `;
}

function renderTranscriptEntry(transcript) {
    if (isRealtimeTranscriptionPaneEntry(transcript)) {
        return renderRealtimeTranscriptionPaneEntry(transcript);
    }
    if (isRealtimeTranslationPaneEntry(transcript)) {
        return renderRealtimeTranslationPaneEntry(transcript);
    }
    if (isStructuredTranscript(transcript)) {
        return renderStructuredTranscriptEntry(transcript);
    }
    return renderLegacyTranscriptEntry(transcript);
}

function renderRealtimeSplitPane(channel, entries) {
    if (getTranslationMode() === 'realtime_beta') {
        if (channel === 'primary') {
            const originalEntries = entries.filter(entry =>
                entry.paneRole === 'original' || isRealtimeTranscriptionPaneEntry(entry));
            return renderRealtimePaneSection(
                '原文转写',
                originalEntries,
                buildRealtimePaneLiveLines('primary', 'original'),
                'streaming-transcript-primary-original'
            );
        }

        return realtimeTranslationTargetLanguages.map((lang) => {
            const translationEntries = entries.filter(entry =>
                (entry.paneRole === 'translation' || isRealtimeTranslationPaneEntry(entry)) &&
                normalizeRealtimeTranslationLanguage(entry.language) === normalizeRealtimeTranslationLanguage(lang));
            // live 译文条目已在 translationEntries 中（按 stream key 维护），无需额外 liveLines。
            return renderRealtimePaneSection(
                `译为 ${getSubtitleLanguageLabel(lang)}`,
                translationEntries,
                [],
                `streaming-translation-${lang}`
            );
        }).join('');
    }

    const originalEntries = entries.filter(entry =>
        entry.paneRole === 'original' || isRealtimeTranscriptionPaneEntry(entry));
    const translationEntries = entries.filter(entry =>
        entry.paneRole === 'translation' || isRealtimeTranslationPaneEntry(entry));
    return [
        renderRealtimePaneSection(
            '原文转写',
            originalEntries,
            buildRealtimePaneLiveLines(channel, 'original'),
            `streaming-transcript-${channel}-original`
        ),
        renderRealtimePaneSection(
            '翻译',
            translationEntries,
            buildRealtimePaneLiveLines(channel, 'translation'),
            `streaming-transcript-${channel}-translation`
        )
    ].join('');
}

function renderStructuredTranscriptEntry(entry) {
    const mainText = getDisplayTranscriptText(entry);
    const notes = [];
    if (entry.lowConfidence) {
        notes.push('识别置信度偏低，建议会后复核');
    }
    const lines = [];
    if (mainText) {
        lines.push({
            language: entry.originalLanguage || 'text',
            label: getSubtitleLanguageLabel(entry.originalLanguage || 'text'),
            text: mainText
        });
    }
    if (entry.primaryTranslation) {
        lines.push({
            language: document.getElementById('primaryLanguage')?.value || 'primary',
            label: getSubtitleLanguageLabel(document.getElementById('primaryLanguage')?.value || 'primary'),
            text: entry.primaryTranslation
        });
    }
    if (entry.secondaryTranslation) {
        lines.push({
            language: document.getElementById('secondaryLanguage')?.value || 'secondary',
            label: getSubtitleLanguageLabel(document.getElementById('secondaryLanguage')?.value || 'secondary'),
            text: entry.secondaryTranslation
        });
    }
    if (notes.length) {
        lines.push({ language: 'note', label: 'NOTE', text: notes.join(' · '), pending: true });
    }
    return renderSubtitleEntry(lines, entry.timestamp);
}

// ---- 渲染节流：合并同一帧内多次 updateDisplay ----
// 高频 delta 会连续触发 updateDisplay（每次全量 innerHTML 重建），用 rAF 合并：
// 一帧内多次 scheduleDisplayUpdate(channel) 只会在下一帧各 channel 各渲染一次。
const _pendingDisplayChannels = new Set();
let _displayRafScheduled = false;

function scheduleDisplayUpdate(channel = 'primary') {
    _pendingDisplayChannels.add(channel);
    if (_displayRafScheduled) return;
    _displayRafScheduled = true;
    requestAnimationFrame(() => {
        _displayRafScheduled = false;
        const channels = Array.from(_pendingDisplayChannels);
        _pendingDisplayChannels.clear();
        channels.forEach((ch) => updateDisplay(ch));
    });
}

function flushDisplayUpdate() {
    // 立即同步刷新所有待渲染 channel，供停止/切换等需要 DOM 即时一致的场合调用。
    if (!_displayRafScheduled) return;
    _displayRafScheduled = false;
    const channels = Array.from(_pendingDisplayChannels);
    _pendingDisplayChannels.clear();
    channels.forEach((ch) => updateDisplay(ch));
}

function updateDisplay(channel = 'primary') {
    const tc = document.getElementById('transcriptContent');
    transcriptSplit = transcriptSplit || document.getElementById('transcriptSplit');
    transcriptLeft = transcriptLeft || document.getElementById('transcriptLeft');
    transcriptRight = transcriptRight || document.getElementById('transcriptRight');

    const splitVisible = transcriptSplit && transcriptSplit.style.display !== 'none';
    const betaVisible = betaSplitContainer && betaSplitContainer.style.display !== 'none';
    if (!splitVisible && !betaVisible && transcripts.length === 0 &&
        !currentStreamingTextMap.primary && !currentStreamingTextMap.secondary) {
        tc.innerHTML = `
            <div class="welcome-message">
                <p>欢迎使用 MeetingEZ！</p>
                <p>点击底部开始按钮开始实时转写，或返回控制台切换会议流程。</p>
            </div>
        `;
        return;
    }

    const hideBefore = localStorage.getItem(HIDE_BEFORE_KEY);
    const displayTranscripts = transcripts
        .filter(t => !hideBefore || t.timestamp > hideBefore)
        .filter(t => splitVisible ? t.channel === channel : true)
        .slice(-50);

    // Beta 3-pane layout: whisper left, lang1/lang2 right
    if (betaVisible) {
        if (channel === 'primary') {
            const originalEntries = displayTranscripts.filter(entry =>
                entry.paneRole === 'original' || isRealtimeTranscriptionPaneEntry(entry));
            betaPaneWhisper.innerHTML = renderBetaWhisperSection(
                originalEntries,
                buildRealtimePaneLiveLines('primary', 'original'),
                'streaming-transcript-primary-original'
            );
        } else {
            realtimeTranslationTargetLanguages.forEach((lang, idx) => {
                const pane = idx === 0 ? betaPaneLang1 : betaPaneLang2;
                if (!pane) return;
                const langEntries = displayTranscripts.filter(entry =>
                    (entry.paneRole === 'translation' || isRealtimeTranslationPaneEntry(entry)) &&
                    normalizeRealtimeTranslationLanguage(entry.language) ===
                    normalizeRealtimeTranslationLanguage(lang));
                pane.innerHTML = renderRealtimePaneSection(
                    getSubtitleLanguageLabel(lang),
                    langEntries,
                    [],
                    `streaming-translation-${lang}`
                );
            });
        }
        if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
            scrollToBottom(channel);
        }
        return;
    }

    const contentHtml = splitVisible
        ? renderRealtimeSplitPane(channel, displayTranscripts)
        : renderEntriesWithMinuteGroups(displayTranscripts, renderTranscriptEntry);

    if (transcriptSplit && transcriptSplit.style.display !== 'none') {
        if (channel === 'secondary') {
            transcriptRight.innerHTML = contentHtml;
        } else {
            transcriptLeft.innerHTML = contentHtml;
        }
        if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
            scrollToBottom(channel);
        }
    } else {
        tc.innerHTML = contentHtml;
        updateStreamingDisplay(channel);
        if (channel !== 'secondary' && currentStreamingTextMap.secondary) {
            updateStreamingDisplay('secondary');
        }
        if (document.getElementById('autoScroll').classList.contains('btn-primary')) {
            scrollToBottom(channel);
        }
    }
}

function buildMergedGlossary() {
    const manualGlossary = document.getElementById('glossaryInput')?.value || '';
    const lines = [];
    const seen = new Set();
    const pushLine = (value) => {
        const normalized = (value || '').trim();
        if (!normalized) return;
        const key = normalized.toLowerCase();
        if (seen.has(key)) return;
        seen.add(key);
        lines.push(normalized);
    };
    (currentContextPack?.glossaryLines || []).forEach(pushLine);
    manualGlossary.split('\n').forEach(pushLine);
    return lines.join('\n');
}

const REALTIME_KEYWORDS_LIMIT = 100;

function buildRealtimeKeywords() {
    // keywords 要的是"希望模型写对的字面词"，所以只取 canonical（每行 `|` 前的部分），
    // 不带别名——别名本身就是识别错误，喂进去会强化错误拼写。
    const keywords = [];
    const seen = new Set();
    const push = (value) => {
        const keyword = (value || '').trim();
        if (!keyword || keywords.length >= REALTIME_KEYWORDS_LIMIT) return;
        const key = keyword.toLowerCase();
        if (seen.has(key)) return;
        seen.add(key);
        keywords.push(keyword);
    };

    (currentContextPack?.realtimeKeywords || []).forEach(push);
    (currentContextPack?.glossaryLines || []).forEach((line) => push(line.split('|')[0]));
    const manualGlossary = document.getElementById('glossaryInput')?.value || '';
    manualGlossary.split('\n').forEach((line) => {
        if (line.trim().startsWith('#')) return;
        push(line.split('|')[0]);
    });

    return keywords;
}

function buildMeetingContextSummary() {
    const parts = [];
    if (currentContextPack?.projectSummary) {
        parts.push(`项目摘要: ${currentContextPack.projectSummary}`);
    }
    if (currentContextPack?.backgroundSummary) {
        parts.push(`背景说明: ${currentContextPack.backgroundSummary}`);
    }
    if (currentContextPack?.pendingActions?.length) {
        parts.push(`近期行动项: ${currentContextPack.pendingActions.join('；')}`);
    }
    if (currentContextPack?.recentMeetings?.length) {
        parts.push(`近期会议: ${currentContextPack.recentMeetings.join('；')}`);
    }
    return parts.join('\n');
}

function updateContextPackPreview() {
    const statusEl = document.getElementById('contextPackStatus');
    const previewEl = document.getElementById('contextPackPreview');
    if (!statusEl || !previewEl) return;

    if (!currentContextPack) {
        statusEl.textContent = isQuickModeProject(getSelectedWorkspaceProject())
            ? '当前为快速模式，未关联项目上下文'
            : '还未关联项目上下文';
        statusEl.className = 'status-message info';
        previewEl.textContent = '';
        updateMeetingEntrySummary();
        return;
    }

    if (!currentContextPack.projectName) {
        statusEl.textContent = '当前为快速模式，未关联项目上下文';
        statusEl.className = 'status-message info';
        previewEl.textContent = '仍会使用 Realtime 原文转写与双语流式翻译。';
        updateMeetingEntrySummary();
        return;
    }

    const projectName = currentContextPack.projectName || '当前项目';
    statusEl.textContent = `已关联项目：${projectName}`;
    statusEl.className = 'status-message success';

    const pieces = [
        `语言模式：${currentContextPack.languageMode === 'bilingual' ? '双语言会议' : '单主语言会议'}`
    ];
    if (currentContextPack.recentMeetings?.length) {
        pieces.push(`近期会议 ${currentContextPack.recentMeetings.length} 条`);
    }
    previewEl.textContent = pieces.join(' · ');
    updateMeetingEntrySummary();
}

async function loadWorkspaceContextPack(options = {}) {
    const silent = options.silent === true;
    const project = getSelectedWorkspaceProject();
    const primaryLanguage = document.getElementById('primaryLanguage')?.value || 'zh-CN';
    const secondaryLanguage = (document.getElementById('secondaryLanguage')?.value || '').trim();
    const languageMode = getLanguageMode();

    const params = new URLSearchParams({
        project,
        primaryLanguage,
        secondaryLanguage,
        languageMode
    });

    if (isQuickModeProject(project)) {
        currentContextPack = buildEmptyContextPack();
        updateContextPackPreview();
        if (!silent) {
            showStatus('当前为快速模式，不加载项目增强包', 'info');
        }
        return;
    }

    try {
        const resp = await fetch(`/api/workspace/context-pack?${params.toString()}`);
        if (resp.status === 401) {
            window.location.href = '/login';
            return;
        }
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${resp.status}`);
        }

        currentContextPack = await resp.json();
        updateContextPackPreview();
        if (!silent) {
            showStatus('项目增强包已刷新', 'success');
        }
    } catch (error) {
        console.warn('加载项目增强包失败:', error);
        currentContextPack = null;
        updateContextPackPreview();
        if (!silent) {
            showStatus('项目增强包加载失败', 'error');
        }
    }
}

// ---- 辅助函数 ----

function detectLanguage(text) {
    if (/[\u3040-\u309f]/.test(text) || /[\u30a0-\u30ff]/.test(text)) return 'ja';
    if (/[\uac00-\ud7af]/.test(text)) return 'ko';
    if (/[\u4e00-\u9fa5]/.test(text)) {
        return /[繁體覽擇檢測]/.test(text) ? 'zh-TW' : 'zh';
    }
    if (/[\u0400-\u04FF]/.test(text)) return 'ru';
    return document.getElementById('primaryLanguage')?.value || 'en';
}

function normalizeText(text) {
    return (text || '').trim().replace(/[。\.]{2,}$/u, (m) => m[0]);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function scrollToBottom(channel = 'primary') {
    const betaVisible = betaSplitContainer && betaSplitContainer.style.display !== 'none';
    if (betaVisible) {
        const panes = channel === 'primary'
            ? [betaPaneWhisper]
            : [betaPaneLang1, betaPaneLang2].filter(Boolean);
        requestAnimationFrame(() => { panes.forEach(p => { p.scrollTop = p.scrollHeight; }); });
        return;
    }
    transcriptSplit = transcriptSplit || document.getElementById('transcriptSplit');
    transcriptLeft = transcriptLeft || document.getElementById('transcriptLeft');
    transcriptRight = transcriptRight || document.getElementById('transcriptRight');
    const container = getTranscriptContainerForChannel(channel);
    if (!container) return;
    requestAnimationFrame(() => { container.scrollTop = container.scrollHeight; });
}

function saveTranscripts() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: STORAGE_VERSION, items: transcripts }));
}

function loadTranscripts() {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored) {
            const parsed = JSON.parse(stored);
            if (parsed && [2, STORAGE_VERSION].includes(parsed.version)
                && Array.isArray(parsed.items)) {
                transcripts = parsed.items.map(normalizeStoredTranscriptEntry).filter(Boolean);
                if (parsed.version !== STORAGE_VERSION) saveTranscripts();
            } else if (Array.isArray(parsed)) {
                transcripts = parsed.map(normalizeStoredTranscriptEntry).filter(Boolean);
                saveTranscripts();
            } else {
                transcripts = [];
            }
            rebuildTranslationContext();
            updateDisplay('primary');
            updateDisplay('secondary');
            updateControls();
        }
    } catch (error) {
        transcripts = [];
        rebuildTranslationContext();
        updateControls();
    }
}

function downloadTranscript() {
    if (transcripts.length === 0) { alert('没有可导出的记录'); return; }

    let content = 'MeetingEZ 会议记录\n';
    content += `导出时间: ${new Date().toLocaleString()}\n`;
    content += `总记录数: ${transcripts.length}\n`;
    content += '='.repeat(50) + '\n\n';

    transcripts.forEach((t, i) => {
        content += `[${i + 1}] ${new Date(t.timestamp).toLocaleString()}\n`;
        if (isStructuredTranscript(t)) {
            content += `原始转写: ${t.rawTranscript || '-'}\n`;
            content += `翻译(主语言): ${t.primaryTranslation || '-'}\n`;
            if (Number.isFinite(t.confidence)) {
                content += `识别置信度: ${Math.round(t.confidence * 100)}%\n`;
            }
            content += `翻译(第二语言): ${t.secondaryTranslation || '-'}\n\n`;
        } else if (t.isTranslation) {
            content += `翻译结果: ${t.text || '-'}\n\n`;
        } else {
            content += `原始转写: ${t.text || '-'}\n`;
            content += '翻译(主语言): -\n';
            content += '翻译(第二语言): -\n\n';
        }
    });

    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `meetingEZ_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function clearTranscript() {
    if (confirm('确定要清空所有记录吗？')) {
        transcripts = [];
        currentStreamingTextMap.primary = '';
        currentStreamingTextMap.secondary = '';
        currentTranscriptIdMap.primary = null;
        currentTranscriptIdMap.secondary = null;
        resetRealtimePaneLiveMap();
        localStorage.setItem(HIDE_BEFORE_KEY, new Date().toISOString());

        document.getElementById('transcriptContent').innerHTML = `
            <div class="welcome-message">
                <p>欢迎使用 MeetingEZ！</p>
                <p>点击底部开始按钮开始实时转写，或返回控制台切换会议流程。</p>
            </div>
        `;
        if (transcriptLeft) transcriptLeft.innerHTML = '';
        if (transcriptRight) transcriptRight.innerHTML = '';
        if (betaPaneWhisper) betaPaneWhisper.innerHTML = '';
        if (betaPaneLang1) betaPaneLang1.innerHTML = '';
        if (betaPaneLang2) betaPaneLang2.innerHTML = '';
        rebuildTranslationContext();
        updateControls();
        setTimeout(() => saveTranscripts(), 0);
    }
}

// ---- 控制 UI ----

function updateControls() {
    meetingActionBtn.disabled = isTestingMicrophone;
    meetingActionBtn.textContent = isConnected ? '结束' : '开始';
    meetingActionBtn.className = `btn ${isConnected ? 'btn-danger' : 'btn-success'}`;
    document.getElementById('downloadTranscript').disabled = transcripts.length === 0;
    document.getElementById('clearTranscript').disabled = transcripts.length === 0;
    document.getElementById('testMicrophone').disabled = isConnected;
    document.getElementById('settingsToggle').disabled = false;
    updateMeetingTimer();
}

function updateAudioInputVisibility() {
    const audioInputContainer = document.getElementById('audioInputContainer');
    const tabAudioOptions = document.getElementById('tabAudioOptions');
    if (selectedAudioSource === 'microphone') {
        audioInputContainer.style.display = 'flex';
        if (tabAudioOptions) tabAudioOptions.style.display = 'none';
    } else {
        audioInputContainer.style.display = 'none';
        if (tabAudioOptions) tabAudioOptions.style.display = 'block';
    }
}

function updateMeetingStatus(status, className) {
    meetingStatusText = status || '未开始';
    updateMeetingTimer();
}

function updateAudioStatus(status, className) {
    const el = document.getElementById('audioStatus');
    el.textContent = status;
    el.className = `status-indicator ${className}`;
}

function updateFontSize() {
    transcriptContent.className = `transcript-content font-${localStorage.getItem('meetingEZ_fontSize') || 'medium'}`;
    const fontSize = localStorage.getItem('meetingEZ_fontSize') || 'medium';
    if (transcriptLeft) transcriptLeft.className = `transcript-pane font-${fontSize}`;
    if (transcriptRight) transcriptRight.className = `transcript-pane font-${fontSize}`;
    if (betaPaneWhisper) betaPaneWhisper.className = `transcript-pane beta-pane-whisper font-${fontSize}`;
    if (betaPaneLang1) betaPaneLang1.className = `transcript-pane beta-pane-lang1 font-${fontSize}`;
    if (betaPaneLang2) betaPaneLang2.className = `transcript-pane beta-pane-lang2 font-${fontSize}`;
}

function disableSettings() {
    ['audioInput', 'primaryLanguage', 'secondaryLanguage', 'fontSize', 'audioSourceMic',
        'audioSourceTab', 'languageMode', 'workspaceProject'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = true;
    });
    ['testConnection'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = true;
    });
}

function enableSettings() {
    ['audioInput', 'primaryLanguage', 'secondaryLanguage', 'fontSize', 'audioSourceMic',
        'audioSourceTab', 'languageMode', 'workspaceProject'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = false;
    });
    ['testConnection'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.disabled = false;
    });
}

function enableSplitView(enabled) {
    const content = document.getElementById('transcriptContent');
    transcriptSplit = transcriptSplit || document.getElementById('transcriptSplit');
    if (!content || !transcriptSplit) return;

    const isBeta = enabled && getTranslationMode() === 'realtime_beta';
    content.style.display = enabled ? 'none' : 'block';
    transcriptSplit.style.display = (!isBeta && enabled) ? 'grid' : 'none';
    if (betaSplitContainer) betaSplitContainer.style.display = isBeta ? 'grid' : 'none';
    updateOriginalTranscriptVisibility();

    if (enabled) {
        updateRealtimeTranslationSplitHeaders();
        updateDisplay('primary');
        updateDisplay('secondary');
    } else {
        updateDisplay('primary');
    }
}

function showStatus(message, type) {
    statusDiv.textContent = message;
    statusDiv.className = `status-message ${type}`;
    statusDiv.style.display = 'block';
    setTimeout(() => { statusDiv.style.display = 'none'; }, 3000);
}

function showError(message) {
    document.getElementById('errorMessage').textContent = message;
    document.getElementById('errorModal').style.display = 'flex';
}

function showLoading(message) {
    const overlay = document.getElementById('loadingOverlay');
    overlay.querySelector('p').textContent = message;
    overlay.style.display = 'flex';
}

function hideLoading() {
    document.getElementById('loadingOverlay').style.display = 'none';
}

function updateVolumeIndicator(volume) {
    const volumeBar = document.getElementById('volumeBar');
    if (!volumeBar) return;
    const pct = Math.min(100, volume * 100);
    volumeBar.style.width = `${pct}%`;
    volumeBar.classList.toggle('high', pct > 70);
}

// ---- 音频设备 ----

async function loadAudioDevices() {
    try {
        const audioInputSelect = document.getElementById('audioInput');
        if (!window.isSecureContext) {
            if (audioInputSelect) audioInputSelect.innerHTML = '<option value="">需要 HTTPS 或 localhost</option>';
            return;
        }

        let devices = await navigator.mediaDevices.enumerateDevices();
        let audioInputs = devices.filter(d => d.kind === 'audioinput');
        audioInputSelect.innerHTML = '';

        if (audioInputs.length === 0 || audioInputs.some(d => !d.label)) {
            try {
                const prewarm = await navigator.mediaDevices.getUserMedia({ audio: true });
                prewarm.getTracks().forEach(t => t.stop());
                devices = await navigator.mediaDevices.enumerateDevices();
                audioInputs = devices.filter(d => d.kind === 'audioinput');
            } catch (e) {
                audioInputSelect.innerHTML = '<option value="">未授权麦克风</option>';
                return;
            }
        }

        if (audioInputs.length === 0) {
            audioInputSelect.innerHTML = '<option value="">无可用设备</option>';
            return;
        }

        audioInputs.forEach((device, i) => {
            const option = document.createElement('option');
            option.value = device.deviceId;
            option.text = device.label || `麦克风 ${i + 1}`;
            audioInputSelect.appendChild(option);
        });

        const saved = localStorage.getItem('meetingEZ_audioDevice');
        if (saved && audioInputs.some(d => d.deviceId === saved)) {
            audioInputSelect.value = saved;
            selectedAudioDevice = saved;
        } else {
            selectedAudioDevice = audioInputs[0].deviceId;
            audioInputSelect.value = selectedAudioDevice;
        }

        audioInputSelect.onchange = (e) => {
            selectedAudioDevice = e.target.value;
            localStorage.setItem('meetingEZ_audioDevice', selectedAudioDevice);
        };
    } catch (error) {
        console.error('加载音频设备失败:', error);
    }
}

// ---- 麦克风测试 ----

async function toggleMicrophoneTest() {
    const btn = document.getElementById('testMicrophone');
    if (isTestingMicrophone) {
        stopMicrophoneTest();
        btn.textContent = '测';
        btn.classList.remove('btn-danger');
        btn.classList.add('btn-outline');
    } else {
        try {
            await startMicrophoneTest();
            btn.textContent = '停';
            btn.classList.remove('btn-outline');
            btn.classList.add('btn-danger');
        } catch (error) {
            showError('麦克风测试失败: ' + error.message);
        }
    }
}

async function startMicrophoneTest() {
    const constraints = { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true };
    if (selectedAudioDevice) constraints.deviceId = { exact: selectedAudioDevice };

    testStream = await navigator.mediaDevices.getUserMedia({ audio: constraints });
    testAudioContext = new AudioContext();
    const source = testAudioContext.createMediaStreamSource(testStream);
    const analyser = testAudioContext.createAnalyser();
    analyser.fftSize = 256;
    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    source.connect(analyser);

    isTestingMicrophone = true;
    updateControls();

    function monitor() {
        if (!isTestingMicrophone) return;
        analyser.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
        updateVolumeIndicator((sum / dataArray.length) / 255);
        volumeAnimationFrame = requestAnimationFrame(monitor);
    }
    monitor();
}

function stopMicrophoneTest() {
    isTestingMicrophone = false;
    if (volumeAnimationFrame) { cancelAnimationFrame(volumeAnimationFrame); volumeAnimationFrame = null; }
    if (testStream) { testStream.getTracks().forEach(t => t.stop()); testStream = null; }
    if (testAudioContext) { testAudioContext.close(); testAudioContext = null; }
    updateVolumeIndicator(0);
    updateControls();
}

// ---- 自动滚动 ----

function toggleAutoScroll() {
    const btn = document.getElementById('autoScroll');
    if (btn.classList.contains('btn-primary')) {
        btn.classList.replace('btn-primary', 'btn-outline');
        btn.textContent = '滚';
        localStorage.setItem('meetingEZ_autoScroll', 'false');
    } else {
        btn.classList.replace('btn-outline', 'btn-primary');
        btn.textContent = '滚\u2713';
        localStorage.setItem('meetingEZ_autoScroll', 'true');
        scrollToBottom('primary');
        scrollToBottom('secondary');
    }
}

function initializeAutoScroll() {
    const btn = document.getElementById('autoScroll');
    if (localStorage.getItem('meetingEZ_autoScroll') !== 'false') {
        btn.classList.replace('btn-outline', 'btn-primary');
        btn.textContent = '滚\u2713';
    } else {
        btn.classList.replace('btn-primary', 'btn-outline');
        btn.textContent = '滚';
    }
}

// ---- 术语校正后置处理（通过后端 /api/refine-transcript） ----
//
// Realtime transcription session 和 Realtime Translation session 都注入不了术语表
// （translation session 连 keywords 字段都不收，会 400），所以术语准确性靠这条文本
// 链路补：realtime 结果先直接上屏，条目定格后排队送给便宜的文本模型按术语表纠错，
// 返回后原位替换。校正失败或超时都不影响已显示的字幕。

const REFINE_BATCH_SIZE = 8;
const REFINE_DEBOUNCE_MS = 1200;
const REFINE_MAX_CONCURRENT = 2;

function resetRefineQueue() {
    refineQueue = [];
    refineInFlight = 0;
    if (refineTimer) {
        clearTimeout(refineTimer);
        refineTimer = null;
    }
    // 会中设置是锁死的，术语表整场不变，开场算一次就够。
    refineKeywords = buildRealtimeKeywords();
    console.log(`[refine] 术语校正${refineKeywords.length ? `已启用，术语 ${refineKeywords.length} 条` : '未启用（无术语表）'}`);
}

function enqueueRefine(entry, text) {
    if (!entry || entry._refineQueued || !refineKeywords.length) return;
    const trimmed = (text || '').trim();
    if (!trimmed) return;

    entry._refineQueued = true;
    refineQueue.push({
        id: String(entry.id),
        lang: entry.language || '',
        text: trimmed
    });

    if (refineTimer) {
        clearTimeout(refineTimer);
        refineTimer = null;
    }
    if (refineQueue.length >= REFINE_BATCH_SIZE) {
        void flushRefineQueue();
    } else {
        refineTimer = setTimeout(() => void flushRefineQueue(), REFINE_DEBOUNCE_MS);
    }
}

async function flushRefineQueue(options = {}) {
    if (refineTimer) {
        clearTimeout(refineTimer);
        refineTimer = null;
    }
    if (!refineQueue.length) return;

    // 并发满了就等下一轮，避免慢会议里请求堆积。force 时（结束会议）不限流，直接排干。
    if (!options.force && refineInFlight >= REFINE_MAX_CONCURRENT) {
        refineTimer = setTimeout(() => void flushRefineQueue(), REFINE_DEBOUNCE_MS);
        return;
    }

    const batch = refineQueue.splice(0, REFINE_BATCH_SIZE);
    refineInFlight += 1;
    try {
        const resp = await fetch('/api/refine-transcript', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                segments: batch,
                keywords: refineKeywords,
                context: buildMeetingContextSummary()
            })
        });
        if (resp.status === 401) {
            window.location.href = '/login';
            return;
        }
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}`);
        }
        const data = await resp.json();
        applyRefineResults(data.segments);
    } catch (error) {
        // 校正只是增强，失败就保留 realtime 原始文本，不打扰用户。
        console.warn('[refine] 术语校正失败，保留原文:', error);
    } finally {
        refineInFlight -= 1;
    }

    if (refineQueue.length) {
        void flushRefineQueue(options);
    }
}

function applyRefineResults(segments) {
    let applied = 0;
    const dirtyChannels = new Set();

    (segments || []).forEach(({ id, text }) => {
        const fixed = (text || '').trim();
        if (!fixed) return;
        const entry = transcripts.find(item => String(item.id) === String(id));
        if (!entry) return;
        // 条目可能已经被别的路径改过，只在内容仍是我们送出去的那份时才替换。
        const current = getDisplayTranscriptText(entry);
        if (!current || current === fixed) return;

        if (isStructuredTranscript(entry)) {
            entry.correctedTranscript = fixed;
            entry.correctionApplied = true;
        } else {
            entry.text = fixed;
        }
        entry.refined = true;
        dirtyChannels.add(entry.channel || 'primary');
        applied += 1;
    });

    if (!applied) return;
    saveTranscripts();
    rebuildTranslationContext();
    dirtyChannels.forEach(channel => scheduleDisplayUpdate(channel));
    console.log(`[refine] 术语校正生效 ${applied} 条`);
}

// ---- 稳定后置翻译（通过后端 /api/translate） ----

async function postProcessText(originalText, opts = {}) {
    const primaryLanguage = opts.primaryLanguage || 'zh';
    const secondaryLanguage = opts.secondaryLanguage || '';
    const enableGlossary = !!opts.enableGlossary;
    const contextInfo = translationContext.length > 0
        ? translationContext.map((item, idx) => `[${idx + 1}] (${item.language}) ${item.text}`).join('\n')
        : '';
    const resp = await fetch('/api/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            text: originalText,
            primaryLanguage,
            secondaryLanguage,
            languageMode: opts.languageMode || getLanguageMode(),
            originalLanguageHint: opts.originalLanguageHint || primaryLanguage,
            enableCorrection: !!opts.enableCorrection,
            enableGlossary,
            glossary: enableGlossary ? (buildMergedGlossary() || opts.glossary || '') : '',
            meetingContext: buildMeetingContextSummary(),
            context: contextInfo
        })
    });
    if (resp.status === 401) {
        window.location.href = '/login';
        throw new Error('登录已失效，请重新登录');
    }
    if (!resp.ok) {
        const error = await resp.json().catch(() => ({}));
        throw new Error(error.error || `HTTP ${resp.status}`);
    }
    return await resp.json();
}

function applyPostProcessToTranscript(provisionalId, structured) {
    const entry = transcripts.find(item => item.id === provisionalId);
    if (!entry) return;
    entry.originalLanguage = structured.originalLanguage || entry.originalLanguage;
    entry.rawTranscript = normalizeText(structured.rawTranscript || entry.rawTranscript);
    entry.correctedTranscript = structured.correctedTranscript
        ? normalizeText(structured.correctedTranscript)
        : null;
    entry.correctionApplied = !!structured.correctionApplied;
    entry.primaryTranslation = structured.primaryTranslation
        ? normalizeText(structured.primaryTranslation)
        : null;
    entry.secondaryTranslation = structured.secondaryTranslation
        ? normalizeText(structured.secondaryTranslation)
        : null;
    entry.postProcessing = false;
    entry.pendingCorrection = false;
    entry.pendingTranslation = false;
    saveTranscripts();
    rebuildTranslationContext();
    updateDisplay(entry.channel || 'primary');
}

// ---- 页面初始化 ----

document.addEventListener('DOMContentLoaded', () => {
    transcriptSplit = document.getElementById('transcriptSplit');
    if (!transcriptSplit) {
        transcriptSplit = document.createElement('div');
        transcriptSplit.id = 'transcriptSplit';
        transcriptSplit.className = 'transcript-split';
        transcriptSplit.style.display = 'none';
        transcriptLeft = document.createElement('div');
        transcriptLeft.id = 'transcriptLeft';
        transcriptLeft.className = 'transcript-pane';
        transcriptRight = document.createElement('div');
        transcriptRight.id = 'transcriptRight';
        transcriptRight.className = 'transcript-pane';
        transcriptSplit.appendChild(transcriptLeft);
        transcriptSplit.appendChild(transcriptRight);
        const container = document.querySelector('.transcript-container');
        if (container) container.appendChild(transcriptSplit);
    }

    // Beta 3-pane container (whisper left, two translation panes right)
    betaSplitContainer = document.createElement('div');
    betaSplitContainer.id = 'betaSplitContainer';
    betaSplitContainer.className = 'transcript-beta-split';
    betaSplitContainer.style.display = 'none';
    betaPaneWhisper = document.createElement('div');
    betaPaneWhisper.id = 'betaPaneWhisper';
    betaPaneWhisper.className = 'transcript-pane beta-pane-whisper';
    betaPaneLang1 = document.createElement('div');
    betaPaneLang1.id = 'betaPaneLang1';
    betaPaneLang1.className = 'transcript-pane beta-pane-lang1';
    betaPaneLang2 = document.createElement('div');
    betaPaneLang2.id = 'betaPaneLang2';
    betaPaneLang2.className = 'transcript-pane beta-pane-lang2';
    betaSplitContainer.append(betaPaneWhisper, betaPaneLang1, betaPaneLang2);
    const betaContainer = document.querySelector('.transcript-container');
    if (betaContainer) betaContainer.appendChild(betaSplitContainer);

    init();
    loadTranscripts();
    updateFontSize();
    updateMeetingTimer();

});
