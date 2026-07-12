const { JSDOM } = require('jsdom');
const SettingsUI = require('../../static/js/settings-ui');

function setup(actions = {}) {
    const dom = new JSDOM('<!doctype html><body><button id="settings"></button><aside id="panel" hidden></aside><div id="overlay" hidden></div><form id="form"><button id="close" type="button"></button><button id="cancel" type="button"></button></form></body>');
    const storage = { setItem: vi.fn() };
    const ui = SettingsUI.create({ document: dom.window.document, window: dom.window, storage, actions });
    return { dom, document: dom.window.document, storage, ui };
}

describe('SettingsUI theme and panel presentation', () => {
    it('normalizes themes, updates body/icon, and persists', () => {
        const { dom, document, storage, ui } = setup();
        const icon = {};
        const setControlIcon = vi.fn();
        expect(ui.applyTheme('chroma', { enableChromaTheme: false, themeIcon: icon, setControlIcon })).toBe('dark');
        expect(document.body.classList.contains('dark-theme')).toBe(true);
        expect(ui.applyTheme('chroma', { enableChromaTheme: true, themeIcon: icon, setControlIcon })).toBe('chroma');
        expect(document.body.classList.contains('chroma-theme')).toBe(true);
        expect(setControlIcon).toHaveBeenLastCalledWith(icon, 'sparkles');
        expect(storage.setItem).toHaveBeenLastCalledWith('theme', 'chroma');
        dom.window.close();
    });

    it('opens and closes a panel with hidden and aria state kept together', () => {
        const { dom, document, ui } = setup();
        const panel = document.getElementById('panel');
        const overlay = document.getElementById('overlay');
        ui.setPanelOpen(panel, overlay, true);
        expect(panel.hidden).toBe(false);
        expect(panel.getAttribute('aria-hidden')).toBe('false');
        expect(overlay.hidden).toBe(false);
        ui.setPanelOpen(panel, overlay, false);
        expect(panel.hidden).toBe(true);
        expect(panel.getAttribute('aria-hidden')).toBe('true');
        dom.window.close();
    });
});

describe('SettingsUI controls', () => {
    it('builds an accessible custom picker and reports a changed value', () => {
        const onChange = vi.fn();
        const { dom, document, ui } = setup();
        const picker = ui.buildCustomSelect([
            { value: 'one', label: 'One' },
            { value: 'two', label: 'Two' },
        ], { value: 'one', onChange });
        document.body.appendChild(picker);
        const trigger = picker.querySelector('button');
        trigger.click();
        expect(trigger.getAttribute('aria-expanded')).toBe('true');
        [...document.querySelectorAll('.lang-select-option')].find((node) => node.textContent === 'Two').click();
        expect(picker.value).toBe('two');
        expect(onChange).toHaveBeenCalledWith('two');
        expect(trigger.getAttribute('aria-expanded')).toBe('false');
        dom.window.close();
    });

    it('binds once and destroy removes settings event handlers', () => {
        const openSettings = vi.fn();
        const closeSettings = vi.fn();
        const handleSettingsSave = vi.fn((event) => event.preventDefault());
        const { dom, document, ui } = setup({ openSettings, closeSettings, handleSettingsSave });
        const elements = {
            settingsButton: document.getElementById('settings'),
            closeButton: document.getElementById('close'),
            cancelButton: document.getElementById('cancel'),
            overlay: document.getElementById('overlay'),
            form: document.getElementById('form'),
        };
        expect(ui.init(elements)).toBe(true);
        expect(ui.init(elements)).toBe(false);
        elements.settingsButton.click();
        elements.closeButton.click();
        elements.form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
        expect(openSettings).toHaveBeenCalledOnce();
        expect(closeSettings).toHaveBeenCalledOnce();
        expect(handleSettingsSave).toHaveBeenCalledOnce();
        ui.destroy();
        elements.settingsButton.click();
        expect(openSettings).toHaveBeenCalledOnce();
        dom.window.close();
    });
});
