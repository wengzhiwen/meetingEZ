/**
 * API 封装层 — 所有 /api/workspace/ 调用
 */

async function request(method, url, body = null) {
    const opts = {
        method,
        headers: {},
    };
    if (body instanceof FormData) {
        opts.body = body;
    } else if (body !== null) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    const data = await resp.json();
    if (!resp.ok) {
        throw new Error(data.error || `请求失败 (${resp.status})`);
    }
    return data;
}

export const api = {
    getDashboard: () => request('GET', '/api/workspace/dashboard'),
    getProjects: () => request('GET', '/api/workspace/projects'),
    createProject: (data) => request('POST', '/api/workspace/project/create', data),
    getProject: (id) => request('GET', `/api/workspace/project/${encodeURIComponent(id)}`),
    updateProject: (id, data) => request('PUT', `/api/workspace/project/${encodeURIComponent(id)}`, data),

    createMeeting: (projectId, data) =>
        request('POST', `/api/workspace/project/${encodeURIComponent(projectId)}/meeting/create`, data),
    updateMeeting: (projectId, meetingDir, data) =>
        request('PUT', `/api/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(meetingDir)}`, data),

    getGlossary: (projectId) =>
        request('GET', `/api/workspace/project/${encodeURIComponent(projectId)}/glossary`),
    saveGlossary: (projectId, editorText) =>
        request('PUT', `/api/workspace/project/${encodeURIComponent(projectId)}/glossary`, { editor_text: editorText }),
    approveGlossaryTerm: (projectId, canonical) =>
        request('POST', `/api/workspace/project/${encodeURIComponent(projectId)}/glossary/approve`, { canonical }),
    rejectGlossaryTerm: (projectId, canonical, reason) =>
        request('POST', `/api/workspace/project/${encodeURIComponent(projectId)}/glossary/reject`, { canonical, reason }),

    getBackground: (projectId) =>
        request('GET', `/api/workspace/project/${encodeURIComponent(projectId)}/background`),
    saveBackground: (projectId, content) =>
        request('PUT', `/api/workspace/project/${encodeURIComponent(projectId)}/background`, { content }),

    getAudio: (projectId, meetingDir) =>
        request('GET', `/api/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(meetingDir)}/audio`),
    uploadAudio: (projectId, meetingDir, formData) =>
        request('POST', `/api/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(meetingDir)}/audio/upload`, formData),
    renameAudio: (projectId, meetingDir, filename, newName) =>
        request('POST', `/api/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(meetingDir)}/audio/${encodeURIComponent(filename)}/rename`, { new_name: newName }),
    deleteAudio: (projectId, meetingDir, filename) =>
        request('DELETE', `/api/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(meetingDir)}/audio/${encodeURIComponent(filename)}`),

    processMeeting: (projectId, meetingDir, action = 'full') =>
        request('POST', `/api/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(meetingDir)}/process`, { action }),

    getFile: (projectId, meetingDir, filename) =>
        request('GET', `/api/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(meetingDir)}/files/${encodeURIComponent(filename)}`),
    saveFile: (projectId, meetingDir, filename, content) =>
        request('PUT', `/api/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(meetingDir)}/files/${encodeURIComponent(filename)}`, { content }),
    deleteFile: (projectId, meetingDir, filename) =>
        request('DELETE', `/api/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(meetingDir)}/files/${encodeURIComponent(filename)}`),

    getAudioFileUrl: (projectId, meetingDir, filename) =>
        `/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(meetingDir)}/audio/${encodeURIComponent(filename)}`,
    getFileDownloadUrl: (projectId, meetingDir, filename) =>
        `/workspace/project/${encodeURIComponent(projectId)}/meeting/${encodeURIComponent(meetingDir)}/files/${encodeURIComponent(filename)}/download`,
};
