/**
 * 术语 Tab — 编辑器 + 待审/已确认/已拒绝
 */
import { api } from '../api.js';
import { showToast } from '../toast.js';

export async function renderGlossary(container, projectId) {
    container.innerHTML = '<div class="spa-loading">加载中...</div>';

    try {
        const data = await api.getGlossary(projectId);
        const pending = data.pending_terms || [];
        const confirmed = data.confirmed_terms || [];
        const rejected = data.rejected_terms || [];

        container.innerHTML = `
            <div class="spa-glossary-grid">
                <div class="spa-glossary-editor">
                    <div class="spa-panel">
                        <h4>术语编辑器</h4>
                        <p style="font-size:0.78rem;color:var(--muted);margin-bottom:0.5rem;">
                            每行一个术语，用 | 分隔别名。如: MeetingEZ | meeting ez | 米听易
                        </p>
                        <textarea id="glossaryEditorText">${esc(data.editor_text || '')}</textarea>
                        <div style="margin-top:0.6rem;">
                            <button class="spa-btn spa-btn-primary spa-btn-sm" id="glossarySaveBtn">保存术语表</button>
                        </div>
                    </div>
                </div>

                <div class="spa-glossary-sidebar">
                    <div class="spa-panel">
                        <h4>待审核 (${pending.length})</h4>
                        ${pending.length ? pending.map(t => `
                            <div class="spa-term-card" data-canonical="${esc(t.canonical)}">
                                <div class="spa-term-canonical">${esc(t.canonical)}</div>
                                ${t.aliases?.length ? `<div class="spa-term-aliases">${t.aliases.map(esc).join(', ')}</div>` : ''}
                                <div class="spa-term-meta">频率: ${t.frequency || 0}${t.source_meeting ? ` · 来源: ${esc(t.source_meeting)}` : ''}</div>
                                <div class="spa-term-actions">
                                    <button class="spa-btn spa-btn-primary spa-btn-xs btn-term-approve">确认</button>
                                    <button class="spa-btn spa-btn-outline spa-btn-xs btn-term-reject">拒绝</button>
                                </div>
                            </div>
                        `).join('') : '<div class="spa-empty" style="padding:0.5rem 0;">无待审核术语</div>'}
                    </div>

                    <div class="spa-panel">
                        <h4>已确认 (${confirmed.length})</h4>
                        ${confirmed.length ? `
                            <div class="spa-tag-list">
                                ${confirmed.map(t => `<span class="spa-tag">${esc(t.canonical)}</span>`).join('')}
                            </div>
                        ` : '<div class="spa-empty" style="padding:0.5rem 0;">暂无</div>'}
                    </div>

                    ${rejected.length ? `
                    <div class="spa-panel">
                        <h4>已拒绝 (${rejected.length})</h4>
                        ${rejected.map(t => `
                            <div style="font-size:0.82rem;padding:0.2rem 0;color:var(--muted);">
                                ${esc(t.canonical)}${t.reason ? ` — ${esc(t.reason)}` : ''}
                            </div>
                        `).join('')}
                    </div>` : ''}
                </div>
            </div>`;

        // 保存
        document.getElementById('glossarySaveBtn').addEventListener('click', async () => {
            const btn = document.getElementById('glossarySaveBtn');
            btn.disabled = true;
            try {
                const text = document.getElementById('glossaryEditorText').value;
                await api.saveGlossary(projectId, text);
                showToast('术语表已保存', 'success');
                await renderGlossary(container, projectId);
            } catch (e) {
                showToast(e.message, 'error');
            } finally {
                btn.disabled = false;
            }
        });

        // 批准
        container.querySelectorAll('.btn-term-approve').forEach(btn => {
            btn.addEventListener('click', async () => {
                const card = btn.closest('.spa-term-card');
                const canonical = card.dataset.canonical;
                btn.disabled = true;
                try {
                    await api.approveGlossaryTerm(projectId, canonical);
                    showToast(`已确认: ${canonical}`, 'success');
                    await renderGlossary(container, projectId);
                } catch (e) {
                    showToast(e.message, 'error');
                    btn.disabled = false;
                }
            });
        });

        // 拒绝
        container.querySelectorAll('.btn-term-reject').forEach(btn => {
            btn.addEventListener('click', async () => {
                const card = btn.closest('.spa-term-card');
                const canonical = card.dataset.canonical;
                const reason = prompt('拒绝原因 (可选):') || '';
                btn.disabled = true;
                try {
                    await api.rejectGlossaryTerm(projectId, canonical, reason);
                    showToast(`已拒绝: ${canonical}`, 'success');
                    await renderGlossary(container, projectId);
                } catch (e) {
                    showToast(e.message, 'error');
                    btn.disabled = false;
                }
            });
        });
    } catch (e) {
        container.innerHTML = `<div class="spa-empty">加载术语失败: ${esc(e.message)}</div>`;
    }
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}
