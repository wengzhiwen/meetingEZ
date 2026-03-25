/**
 * 侧边栏 — 项目列表
 */
import { api } from '../api.js';
import { getState, setState } from '../state.js';
import { navigate } from '../router.js';

const listEl = document.getElementById('projectList');

export async function loadSidebar() {
    try {
        const data = await api.getProjects();
        // 过滤掉 __default__ 等内部默认项目，只展示用户真实创建的项目
        const projects = (data.projects || []).filter(p => !p.isDefault && p.id !== '__default__');
        setState({ projects });
        render();
    } catch {
        listEl.innerHTML = '<div class="spa-empty">加载失败</div>';
    }
}

export function render() {
    const { projects, currentProjectId } = getState();
    if (!projects.length) {
        listEl.innerHTML = '<div class="spa-empty" style="padding:1rem;font-size:0.82rem;">暂无项目</div>';
        return;
    }
    listEl.innerHTML = projects.map(p => `
        <div class="spa-project-item ${p.id === currentProjectId ? 'active' : ''}"
             data-id="${esc(p.id)}">
            <span class="spa-project-dot"></span>
            <span class="spa-project-name">${esc(p.name)}</span>
        </div>
    `).join('');

    listEl.querySelectorAll('.spa-project-item').forEach(el => {
        el.addEventListener('click', () => {
            navigate(`project/${el.dataset.id}`);
        });
    });
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}
