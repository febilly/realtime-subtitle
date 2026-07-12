const { JSDOM } = require('jsdom');
const SettingsRuntime = require('../../static/js/settings-runtime');

function response(data, { ok = true, status = 200 } = {}) {
    return {
        ok,
        status,
        json: vi.fn().mockResolvedValue(data),
    };
}

function setup(overrides = {}) {
    const dom = new JSDOM(`<!doctype html><body>
        <section id="runtime"></section>
        <section id="microphone"><div id="microphonePicker"></div><p id="microphoneHint"></p></section>
        <div id="autoRestart"></div>
        <label id="speakerField"><div id="speakerPicker"></div></label>
        <div id="fontPicker"></div><p id="fontHint"></p>
        <section id="translationSection"><label id="translationField"><div id="translationPicker"></div></label><p id="translationHint"></p></section>
        <label id="segmentField"><div id="segmentPicker"></div></label>
    </body>`, { url: 'http://localhost/' });
    const document = dom.window.document;
    const state = {
        selectedProvider: 'soniox',
        providerSettings: {},
        autoRestartEnabled: true,
        hideSpeakerLabels: false,
        customFontAvailable: true,
        useBundledCjkFont: false,
        llmRefineAvailable: true,
        lockManualControls: false,
        translationUiMode: 'hybrid',
        defaultTranslationUiMode: 'hybrid',
        translationUiModes: ['fast', 'accurate', 'hybrid'],
        segmentModeSupported: true,
        segmentMode: 'punctuation',
        segmentModes: ['translation', 'endpoint', 'punctuation'],
        ...overrides.state,
    };
    const selectConfigs = [];
    const buildCustomSelect = vi.fn((options, config = {}) => {
        const element = document.createElement('div');
        element.className = 'fake-select';
        element.options = options;
        element.value = String(config.value ?? options[0]?.value ?? '');
        element.disabled = !!config.disabled;
        element.choose = (value) => {
            element.value = String(value);
            if (config.onChange) config.onChange(element.value);
        };
        selectConfigs.push({ options, config, element });
        return element;
    });
    const fetch = overrides.fetch || vi.fn().mockResolvedValue(response({}));
    const calls = [];
    const actions = {
        updateAutoRestartButton: vi.fn(() => calls.push('auto')),
        setSpeakerLabelsHidden: vi.fn(async () => { calls.push('speaker'); return true; }),
        setSegmentMode: vi.fn(async () => { calls.push('segment'); return true; }),
        setTranslationUiMode: vi.fn(async () => { calls.push('translation'); return true; }),
        ...overrides.actions,
    };
    const logger = { warn: vi.fn(), error: vi.fn() };
    const controller = SettingsRuntime.create({
        document,
        fetch,
        storage: dom.window.localStorage,
        t: (key, vars) => vars ? `${key}:${vars.name || ''}` : key,
        localizeBackendMessage: (message) => `localized:${message}`,
        buildCustomSelect,
        getState: () => state,
        updateState: (patch) => Object.assign(state, patch),
        actions,
        console: logger,
        elements: {
            runtimeControlsSection: document.getElementById('runtime'),
            microphoneDeviceSection: document.getElementById('microphone'),
            microphoneDevicePickerHost: document.getElementById('microphonePicker'),
            microphoneDeviceHint: document.getElementById('microphoneHint'),
            autoRestartPickerHost: document.getElementById('autoRestart'),
            speakerLabelsSettingField: document.getElementById('speakerField'),
            speakerLabelsPickerHost: document.getElementById('speakerPicker'),
            bundledCjkFontPickerHost: document.getElementById('fontPicker'),
            bundledCjkFontHint: document.getElementById('fontHint'),
            translationModeSection: document.getElementById('translationSection'),
            translationModeSettingField: document.getElementById('translationField'),
            translationModePickerHost: document.getElementById('translationPicker'),
            translationModeHint: document.getElementById('translationHint'),
            segmentModeSettingField: document.getElementById('segmentField'),
            segmentModePickerHost: document.getElementById('segmentPicker'),
        },
    });
    return {
        actions,
        buildCustomSelect,
        calls,
        controller,
        document,
        dom,
        fetch,
        logger,
        selectConfigs,
        state,
    };
}

describe('SettingsRuntime picker state', () => {
    it('normalizes microphone payloads defensively', () => {
        expect(SettingsRuntime.normalizeMicrophoneData(null)).toEqual({
            available: false, default: null, devices: [], selected_id: '',
        });
        expect(SettingsRuntime.normalizeMicrophoneData({
            available: 1,
            default: { name: 'Default mic' },
            devices: [{ id: 'mic-1' }],
            selected_id: 42,
        })).toEqual({
            available: true,
            default: { name: 'Default mic' },
            devices: [{ id: 'mic-1' }],
            selected_id: '42',
        });
    });

    it('renders all supported pickers from current state', () => {
        const page = setup({ state: { providerSettings: { hideSpeakerLabels: true } } });
        page.controller.renderSettingsPickers();
        const pickers = page.controller.getPickers();

        expect(pickers.autoRestart.value).toBe('true');
        expect(pickers.speakerLabels.value).toBe('hide');
        expect(pickers.bundledCjkFont.value).toBe('false');
        expect(pickers.translationMode.value).toBe('hybrid');
        expect(pickers.segmentMode.value).toBe('punctuation');
        expect(page.document.getElementById('translationHint').textContent).toBe('translation_cost_llm');
        expect(page.document.getElementById('runtime').hidden).toBe(false);
        page.dom.window.close();
    });

    it('hides provider/capability-specific controls', () => {
        const page = setup({
            state: {
                selectedProvider: 'gemini',
                llmRefineAvailable: false,
                segmentModeSupported: false,
                customFontAvailable: false,
            },
        });
        page.controller.renderSettingsPickers();
        const pickers = page.controller.getPickers();

        expect(page.document.getElementById('speakerField').hidden).toBe(true);
        expect(pickers.speakerLabels).toBeNull();
        expect(page.document.getElementById('translationSection').hidden).toBe(true);
        expect(pickers.translationMode).toBeNull();
        expect(page.document.getElementById('segmentField').hidden).toBe(true);
        expect(pickers.segmentMode).toBeNull();
        expect(page.document.querySelector('.font-not-detected-status').textContent).toBe('custom_font_not_detected');
        page.dom.window.close();
    });

    it('updates the translation cost hint from the unsaved selection', () => {
        const page = setup();
        const picker = page.controller.renderTranslationModePicker();
        picker.choose('fast');
        expect(page.document.getElementById('translationHint').hidden).toBe(true);
        expect(page.document.getElementById('translationHint').textContent).toBe('');
        picker.choose('accurate');
        expect(page.document.getElementById('translationHint').hidden).toBe(false);
        expect(page.document.getElementById('translationHint').textContent).toBe('translation_cost_llm');
        page.dom.window.close();
    });
});

describe('SettingsRuntime microphone and font effects', () => {
    it('loads devices and saves the selected microphone exactly', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({
                available: true,
                default: { name: 'System default' },
                devices: [
                    { id: 'default', name: 'Default', is_default: true },
                    { id: 'mic-1', name: 'USB mic', is_default: false },
                ],
                selected_id: 'mic-1',
            }))
            .mockResolvedValueOnce(response({ id: 'mic-1' }));
        const page = setup({ fetch });

        expect(await page.controller.fetchMicrophoneDevices()).toBe(true);
        const picker = page.controller.getPickers().microphone;
        expect(picker.options).toEqual([
            { value: '', label: 'microphone_device_default:System default' },
            { value: 'mic-1', label: 'USB mic' },
        ]);
        expect(picker.value).toBe('mic-1');
        expect(await page.controller.saveMicrophoneDeviceSelection()).toEqual({ ok: true });
        expect(fetch).toHaveBeenNthCalledWith(2, '/microphone-device', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: 'mic-1' }),
        });
        page.dom.window.close();
    });

    it('resets microphone availability on fetch failure and localizes save errors', async () => {
        const fetch = vi.fn()
            .mockResolvedValueOnce(response({ available: true, devices: [], selected_id: '' }))
            .mockResolvedValueOnce(response({ message: 'denied' }, { ok: false, status: 403 }));
        const page = setup({ fetch });
        await page.controller.fetchMicrophoneDevices();
        expect(await page.controller.saveMicrophoneDeviceSelection()).toEqual({
            ok: false,
            message: 'localized:denied',
        });

        page.fetch.mockRejectedValueOnce(new Error('offline'));
        expect(await page.controller.fetchMicrophoneDevices()).toBe(false);
        expect(page.controller.getMicrophoneData().available).toBe(false);
        expect(page.logger.error).toHaveBeenCalled();
        page.dom.window.close();
    });

    it('applies, persists, and synchronizes the bundled font preference', async () => {
        const page = setup();
        page.controller.renderBundledCjkFontPicker().choose('true');
        expect(page.controller.applyBundledCjkFontDraft()).toBe(true);
        await Promise.resolve();

        expect(page.state.useBundledCjkFont).toBe(true);
        expect(page.document.body.classList.contains('use-bundled-cjk-fonts')).toBe(true);
        expect(page.dom.window.localStorage.getItem('useBundledCjkFont')).toBe('true');
        expect(page.fetch).toHaveBeenCalledWith('/subtitle-font', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ use_bundled_cjk_fonts: true }),
        });
        page.dom.window.close();
    });
});

describe('SettingsRuntime save orchestration', () => {
    it('applies auto restart, speaker, segment, and translation in order', async () => {
        const page = setup();
        page.controller.renderSettingsPickers();
        const pickers = page.controller.getPickers();
        pickers.autoRestart.choose('false');
        pickers.speakerLabels.choose('hide');
        pickers.segmentMode.choose('endpoint');
        pickers.translationMode.choose('accurate');

        await expect(page.controller.applyRuntimeControlSettings()).resolves.toEqual({ ok: true });
        expect(page.state.autoRestartEnabled).toBe(false);
        expect(page.dom.window.localStorage.getItem('autoRestartEnabled')).toBe('false');
        expect(page.calls).toEqual(['auto', 'speaker', 'segment', 'translation']);
        expect(page.actions.setTranslationUiMode).toHaveBeenCalledWith('accurate', {
            restartIfNeeded: true,
        });
        page.dom.window.close();
    });

    it('stops after the first failed runtime mutation', async () => {
        const page = setup({
            actions: {
                setSpeakerLabelsHidden: vi.fn(async () => false),
            },
        });
        page.controller.renderSettingsPickers();
        const pickers = page.controller.getPickers();
        pickers.speakerLabels.choose('hide');
        pickers.segmentMode.choose('endpoint');
        pickers.translationMode.choose('accurate');

        await expect(page.controller.applyRuntimeControlSettings()).resolves.toEqual({
            ok: false,
            message: 'backend_speaker_labels_disabled',
        });
        expect(page.actions.setSegmentMode).not.toHaveBeenCalled();
        expect(page.actions.setTranslationUiMode).not.toHaveBeenCalled();
        page.dom.window.close();
    });

    it('writes the speaker-label draft only for Soniox', () => {
        const page = setup();
        page.controller.renderSpeakerLabelsPicker().choose('hide');
        expect(page.controller.writeProviderSettingsDraft({}, 'soniox')).toEqual({
            hideSpeakerLabels: true,
        });
        expect(page.controller.writeProviderSettingsDraft({}, 'gemini')).toEqual({});
        page.dom.window.close();
    });
});
