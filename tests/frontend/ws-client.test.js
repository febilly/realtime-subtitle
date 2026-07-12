const { createClient } = require('../../static/js/ws-client');

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

    close() {
        this.readyState = Socket.CLOSED;
        if (this.onclose) this.onclose({ code: 1000 });
    }

    emit(frame) {
        this.onmessage({ data: JSON.stringify(frame) });
    }
}

describe('WsClient socket lifecycle', () => {
    beforeEach(() => {
        Socket.instances = [];
    });

    it('connects once, parses frames, and exposes socket changes', () => {
        const frames = [];
        const changes = [];
        const client = createClient({
            WebSocketImpl: Socket,
            getUrl: () => 'ws://localhost/ws?x=1',
            onFrame: (frame) => frames.push(frame),
            onSocketChange: (socket) => changes.push(socket),
            getShouldReconnect: () => false,
        });

        const socket = client.connect();
        expect(client.connect()).toBe(socket);
        expect(Socket.instances).toHaveLength(1);
        expect(socket.url).toBe('ws://localhost/ws?x=1');
        socket.emit({ type: 'clear' });
        expect(frames).toEqual([{ type: 'clear' }]);
        socket.close();
        expect(changes).toEqual([socket, null]);
    });

    it('prefers auto restart over reconnect', () => {
        const restart = vi.fn();
        const schedule = vi.fn();
        const client = createClient({
            WebSocketImpl: Socket,
            getUrl: () => 'ws://localhost/ws',
            getAutoRestartEnabled: () => true,
            getShouldReconnect: () => true,
            onAutoRestart: restart,
            setTimeout: schedule,
        });
        client.connect().close();
        expect(restart).toHaveBeenCalledOnce();
        expect(schedule).not.toHaveBeenCalled();
    });

    it('schedules reconnect only when allowed and not restarting', () => {
        const schedule = vi.fn();
        const client = createClient({
            WebSocketImpl: Socket,
            getUrl: () => 'ws://localhost/ws',
            getAutoRestartEnabled: () => false,
            getShouldReconnect: () => true,
            getIsRestarting: () => false,
            reconnectDelayMs: 2500,
            setTimeout: schedule,
        });
        client.connect().close();
        expect(schedule).toHaveBeenCalledWith(client.connect, 2500);
    });
});
