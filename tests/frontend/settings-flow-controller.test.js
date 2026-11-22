const SettingsPolicy = require('../../static/js/settings-policy');
const SettingsFlowController = require('../../static/js/settings-flow-controller');

function setup(overrides = {}) {
    const state = {
        lockManualControls: false,
        connectionMode: 'direct',
        serverSettings: { mode: 'direct', modeChosen: true },
        setupRequired: false,
        ...(overrides.state || {}),
    };
    const events = [];
    const panel = overrides.panel || {
        open: vi.fn((options) => {
            events.push(['open', options]);
            return true;
        }),
        hide: vi.fn(() => events.push(['hide'])),
        close: vi.fn(() => {
            events.push(['close']);
            return true;
        }),
        isOpen: vi.fn(() => true),
        isForcedOpen: vi.fn(() => false),
        isSaving: vi.fn(() => false),
        hasUnsavedChanges: vi.fn(() => false),
    };
    const submit = overrides.submit || vi.fn((event) => ({ event }));
    const confirmUnsavedChanges = overrides.confirmUnsavedChanges;
    const openLogin = overrides.openLogin || vi.fn((options) => events.push(['login', options]));
    const showToast = overrides.showToast || vi.fn((message) => events.push(['toast', message]));
    const updateState = overrides.updateState || vi.fn((patch) => {
        events.push(['state', patch]);
        Object.assign(state, patch);
    });
    const controller = SettingsFlowController.create({
        panel,
        policy: SettingsPolicy,
        t: (key) => `t:${key}`,
        getState: () => state,
        updateState,
        submit,
        confirmUnsavedChanges,
        actions: { openLogin, showToast },
    });
    return { controller, events, openLogin, panel, showToast, state, submit, updateState };
}

describe('SettingsFlowController dependencies and delegation', () => {
    it('requires the panel and force-open policy boundaries', () => {
        expect(() => SettingsFlowController.create({ policy: SettingsPolicy }))
            .toThrow('SettingsFlowController requires a settings panel');
        expect(() => SettingsFlowController.create({ panel: { open() {}, hide() {} } }))
            .toThrow('SettingsFlowController requires settings policy');
    });

    it('delegates panel and submit entry points without changing their results', () => {
        const env = setup();
        const event = { type: 'submit' };
        expect(env.controller.open({ forced: true })).toBe(true);
        env.controller.hide();
        expect(env.controller.close()).toBe(true);
        expect(env.controller.handleSubmit(event)).toEqual({ event });
        expect(env.panel.open).toHaveBeenCalledWith({ forced: true });
        expect(env.panel.hide).toHaveBeenCalledOnce();
        expect(env.panel.close).toHaveBeenCalledOnce();
        expect(env.submit).toHaveBeenCalledWith(event);
    });

    it('finishes a hot save in the existing state-hide-toast order', () => {
        const env = setup({ state: { setupRequired: true } });
        env.controller.finishHotSave();
        expect(env.state.setupRequired).toBe(false);
        expect(env.events).toEqual([
            ['state', { setupRequired: false }],
            ['hide'],
            ['toast', 't:settings_saved'],
        ]);
    });
});

describe('SettingsFlowController forced startup action', () => {
    it.each([
        ['locked', { lockManualControls: true, connectionMode: 'direct', setupRequired: true }, null],
        ['ready direct', { connectionMode: 'direct', setupRequired: false }, null],
        ['direct setup required', { connectionMode: 'direct', setupRequired: true }, 'settings'],
        ['relay token missing', { connectionMode: 'relay', serverSettings: { mode: 'relay' } }, 'login'],
        ['relay setup required', {
            connectionMode: 'relay',
            serverSettings: { mode: 'relay', token: 'saved' },
            setupRequired: true,
        }, 'login'],
    ])('%s resolves and performs exactly one panel action', (_label, patch, expected) => {
        const env = setup({ state: patch });
        expect(env.controller.maybeForceOpen()).toBe(expected);
        if (expected === 'settings') {
            expect(env.panel.open).toHaveBeenCalledWith({ forced: true });
            expect(env.openLogin).not.toHaveBeenCalled();
        } else if (expected === 'login') {
            expect(env.openLogin).toHaveBeenCalledWith({ forced: true });
            expect(env.panel.open).not.toHaveBeenCalled();
        } else {
            expect(env.openLogin).not.toHaveBeenCalled();
            expect(env.panel.open).not.toHaveBeenCalled();
        }
    });
});

describe('SettingsFlowController requestClose unsaved changes guard', () => {
    it('closes synchronously without prompting when there are no unsaved changes', () => {
        const confirmUnsavedChanges = vi.fn();
        const env = setup({ confirmUnsavedChanges });
        expect(env.controller.requestClose()).toBe(true);
        expect(confirmUnsavedChanges).not.toHaveBeenCalled();
        expect(env.panel.close).toHaveBeenCalledOnce();
    });

    it('saves when the user chooses ok, discards on alt, and stays open on cancel', async () => {
        let open = true;
        const submit = vi.fn(async () => { open = false; });
        const panel = {
            open: vi.fn(),
            hide: vi.fn(() => { open = false; }),
            close: vi.fn(() => { open = false; return true; }),
            isOpen: vi.fn(() => open),
            isForcedOpen: vi.fn(() => false),
            isSaving: vi.fn(() => false),
            hasUnsavedChanges: vi.fn(() => true),
        };
        const confirmUnsavedChanges = vi.fn()
            .mockResolvedValueOnce('cancel')
            .mockResolvedValueOnce('alt')
            .mockResolvedValueOnce('ok');
        const env = setup({ panel, submit, confirmUnsavedChanges });

        await expect(env.controller.requestClose()).resolves.toBe(false);
        expect(panel.close).not.toHaveBeenCalled();
        expect(submit).not.toHaveBeenCalled();

        await expect(env.controller.requestClose()).resolves.toBe(true);
        expect(panel.close).toHaveBeenCalledOnce();
        expect(submit).not.toHaveBeenCalled();

        open = true;
        await expect(env.controller.requestClose()).resolves.toBe(true);
        expect(submit).toHaveBeenCalledOnce();
    });

    it('blocks requestClose while forced open or saving', () => {
        const confirmUnsavedChanges = vi.fn();
        const panel = {
            open: vi.fn(),
            hide: vi.fn(),
            close: vi.fn(),
            isOpen: vi.fn(() => true),
            isForcedOpen: vi.fn(() => true),
            isSaving: vi.fn(() => false),
            hasUnsavedChanges: vi.fn(() => true),
        };
        const env = setup({ panel, confirmUnsavedChanges });
        expect(env.controller.requestClose()).toBe(false);
        expect(confirmUnsavedChanges).not.toHaveBeenCalled();

        panel.isForcedOpen.mockReturnValue(false);
        panel.isSaving.mockReturnValue(true);
        expect(env.controller.requestClose()).toBe(false);
        expect(confirmUnsavedChanges).not.toHaveBeenCalled();
    });
});
