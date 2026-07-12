const { JSDOM } = require('jsdom');
const ConfirmDialog = require('../../static/js/confirm-dialog');

function setup({ elements = true } = {}) {
    const dom = new JSDOM(`<!doctype html><body>
        <div id="overlay" hidden></div><section id="dialog" hidden>
            <p id="message"></p><button id="ok"></button><button id="cancel"></button>
        </section>
    </body>`);
    const document = dom.window.document;
    dom.window.confirm = vi.fn(() => true);
    const controller = ConfirmDialog.create({
        document,
        window: dom.window,
        t: (key) => `t:${key}`,
        elements: elements ? {
            overlay: document.getElementById('overlay'),
            dialog: document.getElementById('dialog'),
            message: document.getElementById('message'),
            okButton: document.getElementById('ok'),
            cancelButton: document.getElementById('cancel'),
        } : {},
    });
    return { controller, document, dom };
}

describe('ConfirmDialog presentation', () => {
    it('shows default labels, normal style, and focuses cancel', () => {
        const page = setup();
        page.controller.init();
        void page.controller.show('Continue?');

        expect(page.document.getElementById('overlay').hidden).toBe(false);
        expect(page.document.getElementById('dialog').hidden).toBe(false);
        expect(page.document.getElementById('message').textContent).toBe('Continue?');
        expect(page.document.getElementById('ok').textContent).toBe('t:confirm');
        expect(page.document.getElementById('ok').className).toBe('primary-button');
        expect(page.document.getElementById('cancel').textContent).toBe('t:cancel');
        expect(page.document.activeElement).toBe(page.document.getElementById('cancel'));
        page.controller.close(false);
        page.dom.window.close();
    });

    it('applies custom labels and resets danger style between calls', async () => {
        const page = setup();
        page.controller.init();
        const dangerous = page.controller.show('Delete?', {
            okLabel: 'Delete', cancelLabel: 'Keep', danger: true,
        });
        expect(page.document.getElementById('ok').className).toBe('danger-button');
        page.controller.close(false);
        await expect(dangerous).resolves.toBe(false);

        const normal = page.controller.show('Continue?');
        expect(page.document.getElementById('ok').className).toBe('primary-button');
        page.controller.close(false);
        await normal;
        page.dom.window.close();
    });
});

describe('ConfirmDialog completion paths', () => {
    it.each([
        ['ok', true],
        ['cancel', false],
        ['overlay', false],
    ])('resolves %s clicks and hides the dialog', async (id, expected) => {
        const page = setup();
        page.controller.init();
        const result = page.controller.show('Question');
        page.document.getElementById(id).click();

        await expect(result).resolves.toBe(expected);
        expect(page.document.getElementById('overlay').hidden).toBe(true);
        expect(page.document.getElementById('dialog').hidden).toBe(true);
        page.dom.window.close();
    });

    it.each([
        ['Enter', true],
        ['Escape', false],
    ])('resolves %s and removes the active key handler', async (key, expected) => {
        const page = setup();
        page.controller.init();
        const result = page.controller.show('Question');
        page.document.dispatchEvent(new page.dom.window.KeyboardEvent('keydown', { key }));

        await expect(result).resolves.toBe(expected);
        page.document.dispatchEvent(new page.dom.window.KeyboardEvent('keydown', {
            key: expected ? 'Escape' : 'Enter',
        }));
        expect(page.controller.getDebugState().pending).toBe(false);
        page.dom.window.close();
    });

    it('cancels a pending question before showing the next one', async () => {
        const page = setup();
        page.controller.init();
        const first = page.controller.show('First');
        const second = page.controller.show('Second');

        await expect(first).resolves.toBe(false);
        expect(page.document.getElementById('message').textContent).toBe('Second');
        page.controller.close(true);
        await expect(second).resolves.toBe(true);
        page.dom.window.close();
    });

    it('falls back to window.confirm without dialog elements', async () => {
        const page = setup({ elements: false });
        await expect(page.controller.show('Fallback?')).resolves.toBe(true);
        expect(page.dom.window.confirm).toHaveBeenCalledWith('Fallback?');
        page.dom.window.close();
    });

    it('works without optional message or button elements', async () => {
        const page = setup();
        const controller = ConfirmDialog.create({
            document: page.document,
            window: page.dom.window,
            elements: {
                overlay: page.document.getElementById('overlay'),
                dialog: page.document.getElementById('dialog'),
            },
        });
        controller.init();
        const result = controller.show('Question');
        page.document.dispatchEvent(new page.dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
        await expect(result).resolves.toBe(true);
        page.dom.window.close();
    });

    it('initializes once and destroy cancels pending work and unbinds controls', async () => {
        const page = setup();
        expect(page.controller.init()).toBe(true);
        expect(page.controller.init()).toBe(false);
        const result = page.controller.show('Question');
        page.controller.destroy();

        await expect(result).resolves.toBe(false);
        expect(page.controller.getDebugState()).toEqual({ initialized: false, pending: false });
        page.document.getElementById('ok').click();
        expect(page.controller.getDebugState().pending).toBe(false);
        page.dom.window.close();
    });
});
