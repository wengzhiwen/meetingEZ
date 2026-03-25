/**
 * 简单状态管理
 */
const state = {
    projects: [],
    currentProjectId: null,
    currentTab: 'overview',
};

const listeners = new Set();

export function getState() { return state; }

export function setState(patch) {
    Object.assign(state, patch);
    listeners.forEach(fn => fn(state));
}

export function subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
}
