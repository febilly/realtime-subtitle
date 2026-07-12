(function (root) {
    'use strict';

    function buildWebSocketUrl(location = {}) {
        const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
        return `${protocol}://${location.host || ''}/ws${location.search || ''}`;
    }

    function create(options = {}) {
        const windowRef = options.window || root;
        const wsClientApi = options.wsClient || root.WsClient;
        if (!wsClientApi || typeof wsClientApi.createClient !== 'function') {
            throw new TypeError('WebSocketController requires WsClient.createClient');
        }

        const logger = options.logger || root.console;
        const readState = typeof options.getState === 'function'
            ? options.getState
            : () => ({});
        const onFrame = typeof options.onFrame === 'function' ? options.onFrame : () => {};
        const onAutoRestart = typeof options.onAutoRestart === 'function'
            ? options.onAutoRestart
            : () => {};
        let client = null;

        function state() {
            return readState() || {};
        }

        function ensureClient() {
            if (client) return client;
            client = wsClientApi.createClient({
                WebSocketImpl: options.WebSocketImpl || windowRef.WebSocket,
                getUrl: () => buildWebSocketUrl(windowRef.location),
                onOpen: () => logger.log('WebSocket connected'),
                onFrame,
                onError: (error) => logger.error('WebSocket error:', error),
                onClose: () => logger.log('WebSocket closed'),
                getAutoRestartEnabled: () => !!state().autoRestartEnabled,
                onAutoRestart,
                getShouldReconnect: () => !!state().shouldReconnect,
                getIsRestarting: () => !!state().isRestarting,
                reconnectDelayMs: options.reconnectDelayMs || 2000,
                setTimeout: options.setTimeout,
            });
            return client;
        }

        function connect() {
            return ensureClient().connect();
        }

        function getSocket() {
            return client ? client.getSocket() : null;
        }

        function isUsable() {
            return client ? client.isUsable() : false;
        }

        function close() {
            const socket = getSocket();
            if (!socket) return false;
            logger.log('Closing old WebSocket connection...');
            client.close();
            return true;
        }

        return { close, connect, getSocket, isUsable };
    }

    const api = { buildWebSocketUrl, create };
    root.WebSocketController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
