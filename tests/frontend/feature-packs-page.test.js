const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const { createPageHarness, defaultFetchResponse, flushTasks } = require('./helpers/page-harness');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const STATIC_ROOT = path.join(REPO_ROOT, 'static');

async function createCleanFreshPage(options = {}) {
    const html = fs.readFileSync(path.join(STATIC_ROOT, 'index.html'), 'utf8');
    const dom = new JSDOM(html, {
        url: 'http://localhost/',
        runScripts: 'outside-only',
        pretendToBeVisual: true,
        virtualConsole: new VirtualConsole(),
    });
    const sockets = [];
    class StubSocket {
        constructor(url) {
            this.url = url;
            this.readyState = 1;
            sockets.push(this);
            queueMicrotask(() => this.onopen && this.onopen());
        }
        send() {}
        close() { this.readyState = 3; }
    }
    StubSocket.OPEN = 1;
    StubSocket.CLOSED = 3;
    dom.window.WebSocket = StubSocket;
    dom.window.XMLHttpRequest = class {
        open() {}
        setRequestHeader() {}
        send() {
            this.status = 200;
            this.responseText = '{"store":{}}';
            if (this.onload) this.onload();
        }
    };
    const fetchCalls = [];
    dom.window.fetch = async (url, req = {}) => {
        fetchCalls.push([url, req]);
        return defaultFetchResponse(url, { ...options, ...req });
    };
    dom.window.open = () => null;
    dom.window.scrollTo = () => {};
    dom.window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
    dom.window.kuromoji = {
        builder: () => ({ build: (cb) => cb(null, { tokenize: () => [] }) }),
    };
    dom.window.__INITIAL_UI_CONFIG__ = options.initialUiConfig || {};
    for (const script of dom.window.document.querySelectorAll('script[src]')) {
        const src = script.getAttribute('src');
        if (!src || src.includes('kuromoji/kuromoji.js')) continue;
        const filename = path.join(STATIC_ROOT, src.replace(/^\//, ''));
        const source = fs.readFileSync(filename, 'utf8');
        dom.window.eval(`${source}\n//# sourceURL=${filename.replace(/\\/g, '/')}`);
    }
    await flushTasks(15);
    return {
        dom,
        window: dom.window,
        document: dom.window.document,
        fetchCalls,
        sockets,
        flush: flushTasks,
        close() {
            for (const s of sockets) s.onclose = null;
            dom.window.close();
        },
    };
}

describe('full-page feature packs wiring', () => {
    it('shows empty picker on brand-new open-source startup and hides disabled packs until selected', async () => {
        const page = await createCleanFreshPage();
        try {
            const doc = page.document;
            expect(doc.getElementById('featurePackDialog').hidden).toBe(false);
            expect(doc.getElementById('featurePackOverlay').hidden).toBe(false);

            const checkboxes = [...doc.querySelectorAll('#featurePackList .feature-pack-checkbox')];
            expect(checkboxes.map((cb) => cb.value)).toEqual(['vrchat', 'learning', 'obs', 'transcript']);
            expect(checkboxes.every((cb) => !cb.checked)).toBe(true);

            expect(doc.getElementById('oscTranslationButton').hidden).toBe(true);
            expect(doc.getElementById('vrOverlayButton').hidden).toBe(true);
            expect(doc.getElementById('oscSettingsSection').hidden).toBe(true);
            expect(doc.getElementById('vrOverlaySection').hidden).toBe(true);
            expect(doc.getElementById('furiganaButton').hidden).toBe(true);
            expect(doc.getElementById('learningSettingsSection').hidden).toBe(true);

            // Confirm without checking anything
            doc.getElementById('featurePackConfirmButton').click();
            await page.flush(10);
            expect(doc.getElementById('featurePackDialog').hidden).toBe(true);
            expect(JSON.parse(page.window.localStorage.getItem('featurePacks.v1'))).toMatchObject({
                version: 1,
                confirmed: true,
                isUpgrade: false,
                enabled: { vrchat: false, learning: false, obs: false, transcript: false },
            });
        } finally {
            page.close();
        }
    });

    it('pre-ticks vrchat and learning for upgrading users, edits packs from the Settings button, and preserves user prefs when toggled off/on', async () => {
        const page = await createPageHarness({
            localStorage: {
                vrOverlayEnabled: 'true',
            },
            sessionStorage: {
                furiganaEnabled: 'true',
            },
        });
        try {
            const doc = page.document;
            // Upgrade detected via createPageHarness localStorage keys -> picker opens pre-ticked
            expect(doc.getElementById('featurePackDialog').hidden).toBe(false);
            const checked = Object.fromEntries(
                [...doc.querySelectorAll('#featurePackList .feature-pack-checkbox')].map((cb) => [cb.value, cb.checked]),
            );
            expect(checked).toEqual({
                vrchat: true,
                learning: true,
                obs: false,
                transcript: false,
            });
            expect(doc.getElementById('oscTranslationButton').hidden).toBe(false);
            expect(doc.getElementById('furiganaButton').hidden).toBe(false);
            expect(doc.getElementById('learningSettingsSection').hidden).toBe(false);
            expect(doc.getElementById('furiganaButton').classList.contains('active')).toBe(true);

            // Confirm with default upgrade selection
            doc.getElementById('featurePackConfirmButton').click();
            await page.flush(10);
            expect(doc.getElementById('featurePackDialog').hidden).toBe(true);

            const setPack = async (packId, checked) => {
                doc.getElementById('settingsFeaturePacksButton').click();
                await page.flush(2);
                expect(doc.getElementById('featurePackDialog').hidden).toBe(false);
                expect(doc.getElementById('featurePackCancelButton').hidden).toBe(false);
                const box = doc.querySelector(`#featurePackList .feature-pack-checkbox[value="${packId}"]`);
                box.checked = checked;
                box.dispatchEvent(new page.window.Event('change'));
                doc.getElementById('featurePackConfirmButton').click();
                await page.flush(6);
                expect(doc.getElementById('featurePackDialog').hidden).toBe(true);
            };

            // Settings no longer lists the packs inline; the bottom-left button opens the picker.
            doc.getElementById('settingsButton').click();
            await page.flush(4);
            expect(doc.getElementById('featurePacksSection')).toBeNull();
            const packsButton = doc.getElementById('settingsFeaturePacksButton');
            expect(packsButton.hidden).toBe(false);
            expect(packsButton.closest('.settings-actions-left')).not.toBeNull();

            // Cancel keeps the current selection.
            packsButton.click();
            await page.flush(2);
            const learningBox = doc.querySelector('#featurePackList .feature-pack-checkbox[value="learning"]');
            expect(learningBox.checked).toBe(true);
            learningBox.checked = false;
            learningBox.dispatchEvent(new page.window.Event('change'));
            doc.getElementById('featurePackCancelButton').click();
            await page.flush(4);
            expect(doc.getElementById('furiganaButton').hidden).toBe(false);

            // Turn Learning off -> hides furiganaButton & learningSettingsSection, preserves sessionStorage
            await setPack('learning', false);
            expect(doc.getElementById('furiganaButton').hidden).toBe(true);
            expect(doc.getElementById('learningSettingsSection').hidden).toBe(true);
            expect(doc.getElementById('furiganaButton').classList.contains('active')).toBe(false);
            expect(page.window.sessionStorage.getItem('furiganaEnabled')).toBe('true');

            // Re-enable Learning -> restores furiganaButton, learningSettingsSection & active state
            await setPack('learning', true);
            expect(doc.getElementById('furiganaButton').hidden).toBe(false);
            expect(doc.getElementById('learningSettingsSection').hidden).toBe(false);
            expect(doc.getElementById('furiganaButton').classList.contains('active')).toBe(true);

            // Turn VRChat off -> hides VRChat UI & sends stop requests without wiping vrOverlayEnabled
            await setPack('vrchat', false);
            expect(doc.getElementById('oscTranslationButton').hidden).toBe(true);
            expect(doc.getElementById('vrOverlayButton').hidden).toBe(true);
            expect(doc.getElementById('oscSettingsSection').hidden).toBe(true);
            expect(doc.getElementById('vrOverlaySection').hidden).toBe(true);
            expect(page.window.localStorage.getItem('vrOverlayEnabled')).toBe('true');

            const packPosts = page.fetchCalls.filter(([url, opts]) => (
                new URL(String(url), 'http://localhost/').pathname === '/feature-packs'
                && opts && opts.method === 'POST'
            ));
            expect(packPosts.length).toBeGreaterThanOrEqual(2);
            const lastBody = JSON.parse(packPosts.at(-1)[1].body);
            expect(lastBody).toEqual({
                packs: { vrchat: false, learning: true, obs: false, transcript: false },
            });
        } finally {
            page.close();
        }
    });

    it('in hosted mode waits until login before showing feature packs picker', async () => {
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
            const doc = page.document;
            expect(doc.getElementById('loginPanel').hidden).toBe(false);
            expect(doc.getElementById('featurePackDialog').hidden).toBe(true);

            doc.getElementById('loginManualToggle').click();
            const input = doc.getElementById('loginUserInput');
            input.value = 'one-time-code';
            input.dispatchEvent(new page.window.Event('input', { bubbles: true }));
            doc.getElementById('loginPrimaryButton').click();
            await page.flush(20);

            expect(doc.getElementById('loginPanel').hidden).toBe(true);
            expect(doc.getElementById('featurePackDialog').hidden).toBe(false);
        } finally {
            page.close();
        }
    });

    it('in hosted mode shows feature packs picker once when switching to own-key/direct mode', async () => {
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
            const doc = page.document;
            expect(doc.getElementById('featurePackDialog').hidden).toBe(true);

            doc.getElementById('loginModeBackButton').click();
            await page.flush(15);
            expect(doc.getElementById('featurePackDialog').hidden).toBe(false);
        } finally {
            page.close();
        }
    });
});
