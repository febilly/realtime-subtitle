const { JSDOM } = require('jsdom');
const CustomTooltip = require('../../static/js/custom-tooltip');

function setup(overrides = {}) {
    const dom = new JSDOM(`<!doctype html><body>
        <button id="target" title="Initial tooltip"><span id="child">Child</span></button>
        <div id="plain"></div>
    </body>`, { pretendToBeVisual: true });
    const { document } = dom.window;
    const target = document.getElementById('target');
    target.getBoundingClientRect = vi.fn(() => ({
        left: 100,
        right: 140,
        top: 50,
        bottom: 70,
        width: 40,
        height: 20,
    }));
    const original = {
        setAttribute: dom.window.Element.prototype.setAttribute,
        getAttribute: dom.window.Element.prototype.getAttribute,
        removeAttribute: dom.window.Element.prototype.removeAttribute,
        title: Object.getOwnPropertyDescriptor(dom.window.HTMLElement.prototype, 'title'),
    };
    const controller = CustomTooltip.create({
        document,
        window: dom.window,
        ...overrides,
    });
    return { controller, document, dom, original, target };
}

describe('CustomTooltip title hooks', () => {
    it('migrates initial titles and keeps property and attribute APIs synchronized', () => {
        const page = setup();
        page.controller.init();

        expect(page.target.hasAttribute('title')).toBe(false);
        expect(page.target.getAttribute('data-custom-title')).toBe('Initial tooltip');
        expect(page.target.getAttribute('title')).toBe('Initial tooltip');
        expect(page.target.title).toBe('Initial tooltip');

        page.target.setAttribute('TITLE', 'Attribute update');
        expect(page.target.title).toBe('Attribute update');
        expect(page.target.hasAttribute('title')).toBe(false);
        page.target.title = 'Property update';
        expect(page.target.getAttribute('title')).toBe('Property update');
        page.target.removeAttribute('title');
        expect(page.target.title).toBe('');
        expect(page.target.hasAttribute('data-custom-title')).toBe(false);

        page.controller.destroy();
        page.dom.window.close();
    });

    it('initializes once and restores DOM prototypes on destroy', () => {
        const page = setup();
        expect(page.controller.init()).toBe(true);
        expect(page.controller.init()).toBe(false);
        expect(page.dom.window.Element.prototype.setAttribute).not.toBe(page.original.setAttribute);

        expect(page.controller.destroy()).toBe(true);
        expect(page.controller.destroy()).toBe(false);
        expect(page.dom.window.Element.prototype.setAttribute).toBe(page.original.setAttribute);
        expect(page.dom.window.Element.prototype.getAttribute).toBe(page.original.getAttribute);
        expect(page.dom.window.Element.prototype.removeAttribute).toBe(page.original.removeAttribute);
        expect(Object.getOwnPropertyDescriptor(page.dom.window.HTMLElement.prototype, 'title'))
            .toEqual(page.original.title);
        expect(page.document.querySelector('.custom-tooltip')).toBeNull();
        page.dom.window.close();
    });
});

describe('CustomTooltip interactions', () => {
    it('shows the closest titled ancestor on hover and hides after leaving it', () => {
        const page = setup();
        page.controller.init();
        const child = page.document.getElementById('child');
        child.dispatchEvent(new page.dom.window.MouseEvent('mouseover', { bubbles: true }));

        const tooltip = page.document.querySelector('.custom-tooltip');
        expect(tooltip.textContent).toBe('Initial tooltip');
        expect(tooltip.classList.contains('visible')).toBe(true);
        expect(tooltip.style.maxWidth).toBe('350px');
        expect(page.controller.getDebugState().activeTarget).toBe(page.target);

        child.dispatchEvent(new page.dom.window.MouseEvent('mouseout', {
            bubbles: true,
            relatedTarget: page.document.getElementById('plain'),
        }));
        expect(tooltip.classList.contains('visible')).toBe(false);
        expect(page.controller.getDebugState().activeTarget).toBeNull();
        page.controller.destroy();
        page.dom.window.close();
    });

    it('updates an active tooltip through title setters and hides on empty text', () => {
        const page = setup();
        page.controller.init();
        page.target.dispatchEvent(new page.dom.window.MouseEvent('mouseover', { bubbles: true }));
        const tooltip = page.document.querySelector('.custom-tooltip');

        page.target.title = 'Updated tooltip';
        expect(tooltip.textContent).toBe('Updated tooltip');
        expect(tooltip.classList.contains('visible')).toBe(true);
        page.target.setAttribute('title', 'Attribute tooltip');
        expect(tooltip.textContent).toBe('Attribute tooltip');
        page.target.title = '';
        expect(tooltip.classList.contains('visible')).toBe(false);
        expect(page.controller.getDebugState().activeTarget).toBeNull();
        page.controller.destroy();
        page.dom.window.close();
    });

    it('rechecks visibility after click-driven DOM updates', () => {
        let scheduled = null;
        const clearTimeout = vi.fn();
        const page = setup({
            setTimeout(callback) {
                scheduled = callback;
                return 7;
            },
            clearTimeout,
        });
        page.controller.init();
        page.target.dispatchEvent(new page.dom.window.MouseEvent('mouseover', { bubbles: true }));
        page.target.dispatchEvent(new page.dom.window.MouseEvent('click', { bubbles: true }));
        expect(scheduled).toBeTypeOf('function');

        page.target.remove();
        scheduled();
        expect(page.document.querySelector('.custom-tooltip').classList.contains('visible')).toBe(false);
        expect(page.controller.getDebugState().activeTarget).toBeNull();
        page.controller.destroy();
        expect(clearTimeout).not.toHaveBeenCalled();
        page.dom.window.close();
    });

    it('clears pending click checks during destroy', () => {
        const clearTimeout = vi.fn();
        const page = setup({ setTimeout: vi.fn(() => 11), clearTimeout });
        page.controller.init();
        page.target.dispatchEvent(new page.dom.window.MouseEvent('mouseover', { bubbles: true }));
        page.target.dispatchEvent(new page.dom.window.MouseEvent('click', { bubbles: true }));
        page.controller.destroy();
        expect(clearTimeout).toHaveBeenCalledWith(11);
        page.dom.window.close();
    });
});
