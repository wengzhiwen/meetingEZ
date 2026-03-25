/**
 * 通用 Modal
 */
const backdrop = document.getElementById('modalBackdrop');
const modal = document.getElementById('modal');
const titleEl = document.getElementById('modalTitle');
const bodyEl = document.getElementById('modalBody');
const closeBtn = document.getElementById('modalClose');

closeBtn.addEventListener('click', closeModal);
backdrop.addEventListener('click', closeModal);

export function openModal(title, contentHTML) {
    titleEl.textContent = title;
    bodyEl.innerHTML = contentHTML;
    backdrop.classList.remove('hidden');
    modal.classList.remove('hidden');
}

export function closeModal() {
    backdrop.classList.add('hidden');
    modal.classList.add('hidden');
    bodyEl.innerHTML = '';
}

export function getModalBody() {
    return bodyEl;
}
