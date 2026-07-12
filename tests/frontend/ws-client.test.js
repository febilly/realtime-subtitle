const { createClient, dispatchFrame } = require('../../static/js/ws-client');

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

describe('WsClient frame dispatch', () => {
    it.each([
        'subtitle_font_preference', 'recognition_paused', 'overlay_visibility', 'ipc_status', 'error',
        'spec_translation_pending', 'spec_translation', 'refine_result', 'subtitle_retract', 'llm_cost',
        'translation_mode_fallback', 'segment_mode_changed', 'speaker_labels_changed',
        'session_connected', 'session_idle', 'session_disconnected', 'clear', 'update',
    ])('routes %s to exactly its registered handler', (type) => {
        const handlers = Object.fromEntries([
            'subtitle_font_preference', 'recognition_paused', 'overlay_visibility', 'ipc_status', 'error',
            'spec_translation_pending', 'spec_translation', 'refine_result', 'subtitle_retract', 'llm_cost',
            'translation_mode_fallback', 'segment_mode_changed', 'speaker_labels_changed',
            'session_connected', 'session_idle', 'session_disconnected', 'clear', 'update',
        ].map((name) => [name, vi.fn()]));
        const frame = { type };
        dispatchFrame(frame, handlers);
        expect(handlers[type]).toHaveBeenCalledOnce();
        expect(handlers[type]).toHaveBeenCalledWith(frame);
        expect(Object.values(handlers).filter((handler) => handler.mock.calls.length)).toHaveLength(1);
    });

    it('uses the default handler for malformed and unknown frames', () => {
        const fallback = vi.fn();
        dispatchFrame({ type: 'unknown' }, { default: fallback });
        dispatchFrame(null, { default: fallback });
        expect(fallback).toHaveBeenCalledTimes(2);
    });
});

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
