const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const STATIC_ROOT = path.join(REPO_ROOT, 'static');

class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    static instances = [];

    constructor(url) {
        this.url = url;
        this.readyState = FakeWebSocket.OPEN;
        this.sent = [];
        FakeWebSocket.instances.push(this);
        queueMicrotask(() => this.onopen && this.onopen());
    }

    send(value) {
        this.sent.push(value);
    }

    close() {
        this.readyState = FakeWebSocket.CLOSED;
        if (this.onclose) this.onclose();
    }
}

class FakeXMLHttpRequest {
    open(method, url) {
        this.method = method;
        this.url = url;
    }

    setRequestHeader() {}

    send() {
        this.status = 200;
        this.responseText = this.url === '/local-store' ? '{"store":{}}' : '{}';
        if (this.onload) this.onload();
    }
}

function normalizeTranslationUiMode(value) {
    const mode = String(value || 'hybrid').trim().toLowerCase();
    if (!['fast', 'hybrid', 'accurate'].includes(mode)) {
        throw new Error(`Unsupported harness translationUiMode: ${value}`);
    }
    return mode;
}

function llmModeForTranslationUiMode(mode) {
    if (mode === 'accurate') return 'translate';
    return mode === 'hybrid' ? 'refine' : 'off';
}

function defaultFetchResponse(url, options = {}) {
    const translationUiMode = normalizeTranslationUiMode(options.translationUiMode);
    const llmMode = llmModeForTranslationUiMode(translationUiMode);
    const pathname = new URL(String(url), 'http://localhost/').pathname;
    const payloads = {
        '/local-store': { store: {} },
        '/ui-config': {
            provider: 'soniox',
            segment_mode: 'punctuation',
            translation_mode: 'one_way',
            translation_ui_mode: translationUiMode,
            llm_refine_available: true,
            llm_refine_default_mode: llmMode,
            languages: ['en', 'zh', 'ja'],
            relay_available: false,
            lock_manual_controls: false,
        },
        '/api-key-status': { status: 'ok' },
        '/osc-translation': { enabled: false },
        '/audio-source': { source: 'system' },
        '/microphones': { available: false, devices: [] },
        '/llm-refine': { enabled: true, mode: llmMode },
        '/translation-mode': { mode: translationUiMode, needs_restart: false },
    };
    const body = payloads[pathname] || {};
    return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => body,
        text: async () => JSON.stringify(body),
    };
}

function installBrowserStubs(window, fetchImpl, options) {
    FakeWebSocket.instances = [];
    window.WebSocket = FakeWebSocket;
    window.XMLHttpRequest = FakeXMLHttpRequest;
    const fetchCalls = [];
    const resolvedFetch = fetchImpl || (async (url) => defaultFetchResponse(url, options));
    window.fetch = async (...args) => {
        fetchCalls.push(args);
        return resolvedFetch(...args);
    };
    window.open = () => null;
    window.scrollTo = () => {};
    window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
    window.kuromoji = {
        builder: () => ({
            build: (callback) => callback(null, { tokenize: () => [] }),
        }),
    };
    return fetchCalls;
}

function resolveScriptPath(src) {
    const relative = src.replace(/^\//, '');
    return path.join(STATIC_ROOT, relative);
}

function loadPageScripts(window) {
    const scripts = [...window.document.querySelectorAll('script[src]')];
    for (const script of scripts) {
        const src = script.getAttribute('src');
        if (!src || src.includes('kuromoji/kuromoji.js')) continue;
        const filename = resolveScriptPath(src);
        const source = fs.readFileSync(filename, 'utf8');
        window.eval(`${source}\n//# sourceURL=${filename.replace(/\\/g, '/')}`);
    }
}

async function flushTasks(turns = 12) {
    for (let index = 0; index < turns; index += 1) {
        await new Promise((resolve) => setImmediate(resolve));
    }
}

async function createPageHarness(options = {}) {
    const translationUiMode = normalizeTranslationUiMode(
        options.translationUiMode || (options.localStorage && options.localStorage.translationUiMode)
    );
    const llmMode = llmModeForTranslationUiMode(translationUiMode);
    const html = fs.readFileSync(path.join(STATIC_ROOT, 'index.html'), 'utf8');
    const virtualConsole = new VirtualConsole();
    if (options.forwardConsole) virtualConsole.sendTo(console);
    const dom = new JSDOM(html, {
        url: options.url || 'http://localhost/',
        runScripts: 'outside-only',
        pretendToBeVisual: true,
        virtualConsole,
    });
    const fetchCalls = installBrowserStubs(dom.window, options.fetch, { translationUiMode });
    const storedValues = Object.assign({
        autoRestartEnabled: String(options.autoRestartEnabled === true),
        translationUiMode,
        llmTranslationMode: llmMode,
        llmRefineMode: llmMode,
        llmRefineEnabled: String(llmMode !== 'off'),
    }, options.localStorage || {});
    if (options.translationUiMode) {
        Object.assign(storedValues, {
            translationUiMode,
            llmTranslationMode: llmMode,
            llmRefineMode: llmMode,
            llmRefineEnabled: String(llmMode !== 'off'),
        });
    }
    for (const [key, value] of Object.entries(storedValues)) {
        dom.window.localStorage.setItem(key, String(value));
    }
    loadPageScripts(dom.window);
    await flushTasks();
    const sockets = FakeWebSocket.instances;

    return {
        dom,
        window: dom.window,
        document: dom.window.document,
        sockets,
        fetchCalls,
        translationUiMode,
        async emitFrame(frame) {
            const socket = sockets.at(-1);
            if (!socket || typeof socket.onmessage !== 'function') {
                throw new Error('The page has no active WebSocket');
            }
            socket.onmessage({ data: JSON.stringify(frame) });
            await flushTasks(2);
        },
        html() {
            return dom.window.document.getElementById('subtitleContainer').innerHTML;
        },
        async flush(turns) {
            await flushTasks(turns);
        },
        close() {
            for (const socket of sockets) {
                socket.onopen = null;
                socket.onmessage = null;
                socket.onerror = null;
                socket.onclose = null;
                socket.readyState = FakeWebSocket.CLOSED;
            }
            dom.window.close();
        },
    };
}

module.exports = {
    FakeWebSocket,
    createPageHarness,
    defaultFetchResponse,
    flushTasks,
    normalizeTranslationUiMode,
};
