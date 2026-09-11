(() => {
    const el = id => document.getElementById(id);
    const dialog = el('backup-dialog');
    if (!dialog) return;
    let token = '', busy = false;
    const formatDate = value => {
        if (!value) return '아직 저장한 백업이 없습니다.';
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return value;
        const p = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    };
    function status(id, message, error = false) {
        el(id).textContent = message;
        el(id).dataset.error = String(error);
    }
    function setBusy(value) {
        busy = value;
        ['backup-export', 'backup-select', 'restore-close', 'restore-cancel', 'restore-confirm'].forEach(id => el(id).disabled = value);
        dialog.setAttribute('aria-busy', String(value));
    }
    async function readResponse(response) {
        let data;
        try { data = await response.json(); } catch { throw new Error('서버 응답을 확인하지 못했습니다. 연결 상태를 확인해 주세요.'); }
        if (!response.ok) {
            const error = new Error(data.error || '요청을 완료하지 못했습니다.');
            error.reselect = data.reselect;
            throw error;
        }
        return data;
    }
    async function refreshSummary() {
        const data = await readResponse(await fetch('/api/backup/summary', { cache: 'no-store' }));
        el('backup-students').textContent = data.students;
        el('backup-schedules').textContent = data.schedules;
        el('backup-last').textContent = '최근 백업　' + formatDate(data.last_backup);
        const list = el('backup-history');
        list.replaceChildren();
        for (const file of data.automatic || []) {
            const item = document.createElement('li'), link = document.createElement('a');
            link.href = '/api/backup/automatic/' + encodeURIComponent(file.name);
            link.download = file.name;
            link.textContent = `${formatDate(file.created_at)} · 다운로드`;
            item.append(link); list.append(item);
        }
        if (!list.children.length) {
            const item = document.createElement('li');
            item.textContent = '아직 자동 백업이 없습니다.';
            list.append(item);
        }
    }
    el('backup-export').addEventListener('click', async () => {
        if (busy) return;
        setBusy(true); status('backup-status', '백업 파일을 만드는 중입니다.');
        try {
            const response = await fetch('/api/backup/export', { method: 'POST' });
            if (!response.ok) await readResponse(response);
            const blob = await response.blob(), url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '')?.[1] || 'ansimtalk-backup.json';
            document.body.append(link); link.click(); link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 60000);
            status('backup-status', '백업 파일을 다운로드했습니다. 브라우저의 다운로드 목록에서 확인해 주세요.');
            await refreshSummary();
        } catch (error) { status('backup-status', error.message, true); }
        finally { setBusy(false); }
    });
    el('backup-select').addEventListener('click', () => {
        if (busy) return;
        el('backup-file').value = '';
        el('backup-file').click();
    });
    el('backup-file').addEventListener('change', async () => {
        const file = el('backup-file').files[0];
        if (!file || busy) return;
        token = '';
        if (file.size > 2 * 1024 * 1024) return status('backup-status', '백업 파일은 2MB 이하로 선택해 주세요.', true);
        setBusy(true); status('backup-status', '백업 파일을 확인하는 중입니다.');
        try {
            const data = await readResponse(await fetch('/api/backup/preview', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: file
            }));
            token = data.token;
            el('restore-filename').textContent = file.name;
            el('restore-created').textContent = '백업 시각　' + formatDate(data.created_at);
            for (const [kind, unit] of [['students', '명'], ['schedules', '개']]) {
                el('restore-current-' + kind).textContent = data.current[kind] + unit;
                el('restore-new-' + kind).textContent = data.incoming[kind] + unit;
            }
            status('restore-status', ''); status('backup-status', '');
            setBusy(false); dialog.showModal();
        } catch (error) { status('backup-status', error.message, true); }
        finally { setBusy(false); }
    });
    function close() { if (!busy) { dialog.close(); token = ''; el('backup-select').focus(); } }
    el('restore-close').addEventListener('click', close);
    el('restore-cancel').addEventListener('click', close);
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    el('restore-confirm').addEventListener('click', async () => {
        if (busy || !token) return;
        setBusy(true); status('restore-status', '현재 데이터를 자동 백업하고 복원하는 중입니다.');
        try {
            const data = await readResponse(await fetch('/api/backup/restore', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token })
            }));
            token = ''; dialog.close();
            status('backup-status', `입소자 ${data.students}명과 예약 ${data.schedules}개를 복원했습니다. 예약은 예약 관리에서 확인 후 켜 주세요.`);
            await refreshSummary();
            el('backup-select').focus();
        } catch (error) {
            status('restore-status', error.message, true);
            if (error.reselect) token = '';
        } finally {
            setBusy(false);
            if (!token) el('restore-confirm').disabled = true;
        }
    });
    window.addEventListener('sse', event => {
        if (['data_restored', 'students_changed'].includes(event.detail.type)) {
            refreshSummary().catch(() => {});
        }
    });
    refreshSummary().catch(error => status('backup-status', error.message, true));
})();
