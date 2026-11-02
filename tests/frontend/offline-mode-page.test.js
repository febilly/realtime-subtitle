const { createPageHarness, defaultFetchResponse } = require('./helpers/page-harness');

describe('temporary offline startup', () => {
    it.each(['fast', 'off'])('uses %s locally and preserves the saved startup choices', async (mode) => {
        let offline = false;
        let paused = true;
        const savedProvider = JSON.stringify({ providerOverride: 'gemini', localConfig: { backend: 'remote' }, keys: {} });
        const savedServer = JSON.stringify({ mode: 'relay', modeChosen: true, token: 'saved-token' });
        const page = await createPageHarness({
            translationUiMode: 'accurate',
            localStorage: { 'providerSettings.v1': savedProvider, 'subtitleServer.v1': savedServer },
            fetch: async (url, request = {}) => {
                const path = new URL(String(url), 'http://localhost').pathname;
                if (path === '/setup') {
                    const body = JSON.parse(request.body);
                    expect(body).toMatchObject({ provider: 'local', mode: 'direct', local_config: { backend: 'local' } });
                    if (!offline) expect(body).toMatchObject({ offline_mode: true, start_paused: true });
                    offline = true;
                    return { ok: true, json: async () => ({ provider: 'local', mode: 'direct', setup_required: false }) };
                }
                if (path === '/resume') paused = false;
                return defaultFetchResponse(url, { ...request, translationUiMode: 'accurate', uiConfig: {
                    provider: offline ? 'local' : 'gemini', relay_available: true, server_url: 'https://relay.example',
                    mode: offline ? 'direct' : 'relay', offline_mode: offline,
                    hosted_reachable: false, offline_models: { asr: true, translation: mode === 'fast' },
                    translation_ui_mode: offline ? mode : 'accurate', llm_refine_available: !offline,
                    recognition_paused: offline && paused,
                } });
            },
        });
        try {
            expect(page.document.getElementById('confirmDialog').hidden).toBe(false);
            page.document.getElementById('confirmCancelButton').click();
            await page.flush(20);
            expect(offline).toBe(true);
            expect(page.document.getElementById('pauseButton').classList.contains('is-paused')).toBe(true);
            page.document.getElementById('settingsButton').click();
            await page.flush(10);
            const radios = [...page.document.querySelectorAll('input[name="provider"]')];
            expect(radios.every((radio) => radio.disabled)).toBe(true);
            expect(radios.find((radio) => radio.checked).value).toBe('local');
            expect(page.window.localStorage.getItem('translationUiMode')).toBe('accurate');
            expect(page.window.localStorage.getItem('llmRefineMode')).toBe('translate');
            expect(page.window.localStorage.getItem('subtitleServer.v1')).toBe(savedServer);
            expect(page.window.localStorage.getItem('providerSettings.v1')).toBe(savedProvider);
            expect(page.document.getElementById('translationLangButton').getAttribute('aria-disabled')).toBe(String(mode === 'off'));
            page.document.querySelector('#translationModePicker .lang-picker-button').click();
            expect(page.document.querySelectorAll('.lang-select-menu [role="option"]')).toHaveLength(mode === 'fast' ? 2 : 1);
            if (mode === 'fast') {
                const options = [...page.document.querySelectorAll('.lang-select-menu [role="option"]')];
                options.at(-1).click();
            } else {
                page.document.querySelector('.lang-select-menu [role="option"]').click();
            }
            page.document.getElementById('settingsSaveButton').click();
            await page.flush(20);
            expect(JSON.parse(page.window.localStorage.getItem('providerSettings.v1'))).toMatchObject({ providerOverride: 'gemini', localConfig: { backend: 'remote' } });
            expect(page.window.localStorage.getItem('subtitleServer.v1')).toBe(savedServer);
            expect(page.window.localStorage.getItem('translationUiMode')).toBe('accurate');
            expect(page.document.getElementById('pauseButton').classList.contains('is-paused')).toBe(true);
            expect(page.fetchCalls.some(([url]) => ['/resume', '/restart'].includes(new URL(String(url), 'http://localhost').pathname))).toBe(false);
            page.document.getElementById('pauseButton').click();
            await page.flush(10);
            expect(paused).toBe(false);
        } finally { page.close(); }
    });
});
