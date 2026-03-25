/**
 * Toast 通知
 */
const container = document.getElementById('toastContainer');

export function showToast(message, type = 'info', duration = 3500) {
    const el = document.createElement('div');
    el.className = `spa-toast spa-toast-${type}`;
    el.textContent = message;
    container.appendChild(el);

    setTimeout(() => {
        el.classList.add('removing');
        el.addEventListener('animationend', () => el.remove());
    }, duration);
}
