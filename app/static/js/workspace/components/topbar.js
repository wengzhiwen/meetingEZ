/**
 * 面包屑导航
 */
const breadcrumb = document.getElementById('breadcrumb');

export function setBreadcrumb(items) {
    // items: [{label, hash?}]
    breadcrumb.innerHTML = items.map((item, i) => {
        if (i < items.length - 1 && item.hash != null) {
            return `<a href="#${esc(item.hash)}">${esc(item.label)}</a><span class="sep">/</span>`;
        }
        return `<span class="current">${esc(item.label)}</span>`;
    }).join('');
}

function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}
