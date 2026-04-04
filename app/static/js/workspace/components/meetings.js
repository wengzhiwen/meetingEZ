/**
 * 会议列表 Tab — 可展开卡片
 */
import { api } from '../api.js';
import { showToast } from '../toast.js';
import { openFile } from './slide-over.js';
import { invalidateCache, render as renderProject } from './project-tabs.js';
import { openModal, closeModal, lockModal, unlockModal, getModalBody } from './modal.js';

function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1048576).toFixed(1)} MB`;
}

function uploadXHR(url, formData, onProgress) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', url);
        xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable) onProgress(e.loaded, e.total);
        });
        xhr.addEventListener('load', () => {
            try {
                const data = JSON.parse(xhr.responseText);
                if (xhr.status >= 200 && xhr.status < 300) resolve(data);
                else reject(new Error(data.error || `请求失败 (${xhr.status})`));
            } catch { reject(new Error('响应解析失败')); }
        });
        xhr.addEventListener('error', () => reject(new Error('网络错误')));
        xhr.send(formData);
    });
}

async function confirmAndUpload(projectId, dir, files) {
    const rows = Array.from(files).map(f =>
        `<li class="upload-file-row"><span class="upload-file-name">${f.name.replace(/</g, '&lt;')}</span><span class="upload-file-size">${formatSize(f.size)}</span></li>`
    ).join('');

    openModal('上传音频文件', `
        <p style="margin-bottom:0.7rem">即将上传以下 <strong>${files.length}</strong> 个文件：</p>
        <ul class="upload-file-list">${rows}</ul>
        <div class="upload-actions">
            <button class="spa-btn spa-btn-outline spa-btn-sm" id="uploadCancelBtn">取消</button>
            <button class="spa-btn spa-btn-primary spa-btn-sm" id="uploadConfirmBtn">确认上传</button>
        </div>
    `);

    const body = getModalBody();
    const confirmed = await new Promise(resolve => {
        body.querySelector('#uploadConfirmBtn').addEventListener('click', () => resolve(true));
        body.querySelector('#uploadCancelBtn').addEventListener('click', () => resolve(false));
        document.getElementById('modalClose').addEventListener('click', () => resolve(false), { once: true });
    });

    if (!confirmed) {
        closeModal();
        return false;
    }

    lockModal();
    const closeBtn = document.getElementById('modalClose');
    closeBtn.disabled = true;
    closeBtn.style.opacity = '0.3';
    closeBtn.style.pointerEvents = 'none';
    body.innerHTML = `
        <p id="uploadStatusText" style="margin-bottom:0.6rem">准备上传...</p>
        <div class="upload-progress-track">
            <div class="upload-progress-bar" id="uploadProgressBar"></div>
        </div>
        <p id="uploadSpeedText" style="margin-top:0.5rem;font-size:0.82rem;color:var(--muted)"></p>
    `;

    const fd = new FormData();
    for (const f of files) fd.append('audio_files', f);
    const url = `/api/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(dir)}/audio/upload`;

    let lastLoaded = 0, lastTime = Date.now();
    try {
        await uploadXHR(url, fd, (loaded, total) => {
            const now = Date.now();
            const dt = (now - lastTime) / 1000;
            const speed = dt > 0.1 ? (loaded - lastLoaded) / dt : 0;
            if (dt > 0.1) { lastLoaded = loaded; lastTime = now; }
            const pct = Math.min(100, Math.round(loaded / total * 100));
            const bar = document.getElementById('uploadProgressBar');
            const statusEl = document.getElementById('uploadStatusText');
            const speedEl = document.getElementById('uploadSpeedText');
            if (bar) bar.style.width = pct + '%';
            if (statusEl) statusEl.textContent = `已上传 ${formatSize(loaded)} / ${formatSize(total)}  (${pct}%)`;
            if (speedEl && speed > 0) speedEl.textContent = `速率：${formatSize(speed)}/s`;
        });
        closeModal();
        return true;
    } catch (e) {
        unlockModal();
        closeBtn.disabled = false;
        closeBtn.style.opacity = '';
        closeBtn.style.pointerEvents = '';
        body.innerHTML = `<p style="color:var(--danger,#e55);margin-bottom:0.8rem">${e.message}</p>
            <div class="upload-actions">
                <button class="spa-btn spa-btn-outline spa-btn-sm" id="uploadErrCloseBtn">关闭</button>
            </div>`;
        body.querySelector('#uploadErrCloseBtn').addEventListener('click', closeModal);
        throw e;
    }
}

const MEETING_TYPE_LABELS = {
    review: '评审会', weekly: '周会', brainstorm: '头脑风暴',
    retro: '复盘会', kickoff: '启动会', other: '其他',
};

const MEETING_TYPES = Object.entries(MEETING_TYPE_LABELS); // [[v, l], ...]

// 语言选项从 API 数据动态传入，避免与后端定义的码值不一致
let _langPairs = []; // [[value, label], ...]

function typeOptions(selected) {
    return MEETING_TYPES.map(([v, l]) =>
        `<option value="${v}"${v === selected ? ' selected' : ''}>${l}</option>`
    ).join('');
}

function langOptions(selected) {
    return _langPairs.map(([v, l]) =>
        `<option value="${v}"${v === selected ? ' selected' : ''}>${l}</option>`
    ).join('');
}

function langOptionsWithEmpty(selected) {
    const none = `<option value=""${!selected ? ' selected' : ''}>无</option>`;
    return none + langOptions(selected);
}

const _pollingTimers = new Map(); // dir -> timerId

function _startPolling(projectId, dir) {
    if (_pollingTimers.has(dir)) return;
    const timerId = setInterval(async () => {
        try {
            const result = await api.getMeetingProcessStatus(projectId, dir);
            if (!result.is_processing) {
                clearInterval(timerId);
                _pollingTimers.delete(dir);
                if (result.error) {
                    showToast('处理失败：' + result.error, 'error');
                } else {
                    showToast('处理完成', 'success');
                }
                invalidateCache(projectId);
                const { render } = await import('./project-tabs.js');
                await render(projectId, 'meetings');
            }
        } catch {
            // 忽略轮询错误，继续轮询
        }
    }, 3000);
    _pollingTimers.set(dir, timerId);
}

export function renderMeetings(container, data, projectId) {
    // 同步语言选项，与后端 LANGUAGE_OPTIONS 保持一致
    _langPairs = data.language_options || _langPairs;
    const meetings = data.meetings || [];

    if (!meetings.length) {
        container.innerHTML = '<div class="spa-empty">暂无会议，请在概览页新建。</div>';
        return;
    }

    container.innerHTML = `<div class="spa-meeting-list" id="meetingList">
        ${meetings.map(m => meetingCard(m, projectId)).join('')}
    </div>`;

    bindEvents(container, data, projectId);
}

function meetingCard(m, projectId) {
    // 过滤掉 _meeting.json，它的内容已通过配置面板直接展示
    const files = (m.files || []).filter(f => f.name !== '_meeting.json');

    return `
    <div class="spa-meeting-card" data-dir="${esc(m.dir_name)}">
        <div class="spa-meeting-head">
            <span class="spa-meeting-expand-icon">&#9654;</span>
            <span class="spa-meeting-title">${esc(m.title)}</span>
            <span class="spa-meeting-date">${esc(m.date)}</span>
            ${m.is_processing
                ? `<span class="spa-meeting-status spa-status-processing">处理中...</span>`
                : m.needs_asr || m.needs_minutes
                    ? `<span class="spa-meeting-status spa-status-pending">${esc(m.pending_label)}</span>`
                    : ''}
        </div>
        <div class="spa-meeting-body">
            <div class="spa-meeting-detail">

                <!-- 会议配置展示 / 编辑 -->
                <div class="spa-panel spa-meeting-config-panel" style="margin-bottom:1rem;">
                    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.5rem;">
                        <strong style="font-size:0.85rem;">会议配置</strong>
                        <button class="spa-btn spa-btn-sm btn-edit-meeting">编辑</button>
                    </div>
                    <div class="meeting-config-view">
                        <div class="spa-kv-list spa-kv-list-sm">
                            <div class="spa-kv"><span class="spa-kv-label">标题</span><span class="spa-kv-value">${esc(m.title)}</span></div>
                            <div class="spa-kv"><span class="spa-kv-label">日期</span><span class="spa-kv-value">${esc(m.date)}</span></div>
                            <div class="spa-kv"><span class="spa-kv-label">类型</span><span class="spa-kv-value">${esc(MEETING_TYPE_LABELS[m.type] || m.type)}</span></div>
                            <div class="spa-kv"><span class="spa-kv-label">语言</span><span class="spa-kv-value">${esc(m.language_profile)}</span></div>
                            ${m.notes ? `<div class="spa-kv"><span class="spa-kv-label">备注</span><span class="spa-kv-value">${esc(m.notes)}</span></div>` : ''}
                        </div>
                    </div>
                    <div class="meeting-config-edit" style="display:none;">
                        <form class="spa-form spa-form-sm">
                            <div class="spa-form-row">
                                <div class="spa-form-group">
                                    <label>标题 *</label>
                                    <input name="title" required value="${esc(m.title)}">
                                </div>
                                <div class="spa-form-group">
                                    <label>日期 *</label>
                                    <input name="date" type="date" required value="${esc(m.date)}">
                                </div>
                            </div>
                            <div class="spa-form-row">
                                <div class="spa-form-group">
                                    <label>类型</label>
                                    <select name="type">${typeOptions(m.type)}</select>
                                </div>
                                <div class="spa-form-group">
                                    <label>语言模式</label>
                                    <select name="language_mode">
                                        <option value="single_primary"${m.language_mode === 'single_primary' ? ' selected' : ''}>单主语言</option>
                                        <option value="bilingual"${m.language_mode === 'bilingual' ? ' selected' : ''}>双语</option>
                                    </select>
                                </div>
                            </div>
                            <div class="spa-form-row">
                                <div class="spa-form-group">
                                    <label>主要语言</label>
                                    <select name="primary_language">${langOptions(m.primary_language)}</select>
                                </div>
                                <div class="spa-form-group">
                                    <label>第二语言</label>
                                    <select name="secondary_language">${langOptionsWithEmpty(m.secondary_language)}</select>
                                </div>
                            </div>
                            <div class="spa-form-group">
                                <label>备注</label>
                                <input name="notes" value="${esc(m.notes || '')}">
                            </div>
                            <div class="spa-form-actions">
                                <button type="submit" class="spa-btn spa-btn-primary spa-btn-sm">保存</button>
                                <button type="button" class="spa-btn spa-btn-sm btn-cancel-edit-meeting">取消</button>
                            </div>
                        </form>
                    </div>
                </div>

                ${m.is_processing ? `
                <div class="spa-meeting-actions">
                    <span class="spa-processing-indicator">&#9679; 处理中，请稍候...</span>
                </div>` : (m.asr_state && m.asr_state.status === 'blocked') ? `
                <div class="spa-asr-blocked-banner">
                    <div class="spa-asr-blocked-info">
                        <span class="spa-asr-blocked-icon">&#9888;</span>
                        <span>VoiceVoice ASR 转写失败: ${esc(m.asr_state.last_error || '未知错误')}</span>
                    </div>
                    <div class="spa-asr-blocked-meta">
                        ${m.asr_state.next_retry_at ? `<span>下次自动重试: ${esc(_formatRetryTime(m.asr_state.next_retry_at))}</span>` : ''}
                        <span>已重试 ${esc(String(m.asr_state.retry_count || 0))} 次</span>
                    </div>
                    <div class="spa-asr-blocked-actions">
                        <button class="spa-btn spa-btn-primary spa-btn-xs btn-asr-retry">立即重试</button>
                        <button class="spa-btn spa-btn-outline spa-btn-xs btn-asr-fallback">改用智谱 ASR</button>
                    </div>
                </div>` : m.needs_asr || m.needs_minutes ? `
                <div class="spa-meeting-actions">
                    ${m.needs_asr || m.needs_minutes ? `
                        <button class="spa-btn spa-btn-outline spa-btn-sm btn-process" data-action="full">完整处理</button>
                    ` : ''}
                    ${m.has_transcript && m.needs_minutes ? `
                        <button class="spa-btn spa-btn-outline spa-btn-sm btn-process" data-action="minutes">仅处理纪要</button>
                    ` : ''}
                </div>` : m.has_minutes ? `
                <div class="spa-meeting-actions">
                    <button class="spa-btn spa-btn-sm spa-btn-muted btn-process" data-action="reprocess">重新处理</button>
                </div>` : ''}

                ${renderFileSection(files, projectId)}
                ${renderAudioSection(m, projectId)}
            </div>
        </div>
    </div>`;
}

function renderFileSection(files, projectId) {
    if (!files.length) return '';
    return `
        <div class="spa-file-list">
            <strong style="font-size:0.82rem;color:var(--muted);">文件</strong>
            ${files.map(f => `
                <div class="spa-file-row">
                    <span class="spa-file-name" data-file="${esc(f.name)}">${esc(f.label)}</span>
                    <span class="spa-file-meta">${esc(f.size_label)} &middot; ${esc(f.updated_at)}</span>
                </div>
            `).join('')}
        </div>`;
}

const TRANSCRIPT_FILE = 'transcript.json';

function renderAudioSection(m, projectId) {
    const audioFiles = m.audio_files || [];
    const hasAudio = audioFiles.length > 0;
    const realtimeUrl = `/realtime?mode=project&project=${enc(projectId)}&meeting=${enc(m.dir_name)}&meetingTitle=${enc(m.title)}&primaryLanguage=${enc(m.primary_language)}&secondaryLanguage=${enc(m.secondary_language || '')}&languageMode=${enc(m.language_mode)}`;

    if (!hasAudio) {
        return `
        <div class="spa-audio-section">
            <div class="spa-audio-empty">
                <p style="color:var(--muted);font-size:0.85rem;margin-bottom:0.8rem;">还没有音频文件</p>
                <div style="display:flex;gap:0.6rem;flex-wrap:wrap;">
                    <a class="spa-btn spa-btn-primary spa-btn-sm" href="${realtimeUrl}">
                        &#9679; 启动实时录制
                    </a>
                    <label class="spa-btn spa-btn-outline spa-btn-sm" style="cursor:pointer;">
                        &#8679; 上传音频文件
                        <input type="file" class="audio-upload-input" style="display:none;"
                               accept=".mp3,.m4a,.wav,.flac,.ogg,.aac,.wma" multiple>
                    </label>
                </div>
            </div>
        </div>`;
    }

    return `
        <div class="spa-audio-section">
            <div class="spa-audio-header">
                <span style="font-size:0.82rem;color:var(--muted);">音频文件 (${audioFiles.length})</span>
                <div style="display:flex;gap:0.5rem;align-items:center;">
                    ${m.has_transcript
                        ? `<span class="spa-pill spa-pill-accent" style="font-size:0.75rem;">已转写</span>
                           <button class="spa-btn spa-btn-outline spa-btn-xs btn-view-transcript">查看转写</button>`
                        : m.needs_asr
                            ? `<span class="spa-pill spa-pill-warn" style="font-size:0.75rem;">待转写</span>`
                            : ''}
                    <label class="spa-btn spa-btn-outline spa-btn-xs" style="cursor:pointer;">
                        &#8679; 上传
                        <input type="file" class="audio-upload-input" style="display:none;"
                               accept=".mp3,.m4a,.wav,.flac,.ogg,.aac,.wma" multiple>
                    </label>
                </div>
            </div>
            <div class="spa-audio-list">
                ${audioFiles.map(a => `
                    <div class="spa-audio-item" data-audio="${esc(a.name)}">
                        <div class="spa-audio-info">
                            <span class="spa-audio-name">${esc(a.name)}</span>
                            <span class="spa-audio-meta">${esc(a.size_label)}</span>
                        </div>
                        <audio class="spa-audio-player" controls preload="none"
                               src="${esc(api.getAudioFileUrl(projectId, m.dir_name, a.name))}"></audio>
                        <button class="spa-btn spa-btn-danger spa-btn-xs btn-audio-delete">删除</button>
                    </div>
                `).join('')}
            </div>
        </div>`;
}

function bindEvents(container, data, projectId) {
    // 展开/折叠
    container.querySelectorAll('.spa-meeting-head').forEach(head => {
        head.addEventListener('click', () => {
            head.closest('.spa-meeting-card').classList.toggle('expanded');
        });
    });

    // 会议配置编辑切换
    container.querySelectorAll('.btn-edit-meeting').forEach(btn => {
        btn.addEventListener('click', () => {
            const panel = btn.closest('.spa-meeting-config-panel');
            panel.querySelector('.meeting-config-view').style.display = 'none';
            panel.querySelector('.meeting-config-edit').style.display = '';
            btn.style.display = 'none';
        });
    });
    container.querySelectorAll('.btn-cancel-edit-meeting').forEach(btn => {
        btn.addEventListener('click', () => {
            const panel = btn.closest('.spa-meeting-config-panel');
            panel.querySelector('.meeting-config-edit').style.display = 'none';
            panel.querySelector('.meeting-config-view').style.display = '';
            panel.querySelector('.btn-edit-meeting').style.display = '';
        });
    });

    // 会议配置保存
    container.querySelectorAll('.meeting-config-edit form').forEach(form => {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const dir = form.closest('.spa-meeting-card').dataset.dir;
            const btn = form.querySelector('button[type="submit"]');
            btn.disabled = true;
            try {
                const fd = new FormData(form);
                await api.updateMeeting(projectId, dir, Object.fromEntries(fd));
                showToast('会议配置已保存', 'success');
                invalidateCache(projectId);
                await renderProject(projectId, 'meetings');
            } catch (err) {
                showToast(err.message, 'error');
                btn.disabled = false;
            }
        });
    });

    // 文件查看
    container.querySelectorAll('.spa-file-name').forEach(el => {
        el.addEventListener('click', () => {
            const dir = el.closest('.spa-meeting-card').dataset.dir;
            openFile(projectId, dir, el.dataset.file);
        });
    });

    // 查看转写
    container.querySelectorAll('.btn-view-transcript').forEach(btn => {
        btn.addEventListener('click', () => {
            const dir = btn.closest('.spa-meeting-card').dataset.dir;
            openFile(projectId, dir, TRANSCRIPT_FILE);
        });
    });

    // 处理按钮
    container.querySelectorAll('.btn-process').forEach(btn => {
        btn.addEventListener('click', async () => {
            const dir = btn.closest('.spa-meeting-card').dataset.dir;
            const action = btn.dataset.action;
            btn.disabled = true;
            btn.textContent = '启动中...';
            try {
                await api.processMeeting(projectId, dir, action);
                invalidateCache(projectId);
                const { render } = await import('./project-tabs.js');
                await render(projectId, 'meetings');
                _startPolling(projectId, dir);
            } catch (e) {
                showToast(e.message, 'error');
                btn.disabled = false;
                btn.textContent = action === 'full' ? '完整处理' : action === 'minutes' ? '仅处理纪要' : '重新处理';
            }
        });
    });

    // ASR 重试 / 降级按钮
    container.querySelectorAll('.btn-asr-retry').forEach(btn => {
        btn.addEventListener('click', async () => {
            const card = btn.closest('.spa-meeting-card');
            const dir = card.dataset.dir;
            btn.disabled = true;
            btn.textContent = '启动中...';
            try {
                await api.retryASR(projectId, dir);
                showToast('已触发 VibeVoice ASR 重试', 'success');
                invalidateCache(projectId);
                const { render } = await import('./project-tabs.js');
                await render(projectId, 'meetings');
                _startPolling(projectId, dir);
            } catch (e) {
                showToast(e.message, 'error');
                btn.disabled = false;
                btn.textContent = '立即重试';
            }
        });
    });

    container.querySelectorAll('.btn-asr-fallback').forEach(btn => {
        btn.addEventListener('click', async () => {
            const card = btn.closest('.spa-meeting-card');
            const dir = card.dataset.dir;
            if (!confirm('确认改用智谱 ASR 进行转写？')) return;
            btn.disabled = true;
            btn.textContent = '切换中...';
            try {
                await api.fallbackASR(projectId, dir);
                showToast('已切换到智谱 ASR', 'success');
                invalidateCache(projectId);
                const { render } = await import('./project-tabs.js');
                await render(projectId, 'meetings');
                _startPolling(projectId, dir);
            } catch (e) {
                showToast(e.message, 'error');
                btn.disabled = false;
                btn.textContent = '改用智谱 ASR';
            }
        });
    });

    // 启动对处理中会议的轮询
    container.querySelectorAll('.spa-meeting-card').forEach(card => {
        const dir = card.dataset.dir;
        const m = (data.meetings || []).find(x => x.dir_name === dir);
        if (m && m.is_processing) _startPolling(projectId, dir);
    });

    // 音频上传（含空状态和有文件时两种入口）
    container.querySelectorAll('.audio-upload-input').forEach(input => {
        input.addEventListener('change', async () => {
            if (!input.files.length) return;
            const dir = input.closest('.spa-meeting-card').dataset.dir;
            const files = Array.from(input.files);
            input.value = '';
            try {
                const uploaded = await confirmAndUpload(projectId, dir, files);
                if (uploaded) {
                    showToast('音频已上传', 'success');
                    invalidateCache(projectId);
                    const { render } = await import('./project-tabs.js');
                    await render(projectId, 'meetings');
                }
            } catch (e) {
                showToast(e.message, 'error');
            }
        });
    });

    // 音频删除（同时删除转写结果）
    container.querySelectorAll('.btn-audio-delete').forEach(btn => {
        btn.addEventListener('click', async () => {
            const item = btn.closest('.spa-audio-item');
            const card = btn.closest('.spa-meeting-card');
            const dir = card.dataset.dir;
            const name = item.dataset.audio;
            const hasTranscript = card.querySelector('.btn-view-transcript') !== null;
            const msg = hasTranscript
                ? `确认删除音频文件 "${name}"？\n\n相关转写结果也将一并删除。`
                : `确认删除音频文件 "${name}"？`;
            if (!confirm(msg)) return;
            btn.disabled = true;
            try {
                await api.deleteAudio(projectId, dir, name);
                if (hasTranscript) {
                    await api.deleteFile(projectId, dir, TRANSCRIPT_FILE).catch(() => {});
                }
                showToast('已删除', 'success');
                invalidateCache(projectId);
                const { render } = await import('./project-tabs.js');
                await render(projectId, 'meetings');
            } catch (e) {
                showToast(e.message, 'error');
                btn.disabled = false;
            }
        });
    });
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

function enc(s) {
    return encodeURIComponent(s || '');
}

function _formatRetryTime(isoStr) {
    try {
        const d = new Date(isoStr);
        return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch {
        return isoStr;
    }
}
