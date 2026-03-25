/**
 * 仪表盘视图
 */
import { api } from '../api.js';
import { navigate } from '../router.js';
import { setBreadcrumb } from './topbar.js';

const main = document.getElementById('mainContent');

export async function render() {
    setBreadcrumb([{ label: '工作台' }]);
    main.innerHTML = '<div class="spa-loading">加载中...</div>';

    try {
        const data = await api.getDashboard();
        const s = data.workspace_summary || {};
        // 过滤掉内部默认项目，只展示用户真实创建的项目
        const projects = (data.projects || []).filter(p => !p.is_default && p.id !== '__default__');
        const canCreate = data.can_create_project;

        if (!projects.length) {
            main.innerHTML = `
                <div class="spa-empty" style="padding:3rem 1rem;text-align:center;">
                    <p style="font-size:1rem;color:var(--text);margin-bottom:0.6rem;">暂无项目</p>
                    <p style="color:var(--muted);font-size:0.88rem;">
                        ${canCreate
                            ? '点击左侧 <strong style="color:var(--accent)">+</strong> 新建项目，或直接'
                            : ''}
                        <a href="/realtime?mode=quick" style="color:var(--accent);">启动快速转写（无需关联项目）</a>
                    </p>
                </div>`;
            return;
        }

        main.innerHTML = `
            <div class="spa-dashboard-stats">
                <div class="spa-stat-card">
                    <div class="spa-stat-value">${projects.length}</div>
                    <div class="spa-stat-label">项目</div>
                </div>
                <div class="spa-stat-card">
                    <div class="spa-stat-value">${s.meeting_count || 0}</div>
                    <div class="spa-stat-label">会议</div>
                </div>
                <div class="spa-stat-card">
                    <div class="spa-stat-value">${s.pending_count || 0}</div>
                    <div class="spa-stat-label">待处理</div>
                </div>
            </div>
            <div class="spa-section-head">
                <h3>全部项目</h3>
            </div>
            <div class="spa-project-grid" id="dashProjectGrid">
                ${projects.map(p => projectCard(p)).join('')}
            </div>`;

        main.querySelectorAll('.spa-project-card').forEach(el => {
            el.addEventListener('click', () => navigate(`project/${el.dataset.id}`));
        });
    } catch {
        main.innerHTML = `<div class="spa-empty">加载失败，请刷新重试</div>`;
    }
}

function projectCard(p) {
    const pending = (p.pending_asr || 0) + (p.pending_minutes || 0);
    return `
        <div class="spa-project-card" data-id="${esc(p.id)}">
            <h3>${esc(p.name)}</h3>
            <p>${esc(p.description) || '暂无描述'}</p>
            <div class="spa-project-card-stats">
                <span>${p.meeting_count || 0} 会议</span>
                ${pending ? `<span class="spa-badge-pending">${pending} 待处理</span>` : ''}
                ${p.glossary_confirmed ? `<span>${p.glossary_confirmed} 术语</span>` : ''}
            </div>
        </div>`;
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}
