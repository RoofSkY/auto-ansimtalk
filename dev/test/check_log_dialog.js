(async () => {
    const assert = (value, message) => { if (!value) throw new Error(message); };
    const tick = () => new Promise(resolve => setTimeout(resolve, 30));
    const app = Alpine.$data(document.querySelector('[x-data="mainApp()"]'));
    const dialog = document.querySelector('.log-dialog');
    const trigger = document.querySelector('.log-entry');
    trigger.focus();
    trigger.click();
    await tick();
    assert(dialog.open && dialog.contains(document.activeElement), 'click opens modal with focus');
    assert(document.querySelector('.log-detail-message').textContent === app.selectedLog.message, 'full message');
    dialog.close();
    await tick();
    assert(document.activeElement === trigger, 'focus returns to clicked log');

    const message = '긴 메시지 <img src=x onerror="window.logInjected=true">\n\n' + '가'.repeat(5000) + '\n마지막 줄';
    app.openLog(app.prepareLog({id:'detail-test', date:'2026-09-16', time:'15:42:08',
        type:'차량등록', target:'12가6595 김민준', message, ok:false}));
    await tick();
    assert(document.querySelector('.log-detail-message').textContent === message, 'untruncated multiline message');
    assert(!document.querySelector('.log-detail-message img') && !window.logInjected, 'message is plain text');
    assert(document.querySelector('.log-detail-meta').textContent.includes('2026-09-16 15:42:08'), 'date and time');
    assert(document.querySelector('.log-detail-vehicle').textContent === '12가6595', 'full vehicle plate from original log');
    assert(app.selectedLog.displayTarget === '김민준', 'resident name remains separate');
    assert(app.logVehicle({type:'차량등록(예약)',target:'12가6595,1234 예약'}) === '12가6595, 1234', 'scheduled vehicle list');
    assert(app.logVehicle({type:'차량등록',target:'김민준'}) === '', 'missing historical vehicle is not inferred');
    assert(app.logVehicle({type:'안심톡',target:'0001 김민준'}) === null, 'attendance code is not a vehicle');
    const body = document.querySelector('.log-dialog-body');
    assert(body.scrollHeight > body.clientHeight, 'long message scrolls inside modal');
    const bounds = dialog.getBoundingClientRect();
    assert(bounds.left >= 0 && bounds.right <= innerWidth && bounds.bottom <= innerHeight, 'modal fits viewport');
    assert(body.scrollWidth <= body.clientWidth + 1, 'long unbroken text wraps');
    const clipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    try {
        let copied;
        Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:async text => {copied=text;}}});
        await app.copyLog();
        assert(copied.endsWith(message) && copied.includes('아이파킹') && copied.includes('실패'), 'copy metadata and full message');
        assert(copied.includes('차량번호: 12가6595'), 'copy includes full vehicle plate');
        assert(app.copyStatus === '내용을 복사했습니다.', 'copy feedback');
        navigator.clipboard.writeText = async () => {throw new Error('denied');};
        await app.copyLog();
        assert(app.copyFailed && !app.copyingLog, 'clipboard rejection feedback');
        let finish;
        navigator.clipboard.writeText = () => new Promise(resolve => {finish=resolve;});
        const pending = app.copyLog();
        dialog.close();
        await tick();
        app.openLog(app.logs[0]);
        finish();
        await pending;
        assert(app.copyStatus === '', 'old copy result does not affect reopened modal');
    } finally {
        if (clipboard) Object.defineProperty(navigator, 'clipboard', clipboard);
        else delete navigator.clipboard;
    }
    dialog.close();
    await tick();
    return true;
})()
