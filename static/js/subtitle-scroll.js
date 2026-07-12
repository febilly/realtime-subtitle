(function (root) {
    'use strict';

    const DEFAULT_STICKY_THRESHOLD = 50;

    function create(options = {}) {
        const windowRef = options.window || root;
        const container = options.container;
        if (!container) throw new TypeError('SubtitleScroll.create requires a container');
        const stickyThreshold = Number.isFinite(Number(options.stickyThreshold))
            ? Number(options.stickyThreshold)
            : DEFAULT_STICKY_THRESHOLD;
        let autoStickToBottom = true;
        let initialized = false;

        function isCloseToBottom() {
            return (container.scrollTop + container.clientHeight)
                >= (container.scrollHeight - stickyThreshold);
        }

        function capture() {
            const wasAtBottom = isCloseToBottom();
            if (wasAtBottom) return { wasAtBottom: true };

            const sentenceBlocks = container.querySelectorAll('.sentence-block');
            const currentScrollTop = container.scrollTop;
            let anchor = null;
            for (const block of sentenceBlocks) {
                const blockTop = block.offsetTop;
                const blockBottom = blockTop + block.offsetHeight;
                if (blockBottom > currentScrollTop) {
                    anchor = block;
                    break;
                }
            }
            if (anchor) {
                return {
                    wasAtBottom: false,
                    sentenceId: anchor.dataset.sentenceId,
                    offset: currentScrollTop - anchor.offsetTop,
                };
            }
            return { wasAtBottom: false, scrollTop: currentScrollTop };
        }

        function restore(scrollState) {
            if (!scrollState) return;
            if (scrollState.wasAtBottom) {
                container.scrollTop = container.scrollHeight;
                return;
            }
            if (scrollState.sentenceId) {
                const anchor = container.querySelector(
                    `.sentence-block[data-sentence-id="${scrollState.sentenceId}"]`,
                );
                if (anchor) {
                    container.scrollTop = anchor.offsetTop + (scrollState.offset || 0);
                    return;
                }
            }
            if (typeof scrollState.scrollTop === 'number') {
                container.scrollTop = scrollState.scrollTop;
            }
        }

        function reset() {
            container.scrollTop = 0;
            autoStickToBottom = true;
        }

        function restoreAfterEmpty(scrollState) {
            restore(scrollState);
            autoStickToBottom = scrollState ? !!scrollState.wasAtBottom : true;
        }

        function completeRender(scrollState) {
            restore(scrollState);
            autoStickToBottom = scrollState ? !!scrollState.wasAtBottom : isCloseToBottom();
            if (autoStickToBottom) container.scrollTop = container.scrollHeight;
        }

        function handleScroll() {
            autoStickToBottom = isCloseToBottom();
        }

        function handleResize() {
            if (autoStickToBottom) container.scrollTop = container.scrollHeight;
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            container.addEventListener('scroll', handleScroll);
            windowRef.addEventListener('resize', handleResize);
            return true;
        }

        function destroy() {
            if (!initialized) return false;
            container.removeEventListener('scroll', handleScroll);
            windowRef.removeEventListener('resize', handleResize);
            initialized = false;
            return true;
        }

        function getDebugState() {
            return { autoStickToBottom, initialized, stickyThreshold };
        }

        return {
            capture,
            completeRender,
            destroy,
            getDebugState,
            init,
            isCloseToBottom,
            reset,
            restore,
            restoreAfterEmpty,
        };
    }

    const api = { DEFAULT_STICKY_THRESHOLD, create };
    root.SubtitleScroll = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
