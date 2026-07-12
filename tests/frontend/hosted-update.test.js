const { JSDOM } = require('jsdom');
const Hosted = require('../../static/js/hosted');
const HostedUpdate = require('../../static/js/hosted-update');

function setup(overrides = {}) {
    const dom = new JSDOM(`<!doctype html><body>
        <div id="overlay" hidden></div><section id="dialog" hidden>
            <h2 id="title"></h2><p id="body"></p>
            <span id="currentLabel"></span><span id="latestLabel"></span><span id="minimumLabel"></span>
            <span id="current"></span><span id="latest"></span><span id="minimum"></span>
            <p id="notes"></p><p id="noUrl"></p>
            <button id="direct"></button><button id="later"></button><button id="update"></button>
        </section>
    </body>`, { url: 'http://localhost/' });
    const document = dom.window.document;
    const state = {
        relayAvailable: true,
        connectionMode: 'relay',
        currentVersion: '1.0.0',
        latestVersion: '1.1.0',
        minimumVersion: '0.9.0',
        updateUrl: 'https://example.com/update',
        notes: 'changes',
        ...overrides.state,
    };
    let clock = overrides.now ?? 100000;
    const storage = overrides.storage || dom.window.localStorage;
    const showConfirm = overrides.showConfirm || vi.fn().mockResolvedValue(true);
    const onSwitchDirect = vi.fn();
    const open = vi.fn();
    dom.window.open = open;
    const controller = HostedUpdate.create({
        Billing: Hosted.Billing,
        window: dom.window,
        storage,
        t: (key) => key,
        showConfirm,
        now: () => clock,
        getState: () => state,
        onSwitchDirect,
        elements: {
            overlay: document.getElementById('overlay'),
            dialog: document.getElementById('dialog'),
            title: document.getElementById('title'),
            body: document.getElementById('body'),
            currentLabel: document.getElementById('currentLabel'),
            latestLabel: document.getElementById('latestLabel'),
            minimumLabel: document.getElementById('minimumLabel'),
            currentValue: document.getElementById('current'),
            latestValue: document.getElementById('latest'),
            minimumValue: document.getElementById('minimum'),
            notes: document.getElementById('notes'),
            noUrl: document.getElementById('noUrl'),
            directButton: document.getElementById('direct'),
            laterButton: document.getElementById('later'),
            updateButton: document.getElementById('update'),
        },
    });
    return {
        advance(ms) { clock += ms; },
        controller,
        document,
        dom,
        onSwitchDirect,
        open,
        showConfirm,
        state,
        storage,
    };
}

describe('HostedUpdate state', () => {
    it.each([
        ['direct mode', { mode: 'direct' }, { needed: false, forced: false }],
        ['relay unavailable', { relayAvailable: false, mode: 'relay' }, { needed: false, forced: false }],
        ['current', { mode: 'relay', currentVersion: '1.1.0', latestVersion: '1.1.0' }, { needed: false, forced: false }],
        ['optional', { mode: 'relay', currentVersion: '1.0.0', latestVersion: '1.1.0', minimumVersion: '0.9.0' }, { needed: true, forced: false }],
        ['forced', { mode: 'relay', currentVersion: '1.0.0', latestVersion: '1.2.0', minimumVersion: '1.1.0' }, { needed: true, forced: true }],
    ])('%s', (_label, input, expected) => {
        expect(HostedUpdate.resolveUpdateState({ relayAvailable: true, ...input }, Hosted.Billing))
            .toMatchObject(expected);
    });

    it('falls back from missing latest version to the minimum version', () => {
        expect(HostedUpdate.resolveUpdateState({
            relayAvailable: true,
            mode: 'relay',
            currentVersion: '1.0.0',
            minimumVersion: '1.1.0',
        }, Hosted.Billing)).toMatchObject({ latest: '1.1.0', forced: true });
    });
});

describe('HostedUpdate dialog and policy', () => {
    it('renders optional update details, opens the URL, and resolves later', async () => {
        const page = setup();
        const result = page.controller.show(page.controller.getState());
        expect(page.document.getElementById('overlay').hidden).toBe(false);
        expect(page.document.getElementById('notes').textContent).toBe('changes');
        expect(page.document.getElementById('direct').hidden).toBe(true);
        page.document.getElementById('update').click();
        expect(page.open).toHaveBeenCalledWith(
            'https://example.com/update', '_blank', 'noopener,noreferrer',
        );
        page.document.getElementById('later').click();
        await expect(result).resolves.toBe('later');
        page.dom.window.close();
    });

    it('requires confirmation before a forced direct-mode escape', async () => {
        const page = setup({
            state: { currentVersion: '1.0.0', minimumVersion: '1.1.0' },
        });
        const result = page.controller.ensure();
        expect(page.document.getElementById('later').hidden).toBe(true);
        expect(page.document.getElementById('direct').hidden).toBe(false);
        page.document.getElementById('direct').click();
        await Promise.resolve();
        await expect(result).resolves.toBe(false);
        expect(page.showConfirm).toHaveBeenCalledWith('client_update_direct_confirm', {
            okLabel: 'client_update_direct_confirm_ok',
            cancelLabel: 'client_update_direct_confirm_cancel',
            danger: true,
        });
        expect(page.onSwitchDirect).toHaveBeenCalledOnce();
        page.dom.window.close();
    });

    it('restores a forced dialog when direct-mode confirmation is cancelled', async () => {
        const page = setup({
            state: { currentVersion: '1.0.0', minimumVersion: '1.1.0' },
            showConfirm: vi.fn().mockResolvedValue(false),
        });
        const result = page.controller.ensure();
        page.document.getElementById('direct').click();
        await Promise.resolve();
        expect(page.document.getElementById('dialog').hidden).toBe(false);
        expect(page.onSwitchDirect).not.toHaveBeenCalled();
        page.controller.close('later');
        await expect(result).resolves.toBe(false);
        page.dom.window.close();
    });

    it('throttles optional reminders and records the display timestamp', async () => {
        const page = setup();
        page.storage.setItem(HostedUpdate.DEFAULT_REMINDER_KEY, '99999');
        await expect(page.controller.ensure()).resolves.toBe(true);
        expect(page.document.getElementById('dialog').hidden).toBe(true);

        page.advance(HostedUpdate.DEFAULT_REMINDER_MS + 1);
        const result = page.controller.ensure();
        page.document.getElementById('later').click();
        await expect(result).resolves.toBe(true);
        expect(Number(page.storage.getItem(HostedUpdate.DEFAULT_REMINDER_KEY))).toBeGreaterThan(99999);
        page.dom.window.close();
    });

    it('disables the update button and shows a missing-URL hint', () => {
        const page = setup({ state: { updateUrl: '' } });
        page.controller.show(page.controller.getState());
        expect(page.document.getElementById('update').disabled).toBe(true);
        expect(page.document.getElementById('noUrl').hidden).toBe(false);
        page.controller.destroy();
        page.dom.window.close();
    });

    it('falls back without dialog DOM according to forced status', async () => {
        const direct = vi.fn();
        const forced = HostedUpdate.create({
            Billing: Hosted.Billing,
            getState: () => ({
                relayAvailable: true,
                connectionMode: 'relay',
                currentVersion: '1.0.0',
                minimumVersion: '1.1.0',
            }),
            onSwitchDirect: direct,
        });
        await expect(forced.ensure()).resolves.toBe(false);
        expect(direct).toHaveBeenCalledOnce();
    });
});
