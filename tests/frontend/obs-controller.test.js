const { JSDOM } = require('jsdom');
const ControlIcon = require('../../static/js/control-icon');
const ObsController = require('../../static/js/obs-controller');

const doms = [];

function createMemoryStorage(initial = {}) {
    const store = new Map(Object.entries(initial).map(([k, v]) => [k, String(v)]));
    return {
        getItem: (key) => (store.has(key) ? store.get(key) : null),
        setItem: (key, value) => {
            store.set(key, String(value));
        },
        removeItem: (key) => {
            store.delete(key);
        },
        _map: store,
    };
}

function buildObsDom() {
    const dom = new JSDOM(`<!doctype html>
        <button class="control-button" id="obsCopyLinkButton" type="button" hidden>
            <svg class="control-icon" id="obsCopyLinkIcon" aria-hidden="true">
                <use href="icons/lucide-sprite.svg#radio"></use>
            </svg>
        </button>
        <section id="obsSettingsSection" hidden>
            <h2 id="obsSettingsLabel"></h2>
            <label>
                <span id="obsFontSizeLabel"></span>
                <output id="obsFontSizeValue"></output>
                <span class="range-track" style="--default-frac: 0.25;">
                    <input id="obsFontSizeSlider" type="range" min="16" max="120" step="1" value="42" data-default="42">
                    <span class="range-default-mark" id="obsFontSizeDefaultMark"></span>
                </span>
            </label>
            <span id="obsDisplayContentLabel"></span>
            <div id="obsDisplayContentPicker"></div>
        </section>
    `, { url: 'http://127.0.0.1:19001/' });
    doms.push(dom);
    return dom;
}

afterEach(() => {
    vi.useRealTimers();
    while (doms.length) doms.pop().window.close();
});

describe('ObsController gating, settings persistence, and copy link flow', () => {
    it('gates copy button to desktop main window + enabled pack and hides on mobile or non-desktop', () => {
        const dom = buildObsDom();
        const desktopController = ObsController.create({
            document: dom.window.document,
            window: dom.window,
            storage: createMemoryStorage(),
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k, vars) => (vars && vars.value !== undefined ? `${vars.value} px` : k),
            userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            isPackEnabled: () => true,
            ControlIcon,
        });
        desktopController.init();

        // Not desktop available yet
        expect(dom.window.document.getElementById('obsSettingsSection').hidden).toBe(false);
        expect(dom.window.document.getElementById('obsCopyLinkButton').hidden).toBe(true);

        desktopController.applyConfig({
            obs_desktop_available: true,
            obs_url: 'http://127.0.0.1:19001/obs',
            obs_settings: { font_size: 42, display_content: 'both' },
        });
        expect(dom.window.document.getElementById('obsCopyLinkButton').hidden).toBe(false);

        desktopController.setPackEnabled(false);
        expect(dom.window.document.getElementById('obsSettingsSection').hidden).toBe(true);
        expect(dom.window.document.getElementById('obsCopyLinkButton').hidden).toBe(true);
        desktopController.dispose();

        // Mobile browser should still hide the copy link button even if server reports desktop
        const mobileDom = buildObsDom();
        const mobileController = ObsController.create({
            document: mobileDom.window.document,
            window: mobileDom.window,
            storage: createMemoryStorage(),
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => k,
            userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148',
            isPackEnabled: () => true,
            desktopAvailable: true,
            ControlIcon,
        });
        mobileController.init();
        expect(mobileDom.window.document.getElementById('obsCopyLinkButton').hidden).toBe(true);
        mobileController.dispose();
    });

    it('persists font size & display content to obsSettings.v1 and POSTs to /obs-settings', async () => {
        const dom = buildObsDom();
        const storage = createMemoryStorage();
        const fetchMock = vi.fn().mockResolvedValue({ ok: true });
        const controller = ObsController.create({
            document: dom.window.document,
            window: dom.window,
            storage,
            fetch: fetchMock,
            t: (k, vars) => (vars && vars.value !== undefined ? `${vars.value} px` : k),
            userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            isPackEnabled: () => true,
            ControlIcon,
        });
        controller.init();

        const slider = dom.window.document.getElementById('obsFontSizeSlider');
        const mark = dom.window.document.getElementById('obsFontSizeDefaultMark');
        const output = dom.window.document.getElementById('obsFontSizeValue');
        expect(output.textContent).toBe('42 px');
        expect(mark.classList.contains('is-current')).toBe(true);

        slider.value = '68';
        slider.dispatchEvent(new dom.window.Event('change'));
        expect(output.textContent).toBe('68 px');
        expect(mark.classList.contains('is-current')).toBe(false);
        expect(JSON.parse(storage.getItem(ObsController.STORAGE_KEY))).toEqual({
            fontSize: 68,
            displayContent: 'both',
        });
        expect(fetchMock).toHaveBeenCalledWith('/obs-settings', expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ font_size: 68, display_content: 'both' }),
        }));

        const select = dom.window.document.querySelector('#obsDisplayContentPicker select');
        select.value = 'translation';
        select.dispatchEvent(new dom.window.Event('change'));
        expect(JSON.parse(storage.getItem(ObsController.STORAGE_KEY))).toEqual({
            fontSize: 68,
            displayContent: 'translation',
        });
        expect(fetchMock).toHaveBeenLastCalledWith('/obs-settings', expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ font_size: 68, display_content: 'translation' }),
        }));
        controller.dispose();
    });

    it('copies OBS URL via clipboard API or /clipboard fallback and swaps radio -> check -> radio after 1500ms', async () => {
        vi.useFakeTimers();
        const dom = buildObsDom();
        const writeText = vi.fn().mockRejectedValue(new Error('clipboard blocked in webview'));
        const fetchMock = vi.fn().mockResolvedValue({ ok: true });
        const controller = ObsController.create({
            document: dom.window.document,
            window: dom.window,
            storage: createMemoryStorage(),
            fetch: fetchMock,
            navigator: { clipboard: { writeText }, userAgent: 'Windows' },
            t: (k) => k,
            isPackEnabled: () => true,
            desktopAvailable: true,
            ControlIcon,
        });
        controller.init();
        controller.applyConfig({
            obs_desktop_available: true,
            obs_url: 'http://127.0.0.1:19001/obs',
        });

        const btn = dom.window.document.getElementById('obsCopyLinkButton');
        const useEl = dom.window.document.querySelector('#obsCopyLinkIcon use');
        expect(useEl.getAttribute('href')).toBe('icons/lucide-sprite.svg#radio');

        btn.click();
        await vi.advanceTimersByTimeAsync(0);

        expect(writeText).toHaveBeenCalledWith('http://127.0.0.1:19001/obs');
        expect(fetchMock).toHaveBeenCalledWith('/clipboard', expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ text: 'http://127.0.0.1:19001/obs' }),
        }));
        expect(useEl.getAttribute('href')).toBe('icons/lucide-sprite.svg#check');
        expect(btn.classList.contains('copied')).toBe(true);

        vi.advanceTimersByTime(1500);
        expect(useEl.getAttribute('href')).toBe('icons/lucide-sprite.svg#radio');
        expect(btn.classList.contains('copied')).toBe(false);
        controller.dispose();
    });
});
