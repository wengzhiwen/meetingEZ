/**
 * 通用 Modal
 */
const backdrop = document.getElementById('modalBackdrop');
const modal = document.getElementById('modal');
const titleEl = document.getElementById('modalTitle');
const bodyEl = document.getElementById('modalBody');
const closeBtn = document.getElementById('modalClose');

let _locked = false;

closeBtn.addEventListener('click', () => { if (!_locked) closeModal(); });
backdrop.addEventListener('click', () => { if (!_locked) closeModal(); });

export function openModal(title, contentHTML) {
    _locked = false;
    titleEl.textContent = title;
    bodyEl.innerHTML = contentHTML;
    backdrop.classList.remove('hidden');
    modal.classList.remove('hidden');
}

export function closeModal() {
    _locked = false;
    backdrop.classList.add('hidden');
    modal.classList.add('hidden');
    bodyEl.innerHTML = '';
}

export function lockModal() { _locked = true; }
export function unlockModal() { _locked = false; }

export function getModalBody() {
    return bodyEl;
}
