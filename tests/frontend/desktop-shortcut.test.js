const { JSDOM } = require('jsdom');
const DesktopShortcut = require('../../static/js/desktop-shortcut');

function response(data, { ok = true } = {}) {
    return { ok, json: vi.fn().mockResolvedValue(data) };
}

function setup(fetch = vi.fn()) {
    const dom = new JSDOM(`<!doctype html><body>
        <label id="loginDesktopShortcutField" hidden>
            <input id="loginDesktopShortcutCheckbox" type="checkbox" checked>
            <span id="loginDesktopShortcutLabel"></span>
        </label>
    </body>`);
    const document = dom.window.document;
    const showToast = vi.fn();
    const controller = DesktopShortcut.create({
        document,
        fetch,
        t: (key) => `translated:${key}`,
        showToast,
        elements: {
            field: document.getElementById('loginDesktopShortcutField'),
            checkbox: document.getElementById('loginDesktopShortcutCheckbox'),
            label: document.getElementById('loginDesktopShortcutLabel'),
        },
    });
    controller.init();
    return { controller, document, dom, fetch, showToast };
}

describe('DesktopShortcut', () => {
    it('shows a default-checked checkbox when packaged Windows client has no shortcut', async () => {
        const fetch = vi.fn().mockResolvedValue(response({ available: true, exists: false }));
        const page = setup(fetch);
        await expect(page.controller.syncOption({ resetChecked: true })).resolves.toBe(true);
        expect(page.document.getElementById('loginDesktopShortcutField').hidden).toBe(false);
        expect(page.document.getElementById('loginDesktopShortcutCheckbox').checked).toBe(true);
        expect(page.document.getElementById('loginDesktopShortcutLabel').textContent)
            .toBe('translated:desktop_shortcut_create');
        page.dom.window.close();
    });

    it.each([
        { available: false, exists: false },
        { available: true, exists: true },
    ])('hides checkbox and skips creation for status %j', async (status) => {
        const fetch = vi.fn().mockResolvedValue(response(status));
        const page = setup(fetch);
        await expect(page.controller.syncOption({ resetChecked: true })).resolves.toBe(false);
        expect(page.document.getElementById('loginDesktopShortcutField').hidden).toBe(true);
        await expect(page.controller.createIfRequested()).resolves.toBe(false);
        expect(fetch).toHaveBeenCalledTimes(1);
        page.dom.window.close();
    });

    it('does not create a shortcut when the user unchecks the checkbox', async () => {
        const fetch = vi.fn().mockResolvedValue(response({ available: true, exists: false }));
        const page = setup(fetch);
        await page.controller.syncOption({ resetChecked: true });
        page.document.getElementById('loginDesktopShortcutCheckbox').checked = false;
        await expect(page.controller.createIfRequested()).resolves.toBe(false);
        expect(fetch).toHaveBeenCalledTimes(1);
        page.dom.window.close();
    });

    it('creates the shortcut after login when checked and hides the option', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ available: true, exists: false }))
            .mockResolvedValueOnce(response({ available: true, exists: false }))
            .mockResolvedValueOnce(response({ available: true, exists: true, created: true }));
        const page = setup(fetch);
        await page.controller.syncOption({ resetChecked: true });
        await expect(page.controller.createIfRequested()).resolves.toBe(true);
        expect(fetch).toHaveBeenLastCalledWith('/desktop-shortcut', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'create' }),
        });
        expect(page.document.getElementById('loginDesktopShortcutField').hidden).toBe(true);
        expect(page.showToast).toHaveBeenCalledWith('translated:desktop_shortcut_created');
        page.dom.window.close();
    });

    it('avoids duplicate creation if a shortcut already exists at login completion', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ available: true, exists: false }))
            .mockResolvedValueOnce(response({ available: true, exists: true }));
        const page = setup(fetch);
        await page.controller.syncOption({ resetChecked: true });
        await expect(page.controller.createIfRequested()).resolves.toBe(false);
        expect(fetch).toHaveBeenCalledTimes(2);
        expect(page.document.getElementById('loginDesktopShortcutField').hidden).toBe(true);
        expect(page.showToast).not.toHaveBeenCalled();
        page.dom.window.close();
    });

    it('shows an error toast when shortcut creation fails', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ available: true, exists: false }))
            .mockResolvedValueOnce(response({ available: true, exists: false }))
            .mockResolvedValueOnce(response({ status: 'error' }, { ok: false }));
        const page = setup(fetch);
        await page.controller.syncOption({ resetChecked: true });
        await expect(page.controller.createIfRequested()).resolves.toBe(false);
        expect(page.showToast).toHaveBeenCalledWith('translated:desktop_shortcut_failed', true);
        page.dom.window.close();
    });
});
