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
        let flowDirection = options.flowDirection === 'down' ? 'down' : 'up';
        let autoStickToEdge = true;
        let initialized = false;

        function isCloseToBottom() {
            return (container.scrollTop + container.clientHeight)
                >= (container.scrollHeight - stickyThreshold);
        }

        function isCloseToTop() {
            return container.scrollTop <= stickyThreshold;
        }

        function isCloseToFlowEdge() {
            return flowDirection === 'down' ? isCloseToTop() : isCloseToBottom();
        }

        function scrollToFlowEdge() {
            container.scrollTop = flowDirection === 'down' ? 0 : container.scrollHeight;
        }

        function capture() {
            const wasAtFlowEdge = isCloseToFlowEdge();
            if (wasAtFlowEdge) {
                return flowDirection === 'down' ? { wasAtTop: true } : { wasAtBottom: true };
            }

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
            if (scrollState.wasAtBottom || scrollState.wasAtTop) {
                scrollToFlowEdge();
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
            autoStickToEdge = true;
        }

        function restoreAfterEmpty(scrollState) {
            restore(scrollState);
            autoStickToEdge = scrollState
                ? !!(scrollState.wasAtBottom || scrollState.wasAtTop)
                : true;
        }

        function completeRender(scrollState) {
            restore(scrollState);
            autoStickToEdge = scrollState
                ? !!(scrollState.wasAtBottom || scrollState.wasAtTop)
                : isCloseToFlowEdge();
            if (autoStickToEdge) scrollToFlowEdge();
        }

        function handleScroll() {
            autoStickToEdge = isCloseToFlowEdge();
        }

        function handleResize() {
            if (autoStickToEdge) scrollToFlowEdge();
        }

        function setFlowDirection(direction) {
            const normalized = direction === 'down' ? 'down' : 'up';
            const changed = normalized !== flowDirection;
            flowDirection = normalized;
            autoStickToEdge = true;
            scrollToFlowEdge();
            return changed;
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
            return {
                autoStickToBottom: autoStickToEdge,
                autoStickToEdge,
                flowDirection,
                initialized,
                stickyThreshold,
            };
        }

        return {
            capture,
            completeRender,
            destroy,
            getDebugState,
            init,
            isCloseToBottom,
            isCloseToFlowEdge,
            isCloseToTop,
            reset,
            restore,
            restoreAfterEmpty,
            setFlowDirection,
        };
    }

    const api = { DEFAULT_STICKY_THRESHOLD, create };
    root.SubtitleScroll = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
