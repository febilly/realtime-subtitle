const { JSDOM } = require('jsdom');
const DesktopShortcut = require('../../static/js/desktop-shortcut');

function response(data, { ok = true } = {}) {
    return { ok, json: vi.fn().mockResolvedValue(data) };
}

function setup(fetch = vi.fn()) {
    const dom = new JSDOM(`<!doctype html><body>
        <div id="desktopShortcutOverlay" hidden></div>
        <section id="desktopShortcutDialog" hidden>
            <h1 id="desktopShortcutTitle"></h1>
            <p id="desktopShortcutHint"></p>
            <button id="desktopShortcutCreate"><span id="desktopShortcutCreateTitle"></span><span id="desktopShortcutCreateDesc"></span></button>
            <button id="desktopShortcutSkip"><span id="desktopShortcutSkipTitle"></span><span id="desktopShortcutSkipDesc"></span></button>
        </section>
    </body>`);
    const document = dom.window.document;
    const showToast = vi.fn();
    const controller = DesktopShortcut.create({
        document,
        fetch,
        t: (key) => `translated:${key}`,
        showToast,
        elements: {
            overlay: document.getElementById('desktopShortcutOverlay'),
            dialog: document.getElementById('desktopShortcutDialog'),
            createButton: document.getElementById('desktopShortcutCreate'),
            skipButton: document.getElementById('desktopShortcutSkip'),
        },
    });
    controller.init();
    return { controller, document, dom, fetch, showToast };
}

describe('DesktopShortcut', () => {
    it('offers creation only when the packaged Windows client has no shortcut', async () => {
        const fetch = vi.fn().mockResolvedValue(response({ available: true, exists: false }));
        const page = setup(fetch);
        await expect(page.controller.offer()).resolves.toBe(true);
        expect(page.document.getElementById('desktopShortcutDialog').hidden).toBe(false);
        expect(page.document.getElementById('desktopShortcutTitle').textContent)
            .toBe('translated:desktop_shortcut_title');
        page.dom.window.close();
    });

    it.each([
        { available: false, exists: false },
        { available: true, exists: true },
    ])('does not show for status %j', async (status) => {
        const page = setup(vi.fn().mockResolvedValue(response(status)));
        await expect(page.controller.offer()).resolves.toBe(false);
        expect(page.document.getElementById('desktopShortcutDialog').hidden).toBe(true);
        page.dom.window.close();
    });

    it('creates the shortcut, closes the prompt, and confirms success', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ available: true, exists: false }))
            .mockResolvedValueOnce(response({ available: true, exists: true, created: true }));
        const page = setup(fetch);
        await page.controller.offer();
        await expect(page.controller.createShortcut()).resolves.toBe(true);
        expect(fetch).toHaveBeenLastCalledWith('/desktop-shortcut', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'create' }),
        });
        expect(page.document.getElementById('desktopShortcutDialog').hidden).toBe(true);
        expect(page.showToast).toHaveBeenCalledWith('translated:desktop_shortcut_created');
        page.dom.window.close();
    });

    it('keeps the prompt open and shows an error when creation fails', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ available: true, exists: false }))
            .mockResolvedValueOnce(response({ status: 'error' }, { ok: false }));
        const page = setup(fetch);
        await page.controller.offer();
        await expect(page.controller.createShortcut()).resolves.toBe(false);
        expect(page.document.getElementById('desktopShortcutDialog').hidden).toBe(false);
        expect(page.showToast).toHaveBeenCalledWith('translated:desktop_shortcut_failed', true);
        page.dom.window.close();
    });
});
