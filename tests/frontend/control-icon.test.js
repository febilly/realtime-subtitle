const { JSDOM } = require('jsdom');
const ControlIcon = require('../../static/js/control-icon');

describe('ControlIcon', () => {
    it('publishes the fixed sprite URL', () => {
        expect(ControlIcon.SPRITE_URL).toBe('icons/lucide-sprite.svg');
    });

    it('updates both modern and legacy SVG references', () => {
        const dom = new JSDOM('<svg id="icon"><use></use></svg>');
        const icon = dom.window.document.getElementById('icon');
        const use = icon.querySelector('use');
        expect(ControlIcon.set(icon, 'pause')).toBe(true);
        expect(use.getAttribute('href')).toBe('icons/lucide-sprite.svg#pause');
        expect(use.getAttributeNS('http://www.w3.org/1999/xlink', 'href'))
            .toBe('icons/lucide-sprite.svg#pause');
        dom.window.close();
    });

    it.each([
        [null, 'pause'],
        [{ querySelector: vi.fn() }, ''],
        [{ querySelector: vi.fn(() => null) }, 'pause'],
    ])('ignores incomplete icon inputs', (icon, name) => {
        expect(ControlIcon.set(icon, name)).toBe(false);
    });
});
