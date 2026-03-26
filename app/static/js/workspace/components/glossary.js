/**
 * 术语 Tab — 统一列表，每条术语可在 确认/待审核/已拒绝 三种状态间流转
 */
import { api } from '../api.js';
import { showToast } from '../toast.js';

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

const TYPE_LABELS = { person: '人名', technical: '技术', product: '产品', project: '项目', abbr: '缩写', other: '其他' };
const TYPE_OPTIONS = ['person', 'technical', 'product', 'project', 'abbr', 'other'];
const STATE_LABEL  = { confirmed: '已确认', pending: '待审核', rejected: '已拒绝' };

function typeSelectHtml(selected) {
    return TYPE_OPTIONS.map(v =>
        `<option value="${v}"${v === selected ? ' selected' : ''}>${TYPE_LABELS[v] || v}</option>`
    ).join('');
}

function termRowHtml(t) {
    const aliases = (t.aliases || []).join(', ');
    const stateClass = `spa-gstate-${t.state}`;
    let actions = '';
    if (t.state === 'confirmed') {
        actions = `
            <button class="spa-btn spa-btn-outline spa-btn-xs btn-g-edit">编辑</button>
            <button class="spa-btn spa-btn-xs spa-btn-muted btn-g-revert" data-from="confirmed" title="回退到待审核">↩ 待审核</button>`;
    } else if (t.state === 'pending') {
        actions = `
            <button class="spa-btn spa-btn-primary spa-btn-xs btn-g-approve">确认</button>
            <button class="spa-btn spa-btn-outline spa-btn-xs btn-g-reject">拒绝</button>`;
    } else if (t.state === 'rejected') {
        actions = `
            <button class="spa-btn spa-btn-xs spa-btn-muted btn-g-revert" data-from="rejected" title="回退到待审核">↩ 待审核</button>`;
    }

    return `
    <div class="spa-gterm-row" data-canonical="${esc(t.canonical)}" data-state="${t.state}">
        <div class="spa-gterm-row-view">
            <div class="spa-gterm-row-main">
                <span class="spa-gterm-canonical">${esc(t.canonical)}</span>
                <span class="spa-gterm-badge ${stateClass}">${STATE_LABEL[t.state]}</span>
                <span class="spa-gterm-type spa-gterm-type-${esc(t.type || 'other')}">${esc(TYPE_LABELS[t.type] || t.type || '')}</span>
            </div>
            ${aliases ? `<div class="spa-gterm-aliases">别名: ${esc(aliases)}</div>` : ''}
            ${t.context ? `<div class="spa-gterm-context">${esc(t.context)}</div>` : ''}
            ${t.reason ? `<div class="spa-gterm-reason">拒绝原因: ${esc(t.reason)}</div>` : ''}
            ${t.source_meeting ? `<div class="spa-gterm-source">来源: ${esc(t.source_meeting)}</div>` : ''}
            <div class="spa-gterm-row-actions">${actions}</div>
        </div>
        <div class="spa-gterm-row-edit" style="display:none;">
            <div class="spa-form-sm">
                <div class="spa-form-row">
                    <div class="spa-form-group">
                        <label>术语名称</label>
                        <input class="gin-canonical" value="${esc(t.canonical)}">
                    </div>
                    <div class="spa-form-group">
                        <label>类型</label>
                        <select class="gin-type">${typeSelectHtml(t.type)}</select>
                    </div>
                </div>
                <div class="spa-form-group">
                    <label>别名（逗号分隔）</label>
                    <input class="gin-aliases" value="${esc(aliases)}">
                </div>
                <div class="spa-form-group">
                    <label>备注/上下文</label>
                    <input class="gin-context" value="${esc(t.context || '')}">
                </div>
                <div class="spa-form-actions" style="margin-top:0.4rem;">
                    <button class="spa-btn spa-btn-primary spa-btn-xs btn-g-save">保存</button>
                    <button class="spa-btn spa-btn-xs btn-g-cancel">取消</button>
                    <button class="spa-btn spa-btn-danger spa-btn-xs btn-g-delete" style="margin-left:auto;">删除</button>
                </div>
            </div>
        </div>
    </div>`;
}

function addTermFormHtml() {
    return `
    <div class="spa-gterm-add-wrap">
        <button class="spa-btn spa-btn-outline spa-btn-sm" id="btnShowAddTerm">+ 手动添加术语</button>
        <div id="addTermForm" style="display:none;" class="spa-panel spa-gterm-add-form">
            <div class="spa-form-sm">
                <div class="spa-form-row">
                    <div class="spa-form-group">
                        <label>术语名称 *</label>
                        <input id="addCanonical" placeholder="标准名称">
                    </div>
                    <div class="spa-form-group">
                        <label>类型</label>
                        <select id="addType">${typeSelectHtml('other')}</select>
                    </div>
                </div>
                <div class="spa-form-group">
                    <label>别名（逗号分隔）</label>
                    <input id="addAliases" placeholder="别名1, 别名2">
                </div>
                <div class="spa-form-group">
                    <label>备注/上下文</label>
                    <input id="addContext" placeholder="可选">
                </div>
                <div class="spa-form-actions">
                    <button class="spa-btn spa-btn-primary spa-btn-sm" id="btnAddTermSave">添加</button>
                    <button class="spa-btn spa-btn-sm" id="btnAddTermCancel">取消</button>
                </div>
            </div>
        </div>
    </div>`;
}

export async function renderGlossary(container, projectId) {
    container.innerHTML = '<div class="spa-loading">加载中...</div>';
    try {
        const data = await api.getGlossary(projectId);
        _render(container, data, projectId);
    } catch (e) {
        container.innerHTML = `<div class="spa-empty">加载术语失败: ${esc(e.message)}</div>`;
    }
}

function _render(container, data, projectId) {
    const terms = data.terms || [];
    const confirmed = terms.filter(t => t.state === 'confirmed').length;
    const pending   = terms.filter(t => t.state === 'pending').length;
    const rejected  = terms.filter(t => t.state === 'rejected').length;

    container.innerHTML = `
        <div class="spa-panel">
            <div class="spa-gterm-header">
                <div style="display:flex;gap:0.6rem;align-items:center;flex-wrap:wrap;">
                    <h4 style="margin:0;">术语列表</h4>
                    <span class="spa-pill spa-pill-accent">${confirmed} 已确认</span>
                    ${pending ? `<span class="spa-pill spa-pill-warn">${pending} 待审核</span>` : ''}
                    ${rejected ? `<span class="spa-pill">${rejected} 已拒绝</span>` : ''}
                </div>
                <div class="spa-gterm-filters">
                    <button class="spa-btn spa-btn-xs spa-btn-filter active" data-filter="all">全部</button>
                    <button class="spa-btn spa-btn-xs spa-btn-filter" data-filter="confirmed">已确认</button>
                    <button class="spa-btn spa-btn-xs spa-btn-filter" data-filter="pending">待审核</button>
                    <button class="spa-btn spa-btn-xs spa-btn-filter" data-filter="rejected">已拒绝</button>
                </div>
            </div>
            ${addTermFormHtml()}
            <div id="gtermList" style="margin-top:0.6rem;">
                ${terms.length
                    ? terms.map(termRowHtml).join('')
                    : '<div class="spa-empty" style="padding:1.5rem 0;">暂无术语</div>'
                }
            </div>
        </div>`;

    _bindEvents(container, projectId);
}

function _bindEvents(container, projectId) {
    const reload = async () => {
        const activeFilter = container.querySelector('.spa-btn-filter.active')?.dataset.filter || 'all';
        const scrollEl = container.closest('.spa-tab-content') || container.parentElement;
        const scrollTop = scrollEl ? scrollEl.scrollTop : 0;
        try {
            const data = await api.getGlossary(projectId);
            _render(container, data, projectId);
            const fb = container.querySelector(`.spa-btn-filter[data-filter="${activeFilter}"]`);
            if (fb) {
                container.querySelectorAll('.spa-btn-filter').forEach(b => b.classList.remove('active'));
                fb.classList.add('active');
                if (activeFilter !== 'all') {
                    container.querySelectorAll('.spa-gterm-row').forEach(row => {
                        row.style.display = row.dataset.state === activeFilter ? '' : 'none';
                    });
                }
            }
            if (scrollEl) requestAnimationFrame(() => { scrollEl.scrollTop = scrollTop; });
        } catch (e) {
            showToast('刷新失败: ' + e.message, 'error');
        }
    };

    /* ---- Filter buttons ---- */
    container.querySelectorAll('.spa-btn-filter').forEach(btn => {
        btn.addEventListener('click', () => {
            container.querySelectorAll('.spa-btn-filter').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const filter = btn.dataset.filter;
            container.querySelectorAll('.spa-gterm-row').forEach(row => {
                row.style.display = (filter === 'all' || row.dataset.state === filter) ? '' : 'none';
            });
        });
    });

    /* ---- Add term ---- */
    const btnShow = container.querySelector('#btnShowAddTerm');
    const addForm = container.querySelector('#addTermForm');
    btnShow.addEventListener('click', () => {
        addForm.style.display = addForm.style.display === 'none' ? '' : 'none';
    });
    container.querySelector('#btnAddTermCancel').addEventListener('click', () => {
        addForm.style.display = 'none';
    });
    container.querySelector('#btnAddTermSave').addEventListener('click', async () => {
        const canonical = container.querySelector('#addCanonical').value.trim();
        if (!canonical) { showToast('术语名称不能为空', 'error'); return; }
        const btn = container.querySelector('#btnAddTermSave');
        btn.disabled = true;
        try {
            await api.addGlossaryEntry(projectId, {
                canonical,
                aliases: container.querySelector('#addAliases').value,
                type: container.querySelector('#addType').value,
                context: container.querySelector('#addContext').value,
            });
            showToast(`已添加: ${canonical}`, 'success');
            await reload();
        } catch (e) {
            showToast(e.message, 'error');
            btn.disabled = false;
        }
    });

    /* ---- Term row actions (delegated) ---- */
    const list = container.querySelector('#gtermList');
    list.addEventListener('click', async (e) => {
        const row = e.target.closest('.spa-gterm-row');
        if (!row) return;
        const canonical = row.dataset.canonical;
        const state = row.dataset.state;

        // 编辑（已确认）
        if (e.target.classList.contains('btn-g-edit')) {
            row.querySelector('.spa-gterm-row-view').style.display = 'none';
            row.querySelector('.spa-gterm-row-edit').style.display = '';
            return;
        }

        // 取消编辑
        if (e.target.classList.contains('btn-g-cancel')) {
            row.querySelector('.spa-gterm-row-edit').style.display = 'none';
            row.querySelector('.spa-gterm-row-view').style.display = '';
            return;
        }

        // 保存编辑
        if (e.target.classList.contains('btn-g-save')) {
            const btn = e.target;
            btn.disabled = true;
            try {
                await api.updateGlossaryEntry(projectId, canonical, {
                    canonical: row.querySelector('.gin-canonical').value.trim(),
                    aliases: row.querySelector('.gin-aliases').value,
                    type: row.querySelector('.gin-type').value,
                    context: row.querySelector('.gin-context').value,
                });
                showToast('已保存', 'success');
                await reload();
            } catch (err) {
                showToast(err.message, 'error');
                btn.disabled = false;
            }
            return;
        }

        // 删除（在编辑区）
        if (e.target.classList.contains('btn-g-delete')) {
            if (!confirm(`确认删除术语「${canonical}」？`)) return;
            e.target.disabled = true;
            try {
                await api.deleteGlossaryEntry(projectId, canonical);
                showToast(`已删除: ${canonical}`, 'success');
                await reload();
            } catch (err) {
                showToast(err.message, 'error');
                e.target.disabled = false;
            }
            return;
        }

        // 确认（待审核）
        if (e.target.classList.contains('btn-g-approve')) {
            e.target.disabled = true;
            try {
                await api.approveGlossaryTerm(projectId, canonical);
                showToast(`已确认: ${canonical}`, 'success');
                await reload();
            } catch (err) {
                showToast(err.message, 'error');
                e.target.disabled = false;
            }
            return;
        }

        // 拒绝（待审核）
        if (e.target.classList.contains('btn-g-reject')) {
            const reason = prompt('拒绝原因 (可选):') || '';
            e.target.disabled = true;
            try {
                await api.rejectGlossaryTerm(projectId, canonical, reason);
                showToast(`已拒绝: ${canonical}`, 'success');
                await reload();
            } catch (err) {
                showToast(err.message, 'error');
                e.target.disabled = false;
            }
            return;
        }

        // 回退到待审核（已确认 / 已拒绝）
        if (e.target.classList.contains('btn-g-revert')) {
            const fromState = e.target.dataset.from;
            e.target.disabled = true;
            try {
                await api.revertGlossaryTerm(projectId, canonical, fromState);
                showToast(`已回退至待审核: ${canonical}`, 'success');
                await reload();
            } catch (err) {
                showToast(err.message, 'error');
                e.target.disabled = false;
            }
            return;
        }
    });
}
