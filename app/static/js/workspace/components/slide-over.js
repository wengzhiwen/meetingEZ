/**
 * SlideOver 右侧滑出面板 — 文件查看/编辑
 */
import { api } from '../api.js';
import { showToast } from '../toast.js';

const backdrop = document.getElementById('slideOverBackdrop');
const panel = document.getElementById('slideOver');
const titleEl = document.getElementById('slideOverTitle');
const bodyEl = document.getElementById('slideOverBody');
const closeBtn = document.getElementById('slideOverClose');

closeBtn.addEventListener('click', close);
backdrop.addEventListener('click', close);

function open() {
    backdrop.classList.remove('hidden');
    requestAnimationFrame(() => {
        backdrop.classList.add('visible');
        panel.classList.add('open');
    });
}

function close() {
    panel.classList.remove('open');
    backdrop.classList.remove('visible');
    setTimeout(() => {
        backdrop.classList.add('hidden');
        bodyEl.innerHTML = '';
    }, 300);
}

export async function openFile(projectId, meetingDir, filename) {
    titleEl.textContent = filename;
    bodyEl.innerHTML = '<div class="spa-loading">加载中...</div>';
    open();

    try {
        const data = await api.getFile(projectId, meetingDir, filename);
        const file = data.file;
        const downloadUrl = api.getFileDownloadUrl(projectId, meetingDir, filename);

        if (file.editable) {
            bodyEl.innerHTML = `
                <div class="spa-file-editor">
                    <textarea id="slideOverFileContent">${esc(file.content)}</textarea>
                    <div style="display:flex;gap:0.5rem;margin-top:0.7rem;">
                        <button class="spa-btn spa-btn-primary spa-btn-sm" id="slideOverFileSave">保存</button>
                        <a class="spa-btn spa-btn-outline spa-btn-sm" href="${esc(downloadUrl)}" download>下载</a>
                    </div>
                </div>`;
            document.getElementById('slideOverFileSave').addEventListener('click', async () => {
                const content = document.getElementById('slideOverFileContent').value;
                try {
                    await api.saveFile(projectId, meetingDir, filename, content);
                    showToast('文件已保存', 'success');
                } catch (e) {
                    showToast(e.message, 'error');
                }
            });
        } else {
            bodyEl.innerHTML = `
                <div class="spa-file-viewer">${esc(file.content)}</div>
                <div style="margin-top:0.7rem;">
                    <a class="spa-btn spa-btn-outline spa-btn-sm" href="${esc(downloadUrl)}" download>下载</a>
                </div>`;
        }
    } catch (e) {
        bodyEl.innerHTML = `<div class="spa-empty">${esc(e.message)}</div>`;
    }
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}
