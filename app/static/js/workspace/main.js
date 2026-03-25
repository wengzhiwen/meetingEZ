/**
 * MeetingEZ SPA 工作台 — 主入口
 */
import { registerRoutes, startRouter } from './router.js';
import { setState } from './state.js';
import { loadSidebar } from './components/sidebar.js';
import { render as renderDashboard } from './components/dashboard.js';
import { render as renderProject } from './components/project-tabs.js';
import { openModal, closeModal, getModalBody } from './components/modal.js';
import { api } from './api.js';
import { showToast } from './toast.js';

// ---- 路由注册 ----
registerRoutes({
    dashboard: () => {
        setState({ currentProjectId: null, currentTab: null });
        import('./components/sidebar.js').then(m => m.render());
        renderDashboard();
    },
    project: ({ projectId, tab }) => {
        renderProject(projectId, tab);
    },
});

// ---- 侧边栏新建项目按钮 ----
document.getElementById('btnNewProject').addEventListener('click', () => {
    const today = new Date().toISOString().slice(0, 10);
    openModal('新建项目', `
        <form class="spa-form" id="createProjectForm">
            <div class="spa-form-group">
                <label>项目名称 *</label>
                <input name="name" required placeholder="项目名称" autofocus>
            </div>
            <div class="spa-form-group">
                <label>描述</label>
                <textarea name="description" rows="3" placeholder="项目简介"></textarea>
            </div>
            <div class="spa-form-row">
                <div class="spa-form-group">
                    <label>团队成员</label>
                    <input name="team" placeholder="逗号分隔">
                </div>
                <div class="spa-form-group">
                    <label>开始日期</label>
                    <input name="start_date" type="date" value="${today}">
                </div>
            </div>
            <div class="spa-form-actions">
                <button type="submit" class="spa-btn spa-btn-primary">创建</button>
                <button type="button" class="spa-btn spa-btn-outline" id="cancelCreateProject">取消</button>
            </div>
        </form>
    `);

    const form = document.getElementById('createProjectForm');
    document.getElementById('cancelCreateProject').addEventListener('click', closeModal);

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = form.querySelector('button[type="submit"]');
        btn.disabled = true;
        try {
            const fd = new FormData(form);
            const data = Object.fromEntries(fd);
            const result = await api.createProject(data);
            showToast(`项目已创建: ${result.project_name}`, 'success');
            closeModal();
            await loadSidebar();
            location.hash = `project/${result.project_id}`;
        } catch (err) {
            showToast(err.message, 'error');
            btn.disabled = false;
        }
    });
});

// ---- 移动端侧边栏 ----
const sidebar = document.getElementById('sidebar');
document.getElementById('hamburgerBtn').addEventListener('click', () => {
    sidebar.classList.toggle('open');
});
document.getElementById('sidebarClose').addEventListener('click', () => {
    sidebar.classList.remove('open');
});

// ---- 启动 ----
loadSidebar();
startRouter();
