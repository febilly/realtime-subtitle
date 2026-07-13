const { createPageHarness } = require('./helpers/page-harness');

function pathOf(url) {
    return new URL(String(url), 'http://localhost/').pathname;
}

function selectRadio(page, name, value) {
    const radio = page.document.querySelector(`input[name="${name}"][value="${value}"]`);
    radio.checked = true;
    radio.dispatchEvent(new page.window.Event('change', { bubbles: true }));
    return radio;
}

describe('full-page settings save orchestration', () => {
    it('saves a trimmed Gemini key in direct mode', async () => {
        const page = await createPageHarness({
            uiConfig: {
                provider: 'soniox',
                env_key_present: { soniox: false, gemini: false },
                key_source: 'env',
                mode: 'direct',
                setup_required: false,
            },
        });
        try {
            page.document.getElementById('settingsButton').click();
            selectRadio(page, 'provider', 'gemini');
            page.document.getElementById('apiKeyInput').value = '  gemini-key  ';
            const beforeSave = page.fetchCalls.length;
            page.document.getElementById('settingsSaveButton').click();
            await page.flush(12);

            const setupCall = page.fetchCalls.slice(beforeSave).find(([url]) => pathOf(url) === '/setup');
            expect(JSON.parse(setupCall[1].body)).toEqual({
                provider: 'gemini',
                mode: 'direct',
                api_key: 'gemini-key',
                sleep_on_silence: true,
            });
            const provider = JSON.parse(page.window.localStorage.getItem('providerSettings.v1'));
            expect(provider).toMatchObject({
                providerOverride: 'gemini',
                keys: { gemini: 'gemini-key' },
            });
            expect(page.document.getElementById('settingsPanel').hidden).toBe(true);
        } finally {
            page.close();
        }
    });

    it('keeps direct settings open when neither override nor env key exists', async () => {
        const initialProvider = { providerOverride: null, keys: {} };
        const page = await createPageHarness({
            uiConfig: {
                provider: 'soniox',
                env_key_present: { soniox: false, gemini: false },
                key_source: 'env',
                mode: 'direct',
                setup_required: false,
            },
            localStorage: {
                'providerSettings.v1': JSON.stringify(initialProvider),
            },
        });
        try {
            page.document.getElementById('settingsButton').click();
            const beforeSave = page.fetchCalls.length;
            page.document.getElementById('settingsSaveButton').click();
            await page.flush(12);

            expect(page.fetchCalls.slice(beforeSave).some(([url]) => pathOf(url) === '/setup')).toBe(false);
            expect(page.document.getElementById('settingsPanel').hidden).toBe(false);
            expect(page.document.getElementById('settingsError').textContent)
                .toBe('Please enter an API key.');
            expect(JSON.parse(page.window.localStorage.getItem('providerSettings.v1')))
                .toEqual(initialProvider);
            expect(JSON.parse(page.window.localStorage.getItem('subtitleServer.v1')))
                .toMatchObject({ mode: 'direct', modeChosen: true });
        } finally {
            page.close();
        }
    });

    it('switches direct settings to relay login when the token is missing', async () => {
        const hostedConfig = {
            relay_available: true,
            server_url: 'https://relay.example',
            provider: 'soniox',
            env_key_present: { soniox: true, gemini: false },
            key_source: 'env',
            mode: 'direct',
            logged_in: false,
            setup_required: false,
        };
        const page = await createPageHarness({
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            localStorage: {
                'subtitleServer.v1': JSON.stringify({ mode: 'direct', modeChosen: true }),
            },
        });
        try {
            page.document.getElementById('settingsButton').click();
            selectRadio(page, 'connmode', 'relay');
            const beforeSave = page.fetchCalls.length;
            page.document.getElementById('settingsSaveButton').click();
            await page.flush(12);

            expect(page.fetchCalls.slice(beforeSave).some(([url]) => pathOf(url) === '/setup')).toBe(false);
            expect(JSON.parse(page.window.localStorage.getItem('subtitleServer.v1')))
                .toMatchObject({ mode: 'relay', modeChosen: true });
            expect(page.document.getElementById('settingsPanel').hidden).toBe(true);
            expect(page.document.getElementById('loginPanel').hidden).toBe(false);
        } finally {
            page.close();
        }
    });

    it('pushes a relay token without leaking a provider key or Soniox region', async () => {
        const hostedConfig = {
            relay_available: true,
            server_url: 'https://relay.example',
            provider: 'soniox',
            mode: 'relay',
            logged_in: true,
            setup_required: false,
        };
        const page = await createPageHarness({
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            localStorage: {
                'subtitleServer.v1': JSON.stringify({
                    mode: 'relay', modeChosen: true, token: 'relay-token',
                }),
                'providerSettings.v1': JSON.stringify({
                    providerOverride: 'soniox', sonioxRegion: 'eu', keys: { gemini: 'must-not-leak' },
                }),
            },
        });
        try {
            page.document.getElementById('settingsButton').click();
            selectRadio(page, 'provider', 'gemini');
            const beforeSave = page.fetchCalls.length;
            page.document.getElementById('settingsSaveButton').click();
            await page.flush(12);

            const setupCall = page.fetchCalls.slice(beforeSave).find(([url]) => pathOf(url) === '/setup');
            expect(JSON.parse(setupCall[1].body)).toEqual({
                provider: 'gemini',
                mode: 'relay',
                token: 'relay-token',
                sleep_on_silence: true,
            });
            const provider = JSON.parse(page.window.localStorage.getItem('providerSettings.v1'));
            expect(provider.providerOverride).toBe('gemini');
            expect(page.document.getElementById('settingsPanel').hidden).toBe(true);
        } finally {
            page.close();
        }
    });
});
