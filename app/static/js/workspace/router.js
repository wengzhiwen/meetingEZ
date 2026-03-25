/**
 * Hash 路由
 */

let routes = {};

export function registerRoutes(routeMap) {
    routes = routeMap;
}

export function navigate(hash) {
    location.hash = hash;
}

export function parseHash() {
    const raw = (location.hash || '#').slice(1);
    if (!raw || raw === 'dashboard') {
        return { view: 'dashboard' };
    }

    // #project/{id}/meetings  #project/{id}/glossary  #project/{id}/background
    const m = raw.match(/^project\/([^/]+)(?:\/(.+))?$/);
    if (m) {
        return { view: 'project', projectId: decodeURIComponent(m[1]), tab: m[2] || 'overview' };
    }

    return { view: 'dashboard' };
}

export function startRouter() {
    const handle = () => {
        const parsed = parseHash();
        // __default__ 是内部单项目模式的占位符，不应作为可浏览的项目展示
        if (parsed.view === 'project' && parsed.projectId === '__default__') {
            location.hash = '';
            return;
        }
        const handler = routes[parsed.view];
        if (handler) handler(parsed);
    };
    window.addEventListener('hashchange', handle);
    handle();
}
