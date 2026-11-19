const { createPageHarness } = require('./helpers/page-harness');

describe('full-page hosted login wiring', () => {
    it('redeems a manual code, stores server credentials, and pushes relay setup', async () => {
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
            loginCodePayload: {
                success: true,
                api_key: 'relay-token',
                display_name: 'Test User',
                trust_rank: 'user',
            },
        });
        try {
            expect(page.document.getElementById('loginPanel').hidden).toBe(false);
            page.document.getElementById('loginManualToggle').click();
            const input = page.document.getElementById('loginUserInput');
            input.value = 'one-time-code';
            input.dispatchEvent(new page.window.Event('input', { bubbles: true }));
            page.document.getElementById('loginPrimaryButton').click();
            await page.flush(20);

            const loginCall = page.fetchCalls.find(([url]) => (
                new URL(String(url), 'http://localhost/').pathname === '/account/login-code'
            ));
            expect(JSON.parse(loginCall[1].body)).toEqual({ code: 'one-time-code' });
            const setupCall = page.fetchCalls.find(([url, options]) => (
                new URL(String(url), 'http://localhost/').pathname === '/setup'
                && options && options.method === 'POST'
            ));
            expect(JSON.parse(setupCall[1].body)).toMatchObject({
                provider: 'soniox', mode: 'relay', token: 'relay-token',
            });

            const stored = JSON.parse(page.window.localStorage.getItem('subtitleServer.v1'));
            expect(stored).toMatchObject({ mode: 'relay', modeChosen: true });
            expect(stored.servers['https://relay.example']).toMatchObject({
                token: 'relay-token', displayName: 'Test User', trustRank: 'user',
            });
            expect(page.document.getElementById('loginPanel').hidden).toBe(true);
            expect(page.document.getElementById('balanceBar').hidden).toBe(false);
        } finally {
            page.close();
        }
    });

    it('shows the default-checked shortcut option on login and creates only after login succeeds', async () => {
        const hostedConfig = {
            relay_available: true,
            server_url: 'https://relay.example',
            mode: 'relay',
            logged_in: false,
            setup_required: true,
        };
        const shortcutPosts = [];
        const page = await createPageHarness({
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            fetch: async (url, options = {}) => {
                const pathname = new URL(String(url), 'http://localhost/').pathname;
                if (pathname === '/desktop-shortcut') {
                    if (options.method === 'POST') {
                        shortcutPosts.push(JSON.parse(options.body));
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
                        json: async () => ({ available: true, exists: false, matched: 0 }),
                        text: async () => '{"available":true,"exists":false,"matched":0}',
                    };
                }
                if (pathname === '/account/login-code') {
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({
                            success: true,
                            api_key: 'relay-token',
                            display_name: 'Test User',
                            trust_rank: 'user',
                        }),
                        text: async () => '{}',
                    };
                }
                const { defaultFetchResponse } = require('./helpers/page-harness');
                return defaultFetchResponse(url, { ...options, uiConfig: hostedConfig });
            },
        });
        try {
            const field = page.document.getElementById('loginDesktopShortcutField');
            const checkbox = page.document.getElementById('loginDesktopShortcutCheckbox');
            expect(page.document.getElementById('desktopShortcutDialog')).toBeNull();
            expect(field.hidden).toBe(false);
            expect(checkbox.checked).toBe(true);
            expect(shortcutPosts).toEqual([]);

            page.document.getElementById('loginManualToggle').click();
            const input = page.document.getElementById('loginUserInput');
            input.value = 'one-time-code';
            input.dispatchEvent(new page.window.Event('input', { bubbles: true }));
            page.document.getElementById('loginPrimaryButton').click();
            await page.flush(20);

            expect(shortcutPosts).toEqual([{ action: 'create' }]);
        } finally {
            page.close();
        }
    });

    it('hides the shortcut checkbox and skips creation when a shortcut already exists', async () => {
        const hostedConfig = {
            relay_available: true,
            server_url: 'https://relay.example',
            mode: 'relay',
            logged_in: false,
            setup_required: true,
        };
        const shortcutPosts = [];
        const page = await createPageHarness({
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            fetch: async (url, options = {}) => {
                const pathname = new URL(String(url), 'http://localhost/').pathname;
                if (pathname === '/desktop-shortcut') {
                    if (options.method === 'POST') shortcutPosts.push(options.body);
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({ available: true, exists: true, matched: 1 }),
                        text: async () => '{"available":true,"exists":true,"matched":1}',
                    };
                }
                if (pathname === '/account/login-code') {
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({
                            success: true,
                            api_key: 'relay-token',
                            display_name: 'Test User',
                            trust_rank: 'user',
                        }),
                        text: async () => '{}',
                    };
                }
                const { defaultFetchResponse } = require('./helpers/page-harness');
                return defaultFetchResponse(url, { ...options, uiConfig: hostedConfig });
            },
        });
        try {
            expect(page.document.getElementById('loginDesktopShortcutField').hidden).toBe(true);

            page.document.getElementById('loginManualToggle').click();
            const input = page.document.getElementById('loginUserInput');
            input.value = 'one-time-code';
            input.dispatchEvent(new page.window.Event('input', { bubbles: true }));
            page.document.getElementById('loginPrimaryButton').click();
            await page.flush(20);

            expect(shortcutPosts).toEqual([]);
        } finally {
            page.close();
        }
    });

    it('skips shortcut creation when the user unchecks the option before logging in', async () => {
        const hostedConfig = {
            relay_available: true,
            server_url: 'https://relay.example',
            mode: 'relay',
            logged_in: false,
            setup_required: true,
        };
        const shortcutPosts = [];
        const page = await createPageHarness({
            initialUiConfig: hostedConfig,
            uiConfig: hostedConfig,
            fetch: async (url, options = {}) => {
                const pathname = new URL(String(url), 'http://localhost/').pathname;
                if (pathname === '/desktop-shortcut') {
                    if (options.method === 'POST') shortcutPosts.push(options.body);
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({ available: true, exists: false, matched: 0 }),
                        text: async () => '{"available":true,"exists":false,"matched":0}',
                    };
                }
                if (pathname === '/account/login-code') {
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({
                            success: true,
                            api_key: 'relay-token',
                            display_name: 'Test User',
                            trust_rank: 'user',
                        }),
                        text: async () => '{}',
                    };
                }
                const { defaultFetchResponse } = require('./helpers/page-harness');
                return defaultFetchResponse(url, { ...options, uiConfig: hostedConfig });
            },
        });
        try {
            const field = page.document.getElementById('loginDesktopShortcutField');
            const checkbox = page.document.getElementById('loginDesktopShortcutCheckbox');
            expect(field.hidden).toBe(false);
            expect(checkbox.checked).toBe(true);
            checkbox.checked = false;

            page.document.getElementById('loginManualToggle').click();
            const input = page.document.getElementById('loginUserInput');
            input.value = 'one-time-code';
            input.dispatchEvent(new page.window.Event('input', { bubbles: true }));
            page.document.getElementById('loginPrimaryButton').click();
            await page.flush(20);

            expect(shortcutPosts).toEqual([]);
        } finally {
            page.close();
        }
    });
});
