/**
 * 背景说明 Tab — 结构化 Q&A 列表
 * 每条: topic + question + answer(可选) + source_meeting
 * 状态: answered(有答案) / unanswered(待回答)
 */
import { api } from '../api.js';
import { showToast } from '../toast.js';

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

function entryRowHtml(e) {
    const state = e.is_answered ? 'answered' : 'unanswered';
    const stateLabel = e.is_answered ? '已回答' : '待回答';
    const stateClass = e.is_answered ? 'spa-bstate-answered' : 'spa-bstate-unanswered';
    return `
    <div class="spa-bentry-row" data-id="${esc(e.id)}" data-state="${state}">
        <div class="spa-bentry-view">
            <div class="spa-bentry-head">
                <span class="spa-bentry-topic">${esc(e.topic)}</span>
                <span class="spa-bentry-badge ${stateClass}">${stateLabel}</span>
                ${e.source_meeting ? `<span class="spa-bentry-source">${esc(e.source_meeting)}</span>` : ''}
            </div>
            <div class="spa-bentry-question">${esc(e.question)}</div>
            ${e.is_answered ? `<div class="spa-bentry-answer">${esc(e.answer)}</div>` : ''}
            <div class="spa-bentry-actions">
                <button class="spa-btn spa-btn-outline spa-btn-xs btn-b-edit">${e.is_answered ? '编辑' : '回答'}</button>
            </div>
        </div>
        <div class="spa-bentry-edit" style="display:none;">
            <div class="spa-form-sm">
                <div class="spa-form-group">
                    <label>标题</label>
                    <input class="bin-topic" value="${esc(e.topic)}">
                </div>
                <div class="spa-form-group">
                    <label>问题描述</label>
                    <textarea class="bin-question" rows="2">${esc(e.question)}</textarea>
                </div>
                <div class="spa-form-group">
                    <label>解答</label>
                    <textarea class="bin-answer" rows="3" placeholder="在此填写解答...">${esc(e.answer || '')}</textarea>
                </div>
                <div class="spa-form-actions" style="margin-top:0.4rem;">
                    <button class="spa-btn spa-btn-primary spa-btn-xs btn-b-save">保存</button>
                    <button class="spa-btn spa-btn-xs btn-b-cancel">取消</button>
                    <button class="spa-btn spa-btn-danger spa-btn-xs btn-b-delete" style="margin-left:auto;">删除</button>
                </div>
            </div>
        </div>
    </div>`;
}

function addEntryFormHtml() {
    return `
    <div class="spa-bentry-add-wrap">
        <button class="spa-btn spa-btn-outline spa-btn-sm" id="btnShowAddEntry">+ 新增问题</button>
        <div id="addEntryForm" style="display:none;" class="spa-panel spa-bentry-add-form">
            <div class="spa-form-sm">
                <div class="spa-form-group">
                    <label>标题 *</label>
                    <input id="addTopic" placeholder="概念/问题的简短名称">
                </div>
                <div class="spa-form-group">
                    <label>问题描述</label>
                    <textarea id="addQuestion" rows="2" placeholder="详细描述这个概念或问题..."></textarea>
                </div>
                <div class="spa-form-group">
                    <label>解答（可选，稍后填写）</label>
                    <textarea id="addAnswer" rows="2" placeholder="在此填写解答，留空则为待回答状态..."></textarea>
                </div>
                <div class="spa-form-actions">
                    <button class="spa-btn spa-btn-primary spa-btn-sm" id="btnAddEntrySave">添加</button>
                    <button class="spa-btn spa-btn-sm" id="btnAddEntryCancel">取消</button>
                </div>
            </div>
        </div>
    </div>`;
}

export async function renderBackground(container, projectId) {
    container.innerHTML = '<div class="spa-loading">加载中...</div>';
    try {
        const data = await api.getBackground(projectId);
        _render(container, data, projectId);
    } catch (e) {
        container.innerHTML = `<div class="spa-empty">加载失败: ${esc(e.message)}</div>`;
    }
}

function _render(container, data, projectId) {
    const entries = data.entries || [];
    const answered   = entries.filter(e => e.is_answered).length;
    const unanswered = entries.filter(e => !e.is_answered).length;

    container.innerHTML = `
        <div class="spa-panel">
            <div class="spa-bentry-header">
                <div style="display:flex;gap:0.6rem;align-items:center;flex-wrap:wrap;">
                    <h4 style="margin:0;">背景说明</h4>
                    ${answered   ? `<span class="spa-pill spa-pill-accent">${answered} 已回答</span>` : ''}
                    ${unanswered ? `<span class="spa-pill spa-pill-warn">${unanswered} 待回答</span>` : ''}
                </div>
                <div class="spa-bentry-filters">
                    <button class="spa-btn spa-btn-xs spa-btn-filter active" data-filter="all">全部</button>
                    <button class="spa-btn spa-btn-xs spa-btn-filter" data-filter="answered">已回答</button>
                    <button class="spa-btn spa-btn-xs spa-btn-filter" data-filter="unanswered">待回答</button>
                </div>
            </div>
            ${addEntryFormHtml()}
            <div id="bentryList" style="margin-top:0.6rem;">
                ${entries.length
                    ? entries.map(entryRowHtml).join('')
                    : '<div class="spa-empty" style="padding:1.5rem 0;">暂无背景说明，可点击"新增问题"或待 AI 自动提取</div>'
                }
            </div>
        </div>`;

    _bindEvents(container, projectId);
}

function _bindEvents(container, projectId) {
    const reload = () => renderBackground(container, projectId);

    /* ---- Filter ---- */
    container.querySelectorAll('.spa-btn-filter').forEach(btn => {
        btn.addEventListener('click', () => {
            container.querySelectorAll('.spa-btn-filter').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const filter = btn.dataset.filter;
            container.querySelectorAll('.spa-bentry-row').forEach(row => {
                row.style.display = (filter === 'all' || row.dataset.state === filter) ? '' : 'none';
            });
        });
    });

    /* ---- Add entry ---- */
    const btnShow  = container.querySelector('#btnShowAddEntry');
    const addForm  = container.querySelector('#addEntryForm');
    btnShow.addEventListener('click', () => {
        addForm.style.display = addForm.style.display === 'none' ? '' : 'none';
    });
    container.querySelector('#btnAddEntryCancel').addEventListener('click', () => {
        addForm.style.display = 'none';
    });
    container.querySelector('#btnAddEntrySave').addEventListener('click', async () => {
        const topic = container.querySelector('#addTopic').value.trim();
        if (!topic) { showToast('标题不能为空', 'error'); return; }
        const btn = container.querySelector('#btnAddEntrySave');
        btn.disabled = true;
        try {
            await api.addBackgroundEntry(projectId, {
                topic,
                question: container.querySelector('#addQuestion').value,
                answer:   container.querySelector('#addAnswer').value,
            });
            showToast('已添加', 'success');
            await reload();
        } catch (e) {
            showToast(e.message, 'error');
            btn.disabled = false;
        }
    });

    /* ---- Row actions (delegated) ---- */
    const list = container.querySelector('#bentryList');
    list.addEventListener('click', async (e) => {
        const row = e.target.closest('.spa-bentry-row');
        if (!row) return;
        const entryId = row.dataset.id;

        if (e.target.classList.contains('btn-b-edit')) {
            row.querySelector('.spa-bentry-view').style.display = 'none';
            row.querySelector('.spa-bentry-edit').style.display = '';
            row.querySelector('.bin-answer').focus();
            return;
        }

        if (e.target.classList.contains('btn-b-cancel')) {
            row.querySelector('.spa-bentry-edit').style.display = 'none';
            row.querySelector('.spa-bentry-view').style.display = '';
            return;
        }

        if (e.target.classList.contains('btn-b-save')) {
            const btn = e.target;
            btn.disabled = true;
            try {
                await api.updateBackgroundEntry(projectId, entryId, {
                    topic:    row.querySelector('.bin-topic').value.trim(),
                    question: row.querySelector('.bin-question').value,
                    answer:   row.querySelector('.bin-answer').value,
                });
                showToast('已保存', 'success');
                await reload();
            } catch (err) {
                showToast(err.message, 'error');
                btn.disabled = false;
            }
            return;
        }

        if (e.target.classList.contains('btn-b-delete')) {
            const topic = row.querySelector('.spa-bentry-topic')?.textContent || entryId;
            if (!confirm(`确认删除「${topic}」？`)) return;
            e.target.disabled = true;
            try {
                await api.deleteBackgroundEntry(projectId, entryId);
                showToast('已删除', 'success');
                await reload();
            } catch (err) {
                showToast(err.message, 'error');
                e.target.disabled = false;
            }
            return;
        }
    });
}
