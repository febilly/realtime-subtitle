const { JSDOM } = require('jsdom');
const SettingsUI = require('../../static/js/settings-ui.js');
const AudioSourceUI = require('../../static/js/audio-source-ui.js');

const initial = () => ({ desktop_enabled: true, microphone_enabled: false, desktop_mode: 'output',
    output_device_id: '', microphone_device_id: '', window: null, remember: false });
const target = { pid: 42, hwnd: '84', created: '100', executable: 'C:\\player.exe', title: 'Player' };

function setup({ reject = false, unavailable = false } = {}) {
    const dom = new JSDOM('<button id="audio"></button>', { url: 'http://localhost' });
    const document = dom.window.document;
    let saved = initial();
    const onApplied = vi.fn();
    const fetch = vi.fn(async (url, init = {}) => {
        let body;
        if (init.method === 'POST') {
            if (reject) return { ok: false, status: 400, json: async () => ({ message: 'Selected audio window is unavailable' }) };
            saved = JSON.parse(init.body).settings;
            body = { status: 'ok', source: saved.desktop_enabled ? (saved.microphone_enabled ? 'mix' : 'system') : 'microphone', settings: saved };
        } else if (url === '/audio-source') body = { source: 'system', settings: saved };
        else if (url === '/audio-windows') body = { available: !unavailable, windows: unavailable ? [] : [target] };
        else body = { available: true, devices: [{ id: 'usb', name: 'USB' }] };
        return { ok: true, json: async () => body };
    });
    const controller = AudioSourceUI.create({ document, window: dom.window, fetch,
        settingsUI: SettingsUI, t: (key) => key, button: document.querySelector('button'), onApplied });
    return { dom, document, controller, fetch, onApplied, saved: () => saved,
        posts: () => fetch.mock.calls.filter(([, init]) => init?.method === 'POST') };
}

describe('audio source selector', () => {
    it('lets both switches turn off, then validates on Apply without changing the backend', async () => {
        const page = setup();
        await page.controller.show();
        page.document.getElementById('audio-desktop_enabled').click();
        const remember = page.document.getElementById('rememberAudioSettings');
        remember.click();
        expect(page.controller.getDraft()).toMatchObject({ desktop_enabled: false, microphone_enabled: false, remember: true });
        expect(await page.controller.applyDraft()).toBe(false);
        expect(page.document.querySelector('[role="alert"]').textContent).toBe('audio_source_required');
        expect(page.posts()).toHaveLength(0);
        expect(page.saved()).toEqual(initial());
        page.controller.hide();
        page.dom.window.close();
    });

    it('cancels all draft changes, including the remember checkbox', async () => {
        const page = setup();
        await page.controller.show();
        page.document.getElementById('audio-microphone_enabled').click();
        page.document.getElementById('rememberAudioSettings').click();
        page.controller.hide();
        await page.controller.show();
        expect(page.controller.getDraft()).toEqual(initial());
        expect(page.posts()).toHaveLength(0);
        page.controller.hide();
        page.dom.window.close();
    });

    it('applies both sources and remember together; only the accepted response changes the toolbar', async () => {
        const page = setup();
        await page.controller.show();
        page.onApplied.mockClear();
        page.document.getElementById('audio-microphone_enabled').click();
        page.document.getElementById('rememberAudioSettings').click();
        expect(page.onApplied).not.toHaveBeenCalled();
        expect(await page.controller.applyDraft()).toBe(true);
        expect(page.saved()).toMatchObject({ desktop_enabled: true, microphone_enabled: true, remember: true });
        expect(page.onApplied).toHaveBeenCalledWith('mix');
        expect(page.controller.isOpen()).toBe(false);
        page.dom.window.close();
    });

    it('requires a specific window and submits its identity rather than a bare PID', async () => {
        const page = setup();
        await page.controller.show();
        page.document.querySelector('[data-desktop-mode="window"]').click();
        expect(await page.controller.applyDraft()).toBe(false);
        const trigger = page.document.querySelector('.audio-source-options .lang-picker-button');
        trigger.click();
        [...page.document.querySelectorAll('[role="option"]')].find((item) => item.textContent === 'Player').click();
        expect(await page.controller.applyDraft()).toBe(true);
        expect(page.saved()).toMatchObject({ desktop_mode: 'window', window: target });
        page.dom.window.close();
    });

    it('keeps a rejected draft open without applying the remember checkbox', async () => {
        const page = setup({ reject: true });
        await page.controller.show();
        page.document.getElementById('rememberAudioSettings').click();
        page.onApplied.mockClear();
        expect(await page.controller.applyDraft()).toBe(false);
        expect(page.controller.isOpen()).toBe(true);
        expect(page.document.getElementById('rememberAudioSettings').checked).toBe(true);
        expect(page.document.querySelector('[role="alert"]').textContent).toBe('audio_window_missing');
        expect(page.saved().remember).toBe(false);
        expect(page.onApplied).not.toHaveBeenCalled();
        page.controller.hide();
        page.dom.window.close();
    });

    it('shows unsupported window capture while retaining output and microphone choices', async () => {
        const page = setup({ unavailable: true });
        await page.controller.show();
        page.document.querySelector('[data-desktop-mode="window"]').click();
        expect(page.document.querySelector('.audio-source-options .lang-picker-button').disabled).toBe(true);
        expect(page.document.body.textContent).toContain('audio_window_unsupported');
        expect(page.controller.getDraft().desktop_mode).toBe('window');
        page.controller.hide();
        page.dom.window.close();
    });
});
