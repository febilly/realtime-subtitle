(function (root) {
    'use strict';

    function create(options = {}) {
        const session = options.session;
        const renderer = options.renderer;
        if (!session || typeof session.finalizeCurrentNonFinalTokens !== 'function') {
            throw new TypeError('SubtitleRuntimeController requires a subtitle session');
        }
        if (!renderer || typeof renderer.render !== 'function') {
            throw new TypeError('SubtitleRuntimeController requires a subtitle renderer');
        }
        const readState = typeof options.getState === 'function' ? options.getState : () => ({});
        const afterRender = typeof options.afterRender === 'function' ? options.afterRender : () => {};

        function render() {
            const result = renderer.render();
            afterRender(result);
            return result;
        }

        function finalize({ render: shouldRender = true } = {}) {
            const result = session.finalizeCurrentNonFinalTokens();
            if (!result || !result.changed) return false;
            renderer.invalidateAll();
            if (shouldRender) render();
            return true;
        }

        function clear() {
            const state = readState() || {};
            session.clear({
                translateMode: state.translateMode,
                translationUiMode: state.translationUiMode,
            });
            renderer.clearSession();
        }

        return { clear, finalize, render };
    }

    const api = { create };
    root.SubtitleRuntimeController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
