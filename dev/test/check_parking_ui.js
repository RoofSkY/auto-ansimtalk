(async () => {
    const assert = (value, message) => { if (!value) throw new Error(message); };
    const tick = () => new Promise(resolve => setTimeout(resolve, 40));
    const app = Alpine.$data(document.querySelector('[x-data="parkingDesk()"]'));
    const originalFetch = window.fetch;
    const tickets = [{key:'free', label:'1시간 무료권', remaining:128, max:2},
        {key:'paid', label:'1시간 유료권', remaining:42, max:42}];
    const car = {plate:'123가6595', history_id:'visit-1', entered_at:'2026-09-16 13:20'};
    let registrations = 0, cancellations = 0, lostResponse = false, operation = '등록';
    let inventoryReads = 0, detailInventory = true, holdInventory = false, releaseInventory;
    window.fetch = async (url, options = {}) => {
        if (!String(url).startsWith('/api/parking/')) return originalFetch(url, options);
        const path = new URL(url, location.href);
        let data;
        if (path.pathname.endsWith('/inventory')) {
            inventoryReads++;
            data = tickets;
            if (holdInventory) await new Promise(resolve => { releaseInventory = resolve; });
        }
        else if (path.pathname.endsWith('/search')) {
            data = path.searchParams.get('number') === '0000' ? [] : [car, {...car, plate:'55나6595', history_id:'visit-2'}];
        } else if (path.pathname.endsWith('/detail')) {
            data = {...car, plate:path.searchParams.get('plate'), history_id:path.searchParams.get('history'),
                token:'token-' + registrations + '-' + cancellations, minutes:142, applied:[
                    {key:'applied-id', label:'1시간 무료권', count:1 + registrations - cancellations,
                        cancel_max:1 + registrations - cancellations, other_store:false},
                    {key:null, label:'다른 무료권', count:1, cancel_max:0, other_store:true}], tickets,
                inventory:detailInventory ? tickets : null};
        } else if (path.pathname.endsWith('/register')) {
            const payload = JSON.parse(options.body);
            assert(payload.counts.free === 1 && payload.counts.paid === 0, 'explicit quantity submitted');
            registrations++;
            operation = '등록';
            if (lostResponse) throw new TypeError('lost response');
            data = {state:'pending', results:[], message:''};
        } else if (path.pathname.endsWith('/cancel')) {
            const payload = JSON.parse(options.body);
            assert(payload.key === 'applied-id' && payload.count === 1, 'exact applied ticket and selected count submitted');
            cancellations++;
            operation = '취소';
            if (lostResponse) throw new TypeError('lost cancel response');
            data = {state:'pending', results:[], message:''};
        } else if (path.pathname.includes('/requests/')) {
            data = {state:'success', message:'주차권 ' + operation + '를 완료했습니다.', results:[{label:'1시간 무료권', count:1, ok:true, message:operation + ' 완료'}]};
        } else throw new Error('unexpected parking request ' + url);
        return new Response(JSON.stringify(data), {status:200, headers:{'Content-Type':'application/json'}});
    };
    try {
        document.querySelector('.parking-launcher').click();
        await tick();
        assert(document.querySelector('.parking-dialog').open, 'launcher opens modal');
        assert(app.inventory[0].remaining === 128, 'inventory loaded');
        app.query = '0000';
        await app.search();
        assert(!app.selected && app.error, 'no parked car feedback');
        app.query = '6595';
        await app.search();
        assert(app.cars.length === 2 && !app.selected, 'colliding suffix needs explicit choice');
        await app.selectCar(app.cars[1]);
        assert(app.selected.plate === '55나6595', 'chosen full plate shown');
        assert(!app.canRegister, 'zero quantities cannot register');
        app.counts.free = 3;
        assert(!app.canRegister, 'over-limit quantity cannot register');
        app.counts.free = 1;
        assert(app.canRegister, 'valid count enables registration');
        await app.register();
        assert(registrations === 1 && app.totalApplied === 3 && !app.busy, 'register and reload actual applied count');
        assert(inventoryReads === 1, 'detail stock reuses inventory without another request after registration');
        assert(app.counts.free === 0 && !app.canRegister, 'completed quantity is reset');
        lostResponse = true;
        app.counts.free = 1;
        await app.register();
        assert(registrations === 2 && app.result.state === 'success', 'lost POST response recovered without resend');
        detailInventory = false;
        await app.selectCar(car);
        assert(inventoryReads === 2 && app.inventory[1].remaining === 42, 'incomplete detail stock falls back to inventory endpoint');
        detailInventory = true;
        await tick();
        const cancelButtons = [...document.querySelectorAll('.parking-cancel-button')].filter(b => !b.hidden);
        assert(cancelButtons.length === 1, 'only own store ticket shows cancel button');
        cancelButtons[0].click();
        await tick();
        assert(cancellations === 0 && app.cancellation && !app.canRegister, 'cancel requires quantity confirmation');
        const cancelPanel = document.querySelector('.parking-cancel-confirm');
        assert(cancelPanel.textContent.includes(app.selected.plate), 'confirmation shows full plate');
        const cancelInput = cancelPanel.querySelector('input');
        cancelInput.value = '999';
        cancelInput.dispatchEvent(new Event('input', {bubbles:true}));
        assert(app.cancellation.count === 3 && cancelInput.value === '3', 'cancel count limited to applied count');
        app.cancellation = null;
        await tick();
        assert(cancellations === 0, 'dismissing confirmation does not cancel');
        app.prepareCancel(app.selected.applied[0]);
        await tick();
        await app.cancelTicket();
        assert(cancellations === 1 && app.totalApplied === 3 && app.result.state === 'success', 'cancel response loss recovered once and applied count reloaded');
        assert(inventoryReads === 2, 'cancel detail also avoids duplicate inventory request');
        const input = document.querySelector('#parking-number');
        input.value = '1234';
        input.dispatchEvent(new Event('input', {bubbles:true}));
        assert(!app.selected && !app.canRegister, 'editing query invalidates old vehicle selection');
        app.query = '6595';
        await app.selectCar(car);
        app.counts.free = 1;
        await tick();
        const dialog = document.querySelector('.parking-dialog');
        assert(getComputedStyle(dialog).userSelect === 'none', 'dialog text cannot be selected by dragging');
        assert(!document.querySelector('.parking-header p'), 'header helper text removed');
        const quantity = document.querySelector('#parking-count-paid');
        assert(getComputedStyle(quantity).textAlign === 'center', 'quantity is centered');
        assert(app.countLimit({max:1000}) === 100, 'absolute quantity cap is 100');
        quantity.value = '999';
        quantity.dispatchEvent(new Event('input', {bubbles:true}));
        assert(quantity.value === '42' && app.counts.paid === 42, 'typed quantity clamps to available limit');
        quantity.value = '101';
        app.setCount({key:'paid',max:100}, quantity);
        assert(quantity.value === '100' && app.counts.paid === 100, 'typed quantity never exceeds 100');
        app.counts.paid = 0;
        holdInventory = true;
        const oldInventory = app.refreshInventory();
        await tick();
        app.applyVehicle({...app.selected, inventory:tickets.map(t => ({...t, remaining:77}))});
        releaseInventory();
        await oldInventory;
        assert(app.inventory[0].remaining === 77, 'older inventory response cannot overwrite fresh detail stock');
        holdInventory = false;
        app.applyVehicle({...app.selected, inventory:tickets});
        await tick();
        const bounds = dialog.getBoundingClientRect();
        assert(bounds.left >= 0 && bounds.right <= innerWidth && bounds.bottom <= innerHeight, 'modal fits viewport');
        assert(dialog.scrollWidth <= dialog.clientWidth + 1, 'modal has no horizontal overflow');
        assert(dialog.contains(document.activeElement), 'focus stays in modal');
        const logApp = Alpine.$data(document.querySelector('[x-data="mainApp()"]'));
        if (logApp?.logDetailTitle) {
            const log = {type:'차량취소', target:'123가6595 직접 취소', ok:true};
            assert(logApp.logDetailTitle(log) === '주차권 취소 완료', 'cancel log has distinct title');
            assert(logApp.logVehicle(log) === '123가6595', 'cancel log preserves full vehicle number');
            assert(logApp.logTabs.find(t => t.key === 'park').match(log), 'cancel appears in parking tab');
        }
        return true;
    } finally {
        window.fetch = originalFetch;
        clearTimeout(app.timer);
        sessionStorage.removeItem('manual-parking-request');
    }
})()
