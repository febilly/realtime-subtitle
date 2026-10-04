const { JSDOM } = require('jsdom');
const Announcements = require('../../static/js/announcements');
const Hosted = require('../../static/js/hosted');
const Update = require('../../static/js/hosted-update');

function setup() {
    const dom = new JSDOM('<!doctype html><body><button id="announcementsButton">notice</button></body>', { url: 'http://localhost/' });
    let rows = [{ id: 'uuid-1', title: 'Hello', content: '**bold** ==mark== [link](https://example.com) <script>bad()</script>', published_at: '2026-10-04T00:00:00Z', visible: true, popup: false, revision: 1 }];
    const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ announcements: rows }) }));
    const client = Announcements.create({ fetch, storage: dom.window.localStorage, getDocument: () => dom.window.document, getLanguage: () => 'zh' });
    return { dom, doc: dom.window.document, fetch, client, setRows: next => { rows = next; }, getRows: () => rows };
}
describe('announcement list, local unread state and rich text', () => {
    it('shows a dot without auto-opening a non-popup announcement; opening clears it persistently', async () => {
        const page = setup();
        await page.client.check({ autoPopup: true });
        expect(page.client.hasUnread()).toBe(true);
        expect(page.doc.querySelector('[role=dialog]')).toBeNull();
        expect(page.doc.querySelector('.announcement-unread-dot').hidden).toBe(false);
        await page.client.open();
        expect(page.doc.querySelector('[role=dialog]')).not.toBeNull();
        expect(page.client.hasUnread()).toBe(false);
        expect(page.doc.querySelector('.announcement-unread-dot').hidden).toBe(true);
        const body = page.doc.querySelector('.announcement-item');
        expect(body.querySelector('strong').textContent).toBe('bold');
        expect(body.querySelector('mark').textContent).toBe('mark');
        expect(body.querySelector('a').rel).toBe('noopener noreferrer');
        expect(body.querySelector('script')).toBeNull();
        expect(body.textContent).toContain('<script>bad()</script>');
        page.client.close();
        const another = Announcements.create({ fetch: page.fetch, storage: page.dom.window.localStorage, getDocument: () => page.doc });
        await another.check({ autoPopup: true });
        expect(another.hasUnread()).toBe(false);
        another.destroy(); page.client.destroy(); page.dom.window.close();
    });
    it('auto-opens only unread popup revisions, and Escape restores focus', async () => {
        const page = setup();
        page.setRows([{ ...page.getRows()[0], popup: true }]);
        const button = page.doc.getElementById('announcementsButton'); button.focus();
        await page.client.check({ autoPopup: true });
        expect(page.doc.querySelector('[role=dialog]')).not.toBeNull();
        page.doc.dispatchEvent(new page.dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
        expect(page.doc.querySelector('[role=dialog]')).toBeNull();
        expect(page.doc.activeElement).toBe(button);
        await page.client.check({ autoPopup: true, force: true });
        expect(page.doc.querySelector('[role=dialog]')).toBeNull();
        page.setRows([{ ...page.getRows()[0], revision: 2 }]);
        await page.client.check({ autoPopup: true, force: true });
        expect(page.doc.querySelector('[role=dialog]')).not.toBeNull();
        page.client.destroy(); page.dom.window.close();
    });
    it('removes hidden entries and coalesces concurrent checks', async () => {
        const page = setup();
        page.setRows([{ ...page.getRows()[0], visible: false, popup: true }]);
        await Promise.all([page.client.check({ autoPopup: true }), page.client.check({ autoPopup: true })]);
        expect(page.fetch).toHaveBeenCalledTimes(1);
        expect(page.client.hasUnread()).toBe(false);
        expect(page.doc.querySelector('[role=dialog]')).toBeNull();
        page.client.destroy(); page.dom.window.close();
    });
    it('does not show stale content after a failed refresh; retries without losing reads', async () => {
        const page = setup(); await page.client.open(); page.client.close();
        page.fetch.mockRejectedValueOnce(new Error('offline'));
        await page.client.open();
        expect(page.doc.querySelector('[role=alert]').textContent).toContain('公告加载失败');
        expect(page.doc.querySelector('.announcement-item')).toBeNull();
        await page.client.check({ force: true });
        expect(page.doc.querySelector('.announcement-item')).not.toBeNull();
        expect(page.client.hasUnread()).toBe(false);
        page.client.destroy(); page.dom.window.close();
    });
    it('keeps working when storage throws and maintains read state within the session', async () => {
        const page = setup();
        const client = Announcements.create({ fetch: page.fetch, getDocument: () => page.doc, storage: { getItem() { throw new Error('denied'); }, setItem() { throw new Error('full'); } } });
        await client.open(); client.close(); await client.check({ autoPopup: true });
        expect(client.hasUnread()).toBe(false);
        client.destroy(); page.client.destroy(); page.dom.window.close();
    });
    it('binds unread dots and the modal to a second UI document', async () => {
        const page = setup();
        const pip = new JSDOM('<!doctype html><body><button id="announcementsButton">notice</button></body>', { url: 'http://localhost/' });
        page.client.bind(pip.window.document);
        await page.client.check();
        expect(pip.window.document.querySelector('.announcement-unread-dot').hidden).toBe(false);
        await page.client.open(pip.window.document);
        expect(pip.window.document.querySelector('[role=dialog]')).not.toBeNull();
        expect(page.doc.querySelector('[role=dialog]')).toBeNull();
        expect(page.doc.querySelector('.announcement-unread-dot').hidden).toBe(true);
        page.client.destroy(); pip.window.close(); page.dom.window.close();
    });
    it('does not reopen a loading list after the user dismisses it', async () => {
        const page = setup(); let resolveFetch;
        page.fetch.mockImplementationOnce(() => new Promise(resolve => { resolveFetch = resolve; }));
        const pending = page.client.open();
        expect(page.doc.querySelector('[role=dialog]').textContent).toContain('正在加载');
        page.client.close();
        resolveFetch({ ok: true, json: async () => ({ announcements: page.getRows() }) });
        await pending;
        expect(page.doc.querySelector('[role=dialog]')).toBeNull();
        expect(page.client.hasUnread()).toBe(true);
        page.client.destroy(); page.dom.window.close();
    });
    it('supports combined bold and highlight without losing reads after temporary hiding', async () => {
        const page = setup(), original = { ...page.getRows()[0], content: '**==both==**' };
        page.setRows([original]); await page.client.open();
        expect(page.doc.querySelector('strong mark').textContent).toBe('both');
        page.client.close(); page.setRows([]); await page.client.open(); page.client.close();
        page.setRows([original]); await page.client.check({ force: true });
        expect(page.client.hasUnread()).toBe(false);
        page.client.destroy(); page.dom.window.close();
    });
});

function updateSetup(overrides = {}) {
    const dom = new JSDOM('<!doctype html><body><div id="overlay" hidden></div><section id="dialog" hidden><button id="update"></button><button id="later"></button><button id="direct"></button></section></body>', { url: 'http://localhost/' });
    const doc = dom.window.document, order = [];
    const onChecked = vi.fn(() => { order.push('announcement'); });
    const state = { relayAvailable: true, connectionMode: 'relay', currentVersion: '1.0.0', latestVersion: '1.1.0', minimumVersion: '0.9.0', updateUrl: 'https://example.com/update', ...overrides };
    dom.window.open = vi.fn();
    const controller = Update.create({ Billing: Hosted.Billing, window: dom.window, storage: dom.window.localStorage, getState: () => state,
        showConfirm: vi.fn().mockResolvedValue(true), onSwitchDirect: async () => { order.push('direct'); }, onChecked,
        elements: { overlay: doc.getElementById('overlay'), dialog: doc.getElementById('dialog'), updateButton: doc.getElementById('update'), laterButton: doc.getElementById('later'), directButton: doc.getElementById('direct') } });
    return { dom, doc, order, state, controller, onChecked };
}
describe('announcement checks after update decisions', () => {
    it.each(['later', 'update'])('waits for optional %s before checking announcements', async action => {
        const page = updateSetup(); const pending = page.controller.ensure();
        expect(page.onChecked).not.toHaveBeenCalled();
        page.doc.getElementById(action).click();
        await expect(pending).resolves.toBe(true);
        expect(page.doc.getElementById('dialog').hidden).toBe(true);
        expect(page.onChecked).toHaveBeenCalledOnce();
        page.dom.window.close();
    });
    it('checks immediately after a completed no-update check', async () => {
        const page = updateSetup({ latestVersion: '1.0.0' });
        await page.controller.ensure(); expect(page.onChecked).toHaveBeenCalledOnce(); page.dom.window.close();
    });
    it('waits until the switch to direct mode has completed', async () => {
        const page = updateSetup({ minimumVersion: '1.1.0' }); const pending = page.controller.ensure();
        expect(page.order).toEqual([]); page.doc.getElementById('direct').click();
        await expect(pending).resolves.toBe(false); expect(page.order).toEqual(['direct', 'announcement']); page.dom.window.close();
    });
    it('allows announcements after choosing a required download while the version gate remains blocking', async () => {
        const page = updateSetup({ minimumVersion: '1.1.0' }); const pending = page.controller.ensure();
        expect(page.onChecked).not.toHaveBeenCalled(); page.doc.getElementById('update').click();
        expect(page.onChecked).toHaveBeenCalledOnce(); expect(page.doc.getElementById('dialog').hidden).toBe(false);
        page.controller.close('later'); await expect(pending).resolves.toBe(false); page.dom.window.close();
    });
});
