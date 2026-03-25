/**
 * 项目视图 — Tab 栏 + 内容路由
 */
import { api } from '../api.js';
import { getState, setState } from '../state.js';
import { navigate } from '../router.js';
import { setBreadcrumb } from './topbar.js';
import { renderOverview } from './overview.js';
import { renderMeetings } from './meetings.js';
import { renderGlossary } from './glossary.js';
import { renderBackground } from './background.js';
import { loadSidebar, render as renderSidebar } from './sidebar.js';

const main = document.getElementById('mainContent');

const TABS = [
    { key: 'overview', label: '概览' },
    { key: 'meetings', label: '会议' },
    { key: 'glossary', label: '术语' },
    { key: 'background', label: '背景' },
];

let cachedProjectData = null;
let cachedProjectId = null;

export async function render(projectId, tab = 'overview') {
    setState({ currentProjectId: projectId, currentTab: tab });
    renderSidebar();

    main.innerHTML = '<div class="spa-loading">加载中...</div>';

    try {
        // 仅在切换项目时重新加载
        if (cachedProjectId !== projectId) {
            cachedProjectData = await api.getProject(projectId);
            cachedProjectId = projectId;
        }
        const data = cachedProjectData;
        const projectName = data.project?.name || projectId;

        setBreadcrumb([
            { label: '工作台', hash: 'dashboard' },
            { label: projectName },
        ]);

        const tabBar = TABS.map(t =>
            `<button class="spa-tab ${t.key === tab ? 'active' : ''}" data-tab="${t.key}">${t.label}</button>`
        ).join('');

        main.innerHTML = `
            <div class="spa-tab-bar" id="projectTabBar">${tabBar}</div>
            <div id="tabContent"></div>`;

        main.querySelectorAll('.spa-tab').forEach(btn => {
            btn.addEventListener('click', () => {
                const t = btn.dataset.tab;
                if (t === 'overview') navigate(`project/${projectId}`);
                else navigate(`project/${projectId}/${t}`);
            });
        });

        const tabContent = document.getElementById('tabContent');
        switch (tab) {
            case 'overview':
                renderOverview(tabContent, data, projectId);
                break;
            case 'meetings':
                renderMeetings(tabContent, data, projectId);
                break;
            case 'glossary':
                renderGlossary(tabContent, projectId);
                break;
            case 'background':
                renderBackground(tabContent, projectId);
                break;
        }
    } catch (e) {
        main.innerHTML = `<div class="spa-empty">加载项目失败: ${esc(e.message)}</div>`;
    }
}

export function invalidateCache(projectId) {
    if (!projectId || cachedProjectId === projectId) {
        cachedProjectData = null;
        cachedProjectId = null;
    }
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}
