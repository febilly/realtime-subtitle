const WsClient = require('../../static/js/ws-client');
const WebSocketController = require('../../static/js/websocket-controller');

class Socket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;
    static instances = [];

    constructor(url) {
        this.url = url;
        this.readyState = Socket.CONNECTING;
        Socket.instances.push(this);
    }

    open() {
        this.readyState = Socket.OPEN;
        if (this.onopen) this.onopen({ type: 'open' });
    }

    emit(frame) {
        this.onmessage({ data: JSON.stringify(frame) });
    }

    fail(error) {
        if (this.onerror) this.onerror(error);
    }

    close() {
        this.readyState = Socket.CLOSED;
        if (this.onclose) this.onclose({ code: 1000 });
    }
}

function setup(overrides = {}) {
    const state = {
        autoRestartEnabled: false,
        shouldReconnect: false,
        isRestarting: false,
        ...(overrides.state || {}),
    };
    const logger = overrides.logger || { log: vi.fn(), error: vi.fn() };
    const onFrame = overrides.onFrame || vi.fn();
    const onAutoRestart = overrides.onAutoRestart || vi.fn();
    const schedule = overrides.setTimeout || vi.fn();
    const controller = WebSocketController.create({
        window: {
            WebSocket: Socket,
            location: {
                protocol: 'https:',
                host: 'captions.example:8443',
                search: '?room=alpha',
            },
        },
        wsClient: WsClient,
        logger,
        getState: () => state,
        onFrame,
        onAutoRestart,
        setTimeout: schedule,
        ...overrides.options,
    });
    return { controller, logger, onAutoRestart, onFrame, schedule, state };
}

describe('WebSocketController URL policy', () => {
    it.each([
        [{ protocol: 'http:', host: 'localhost:8000', search: '' }, 'ws://localhost:8000/ws'],
        [{ protocol: 'https:', host: 'sub.example', search: '?token=a%20b' }, 'wss://sub.example/ws?token=a%20b'],
    ])('builds the application socket URL from the current location', (location, expected) => {
        expect(WebSocketController.buildWebSocketUrl(location)).toBe(expected);
    });

    it('rejects a missing transport dependency at creation time', () => {
        expect(() => WebSocketController.create({ wsClient: {} }))
            .toThrow('WebSocketController requires WsClient.createClient');
    });
});

describe('WebSocketController lifecycle ownership', () => {
    beforeEach(() => {
        Socket.instances = [];
    });

    it('lazily owns one client, routes frames, logs transport events, and closes it', () => {
        const env = setup();
        expect(env.controller.getSocket()).toBeNull();
        expect(env.controller.isUsable()).toBe(false);
        expect(env.controller.close()).toBe(false);

        const socket = env.controller.connect();
        expect(env.controller.connect()).toBe(socket);
        expect(Socket.instances).toHaveLength(1);
        expect(socket.url).toBe('wss://captions.example:8443/ws?room=alpha');
        expect(env.controller.getSocket()).toBe(socket);
        expect(env.controller.isUsable()).toBe(true);

        socket.open();
        socket.emit({ type: 'clear' });
        const error = new Error('transport failed');
        socket.fail(error);
        expect(env.onFrame).toHaveBeenCalledWith({ type: 'clear' }, socket);
        expect(env.logger.log).toHaveBeenCalledWith('WebSocket connected');
        expect(env.logger.error).toHaveBeenCalledWith('WebSocket error:', error);

        expect(env.controller.close()).toBe(true);
        expect(env.logger.log.mock.calls).toEqual([
            ['WebSocket connected'],
            ['Closing old WebSocket connection...'],
            ['WebSocket closed'],
        ]);
        expect(env.controller.getSocket()).toBeNull();
        expect(env.controller.isUsable()).toBe(false);
    });

    it('reads restart and reconnect state at close time', () => {
        const restart = setup({ state: { autoRestartEnabled: true, shouldReconnect: true } });
        restart.controller.connect().close();
        expect(restart.onAutoRestart).toHaveBeenCalledOnce();
        expect(restart.schedule).not.toHaveBeenCalled();

        const reconnect = setup({ state: { shouldReconnect: true } });
        reconnect.controller.connect().close();
        expect(reconnect.schedule).toHaveBeenCalledWith(expect.any(Function), 2000);

        reconnect.state.isRestarting = true;
        reconnect.controller.connect().close();
        expect(reconnect.schedule).toHaveBeenCalledOnce();
    });
});
