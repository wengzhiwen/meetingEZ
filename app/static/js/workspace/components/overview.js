/**
 * 项目概览 Tab
 */
import { api } from '../api.js';
import { showToast } from '../toast.js';
import { invalidateCache, render as renderProject } from './project-tabs.js';
import { loadSidebar } from './sidebar.js';

const MEETING_TYPE_LABELS = {
    review: '评审会', weekly: '周会', brainstorm: '头脑风暴',
    retro: '复盘会', kickoff: '启动会', other: '其他',
};

export function renderOverview(container, data, projectId) {
    const p = data.project || {};
    const actions = data.recent_actions || [];
    const typeOptions = (data.meeting_type_options || []).map(([v, l]) =>
        `<option value="${esc(v)}">${esc(l)}</option>`).join('');
    const langOptions = (data.language_options || []).map(([v, l]) =>
        `<option value="${esc(v)}">${esc(l)}</option>`).join('');
    const today = new Date().toISOString().slice(0, 10);

    container.innerHTML = `
        <div class="spa-overview-grid">
            <div class="spa-panel">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.6rem;">
                    <h4 style="margin:0;">项目信息</h4>
                    <button class="spa-btn spa-btn-sm" id="editProjectBtn">编辑</button>
                </div>
                <div id="projectInfoView">
                    <div class="spa-kv-list">
                        <div class="spa-kv"><span class="spa-kv-label">名称</span><span class="spa-kv-value">${esc(p.name)}</span></div>
                        <div class="spa-kv"><span class="spa-kv-label">描述</span><span class="spa-kv-value">${esc(p.description) || '-'}</span></div>
                        <div class="spa-kv"><span class="spa-kv-label">团队</span><span class="spa-kv-value">${(p.team || []).map(esc).join('、') || '-'}</span></div>
                        <div class="spa-kv"><span class="spa-kv-label">开始日期</span><span class="spa-kv-value">${esc(p.start_date) || '-'}</span></div>
                    </div>
                </div>
                <div id="projectInfoEdit" style="display:none;">
                    <form class="spa-form" id="editProjectForm">
                        <div class="spa-form-group">
                            <label>名称 *</label>
                            <input name="name" required value="${esc(p.name)}">
                        </div>
                        <div class="spa-form-group">
                            <label>描述</label>
                            <input name="description" value="${esc(p.description || '')}">
                        </div>
                        <div class="spa-form-group">
                            <label>团队成员（逗号分隔）</label>
                            <input name="team" value="${esc((p.team || []).join(', '))}">
                        </div>
                        <div class="spa-form-group">
                            <label>开始日期</label>
                            <input name="start_date" type="date" value="${esc(p.start_date || '')}">
                        </div>
                        <div class="spa-form-actions">
                            <button type="submit" class="spa-btn spa-btn-primary spa-btn-sm">保存</button>
                            <button type="button" class="spa-btn spa-btn-sm" id="cancelEditBtn">取消</button>
                        </div>
                    </form>
                </div>
                <div class="spa-stat-pills" style="margin-top:0.8rem;">
                    <span class="spa-pill spa-pill-accent">${p.meeting_count || 0} 会议</span>
                    ${p.pending_asr ? `<span class="spa-pill spa-pill-warn">${p.pending_asr} 待转写</span>` : ''}
                    ${p.pending_minutes ? `<span class="spa-pill spa-pill-warn">${p.pending_minutes} 待纪要</span>` : ''}
                    ${p.glossary_confirmed ? `<span class="spa-pill">${p.glossary_confirmed} 术语</span>` : ''}
                    ${p.actions_overdue ? `<span class="spa-pill spa-pill-warn">${p.actions_overdue} 逾期</span>` : ''}
                </div>
            </div>

            <div class="spa-panel">
                <h4>最近行动项</h4>
                ${actions.length ? `
                    <div class="spa-action-list">
                        ${actions.map(a => `
                            <div class="spa-action-row">
                                <span class="spa-action-id">${esc(a.id)}</span>
                                <span class="spa-action-task">${esc(a.task)}</span>
                                <span class="spa-action-owner">${esc(a.owner)}</span>
                            </div>
                        `).join('')}
                    </div>
                ` : '<div class="spa-empty" style="padding:1rem 0;">暂无行动项</div>'}
            </div>
        </div>

        <div style="margin-top:1.3rem;">
            <div class="spa-panel">
                <h4>新建会议</h4>
                <form class="spa-form" id="createMeetingForm">
                    <div class="spa-form-row">
                        <div class="spa-form-group">
                            <label>标题 *</label>
                            <input name="title" required placeholder="会议标题">
                        </div>
                        <div class="spa-form-group">
                            <label>日期 *</label>
                            <input name="meeting_date" type="date" required value="${today}">
                        </div>
                    </div>
                    <div class="spa-form-row">
                        <div class="spa-form-group">
                            <label>类型</label>
                            <select name="meeting_type">${typeOptions}</select>
                        </div>
                        <div class="spa-form-group">
                            <label>语言模式</label>
                            <select name="language_mode">
                                <option value="single_primary">单主语言</option>
                                <option value="bilingual">双语</option>
                            </select>
                        </div>
                    </div>
                    <div class="spa-form-row">
                        <div class="spa-form-group">
                            <label>主要语言</label>
                            <select name="primary_language">${langOptions}</select>
                        </div>
                        <div class="spa-form-group">
                            <label>第二语言</label>
                            <select name="secondary_language">
                                <option value="">无</option>
                                ${langOptions}
                            </select>
                        </div>
                    </div>
                    <div class="spa-form-group">
                        <label>备注</label>
                        <input name="notes" placeholder="可选">
                    </div>
                    <div class="spa-form-actions">
                        <button type="submit" class="spa-btn spa-btn-primary">创建会议</button>
                    </div>
                </form>
            </div>
        </div>`;

    // Project info edit toggle
    const editBtn = document.getElementById('editProjectBtn');
    const viewEl = document.getElementById('projectInfoView');
    const editEl = document.getElementById('projectInfoEdit');
    const cancelBtn = document.getElementById('cancelEditBtn');

    editBtn.addEventListener('click', () => {
        viewEl.style.display = 'none';
        editEl.style.display = '';
        editBtn.style.display = 'none';
    });
    cancelBtn.addEventListener('click', () => {
        editEl.style.display = 'none';
        viewEl.style.display = '';
        editBtn.style.display = '';
    });
    document.getElementById('editProjectForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const form = e.target;
        const btn = form.querySelector('button[type="submit"]');
        btn.disabled = true;
        try {
            const fd = new FormData(form);
            await api.updateProject(projectId, Object.fromEntries(fd));
            showToast('项目信息已保存', 'success');
            invalidateCache(projectId);
            await renderProject(projectId, 'overview');
            loadSidebar();
        } catch (err) {
            showToast(err.message, 'error');
            btn.disabled = false;
        }
    });

    document.getElementById('createMeetingForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const form = e.target;
        const btn = form.querySelector('button[type="submit"]');
        btn.disabled = true;
        try {
            const fd = new FormData(form);
            await api.createMeeting(projectId, Object.fromEntries(fd));
            showToast('会议已创建', 'success');
            form.reset();
            form.querySelector('[name="meeting_date"]').value = today;
            invalidateCache(projectId);
            await renderProject(projectId, 'meetings');
            loadSidebar();
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            btn.disabled = false;
        }
    });
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}
