const { JSDOM } = require('jsdom');
const SubtitleScroll = require('../../static/js/subtitle-scroll');

function setMetric(element, name, value) {
    Object.defineProperty(element, name, { configurable: true, value });
}

function setup(options = {}) {
    const dom = new JSDOM('<!doctype html><body><main id="container"></main></body>', {
        pretendToBeVisual: true,
    });
    const container = dom.window.document.getElementById('container');
    setMetric(container, 'clientHeight', options.clientHeight ?? 100);
    setMetric(container, 'scrollHeight', options.scrollHeight ?? 500);
    container.scrollTop = options.scrollTop ?? 0;
    const controller = SubtitleScroll.create({
        container,
        window: dom.window,
        stickyThreshold: options.stickyThreshold,
    });
    return { container, controller, document: dom.window.document, dom };
}

function appendBlock(page, { id, top, height }) {
    const block = page.document.createElement('section');
    block.className = 'sentence-block';
    block.dataset.sentenceId = id;
    setMetric(block, 'offsetTop', top);
    setMetric(block, 'offsetHeight', height);
    page.container.appendChild(block);
    return block;
}

describe('SubtitleScroll capture and restore', () => {
    it('uses the sticky threshold when deciding whether the viewport is at bottom', () => {
        const page = setup({ scrollTop: 349 });
        expect(page.controller.isCloseToBottom()).toBe(false);
        page.container.scrollTop = 350;
        expect(page.controller.isCloseToBottom()).toBe(true);
        page.dom.window.close();
    });

    it('captures a compact bottom marker', () => {
        const page = setup({ scrollTop: 400 });
        expect(page.controller.capture()).toEqual({ wasAtBottom: true });
        page.dom.window.close();
    });

    it('captures the first visible sentence as an anchored offset', () => {
        const page = setup({ scrollTop: 135 });
        appendBlock(page, { id: 'one', top: 0, height: 100 });
        appendBlock(page, { id: 'two', top: 120, height: 80 });
        appendBlock(page, { id: 'three', top: 220, height: 80 });

        expect(page.controller.capture()).toEqual({
            wasAtBottom: false,
            sentenceId: 'two',
            offset: 15,
        });
        page.dom.window.close();
    });

    it('falls back to absolute scrollTop when no sentence can anchor it', () => {
        const page = setup({ scrollTop: 120 });
        expect(page.controller.capture()).toEqual({ wasAtBottom: false, scrollTop: 120 });
        page.dom.window.close();
    });

    it('restores bottom, anchored, and absolute states', () => {
        const page = setup();
        appendBlock(page, { id: 'two', top: 200, height: 80 });

        page.controller.restore({ wasAtBottom: true });
        expect(page.container.scrollTop).toBe(500);
        page.controller.restore({ wasAtBottom: false, sentenceId: 'two', offset: 12 });
        expect(page.container.scrollTop).toBe(212);
        page.controller.restore({ wasAtBottom: false, sentenceId: 'missing', scrollTop: 77 });
        expect(page.container.scrollTop).toBe(77);
        page.dom.window.close();
    });
});

describe('SubtitleScroll render and event lifecycle', () => {
    it('resets, restores empty renders, and completes sticky renders', () => {
        const page = setup({ scrollTop: 120 });
        page.controller.reset();
        expect(page.container.scrollTop).toBe(0);
        expect(page.controller.getDebugState().autoStickToBottom).toBe(true);

        page.controller.restoreAfterEmpty({ wasAtBottom: false, scrollTop: 80 });
        expect(page.container.scrollTop).toBe(80);
        expect(page.controller.getDebugState().autoStickToBottom).toBe(false);

        page.controller.completeRender({ wasAtBottom: true });
        expect(page.container.scrollTop).toBe(500);
        expect(page.controller.getDebugState().autoStickToBottom).toBe(true);
        page.dom.window.close();
    });

    it('binds scroll and resize once and removes both handlers on destroy', () => {
        const page = setup({ scrollTop: 350 });
        expect(page.controller.init()).toBe(true);
        expect(page.controller.init()).toBe(false);
        page.container.dispatchEvent(new page.dom.window.Event('scroll'));
        expect(page.controller.getDebugState().autoStickToBottom).toBe(true);

        page.container.scrollTop = 100;
        page.container.dispatchEvent(new page.dom.window.Event('scroll'));
        expect(page.controller.getDebugState().autoStickToBottom).toBe(false);
        page.dom.window.dispatchEvent(new page.dom.window.Event('resize'));
        expect(page.container.scrollTop).toBe(100);

        page.container.scrollTop = 400;
        page.container.dispatchEvent(new page.dom.window.Event('scroll'));
        page.container.scrollTop = 200;
        page.dom.window.dispatchEvent(new page.dom.window.Event('resize'));
        expect(page.container.scrollTop).toBe(500);

        expect(page.controller.destroy()).toBe(true);
        expect(page.controller.destroy()).toBe(false);
        page.container.scrollTop = 0;
        page.dom.window.dispatchEvent(new page.dom.window.Event('resize'));
        expect(page.container.scrollTop).toBe(0);
        page.dom.window.close();
    });
});
