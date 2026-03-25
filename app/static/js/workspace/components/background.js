/**
 * 背景 Tab — 编辑器 + 提示
 */
import { api } from '../api.js';
import { showToast } from '../toast.js';

export async function renderBackground(container, projectId) {
    container.innerHTML = '<div class="spa-loading">加载中...</div>';

    try {
        const data = await api.getBackground(projectId);

        container.innerHTML = `
            <div class="spa-background-grid">
                <div class="spa-background-editor">
                    <div class="spa-panel">
                        <h4>背景说明</h4>
                        <textarea id="backgroundEditorText" placeholder="补充项目背景、团队角色、业务流程、核心概念等...">${esc(data.content || '')}</textarea>
                        <div style="margin-top:0.6rem;">
                            <button class="spa-btn spa-btn-primary spa-btn-sm" id="backgroundSaveBtn">保存背景说明</button>
                        </div>
                    </div>
                </div>

                <div>
                    <div class="spa-panel">
                        <h4>维护建议</h4>
                        <div class="spa-tip-card">
                            <strong>核心概念</strong>
                            <p style="font-size:0.82rem;color:var(--muted);margin-top:0.2rem;">
                                解释术语的业务含义、英文缩写以及为什么重要
                            </p>
                        </div>
                        <div class="spa-tip-card">
                            <strong>业务流程</strong>
                            <p style="font-size:0.82rem;color:var(--muted);margin-top:0.2rem;">
                                写清楚一场会议里经常提到的流程、角色和上下游关系
                            </p>
                        </div>
                        <div class="spa-tip-card">
                            <strong>团队角色</strong>
                            <p style="font-size:0.82rem;color:var(--muted);margin-top:0.2rem;">
                                记录关键人物、团队边界和常见职责，方便后续纪要更准确
                            </p>
                        </div>
                    </div>
                </div>
            </div>`;

        document.getElementById('backgroundSaveBtn').addEventListener('click', async () => {
            const btn = document.getElementById('backgroundSaveBtn');
            btn.disabled = true;
            try {
                const content = document.getElementById('backgroundEditorText').value;
                await api.saveBackground(projectId, content);
                showToast('背景说明已保存', 'success');
            } catch (e) {
                showToast(e.message, 'error');
            } finally {
                btn.disabled = false;
            }
        });
    } catch (e) {
        container.innerHTML = `<div class="spa-empty">加载失败: ${esc(e.message)}</div>`;
    }
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}
