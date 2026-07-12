(function (root) {
    'use strict';

    function createClient(options = {}) {
        const WebSocketImpl = options.WebSocketImpl || root.WebSocket;
        const getUrl = options.getUrl || (() => '');
        const schedule = options.setTimeout || root.setTimeout.bind(root);
        let socket = null;

        function isUsable(value = socket) {
            return !!value
                && (value.readyState === WebSocketImpl.OPEN || value.readyState === WebSocketImpl.CONNECTING);
        }

        function notifySocket(value) {
            if (typeof options.onSocketChange === 'function') options.onSocketChange(value);
        }

        function connect() {
            if (isUsable()) return socket;
            const created = new WebSocketImpl(getUrl());
            socket = created;
            notifySocket(created);

            created.onopen = (event) => {
                if (typeof options.onOpen === 'function') options.onOpen(event, created);
            };
            created.onmessage = (event) => {
                const frame = JSON.parse(event.data);
                if (typeof options.onFrame === 'function') options.onFrame(frame, created);
            };
            created.onerror = (error) => {
                if (typeof options.onError === 'function') options.onError(error, created);
            };
            created.onclose = (event) => {
                if (socket === created) {
                    socket = null;
                    notifySocket(null);
                }
                if (typeof options.onClose === 'function') options.onClose(event, created);
                if (options.getAutoRestartEnabled && options.getAutoRestartEnabled()) {
                    if (typeof options.onAutoRestart === 'function') options.onAutoRestart();
                    return;
                }
                const shouldReconnect = !options.getShouldReconnect || options.getShouldReconnect();
                const isRestarting = !!(options.getIsRestarting && options.getIsRestarting());
                if (shouldReconnect && !isRestarting) schedule(connect, options.reconnectDelayMs || 2000);
            };
            return created;
        }

        function close() {
            const current = socket;
            if (current && typeof current.close === 'function') current.close();
        }

        return { connect, close, getSocket: () => socket, isUsable };
    }

    const api = { createClient };
    root.WsClient = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
