const { JSDOM } = require('jsdom');
const SettingsUI = require('../../static/js/settings-ui.js');
const AudioSourceUI = require('../../static/js/audio-source-ui.js');

const initial = () => ({ desktop_enabled: true, microphone_enabled: false, desktop_mode: 'output',
    output_device_id: '', microphone_device_id: '', window: null, remember: false });
const target = { pid: 42, hwnd: '84', created: '100', executable: 'C:\\player.exe', title: 'Player' };

function setup({ reject = false, unavailable = false, settings = initial(), captureError = '', windows = [target], vrchatPackEnabled = false } = {}) {
    const dom = new JSDOM('<button id="audio"></button>', { url: 'http://localhost' });
    const document = dom.window.document;
    let saved = settings;
    const onApplied = vi.fn();
    const fetch = vi.fn(async (url, init = {}) => {
        let body;
        if (init.method === 'POST') {
            if (reject) return { ok: false, status: 400, json: async () => ({ message: 'Selected audio window is unavailable' }) };
            saved = JSON.parse(init.body).settings;
            body = { status: 'ok', source: saved.desktop_enabled ? (saved.microphone_enabled ? 'mix' : 'system') : 'microphone', settings: saved };
        } else if (url === '/audio-source') body = { source: 'system', settings: saved, capture_error: captureError };
        else if (url === '/audio-windows') body = { available: !unavailable, windows: unavailable ? [] : windows };
        else body = { available: true, devices: [{ id: 'usb', name: 'USB' }] };
        return { ok: true, json: async () => body };
    });
    const controller = AudioSourceUI.create({ document, window: dom.window, fetch,
        settingsUI: SettingsUI, t: (key) => key, button: document.querySelector('button'),
        isVrchatPackEnabled: () => vrchatPackEnabled, onApplied });
    return { dom, document, controller, fetch, onApplied, saved: () => saved,
        setCaptureError: (value) => { captureError = value; },
        posts: () => fetch.mock.calls.filter(([, init]) => init?.method === 'POST') };
}

describe('audio source selector', () => {
    it('monitors a missing runtime window, flashes only the button, and clears it after recovery', async () => {
        const page = setup({ settings: { ...initial(), desktop_mode: 'window', window: target },
            captureError: 'Selected audio window is unavailable' });
        let tick;
        const interval = vi.spyOn(page.dom.window, 'setInterval').mockImplementation((callback) => {
            tick = callback;
            return 1;
        });
        try {
            await page.controller.fetchState();
            const button = page.document.getElementById('audio');
            expect(button.classList.contains('audio-window-waiting')).toBe(true);
            page.controller.startMonitoring();
            page.controller.startMonitoring();
            expect(interval).toHaveBeenCalledTimes(1);
            expect(interval).toHaveBeenCalledWith(expect.any(Function), 1000);
            await tick();
            expect(page.controller.isOpen()).toBe(false);
            expect(button.classList.contains('audio-window-waiting')).toBe(true);
            page.setCaptureError('');
            await tick();
            expect(button.classList.contains('audio-window-waiting')).toBe(false);
            expect(page.controller.isOpen()).toBe(false);
            expect(page.posts()).toHaveLength(0);
        } finally {
            page.dom.window.close();
        }
    });

    it('does not flash the runtime warning for an empty startup selection or a disabled desktop source', async () => {
        for (const settings of [
            { ...initial(), desktop_mode: 'window' },
            { ...initial(), desktop_enabled: false, microphone_enabled: true, desktop_mode: 'window', window: target },
            initial(),
        ]) {
            const page = setup({ settings, captureError: 'Selected audio window is unavailable' });
            try {
                await page.controller.fetchState();
                expect(page.document.getElementById('audio').classList.contains('audio-window-waiting')).toBe(false);
            } finally {
                page.dom.window.close();
            }
        }
    });

    it('ignores an old monitoring response after the user applies a different capture mode', async () => {
        const settings = { ...initial(), desktop_mode: 'window', window: target };
        const page = setup({ settings });
        try {
            await page.controller.show();
            page.document.querySelector('[data-desktop-mode="output"]').click();
            let release;
            const gate = new Promise(resolve => { release = resolve; });
            page.fetch.mockImplementationOnce(async () => {
                await gate;
                return { ok: true, json: async () => ({ source: 'system', settings,
                    capture_error: 'Selected audio window is unavailable' }) };
            });
            const oldRequest = page.controller.fetchState();
            expect(await page.controller.applyDraft()).toBe(true);
            release();
            expect(await oldRequest).toBe(false);
            expect(page.document.getElementById('audio').classList.contains('audio-window-waiting')).toBe(false);
            expect(page.saved().desktop_mode).toBe('output');
        } finally {
            page.dom.window.close();
        }
    });

    it('shows the missing window name in orange while keeping the placeholder first in the dropdown', async () => {
        const page = setup({ windows: [],
            settings: { ...initial(), desktop_mode: 'window', window: target, remember: true },
            captureError: 'Selected audio window is unavailable' });
        try {
            await page.controller.fetchState();
            expect(page.controller.isOpen()).toBe(false);
            await page.controller.show();
            expect(page.controller.isOpen()).toBe(true);
            expect(page.controller.getDraft().window).toEqual(target);
            expect(page.document.querySelector('.audio-window-placeholder .custom-select-label').textContent)
                .toBe('[player.exe] Player');
            expect(page.document.querySelector('[role="alert"]').textContent).toBe('audio_window_missing');
            const trigger = page.document.querySelector('.audio-window-placeholder .custom-select-button');
            trigger.click();
            const choices = [...page.document.querySelectorAll('[role="option"]')];
            expect(choices.map(item => item.textContent)).toEqual(['audio_choose_window', '[player.exe] Player']);
            expect(choices[1].getAttribute('aria-selected')).toBe('true');
            choices[0].click();
            expect(page.controller.getDraft().window).toBeNull();
            expect(page.document.querySelector('.audio-window-placeholder .custom-select-label').textContent)
                .toBe('audio_choose_window');
            trigger.click();
            page.document.querySelectorAll('[role="option"]')[1].click();
            expect(page.controller.getDraft().window).toEqual(target);
            expect(page.posts()).toHaveLength(0);
        } finally {
            page.controller.hide();
            page.dom.window.close();
        }
    });

    it('does not prompt while fetching audio state for any source', async () => {
        for (const settings of [
            { ...initial(), desktop_mode: 'window', window: target },
            initial(),
            { ...initial(), desktop_enabled: false, microphone_enabled: true, desktop_mode: 'window' },
        ]) {
            const page = setup({ settings });
            try {
                await page.controller.fetchState();
                expect(page.controller.isOpen()).toBe(false);
            } finally {
                page.dom.window.close();
            }
        }
    });

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

    it('cancels all draft changes, including the remember switch', async () => {
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
        const trigger = page.document.querySelector('.audio-source-options .custom-select-button');
        trigger.click();
        [...page.document.querySelectorAll('[role="option"]')].find((item) => item.textContent === '[player.exe] Player').click();
        expect(await page.controller.applyDraft()).toBe(true);
        expect(page.saved()).toMatchObject({ desktop_mode: 'window', window: target });
        page.dom.window.close();
    });

    it('keeps a rejected draft open without applying the remember switch', async () => {
        const page = setup({ reject: true });
        await page.controller.show();
        page.document.getElementById('rememberAudioSettings').click();
        page.onApplied.mockClear();
        expect(await page.controller.applyDraft()).toBe(false);
        expect(page.controller.isOpen()).toBe(true);
        expect(page.document.getElementById('rememberAudioSettings').getAttribute('aria-checked')).toBe('true');
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
        expect(page.document.querySelector('.audio-source-options .custom-select-button').disabled).toBe(true);
        expect(page.document.body.textContent).toContain('audio_window_unsupported');
        expect(page.controller.getDraft().desktop_mode).toBe('window');
        page.controller.hide();
        page.dom.window.close();
    });

    it('renders the VRChat mic mute follow switch only when VRChat pack is enabled, defaults off, has underline + tooltip, and submits changes', async () => {
        const disabledPage = setup({ vrchatPackEnabled: false });
        try {
            await disabledPage.controller.show();
            expect(disabledPage.document.getElementById('audio-follow_vrchat_mic_mute')).toBeNull();
        } finally {
            disabledPage.controller.hide();
            disabledPage.dom.window.close();
        }

        let packEnabled = true;
        const page = setup({
            vrchatPackEnabled: true,
            settings: { ...initial(), microphone_enabled: true, follow_vrchat_mic_mute: false },
        });
        try {
            await page.controller.show();
            const toggle = page.document.getElementById('audio-follow_vrchat_mic_mute');
            const label = page.document.getElementById('audio-follow_vrchat_mic_mute-label');
            const row = toggle.closest('.audio-follow-vrchat-mute-row');
            expect(toggle).not.toBeNull();
            expect(toggle.getAttribute('aria-checked')).toBe('false');
            expect(label.classList.contains('audio-tooltip-underline')).toBe(true);
            expect(label.textContent).toBe('audio_follow_vrchat_mute');
            expect(row.getAttribute('title')).toBe('audio_follow_vrchat_mute_hint');
            // Located inside the microphone section below the microphone picker
            const micOptions = page.document.querySelectorAll('.audio-source-options')[1];
            expect(micOptions.contains(row)).toBe(true);
            expect(micOptions.firstElementChild.classList.contains('custom-select')).toBe(true);

            toggle.click();
            expect(toggle.getAttribute('aria-checked')).toBe('true');
            expect(await page.controller.applyDraft()).toBe(true);
            expect(page.saved().follow_vrchat_mic_mute).toBe(true);
        } finally {
            packEnabled = false;
            void packEnabled;
            page.dom.window.close();
        }
    });

    it('positions the popover higher using the full expanded baseline and refreshes when feature packs change', async () => {
        let vrchatEnabled = true;
        const dom = new JSDOM('<button id="audio"></button>', { url: 'http://localhost' });
        const document = dom.window.document;
        Object.defineProperty(dom.window, 'innerHeight', { configurable: true, value: 560 });
        const positionPopoverAtBaseline = vi.fn((panel, prepareBaseline, opts) => {
            const clone = panel.cloneNode(true);
            prepareBaseline(clone);
            const contents = clone.querySelectorAll('.audio-source-options');
            expect(contents[0].hidden).toBe(false);
            expect(contents[1].hidden).toBe(false);
            expect(opts).toEqual({ verticalRatio: 0.15, minTop: 12 });
            panel.style.setProperty('--lang-popover-top', '18px');
        });
        const fetch = vi.fn(async (url) => {
            if (url === '/audio-source') return { ok: true, json: async () => ({ source: 'system', settings: { ...initial(), follow_vrchat_mic_mute: false } }) };
            if (url === '/audio-windows') return { ok: true, json: async () => ({ available: true, windows: [] }) };
            return { ok: true, json: async () => ({ available: true, devices: [] }) };
        });
        const controller = AudioSourceUI.create({
            document,
            window: dom.window,
            fetch,
            settingsUI: { create: () => ({ ...SettingsUI.create({ document, window: dom.window }), positionPopoverAtBaseline }) },
            t: (key) => key,
            button: document.querySelector('button'),
            isVrchatPackEnabled: () => vrchatEnabled,
        });
        try {
            await controller.show();
            expect(positionPopoverAtBaseline).toHaveBeenCalled();
            expect(document.querySelector('.audio-source-popover').style.getPropertyValue('--lang-popover-top')).toBe('18px');
            expect(document.getElementById('audio-follow_vrchat_mic_mute')).not.toBeNull();

            vrchatEnabled = false;
            controller.refreshView();
            expect(document.getElementById('audio-follow_vrchat_mic_mute')).toBeNull();
        } finally {
            controller.hide();
            dom.window.close();
        }
    });
});
