document.addEventListener('submit', async event => {
    const form = event.target;
    if (!form.matches('[data-async-form]')) return;
    event.preventDefault();
    if (form.dataset.sending) return;
    if (form.dataset.confirm && !confirm(form.dataset.confirm)) return;
    const status = form.querySelector('[data-form-status]') || form.closest('.directory-item')?.querySelector('[data-row-status]');
    const buttons = [...form.querySelectorAll('button')];
    const body = new FormData(form);
    form.dataset.sending = 'true';
    buttons.forEach(button => button.disabled = true);
    if (status) { status.textContent = '저장 중…'; status.dataset.error = 'false'; }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
        const response = await fetch(form.action, { method: 'POST', body, signal: controller.signal });
        if (!response.ok) {
            let message = '요청을 처리하지 못했습니다. 입력 내용을 확인해 주세요.';
            try {
                const result = await response.json();
                message = result.error || (typeof result.detail === 'string' ? result.detail : message);
            } catch {}
            throw new Error(message);
        }
        if (response.redirected) location.assign(response.url);
        else location.reload();
    } catch (error) {
        if (status) {
            status.textContent = error.name === 'AbortError' || error instanceof TypeError
                ? '응답을 확인하지 못했습니다. 새로고침해 저장 여부를 확인해 주세요.'
                : error.message;
            status.dataset.error = 'true';
        }
        buttons.forEach(button => button.disabled = false);
        delete form.dataset.sending;
    } finally {
        clearTimeout(timeout);
    }
});

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-dirty-form]').forEach(form => {
        const initial = new URLSearchParams(new FormData(form)).toString();
        const update = () => {
            const dirty = new URLSearchParams(new FormData(form)).toString() !== initial;
            const status = form.querySelector('[data-dirty-status]');
            if (status) status.textContent = dirty ? '저장하지 않은 변경사항' : '';
        };
        form.addEventListener('input', update);
        form.addEventListener('change', update);
    });
});
