const { JSDOM } = require('jsdom');
const FeaturePacks = require('../../static/js/feature-packs');
const FuriganaToggleController = require('../../static/js/furigana-toggle-controller');
const OscTranslationController = require('../../static/js/osc-translation-controller');

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

function buildFeaturePacksDom() {
    const dom = new JSDOM(`<!doctype html>
        <button id="oscTranslationButton"></button>
        <button id="vrOverlayButton"></button>
        <div id="oscSettingsSection"></div>
        <div id="vrOverlaySection"></div>
        <button id="furiganaButton"></button>
        <div id="learningSettingsSection"></div>
        <div id="featurePackOverlay" hidden>
            <div id="featurePackDialog" hidden>
                <h2 id="featurePackTitle"></h2>
                <p id="featurePackHint"></p>
                <div id="featurePackList"></div>
                <button id="featurePackCancelButton" hidden></button>
                <button id="featurePackConfirmButton"></button>
            </div>
        </div>
    `);
    doms.push(dom);
    return dom;
}

afterEach(() => {
    while (doms.length) doms.pop().window.close();
});

describe('FeaturePacks registry and install detection', () => {
    it('registers the 4 expected packs in order', () => {
        expect(FeaturePacks.PACK_IDS).toEqual(['vrchat', 'learning', 'obs', 'transcript']);
        expect(FeaturePacks.PACKS.map((p) => p.id)).toEqual(['vrchat', 'learning', 'obs', 'transcript']);
        expect(FeaturePacks.PACKS.find((p) => p.id === 'vrchat').elementIds).toEqual([
            'oscTranslationButton',
            'vrOverlayButton',
            'oscSettingsSection',
            'vrOverlaySection',
        ]);
        expect(FeaturePacks.PACKS.find((p) => p.id === 'learning').elementIds).toEqual([
            'furiganaButton',
            'learningSettingsSection',
        ]);
        expect(FeaturePacks.PACKS.find((p) => p.id === 'obs').elementIds).toEqual([
            'obsCopyLinkButton',
            'obsSettingsSection',
        ]);
        expect(FeaturePacks.PACKS.find((p) => p.id === 'transcript').elementIds).toEqual([
            'exportTranscriptButton',
            'transcriptSettingsSection',
        ]);
    });

    it('treats empty storage as a brand-new install with all packs disabled', () => {
        const dom = buildFeaturePacksDom();
        const storage = createMemoryStorage();
        const fetchMock = vi.fn().mockResolvedValue({ ok: true });
        const fp = FeaturePacks.create({
            document: dom.window.document,
            storage,
            fetch: fetchMock,
            t: (k) => k,
        });

        expect(fp.isUpgrade()).toBe(false);
        expect(fp.isConfirmed()).toBe(false);
        expect(fp.getEnabledMap()).toEqual({
            vrchat: false,
            learning: false,
            obs: false,
            transcript: false,
        });
    });

    it('detects an existing install from legacy storage keys and enables vrchat+learning until confirmed', () => {
        const dom = buildFeaturePacksDom();
        const storage = createMemoryStorage({ theme: 'dark' });
        const fp = FeaturePacks.create({
            document: dom.window.document,
            storage,
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => k,
        });

        expect(fp.isUpgrade()).toBe(true);
        expect(fp.isConfirmed()).toBe(false);
        expect(fp.getEnabledMap()).toEqual({
            vrchat: true,
            learning: true,
            obs: false,
            transcript: false,
        });
    });
});

describe('FeaturePacks visibility, hooks, backend sync, and settings UI', () => {
    it('applies element visibility, invokes hooks, and syncs to backend', async () => {
        const dom = buildFeaturePacksDom();
        const storage = createMemoryStorage();
        const fetchMock = vi.fn().mockResolvedValue({ ok: true });
        const hooks = {
            vrchat: { onEnable: vi.fn(), onDisable: vi.fn() },
            learning: { onEnable: vi.fn(), onDisable: vi.fn() },
        };
        const fp = FeaturePacks.create({
            document: dom.window.document,
            storage,
            fetch: fetchMock,
            t: (k) => `tr:${k}`,
            hooks,
        });

        fp.applyAll();
        expect(dom.window.document.getElementById('oscTranslationButton').hidden).toBe(true);
        expect(dom.window.document.getElementById('vrOverlayButton').hidden).toBe(true);
        expect(dom.window.document.getElementById('oscSettingsSection').hidden).toBe(true);
        expect(dom.window.document.getElementById('vrOverlaySection').hidden).toBe(true);
        expect(dom.window.document.getElementById('furiganaButton').hidden).toBe(true);
        expect(dom.window.document.getElementById('learningSettingsSection').hidden).toBe(true);
        expect(hooks.vrchat.onDisable).toHaveBeenCalledWith(false);
        expect(hooks.learning.onDisable).toHaveBeenCalledWith(false);
        expect(fetchMock).toHaveBeenCalledWith('/feature-packs', expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({
                packs: { vrchat: false, learning: false, obs: false, transcript: false },
            }),
        }));

        fp.setEnabled('vrchat', true);
        expect(dom.window.document.getElementById('oscTranslationButton').hidden).toBe(false);
        expect(dom.window.document.getElementById('vrOverlaySection').hidden).toBe(false);
        expect(dom.window.document.getElementById('furiganaButton').hidden).toBe(true);
        expect(dom.window.document.getElementById('learningSettingsSection').hidden).toBe(true);
        expect(hooks.vrchat.onEnable).toHaveBeenCalledWith(true);
    });

    it('reopens the picker from Settings with the current selection and lets the user cancel or change it', async () => {
        const dom = buildFeaturePacksDom();
        const doc = dom.window.document;
        const storage = createMemoryStorage();
        const fp = FeaturePacks.create({
            document: doc,
            storage,
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => k,
        });
        fp.confirmSelection({ vrchat: true, learning: false, obs: true, transcript: false }, { sync: false });

        const checkedState = () => Object.fromEntries(
            [...doc.querySelectorAll('#featurePackList .feature-pack-checkbox')].map((cb) => [cb.value, cb.checked]),
        );

        // Cancel leaves everything unchanged.
        let pending = fp.openPicker({ cancellable: true });
        expect(doc.getElementById('featurePackCancelButton').hidden).toBe(false);
        expect(checkedState()).toEqual({ vrchat: true, learning: false, obs: true, transcript: false });
        const learningBox = doc.querySelector('#featurePackList .feature-pack-checkbox[value="learning"]');
        learningBox.checked = true;
        learningBox.dispatchEvent(new dom.window.Event('change'));
        doc.getElementById('featurePackCancelButton').click();
        await expect(pending).resolves.toBeNull();
        expect(doc.getElementById('featurePackDialog').hidden).toBe(true);
        expect(fp.getEnabledMap()).toEqual({ vrchat: true, learning: false, obs: true, transcript: false });

        // Confirm applies the change.
        pending = fp.openPicker({ cancellable: true });
        expect(checkedState()).toEqual({ vrchat: true, learning: false, obs: true, transcript: false });
        const vrchatBox = doc.querySelector('#featurePackList .feature-pack-checkbox[value="vrchat"]');
        vrchatBox.checked = false;
        vrchatBox.dispatchEvent(new dom.window.Event('change'));
        doc.getElementById('featurePackConfirmButton').click();
        await pending;
        expect(fp.getEnabledMap()).toEqual({ vrchat: false, learning: false, obs: true, transcript: false });
        expect(doc.getElementById('oscTranslationButton').hidden).toBe(true);

        // The first-run picker has no Cancel.
        fp.openPicker();
        expect(doc.getElementById('featurePackCancelButton').hidden).toBe(true);
    });

    it('applies backend WebSocket updates without overriding confirmed flag', () => {
        const dom = buildFeaturePacksDom();
        const storage = createMemoryStorage();
        const onLearningEnable = vi.fn();
        const fp = FeaturePacks.create({
            document: dom.window.document,
            storage,
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => k,
            hooks: { learning: { onEnable: onLearningEnable } },
        });
        fp.applyAll({ sync: false });

        expect(fp.applyBackendPacks({ learning: true })).toBe(true);
        expect(fp.isEnabled('learning')).toBe(true);
        expect(dom.window.document.getElementById('furiganaButton').hidden).toBe(false);
        expect(dom.window.document.getElementById('learningSettingsSection').hidden).toBe(false);
        expect(onLearningEnable).toHaveBeenCalledWith(true);
        expect(fp.applyBackendPacks({ learning: true })).toBe(false);
    });
});

describe('FeaturePacks first-launch picker routing', () => {
    it('shows picker once for brand-new open-source startup with nothing checked and confirms selection', async () => {
        const dom = buildFeaturePacksDom();
        const storage = createMemoryStorage();
        const fp = FeaturePacks.create({
            document: dom.window.document,
            storage,
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => k,
            getRuntimeState: () => ({ relayAvailable: false, lockManualControls: false }),
        });

        const promise = fp.maybeShowStartupPicker();
        expect(fp.isPickerOpen()).toBe(true);
        expect(dom.window.document.getElementById('featurePackDialog').hidden).toBe(false);
        const checkboxes = [...dom.window.document.querySelectorAll('#featurePackList .feature-pack-checkbox')];
        expect(checkboxes).toHaveLength(4);
        expect(checkboxes.every((cb) => !cb.checked)).toBe(true);

        // Toggle learning on and confirm
        checkboxes[1].checked = true;
        checkboxes[1].dispatchEvent(new dom.window.Event('change'));
        dom.window.document.getElementById('featurePackConfirmButton').click();

        const shown = await promise;
        expect(shown).toBe(true);
        expect(fp.isConfirmed()).toBe(true);
        expect(fp.getEnabledMap()).toEqual({
            vrchat: false,
            learning: true,
            obs: false,
            transcript: false,
        });
        expect(dom.window.document.getElementById('featurePackDialog').hidden).toBe(true);

        // Second call does not re-open once confirmed
        expect(await fp.maybeShowStartupPicker()).toBe(false);
    });

    it('pre-ticks vrchat and learning in picker on upgrade install', async () => {
        const dom = buildFeaturePacksDom();
        const storage = createMemoryStorage({ displayMode: 'both' });
        const fp = FeaturePacks.create({
            document: dom.window.document,
            storage,
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => k,
            getRuntimeState: () => ({ relayAvailable: false, lockManualControls: false }),
        });

        const promise = fp.maybeShowStartupPicker();
        const checkedById = Object.fromEntries(
            [...dom.window.document.querySelectorAll('#featurePackList .feature-pack-checkbox')].map((cb) => [cb.value, cb.checked]),
        );
        expect(checkedById).toEqual({
            vrchat: true,
            learning: true,
            obs: false,
            transcript: false,
        });

        dom.window.document.getElementById('featurePackConfirmButton').click();
        await promise;
        expect(fp.getEnabledMap()).toEqual({
            vrchat: true,
            learning: true,
            obs: false,
            transcript: false,
        });
    });

    it('defers picker on unauthenticated hosted startup and shows after login or direct mode entry', async () => {
        const dom = buildFeaturePacksDom();
        const storage = createMemoryStorage();
        let runtime = {
            relayAvailable: true,
            backendLoggedIn: false,
            signedIn: false,
            connectionMode: 'relay',
            lockManualControls: false,
        };
        const fp = FeaturePacks.create({
            document: dom.window.document,
            storage,
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => k,
            getRuntimeState: () => runtime,
        });

        expect(await fp.maybeShowStartupPicker()).toBe(false);
        expect(fp.isPickerOpen()).toBe(false);

        const loginPromise = fp.maybeShowAfterLogin();
        expect(fp.isPickerOpen()).toBe(true);
        dom.window.document.getElementById('featurePackConfirmButton').click();
        expect(await loginPromise).toBe(true);

        // Once confirmed, direct mode entry won't reopen
        expect(await fp.maybeShowOnModeEnter('direct')).toBe(false);
    });

    it('shows picker once when entering direct mode in hosted build if unconfirmed', async () => {
        const dom = buildFeaturePacksDom();
        const storage = createMemoryStorage();
        const fp = FeaturePacks.create({
            document: dom.window.document,
            storage,
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => k,
            getRuntimeState: () => ({ relayAvailable: true, lockManualControls: false }),
        });

        expect(await fp.maybeShowOnModeEnter('relay')).toBe(false);
        const directPromise = fp.maybeShowOnModeEnter('direct');
        expect(fp.isPickerOpen()).toBe(true);
        dom.window.document.getElementById('featurePackConfirmButton').click();
        expect(await directPromise).toBe(true);
    });

    it('never auto-shows picker when lockManualControls is true', async () => {
        const dom = buildFeaturePacksDom();
        const storage = createMemoryStorage();
        const fp = FeaturePacks.create({
            document: dom.window.document,
            storage,
            fetch: vi.fn().mockResolvedValue({ ok: true }),
            t: (k) => k,
            getRuntimeState: () => ({ lockManualControls: true }),
        });

        expect(await fp.maybeShowStartupPicker()).toBe(false);
        expect(await fp.maybeShowAfterLogin()).toBe(false);
        expect(await fp.maybeShowOnModeEnter('direct')).toBe(false);
    });
});

describe('Pack gating on FuriganaToggleController and OscTranslationController', () => {
    it('disables furigana output when learning pack turns off while preserving sessionStorage', () => {
        const dom = new JSDOM('<!doctype html><button id="btn"></button><svg id="ico"></svg>');
        doms.push(dom);
        const storage = createMemoryStorage({ furiganaEnabled: 'true' });
        const onChange = vi.fn();
        const ctrl = FuriganaToggleController.create({
            storage,
            button: dom.window.document.getElementById('btn'),
            icon: dom.window.document.getElementById('ico'),
            t: (k) => k,
            console: { log() {}, warn() {} },
            onChange,
        });

        expect(ctrl.isEnabled()).toBe(true);
        ctrl.setPackEnabled(false);
        expect(ctrl.isEnabled()).toBe(false);
        expect(storage.getItem('furiganaEnabled')).toBe('true');
        expect(onChange).toHaveBeenLastCalledWith(false);
        expect(ctrl.toggle()).toBe(false);

        ctrl.setPackEnabled(true);
        expect(ctrl.isEnabled()).toBe(true);
        expect(onChange).toHaveBeenLastCalledWith(true);
    });

    it('stops OSC translation and blocks toggles while vrchat pack is disabled', async () => {
        const dom = new JSDOM('<!doctype html><button id="osc"></button><svg id="oscIcon"></svg>');
        doms.push(dom);
        let vrchatEnabled = true;
        const fetchMock = vi.fn(async (_url, opts) => {
            const body = opts && opts.body ? JSON.parse(opts.body) : {};
            return {
                ok: true,
                json: async () => ({ enabled: !!body.enabled }),
            };
        });
        const ctrl = OscTranslationController.create({
            fetch: fetchMock,
            button: dom.window.document.getElementById('osc'),
            icon: dom.window.document.getElementById('oscIcon'),
            t: (k) => k,
            console: { log() {}, error() {} },
            isLocked: () => false,
            isPackEnabled: () => vrchatEnabled,
        });

        expect(await ctrl.toggle()).toBe(true);
        vrchatEnabled = false;
        expect(await ctrl.toggle()).toBe(false);
        expect(fetchMock).toHaveBeenCalledTimes(1);

        await ctrl.disable();
        expect(fetchMock).toHaveBeenLastCalledWith('/osc-translation', expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ enabled: false }),
        }));
    });
});
