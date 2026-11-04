const { JSDOM } = require('jsdom');
const TimedNotifications = require('../../static/js/timed-notifications');
const UiFeedback = require('../../static/js/ui-feedback-controller');

function setup({ mode = 'auto', cooldown = 14400, days = 7, count = 0, pending = [] } = {}) {
    let clock = 1000000000;
    let last = 0;
    let suppressed = false;
    const state = { star_mode: mode, usage_days: days, star_disabled: false, notices: pending };
    const posts = [];
    const dom = new JSDOM('<div id="toast" hidden></div>');
    dom.window.open = vi.fn();
    const toast = dom.window.document.getElementById('toast');
    const feedback = UiFeedback.create({ document: dom.window.document, toast });
    const renderServerNotice = vi.fn();
    const fetch = vi.fn(async (url, request = {}) => {
        let data;
        const remaining = () => last && cooldown ? Math.max(0, cooldown - (clock - last) / 1000) : 0;
        if (request.method === 'POST') {
            const body = JSON.parse(request.body);
            posts.push(body);
            if (body.disable_star) {
                state.star_disabled = true;
                data = { ok: true };
            } else if (remaining()) {
                data = { shown: false, retry_after_seconds: remaining() };
            } else {
                last = clock;
                if (body.kind === 'local_star') { count += 1; state.usage_days = 0; }
                if (body.kind === 'server') state.notices = state.notices.filter(item => item.notice_id !== body.notice_id);
                data = { shown: true, retry_after_seconds: cooldown, star_shown_count: count };
            }
        } else data = { ...state, retry_after_seconds: remaining() };
        return { ok: true, json: async () => data };
    });
    const make = () => TimedNotifications.create({
        window: dom.window, fetch, now: () => clock,
        showToast: feedback.showToast, renderServerNotice,
        isSuppressed: () => suppressed,
    });
    const controller = make();
    return { controller, state, toast, posts, make, dom, renderServerNotice,
        advance(seconds) { clock += seconds * 1000; },
        suppress(value) { suppressed = value; },
        close() { controller.destroy(); feedback.hideToast(); dom.window.close(); },
    };
}

describe('timed reminders', () => {
    it('holds every reminder while the update dialog is up and runs update checks outside the cooldown', async () => {
        const page = setup({ pending: [{ notice_id: 'server-1', notice: { id: 'soniox_quota' } }] });
        try {
            const update = vi.fn(async () => { page.suppress(true); });
            page.controller.addUngated('update', update);
            await page.controller.tick();
            expect(update).toHaveBeenCalledOnce();
            expect(page.renderServerNotice).not.toHaveBeenCalled();
            expect(page.toast.hidden).toBe(true);
            expect(page.controller.canShow()).toBe(false);
            page.suppress(false);
            update.mockImplementation(async () => {});
            await page.controller.tick();
            expect(page.renderServerNotice).toHaveBeenCalledOnce();
            await page.controller.tick();
            expect(update).toHaveBeenCalledTimes(3);
        } finally { page.close(); }
    });

    it('gives startup server notices priority and retries a due Star reminder after four hours', async () => {
        const page = setup({ pending: [{ notice_id: 'server-1', notice: { id: 'soniox_quota' } }] });
        try {
            await page.controller.prepare();
            await page.controller.tick();
            expect(page.renderServerNotice).toHaveBeenCalledOnce();
            expect(page.toast.hidden).toBe(true);
            expect(page.state.usage_days).toBe(7);
            page.advance(14400);
            await page.controller.tick();
            expect(page.toast.hidden).toBe(false);
            expect(page.state.usage_days).toBe(0);
        } finally { page.close(); }
    });

    it('closes on GitHub, offers permanent opt-out from the second display, and persists it across launches', async () => {
        const page = setup();
        try {
            await page.controller.tick();
            expect(page.toast.querySelectorAll('.toast-action')).toHaveLength(1);
            page.toast.querySelector('.toast-action').click();
            expect(page.dom.window.open).toHaveBeenCalledWith('https://github.com/febilly/realtime-subtitle', '_blank', 'noopener,noreferrer');
            expect(page.toast.textContent).toBe('local_model_star_thanks');
            page.toast.querySelector('.toast-close').click();
            expect(page.toast.hidden).toBe(true);
            expect(page.state.star_disabled).toBe(false);
            page.state.usage_days = 7;
            page.advance(14400);
            await page.controller.tick();
            const actions = page.toast.querySelectorAll('.toast-action');
            expect(actions).toHaveLength(2);
            actions[1].click();
            await Promise.resolve();
            expect(page.toast.textContent).toBe('local_model_star_thanks');
            page.toast.querySelector('.toast-close').click();
            expect(page.toast.hidden).toBe(true);
            expect(page.state.star_disabled).toBe(true);
            page.state.usage_days = 7;
            page.advance(14400);
            const reopened = page.make();
            await reopened.tick();
            expect(page.toast.hidden).toBe(true);
            reopened.destroy();
        } finally { page.close(); }
    });

    it('leaves a Star reminder visible until explicitly closed', async () => {
        vi.useFakeTimers();
        const page = setup();
        try {
            await page.controller.tick();
            vi.advanceTimersByTime(60000);
            expect(page.toast.hidden).toBe(false);
            page.toast.querySelector('.toast-close').click();
            expect(page.toast.hidden).toBe(true);
            expect(page.state.star_disabled).toBe(false);
        } finally { page.close(); vi.useRealTimers(); }
    });

    it.each([
        ['GitHub', 0, false],
        ['already starred', 1, false],
        ['GitHub', 0, true],
        ['already starred', 1, true],
    ])('thanks after %s, with manual close = %s', async (_label, actionIndex, manualClose) => {
        vi.useFakeTimers();
        const page = setup({ count: 1 });
        try {
            await page.controller.tick();
            const admissions = page.posts.length;
            page.toast.querySelectorAll('.toast-action')[actionIndex].click();
            expect(page.toast.querySelectorAll('.toast')).toHaveLength(1);
            expect(page.toast.textContent).toBe('local_model_star_thanks');
            expect(page.toast.hidden).toBe(false);
            // Thank-you is immediate even while the shared cooldown is active.
            expect(page.posts.slice(admissions).every(item => item.disable_star === true)).toBe(true);
            if (manualClose) {
                page.toast.querySelector('.toast-close').click();
                expect(page.toast.hidden).toBe(true);
                vi.advanceTimersByTime(6000);
            } else {
                vi.advanceTimersByTime(5999);
                expect(page.toast.hidden).toBe(false);
                vi.advanceTimersByTime(1);
                expect(page.toast.hidden).toBe(true);
            }
        } finally { page.close(); vi.useRealTimers(); }
    });

    it('supports always once per launch and never', async () => {
        const page = setup({ mode: 'always', cooldown: 0, days: 0 });
        try {
            await page.controller.tick();
            await page.controller.tick();
            expect(page.posts.filter(item => item.kind === 'local_star')).toHaveLength(1);
            const reopened = page.make();
            await reopened.tick();
            expect(page.posts.filter(item => item.kind === 'local_star')).toHaveLength(2);
            reopened.destroy();
            page.state.star_mode = 'never';
            page.state.usage_days = 20;
            await page.make().tick();
            expect(page.posts.filter(item => item.kind === 'local_star')).toHaveLength(2);
        } finally { page.close(); }
    });

    it('allows multiple reminders together with zero cooldown', async () => {
        const page = setup({ cooldown: 0, pending: [{ notice_id: 'server-1', notice: { id: 'soniox_quota' } }] });
        try {
            const localDisplay = vi.fn();
            page.controller.add('invite', () => page.controller.tryShow('invite', localDisplay));
            await page.controller.tick();
            expect(page.renderServerNotice).toHaveBeenCalledOnce();
            expect(localDisplay).toHaveBeenCalledOnce();
            expect(page.toast.hidden).toBe(false);
            expect(page.posts.map(item => item.kind)).toEqual(['server', 'invite', 'local_star']);
        } finally { page.close(); }
    });
});
