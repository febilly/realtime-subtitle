const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const ControlIcon = require('../../static/js/control-icon');
const ObsSettings = require('../../static/js/obs-settings');
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

function buildObsDom(url = 'http://127.0.0.1:19001/') {
    const dom = new JSDOM(`<!doctype html>
        <button class="control-button" id="obsCopyLinkButton" type="button" hidden>
            <svg class="control-icon" id="obsCopyLinkIcon" aria-hidden="true">
                <use href="icons/lucide-sprite.svg#radio"></use>
            </svg>
        </button>

        <div class="confirm-overlay" id="obsDialogOverlay" hidden></div>
        <div class="obs-dialog" id="obsDialog" role="dialog" hidden>
            <div class="obs-dialog-header">
                <h1 id="obsDialogTitle">OBS subtitles</h1>
                <button id="obsDialogCloseButton" type="button">×</button>
            </div>
            <div class="obs-dialog-body" id="obsDialogBody">
                <section id="obsAddSection">
                    <div id="obsAddSectionLabel"></div>
                    <a id="obsDragCard" draggable="true" href="#">
                        <span id="obsDragCardText"></span>
                    </a>
                    <button id="obsCopyUrlButton" type="button">
                        <svg id="obsCopyUrlIcon"><use href="icons/lucide-sprite.svg#link"></use></svg>
                        <span id="obsCopyUrlText"></span>
                    </button>
                    <p id="obsPortWarning" hidden></p>
                    <details id="obsHowToDetails">
                        <summary id="obsHowToSummary"></summary>
                        <ul>
                            <li id="obsHowToDrag"></li>
                            <li id="obsHowToCopy"></li>
                            <li id="obsHowToScaleTip"></li>
                            <li id="obsHowToKeepRunning"></li>
                        </ul>
                    </details>
                </section>

                <section id="obsPreviewSection">
                    <div id="obsPreviewLabel"></div>
                    <div id="obsPreviewBox">
                        <iframe id="obsPreviewFrame"></iframe>
                    </div>
                </section>

                <details id="obsGroupText" data-obs-group="text" open>
                    <summary><span id="obsGroupTextTitle"></span></summary>
                    <div>
                        <span id="obsDisplayContentLabel"></span>
                        <div id="obsDisplayContentPicker"></div>

                        <span id="obsFontSizeLabel"></span>
                        <output id="obsFontSizeValue"></output>
                        <span>
                            <input id="obsFontSizeSlider" type="range" min="16" max="120" step="1" value="42">
                            <span id="obsFontSizeDefaultMark"></span>
                        </span>

                        <span id="obsTranslationScaleLabel"></span>
                        <output id="obsTranslationScaleValue"></output>
                        <span>
                            <input id="obsTranslationScaleSlider" type="range" min="50" max="150" step="5" value="100">
                            <span id="obsTranslationScaleDefaultMark"></span>
                        </span>

                        <span id="obsFontFamilyLabel"></span>
                        <div id="obsFontFamilyPicker"></div>

                        <span id="obsBoldLabel"></span>
                        <span id="obsBoldPicker"></span>

                        <span id="obsShowLangTagLabel"></span>
                        <span id="obsShowLangTagPicker"></span>
                    </div>
                </details>

                <details id="obsGroupColor" data-obs-group="color">
                    <summary><span id="obsGroupColorTitle"></span></summary>
                    <div>
                        <span id="obsOriginalColorLabel"></span>
                        <span id="obsOriginalColorValue"></span>
                        <input id="obsOriginalColorInput" type="color" value="#e5e7eb">

                        <span id="obsTranslationColorLabel"></span>
                        <span id="obsTranslationColorValue"></span>
                        <input id="obsTranslationColorInput" type="color" value="#e5e7eb">

                        <span id="obsOutlineWidthLabel"></span>
                        <output id="obsOutlineWidthValue"></output>
                        <span>
                            <input id="obsOutlineWidthSlider" type="range" min="0" max="8" step="1" value="0">
                            <span id="obsOutlineWidthDefaultMark"></span>
                        </span>

                        <span id="obsOutlineColorLabel"></span>
                        <span id="obsOutlineColorValue"></span>
                        <input id="obsOutlineColorInput" type="color" value="#000000">

                        <span id="obsDropShadowLabel"></span>
                        <span id="obsDropShadowPicker"></span>
                    </div>
                </details>

                <details id="obsGroupBackground" data-obs-group="background">
                    <summary><span id="obsGroupBackgroundTitle"></span></summary>
                    <div>
                        <span id="obsBgModeLabel"></span>
                        <div id="obsBgModePicker"></div>

                        <span id="obsBgColorLabel"></span>
                        <span id="obsBgColorValue"></span>
                        <input id="obsBgColorInput" type="color" value="#000000">

                        <span id="obsBgOpacityLabel"></span>
                        <output id="obsBgOpacityValue"></output>
                        <span>
                            <input id="obsBgOpacitySlider" type="range" min="0" max="100" step="1" value="59">
                            <span id="obsBgOpacityDefaultMark"></span>
                        </span>
                    </div>
                </details>

                <details id="obsGroupLayout" data-obs-group="layout">
                    <summary><span id="obsGroupLayoutTitle"></span></summary>
                    <div>
                        <span id="obsFlowDirectionLabel"></span>
                        <div id="obsFlowDirectionPicker"></div>

                        <span id="obsAlignmentLabel"></span>
                        <div id="obsAlignmentPicker"></div>

                        <span id="obsMaxSentencesLabel"></span>
                        <output id="obsMaxSentencesValue"></output>
                        <span>
                            <input id="obsMaxSentencesSlider" type="range" min="1" max="10" step="1" value="2">
                            <span id="obsMaxSentencesDefaultMark"></span>
                        </span>

                        <span id="obsAutoHideLabel"></span>
                        <output id="obsAutoHideValue"></output>
                        <span>
                            <input id="obsAutoHideSlider" type="range" min="0" max="30" step="1" value="0">
                            <span id="obsAutoHideDefaultMark"></span>
                        </span>

                        <button id="obsResetDefaultsButton" type="button">Reset</button>
                    </div>
                </details>
            </div>
            <div class="obs-dialog-footer">
                <button id="obsDialogDoneButton" type="button">Done</button>
            </div>
        </div>
    `, { url });
    doms.push(dom);
    return dom;
}

afterEach(() => {
    vi.useRealTimers();
    while (doms.length) doms.pop().window.close();
});

describe('ObsController dialog gating, opening/closing, and link sharing', () => {
    it('gates toolbar button to desktop main window + enabled pack and hides on mobile or disabled pack', () => {
        const dom = buildObsDom();
        const controller = ObsController.create({
            document: dom.window.document,
            window: dom.window,
            storage: createMemoryStorage(),
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => (k === 'obs_layer_name' ? 'Realtime Subtitle' : k),
            userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            isPackEnabled: () => true,
            ControlIcon,
        });
        controller.init();

        const btn = dom.window.document.getElementById('obsCopyLinkButton');
        expect(btn.hidden).toBe(true);

        controller.applyConfig({
            obs_desktop_available: true,
            obs_url: 'http://127.0.0.1:19001/obs',
        });
        expect(btn.hidden).toBe(false);

        // Open dialog, then disable pack -> dialog auto-closes & button hides
        btn.click();
        expect(controller.isDialogOpen()).toBe(true);
        controller.setPackEnabled(false);
        expect(controller.isDialogOpen()).toBe(false);
        expect(dom.window.document.getElementById('obsDialog').hidden).toBe(true);
        expect(btn.hidden).toBe(true);
        controller.dispose();

        // Mobile browser should always hide button
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

    it('opens and closes dialog via close button, Done button, overlay click, and Escape', () => {
        const dom = buildObsDom();
        const controller = ObsController.create({
            document: dom.window.document,
            window: dom.window,
            storage: createMemoryStorage(),
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => k,
            getLang: () => 'zh',
            userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            isPackEnabled: () => true,
            desktopAvailable: true,
            ControlIcon,
        });
        controller.init();

        const openBtn = dom.window.document.getElementById('obsCopyLinkButton');
        const overlay = dom.window.document.getElementById('obsDialogOverlay');
        const dialog = dom.window.document.getElementById('obsDialog');
        const frame = dom.window.document.getElementById('obsPreviewFrame');

        openBtn.click();
        expect(overlay.hidden).toBe(false);
        expect(dialog.hidden).toBe(false);
        expect(frame.getAttribute('src')).toBe('/obs?preview=1&lang=zh');

        dom.window.document.getElementById('obsDialogCloseButton').click();
        expect(dialog.hidden).toBe(true);

        openBtn.click();
        dom.window.document.getElementById('obsDialogDoneButton').click();
        expect(dialog.hidden).toBe(true);

        openBtn.click();
        overlay.click();
        expect(dialog.hidden).toBe(true);

        openBtn.click();
        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
        expect(dialog.hidden).toBe(true);

        controller.dispose();
    });

    it('configures draggable link card with ?layer-name, layer-width=1600, layer-height=320 and copies plain URL on click or Copy button', async () => {
        vi.useFakeTimers();
        const dom = buildObsDom();
        const storage = createMemoryStorage();
        const writeText = vi.fn().mockRejectedValue(new Error('clipboard blocked'));
        const fetchMock = vi.fn().mockResolvedValue({ ok: true });

        const controller = ObsController.create({
            document: dom.window.document,
            window: dom.window,
            storage,
            fetch: fetchMock,
            navigator: { clipboard: { writeText }, userAgent: 'Windows' },
            t: (k) => (k === 'obs_layer_name' ? '实时字幕' : k),
            isPackEnabled: () => true,
            desktopAvailable: true,
            ControlIcon,
        });
        controller.init();
        controller.applyConfig({
            obs_desktop_available: true,
            obs_url: 'http://127.0.0.1:19001/obs',
        });

        expect(ObsController.PREVIEW_VIRTUAL_WIDTH).toBe(1600);
        expect(ObsController.PREVIEW_VIRTUAL_HEIGHT).toBe(320);
        expect(dom.window.document.getElementById('obsHowToScaleTip').textContent).toBe('obs_howto_scale_tip');

        const dragCard = dom.window.document.getElementById('obsDragCard');
        const expectedDragUrl = 'http://127.0.0.1:19001/obs?layer-name=%E5%AE%9E%E6%97%B6%E5%AD%97%E5%B9%95&layer-width=1600&layer-height=320';
        expect(dragCard.getAttribute('href')).toBe(expectedDragUrl);

        // dragstart sets text/uri-list & text/plain and records port
        const transferred = {};
        // Same listener pywebview injects when draggable=False.
        dom.window.document.addEventListener('dragstart', (e) => {
            if (e.target.tagName === 'IMG' || e.target.tagName === 'A') e.preventDefault();
        });
        const dragEvent = new dom.window.Event('dragstart', { bubbles: true, cancelable: true });
        dragEvent.dataTransfer = {
            effectAllowed: 'uninitialized',
            setData: (mime, value) => {
                transferred[mime] = value;
            },
        };
        dragCard.dispatchEvent(dragEvent);
        expect(dragEvent.defaultPrevented).toBe(false);
        expect(transferred).toEqual({
            'text/uri-list': expectedDragUrl,
            'text/plain': expectedDragUrl,
        });
        expect(storage.getItem(ObsController.LAST_PORT_STORAGE_KEY)).toBe('19001');

        // Clicking drag card prevents navigation and copies plain URL
        const clickEvent = new dom.window.MouseEvent('click', { cancelable: true });
        dragCard.dispatchEvent(clickEvent);
        expect(clickEvent.defaultPrevented).toBe(true);
        await vi.advanceTimersByTimeAsync(0);
        expect(writeText).toHaveBeenCalledWith('http://127.0.0.1:19001/obs');
        expect(fetchMock).toHaveBeenCalledWith('/clipboard', expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ text: 'http://127.0.0.1:19001/obs' }),
        }));

        // Copy URL button swaps link -> check -> link after 1500ms
        const copyBtn = dom.window.document.getElementById('obsCopyUrlButton');
        const copyUse = dom.window.document.querySelector('#obsCopyUrlIcon use');
        vi.advanceTimersByTime(1500);
        expect(copyUse.getAttribute('href')).toBe('icons/lucide-sprite.svg#link');

        copyBtn.click();
        await vi.advanceTimersByTimeAsync(0);
        expect(copyUse.getAttribute('href')).toBe('icons/lucide-sprite.svg#check');
        expect(copyBtn.classList.contains('copied')).toBe(true);

        vi.advanceTimersByTime(1500);
        expect(copyUse.getAttribute('href')).toBe('icons/lucide-sprite.svg#link');
        expect(copyBtn.classList.contains('copied')).toBe(false);

        controller.dispose();
    });

    it('shows port warning when stored obsLastPort differs and clears it once new port is copied/dragged', async () => {
        const dom = buildObsDom('http://127.0.0.1:19002/');
        const storage = createMemoryStorage({ [ObsController.LAST_PORT_STORAGE_KEY]: '8080' });
        const writeText = vi.fn().mockResolvedValue(undefined);

        const controller = ObsController.create({
            document: dom.window.document,
            window: dom.window,
            storage,
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            navigator: { clipboard: { writeText }, userAgent: 'Windows' },
            t: (k) => k,
            isPackEnabled: () => true,
            desktopAvailable: true,
            ControlIcon,
        });
        controller.init();
        controller.applyConfig({
            obs_desktop_available: true,
            obs_url: 'http://127.0.0.1:19002/obs',
        });

        controller.openDialog();
        const warnEl = dom.window.document.getElementById('obsPortWarning');
        expect(warnEl.hidden).toBe(false);
        expect(warnEl.textContent).toBe('obs_port_changed_warning');

        await controller.copyObsLink();
        expect(storage.getItem(ObsController.LAST_PORT_STORAGE_KEY)).toBe('19002');
        expect(warnEl.hidden).toBe(true);

        controller.dispose();
    });
});

describe('ObsController settings persistence, groups, preview sync, and reset', () => {
    it('persists collapsible group open state locally and restores it without syncing to local-store-sync allowlist', () => {
        const syncSource = fs.readFileSync(
            path.resolve(__dirname, '../../static/local-store-sync.js'),
            'utf8',
        );
        expect(syncSource).toContain(`'${ObsController.STORAGE_KEY}'`);
        expect(syncSource).toContain(`'${ObsController.LAST_PORT_STORAGE_KEY}'`);
        expect(syncSource).not.toContain(ObsController.GROUPS_STORAGE_KEY);

        const dom = buildObsDom();
        const storage = createMemoryStorage();
        const controller = ObsController.create({
            document: dom.window.document,
            window: dom.window,
            storage,
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => k,
            userAgent: 'Windows',
            isPackEnabled: () => true,
            ControlIcon,
        });
        controller.init();

        const textGroup = dom.window.document.getElementById('obsGroupText');
        const colorGroup = dom.window.document.getElementById('obsGroupColor');
        expect(textGroup.open).toBe(true);
        expect(colorGroup.open).toBe(false);

        textGroup.open = false;
        textGroup.dispatchEvent(new dom.window.Event('toggle'));
        colorGroup.open = true;
        colorGroup.dispatchEvent(new dom.window.Event('toggle'));

        expect(JSON.parse(storage.getItem(ObsController.GROUPS_STORAGE_KEY))).toEqual({
            text: false,
            color: true,
            background: false,
            layout: false,
        });
        controller.dispose();

        // New controller instance restores group state from storage
        const dom2 = buildObsDom();
        const controller2 = ObsController.create({
            document: dom2.window.document,
            window: dom2.window,
            storage,
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => k,
            userAgent: 'Windows',
            isPackEnabled: () => true,
            ControlIcon,
        });
        controller2.init();
        expect(dom2.window.document.getElementById('obsGroupText').open).toBe(false);
        expect(dom2.window.document.getElementById('obsGroupColor').open).toBe(true);
        controller2.dispose();
    });

    it('updates storage, preview postMessage, backend /obs-settings, and resets defaults', async () => {
        vi.useFakeTimers();
        const dom = buildObsDom();
        const storage = createMemoryStorage();
        const fetchMock = vi.fn().mockResolvedValue({ ok: true });
        const postMessageMock = vi.fn();
        const frame = dom.window.document.getElementById('obsPreviewFrame');
        Object.defineProperty(frame, 'contentWindow', {
            value: { postMessage: postMessageMock },
            configurable: true,
        });

        const controller = ObsController.create({
            document: dom.window.document,
            window: dom.window,
            storage,
            fetch: fetchMock,
            t: (k, vars) => (vars && vars.value !== undefined ? `${vars.value} px` : k),
            getLang: () => 'en',
            userAgent: 'Windows',
            isPackEnabled: () => true,
            desktopAvailable: true,
            ControlIcon,
        });
        controller.init();
        controller.openDialog();
        postMessageMock.mockClear();

        // Slider input debounces POST /obs-settings but immediately sends postMessage
        const fontSizeSlider = dom.window.document.getElementById('obsFontSizeSlider');
        fontSizeSlider.value = '68';
        fontSizeSlider.dispatchEvent(new dom.window.Event('input'));
        expect(dom.window.document.getElementById('obsFontSizeValue').textContent).toBe('68 px');
        expect(postMessageMock).toHaveBeenLastCalledWith(expect.objectContaining({
            type: 'obs_preview_update',
            lang: 'en',
            settings: expect.objectContaining({ fontSize: 68 }),
        }), '*');
        expect(fetchMock).not.toHaveBeenCalled();

        await vi.advanceTimersByTimeAsync(60);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock).toHaveBeenLastCalledWith('/obs-settings', expect.objectContaining({
            method: 'POST',
            body: expect.stringContaining('"font_size":68'),
        }));

        // Color change syncs immediately
        const origColorInput = dom.window.document.getElementById('obsOriginalColorInput');
        origColorInput.value = '#38bdf8';
        origColorInput.dispatchEvent(new dom.window.Event('change'));
        expect(dom.window.document.getElementById('obsOriginalColorValue').textContent).toBe('#38bdf8');
        expect(JSON.parse(storage.getItem(ObsController.STORAGE_KEY))).toMatchObject({
            fontSize: 68,
            originalColor: '#38bdf8',
        });

        // Toggle & select controls update immediately
        const boldSwitch = dom.window.document.querySelector('#obsBoldPicker button');
        boldSwitch.click();
        const bgSelect = dom.window.document.querySelector('#obsBgModePicker select');
        bgSelect.value = 'block';
        bgSelect.dispatchEvent(new dom.window.Event('change'));
        const flowSelect = dom.window.document.querySelector('#obsFlowDirectionPicker select');
        flowSelect.value = 'up';
        flowSelect.dispatchEvent(new dom.window.Event('change'));

        expect(JSON.parse(storage.getItem(ObsController.STORAGE_KEY))).toMatchObject({
            bold: true,
            bgMode: 'block',
            flowDirection: 'up',
        });
        expect(fetchMock).toHaveBeenLastCalledWith('/obs-settings', expect.objectContaining({
            method: 'POST',
            body: expect.stringContaining('"flow_direction":"up"'),
        }));

        // Reset button restores all 18 defaults
        dom.window.document.getElementById('obsResetDefaultsButton').click();
        expect(JSON.parse(storage.getItem(ObsController.STORAGE_KEY))).toEqual(ObsSettings.DEFAULT_SETTINGS);
        expect(ObsSettings.DEFAULT_SETTINGS.flowDirection).toBe('down');
        expect(ObsSettings.DEFAULT_SETTINGS.alignment).toBe('left');
        expect(ObsSettings.DEFAULT_SETTINGS.maxSentences).toBe(2);
        expect(dom.window.document.getElementById('obsFontSizeValue').textContent).toBe('42 px');
        expect(dom.window.document.getElementById('obsOriginalColorValue').textContent).toBe('#e5e7eb');
        expect(boldSwitch.getAttribute('aria-checked')).toBe('false');
        expect(bgSelect.value).toBe('strip');
        expect(flowSelect.value).toBe('down');
        expect(dom.window.document.querySelector('#obsAlignmentPicker select').value).toBe('left');
        expect(fetchMock).toHaveBeenLastCalledWith('/obs-settings', expect.objectContaining({
            method: 'POST',
            body: JSON.stringify(ObsSettings.toBackendPayload(ObsSettings.DEFAULT_SETTINGS)),
        }));

        controller.dispose();
    });
});
