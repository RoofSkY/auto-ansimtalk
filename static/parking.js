function parkingDesk() {
    return {
        inventory: [], cars: [], selected: null, query: '', counts: {free: 0, paid: 0},
        loading: false, inventoryLoading: false, busy: false, error: '', inventoryError: '',
        result: null, generation: 0, timer: null, pending: null, cancellation: null,
        init() {
            try { this.pending = JSON.parse(sessionStorage.getItem('manual-parking-request')); } catch {}
            if (this.pending?.token) { this.busy = true; this.poll(); }
        },
        destroy() { clearTimeout(this.timer); },
        async api(path, options = {}) {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 25000);
            try {
                const response = await fetch('/api/parking/' + path, {...options, signal: controller.signal});
                const data = await response.json();
                if (!response.ok) {
                    const error = new Error(data.error || '요청을 처리하지 못했습니다. 다시 확인해 주세요.');
                    error.status = response.status;
                    throw error;
                }
                return data;
            } catch (error) {
                if (error.name === 'AbortError' || error instanceof TypeError) {
                    throw new Error('응답을 확인하지 못했습니다. 연결 상태를 확인해 주세요.');
                }
                throw error;
            } finally { clearTimeout(timeout); }
        },
        open() {
            this.$refs.parkingDialog.showModal();
            this.refreshInventory();
        },
        restoreFocus() {
            this.$nextTick(() => {
                const dialog = this.$refs.parkingDialog;
                if (dialog.open && !dialog.contains(document.activeElement)) {
                    dialog.querySelector('input:not(:disabled), button:not(:disabled)')?.focus({preventScroll: true});
                }
            });
        },
        async refreshInventory() {
            if (this.inventoryLoading) return;
            this.inventoryLoading = true;
            this.inventoryError = '';
            try { this.inventory = await this.api('inventory'); }
            catch (error) { this.inventory = []; this.inventoryError = error.message; }
            finally { this.inventoryLoading = false; }
        },
        async search() {
            if (this.busy || this.loading) return;
            this.cancellation = null;
            const generation = ++this.generation;
            this.error = ''; this.result = null; this.selected = null; this.cars = [];
            this.counts = {free: 0, paid: 0};
            this.loading = true;
            try {
                const cars = await this.api('search?' + new URLSearchParams({number: this.query}));
                if (generation !== this.generation) return;
                this.cars = cars;
                if (!cars.length) this.error = '입차 중인 차량이 없습니다. 차량번호를 확인해 주세요.';
                if (cars.length === 1) await this.selectCar(cars[0], generation);
            } catch (error) { if (generation === this.generation) this.error = error.message; }
            finally { if (generation === this.generation) { this.loading = false; this.restoreFocus(); } }
        },
        changeQuery() {
            this.cancellation = null;
            this.generation++;
            this.selected = null; this.cars = []; this.result = null; this.error = '';
            this.counts = {free: 0, paid: 0};
        },
        async selectCar(car, generation = ++this.generation) {
            if (this.busy) return;
            this.cancellation = null;
            this.loading = true; this.error = ''; this.selected = null; this.result = null;
            this.counts = {free: 0, paid: 0};
            try {
                const view = await this.api('detail?' + new URLSearchParams({plate: car.plate, history: car.history_id}));
                if (generation !== this.generation) return;
                this.selected = view;
                this.inventory = view.tickets;
                this.inventoryError = '';
            } catch (error) { if (generation === this.generation) this.error = error.message; }
            finally { if (generation === this.generation) { this.loading = false; this.restoreFocus(); } }
        },
        duration(minutes) {
            if (!Number.isInteger(minutes)) return '확인 불가';
            return (minutes >= 1440 ? Math.floor(minutes / 1440) + '일 ' : '')
                + Math.floor(minutes % 1440 / 60) + '시간 ' + minutes % 60 + '분';
        },
        get totalApplied() { return (this.selected?.applied || []).reduce((n, ticket) => n + ticket.count, 0); },
        get summary() {
            return (this.selected?.tickets || []).filter(t => this.counts[t.key] > 0)
                .map(t => t.label + ' ' + this.counts[t.key] + '장').join(' · ') || '수량을 선택해 주세요';
        },
        get canRegister() {
            return this.selected && !this.busy && !this.loading && !this.cancellation
                && this.selected.tickets.every(t => Number.isInteger(this.counts[t.key]) && this.counts[t.key] >= 0 && this.counts[t.key] <= this.countLimit(t))
                && Object.values(this.counts).some(n => n > 0);
        },
        countLimit(ticket) {
            return Math.max(0, Math.min(100, Number(ticket.max) || 0));
        },
        setCount(ticket, input, commit = false) {
            if (input.value === '' && !commit) {
                this.counts[ticket.key] = '';
                return;
            }
            const number = Number(input.value);
            const count = Math.max(0, Math.min(this.countLimit(ticket), Number.isFinite(number) ? Math.trunc(number) : 0));
            this.counts[ticket.key] = count;
            input.value = String(count);
        },
        adjust(ticket, change) {
            this.counts[ticket.key] = Math.max(0, Math.min(this.countLimit(ticket), (Number(this.counts[ticket.key]) || 0) + change));
        },
        savePending() {
            try {
                if (this.pending) sessionStorage.setItem('manual-parking-request', JSON.stringify(this.pending));
                else sessionStorage.removeItem('manual-parking-request');
            } catch {}
        },
        async register() {
            if (!this.canRegister) return;
            await this.sendOperation('register', {counts: {...this.counts}});
        },
        prepareCancel(ticket) {
            if (!this.selected || this.busy || this.loading || !ticket.cancel_max || ticket.other_store) return;
            this.cancellation = {key: ticket.key, label: ticket.label, count: 1, max: Math.min(100, ticket.cancel_max)};
            this.$nextTick(() => this.$refs.cancelQuantity?.focus());
        },
        setCancelCount(value, input = null) {
            if (!this.cancellation) return;
            const number = Number(value);
            this.cancellation.count = Math.max(1, Math.min(this.cancellation.max, Number.isFinite(number) ? Math.trunc(number) : 1));
            if (input) input.value = String(this.cancellation.count);
        },
        async cancelTicket() {
            const item = this.cancellation;
            if (!this.selected || this.busy || this.loading || !item || !Number.isInteger(item.count)
                || item.count < 1 || item.count > item.max) return;
            await this.sendOperation('cancel', {key: item.key, count: item.count});
        },
        async sendOperation(operation, payload) {
            this.busy = true; this.error = ''; this.result = null;
            this.pending = {token: this.selected.token, plate: this.selected.plate, history_id: this.selected.history_id};
            this.cancellation = null;
            this.savePending();
            try {
                await this.api(operation, {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({token: this.pending.token, ...payload})});
            } catch (error) {
                this.error = error.message;
                // 응답이 유실되어도 재전송하지 않고 같은 요청의 결과만 확인한다.
            }
            await this.poll();
        },
        async poll() {
            clearTimeout(this.timer);
            if (!this.pending) return;
            const pending = {...this.pending};
            try {
                const result = await this.api('requests/' + encodeURIComponent(pending.token));
                if (result.state === 'pending') {
                    this.timer = setTimeout(() => this.poll(), 1500);
                    return;
                }
                this.pending = null; this.savePending(); this.busy = false;
                if (result.state === 'ready') {
                    this.error = this.error || '처리 요청이 접수되지 않았습니다. 차량과 수량을 확인한 후 다시 시도해 주세요.';
                    this.restoreFocus();
                    return;
                }
                this.counts = {free: 0, paid: 0};
                this.selected = null;
                this.error = '';
                this.result = result;
                this.loading = true;
                try {
                    const view = await this.api('detail?' + new URLSearchParams({plate: pending.plate, history: pending.history_id}));
                    this.selected = view; this.inventory = view.tickets; this.inventoryError = '';
                } catch (error) { this.error = '처리 후 조회: ' + error.message; }
                finally { this.loading = false; this.restoreFocus(); }
                await this.refreshInventory();
            } catch (error) {
                this.error = error.message;
                if (error.status === 404) {
                    this.pending = null; this.savePending(); this.busy = false; this.selected = null;
                    this.counts = {free: 0, paid: 0};
                    this.restoreFocus();
                } else this.timer = setTimeout(() => this.poll(), 4000);
            }
        },
    };
}
