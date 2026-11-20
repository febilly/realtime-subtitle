const { createPageHarness, defaultFetchResponse } = require('./helpers/page-harness');

describe('full-page hosted mode wiring', () => {
    it('preopens first-launch login with the own-key switch button and no direct mode switch yet', async () => {
        const hostedConfig = {
            relay_available: true,
            server_url: 'https://relay.example',
            mode: 'relay',
            logged_in: false,
            setup_required: true,
        };
        const page = await createPageHarness({
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
        });
        try {
            const ownKeyButton = page.document.getElementById('loginModeBackButton');
            expect(page.document.getElementById('loginPanel').hidden).toBe(false);
            expect(page.document.getElementById('loginOverlay').hidden).toBe(false);
            expect(ownKeyButton).not.toBeNull();
            expect(ownKeyButton.hidden).toBe(false);
            expect(page.document.getElementById('ownKeyDialog').hidden).toBe(true);
            expect(JSON.parse(page.window.localStorage.getItem('subtitleServer.v1')))
                .toMatchObject({ mode: 'relay', modeChosen: true });
        } finally {
            page.close();
        }
    });

    it('opens own-key dialog without immediate switch, validates key, and pre-fills Settings only after confirmation succeeds', async () => {
        const hostedConfig = {
            relay_available: true,
            server_url: 'https://relay.example',
            mode: 'relay',
            logged_in: false,
            setup_required: true,
        };
        const setupBodies = [];
        const shortcutPosts = [];
        let shortcutExists = false;
        let allowSetup = false;
        let activeUiConfig = { ...hostedConfig };
        const page = await createPageHarness({
            initialUiConfig: hostedConfig,
            fetch: async (url, options = {}) => {
                const pathname = new URL(String(url), 'http://localhost/').pathname;
                if (pathname === '/desktop-shortcut') {
                    if (options.method === 'POST') {
                        shortcutPosts.push(JSON.parse(options.body));
                        shortcutExists = true;
                        return {
                            ok: true,
                            status: 200,
                            json: async () => ({ available: true, exists: true, created: true }),
                            text: async () => '{"available":true,"exists":true,"created":true}',
                        };
                    }
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({ available: true, exists: shortcutExists, matched: shortcutExists ? 1 : 0 }),
                        text: async () => '{}',
                    };
                }
                if (pathname === '/setup' && options.method === 'POST') {
                    const body = JSON.parse(options.body);
                    setupBodies.push(body);
                    if (!allowSetup) {
                        return {
                            ok: false,
                            status: 400,
                            json: async () => ({ status: 'error', message: 'Invalid API key' }),
                            text: async () => '{"status":"error","message":"Invalid API key"}',
                        };
                    }
                    activeUiConfig = {
                        ...hostedConfig,
                        provider: body.provider,
                        mode: 'direct',
                        setup_required: false,
                        soniox_region: body.soniox_region || 'us',
                        key_source: 'localstorage',
                    };
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({
                            status: 'ok',
                            boot_id: 'boot-1',
                            provider: body.provider,
                            mode: 'direct',
                            setup_required: false,
                            soniox_region: body.soniox_region || 'us',
                        }),
                        text: async () => '{}',
                    };
                }
                return defaultFetchResponse(url, { ...options, uiConfig: activeUiConfig });
            },
        });
        try {
            // Clicking the own-key button opens the standalone dialog, keeping login and relay mode intact.
            page.document.getElementById('loginModeBackButton').click();
            await page.flush(10);
            expect(page.document.getElementById('ownKeyDialog').hidden).toBe(false);
            expect(page.document.getElementById('ownKeyDesktopShortcutField').hidden).toBe(false);
            expect(page.document.getElementById('ownKeyDesktopShortcutCheckbox').checked).toBe(true);
            expect(page.document.getElementById('settingsPanel').hidden).toBe(true);
            expect(page.document.querySelector('#ownKeyDialog .panel-close-button')).toBeNull();
            expect(page.document.querySelectorAll('input[name="ownKeyProvider"]')).toHaveLength(2);
            expect(JSON.parse(page.window.localStorage.getItem('subtitleServer.v1')).mode).toBe('relay');

            // Back returns to login without changing mode or creating a shortcut.
            page.document.getElementById('ownKeyBackButton').click();
            expect(page.document.getElementById('ownKeyDialog').hidden).toBe(true);
            expect(page.document.getElementById('loginPanel').hidden).toBe(false);
            expect(shortcutPosts).toEqual([]);

            // Empty submission prompts for a key in the warning line without calling /setup or creating a shortcut.
            page.document.getElementById('loginModeBackButton').click();
            await page.flush(10);
            page.document.getElementById('ownKeyConfirmButton').click();
            await page.flush(10);
            expect(setupBodies).toEqual([]);
            expect(shortcutPosts).toEqual([]);
            expect(page.document.getElementById('ownKeyError').textContent.length).toBeGreaterThan(0);

            // Invalid key shows unavailable warning and stays in relay mode without creating a shortcut.
            const keyInput = page.document.getElementById('ownKeyApiKeyInput');
            keyInput.value = 'sk-bad';
            page.document.getElementById('ownKeyConfirmButton').click();
            await page.flush(20);
            expect(setupBodies).toHaveLength(1);
            expect(shortcutPosts).toEqual([]);
            expect(page.document.getElementById('ownKeyDialog').hidden).toBe(false);
            expect(page.document.getElementById('ownKeyError').textContent.length).toBeGreaterThan(0);
            expect(JSON.parse(page.window.localStorage.getItem('subtitleServer.v1')).mode).toBe('relay');

            // Valid Soniox JP key verifies, creates the checked shortcut silently, switches to direct mode, and pre-fills Settings.
            allowSetup = true;
            page.document.querySelector('#ownKeySonioxRegionPicker .lang-picker-button').click();
            page.document.querySelectorAll('.lang-select-option')[2].click();
            keyInput.value = 'sk-good-jp';
            page.document.getElementById('ownKeyConfirmButton').click();
            await page.flush(30);

            expect(shortcutPosts).toEqual([{ action: 'create' }]);

            expect(page.document.getElementById('ownKeyDialog').hidden).toBe(true);
            expect(page.document.getElementById('loginPanel').hidden).toBe(true);
            expect(page.document.getElementById('settingsPanel').hidden).toBe(false);
            expect(JSON.parse(page.window.localStorage.getItem('subtitleServer.v1')))
                .toMatchObject({ mode: 'direct', modeChosen: true });
            expect(JSON.parse(page.window.localStorage.getItem('providerSettings.v1')))
                .toMatchObject({
                    providerOverride: 'soniox',
                    sonioxRegion: 'jp',
                    keys: { soniox: 'sk-good-jp' },
                });
            expect(page.document.querySelector('input[name="connmode"][value="direct"]').checked).toBe(true);
            expect(page.document.querySelector('input[name="provider"][value="soniox"]').checked).toBe(true);
            expect(page.document.getElementById('apiKeyInput').value).toBe('sk-good-jp');
        } finally {
            page.close();
        }
    });

    it('hides the own-key shortcut option when a shortcut already exists and skips creation when unchecked', async () => {
        const hostedConfig = {
            relay_available: true,
            server_url: 'https://relay.example',
            mode: 'relay',
            logged_in: false,
            setup_required: true,
        };
        const shortcutPosts = [];
        let shortcutExists = true;
        const page = await createPageHarness({
            initialUiConfig: hostedConfig,
            fetch: async (url, options = {}) => {
                const pathname = new URL(String(url), 'http://localhost/').pathname;
                if (pathname === '/desktop-shortcut') {
                    if (options.method === 'POST') shortcutPosts.push(JSON.parse(options.body));
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({ available: true, exists: shortcutExists, matched: shortcutExists ? 1 : 0 }),
                        text: async () => '{}',
                    };
                }
                if (pathname === '/setup' && options.method === 'POST') {
                    const body = JSON.parse(options.body);
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({
                            status: 'ok',
                            boot_id: 'boot-2',
                            provider: body.provider,
                            mode: 'direct',
                            setup_required: false,
                        }),
                        text: async () => '{}',
                    };
                }
                return defaultFetchResponse(url, { ...options, uiConfig: hostedConfig });
            },
        });
        try {
            page.document.getElementById('loginModeBackButton').click();
            await page.flush(10);
            expect(page.document.getElementById('ownKeyDesktopShortcutField').hidden).toBe(true);

            // Returning and reopening when no shortcut exists shows it checked, then unchecking skips creation.
            page.document.getElementById('ownKeyBackButton').click();
            shortcutExists = false;
            page.document.getElementById('loginModeBackButton').click();
            await page.flush(10);

            const field = page.document.getElementById('ownKeyDesktopShortcutField');
            const checkbox = page.document.getElementById('ownKeyDesktopShortcutCheckbox');
            expect(field.hidden).toBe(false);
            expect(checkbox.checked).toBe(true);
            checkbox.checked = false;

            page.document.getElementById('ownKeyApiKeyInput').value = 'sk-good-no-shortcut';
            page.document.getElementById('ownKeyConfirmButton').click();
            await page.flush(30);

            expect(shortcutPosts).toEqual([]);
        } finally {
            page.close();
        }
    });
});
