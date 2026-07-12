const { JSDOM } = require('jsdom');
const LanguageCatalog = require('../../static/js/language-catalog');
const LanguageUI = require('../../static/js/language-ui');

function setup(overrides = {}) {
    const dom = new JSDOM('<!doctype html><body><button id="languages"></button></body>', { url: 'http://localhost/' });
    const state = {
        uiTranslationMode: 'one_way',
        backendTranslationMode: 'one_way',
        twoWaySupported: true,
        currentTranslationTargetLang: 'en',
        defaultTranslationTargetLang: 'en',
        backendTargetLang1: 'en',
        backendTargetLang2: 'zh',
        suppressTranslationDisplay: false,
        translationProvider: 'soniox',
        lockManualControls: false,
        ...overrides,
    };
    const restartRecognition = vi.fn();
    const setUiTranslationMode = vi.fn((mode) => { state.uiTranslationMode = mode; });
    const renderSubtitles = vi.fn();
    const catalog = LanguageCatalog.create(['en', 'zh', 'ja']);
    const controller = LanguageUI.create({
        document: dom.window.document,
        window: dom.window,
        storage: dom.window.localStorage,
        button: dom.window.document.getElementById('languages'),
        t: (key) => key,
        catalog,
        getState: () => state,
        updateState: (patch) => Object.assign(state, patch),
        setUiTranslationMode,
        restartRecognition,
        renderSubtitles,
    });
    return {
        dom,
        state,
        controller,
        restartRecognition,
        setUiTranslationMode,
        renderSubtitles,
        setLanguages: (value) => { catalog.setCodes(value); },
    };
}

describe('LanguageUI', () => {
    it('coerces supported language codes and seeds favorites', () => {
        const { dom, controller } = setup();
        expect(controller.coerceSupportedLanguageCode('JA', 'en')).toBe('ja');
        expect(controller.coerceSupportedLanguageCode('xx', 'zh')).toBe('zh');
        expect(controller.getFavoriteLanguages()).toEqual(['en', 'zh', 'ja']);
        dom.window.close();
    });

    it('falls back to seeded favorites when stored data contains non-string codes', () => {
        const { dom, controller } = setup();
        dom.window.localStorage.setItem('favoriteLanguages', JSON.stringify([123, null]));
        const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
        expect(controller.getFavoriteLanguages()).toEqual(['en', 'zh', 'ja']);
        expect(warning).toHaveBeenCalledOnce();
        warning.mockRestore();
        dom.window.close();
    });

    it('builds a picker and dispatches change after selection', () => {
        const { dom, controller } = setup();
        const picker = controller.buildPicker('en');
        dom.window.document.body.appendChild(picker);
        const changed = vi.fn();
        picker.addEventListener('change', changed);
        picker.querySelector('.lang-picker-button').click();
        const japanese = [...dom.window.document.querySelectorAll('.lang-select-option-btn')]
            .find((node) => node.textContent.includes('Japanese'));
        japanese.click();
        expect(picker.value).toBe('ja');
        expect(changed).toHaveBeenCalledOnce();
        dom.window.close();
    });

    it('applies one-way and two-way drafts through explicit state callbacks', () => {
        const { dom, controller, state, restartRecognition, setUiTranslationMode } = setup();
        controller.setDraft({ mode: 'one_way', targetLang: 'ja' });
        expect(controller.applyDraft()).toBe(true);
        expect(state.currentTranslationTargetLang).toBe('ja');
        expect(setUiTranslationMode).toHaveBeenLastCalledWith('one_way');
        expect(restartRecognition).toHaveBeenLastCalledWith({ translationMode: 'one_way', targetLang: 'ja' });

        controller.setDraft({ mode: 'two_way', targetLang1: 'ja', targetLang2: 'zh' });
        expect(controller.applyDraft()).toBe(true);
        expect(state).toMatchObject({ backendTargetLang1: 'ja', backendTargetLang2: 'zh' });
        expect(restartRecognition).toHaveBeenLastCalledWith({
            translationMode: 'two_way', targetLang1: 'ja', targetLang2: 'zh',
        });
        dom.window.close();
    });

    it('suppresses restart for Gemini none mode and rerenders locally', () => {
        const { dom, controller, restartRecognition, renderSubtitles } = setup({ translationProvider: 'gemini' });
        controller.setDraft({ mode: 'none' });
        controller.applyDraft();
        expect(restartRecognition).not.toHaveBeenCalled();
        expect(renderSubtitles).toHaveBeenCalledOnce();
        dom.window.close();
    });

    it('binds one popover toggle and respects locked controls', () => {
        const unlocked = setup();
        expect(unlocked.controller.init()).toBe(true);
        unlocked.dom.window.document.getElementById('languages').click();
        expect(unlocked.controller.isOpen()).toBe(true);
        unlocked.dom.window.document.getElementById('languages').click();
        expect(unlocked.controller.isOpen()).toBe(false);
        unlocked.dom.window.close();

        const locked = setup({ lockManualControls: true });
        locked.controller.init();
        locked.dom.window.document.getElementById('languages').click();
        expect(locked.controller.isOpen()).toBe(false);
        locked.dom.window.close();
    });

    it('closes a portal picker before closing the popover on Escape', () => {
        const { dom, controller } = setup();
        controller.init();
        controller.show();
        dom.window.document.querySelector('.lang-picker-button').click();
        const menu = dom.window.document.querySelector('.lang-select-menu');
        expect(menu.hidden).toBe(false);

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
        expect(menu.hidden).toBe(true);
        expect(controller.isOpen()).toBe(true);

        dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
        expect(controller.isOpen()).toBe(false);
        dom.window.close();
    });

    it('invalidates cached DOM and rebuilds it from the latest provider list', () => {
        const { dom, controller, setLanguages } = setup();
        controller.init();
        controller.show();
        dom.window.document.querySelector('.lang-picker-button').click();
        expect(dom.window.document.body.textContent).toContain('Japanese');

        setLanguages(['fr']);
        controller.invalidate();
        expect(dom.window.document.querySelector('.lang-popover')).toBeNull();
        controller.show();
        dom.window.document.querySelector('.lang-picker-button').click();
        expect(dom.window.document.body.textContent).toContain('French');
        expect(dom.window.document.body.textContent).not.toContain('Japanese');
        dom.window.close();
    });

    it('keeps direct show calls idempotent without leaking document listeners', () => {
        const { dom, controller } = setup();
        const addListener = vi.spyOn(dom.window.document, 'addEventListener');
        const removeListener = vi.spyOn(dom.window.document, 'removeEventListener');

        expect(controller.show()).toBe(true);
        expect(controller.show()).toBe(true);
        controller.hide();

        const tracked = (calls) => calls.filter(([type]) => type === 'mousedown' || type === 'keydown');
        expect(tracked(addListener.mock.calls)).toHaveLength(2);
        expect(tracked(removeListener.mock.calls)).toHaveLength(2);
        dom.window.close();
    });
});
