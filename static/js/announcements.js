(function (root) {
    'use strict';
    const labels = {
        zh: { title: '公告', unread: '未读公告', loading: '正在加载公告…', empty: '暂无公告', error: '公告加载失败，请重试', retry: '重试', close: '关闭', refresh: '刷新' },
        en: { title: 'Announcements', unread: 'Unread announcements', loading: 'Loading announcements…', empty: 'No announcements', error: 'Unable to load announcements. Please retry.', retry: 'Retry', close: 'Close', refresh: 'Refresh' },
        ja: { title: 'お知らせ', unread: '未読のお知らせ', loading: '読み込み中…', empty: 'お知らせはありません', error: 'お知らせを読み込めませんでした。再試行してください。', retry: '再試行', close: '閉じる', refresh: '更新' },
    };
    function renderContent(element, content, depth = 0) {
        const doc = element.ownerDocument;
        const text = String(content || '');
        const pattern = /\*\*([^*\n]+)\*\*|==([^=\n]+)==|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g;
        element.replaceChildren();
        element.style.whiteSpace = 'pre-wrap';
        if (depth > 4) { element.textContent = text; return; }
        let offset = 0;
        for (const match of text.matchAll(pattern)) {
            element.append(doc.createTextNode(text.slice(offset, match.index)));
            let node;
            if (match[1] || match[2]) {
                node = doc.createElement(match[1] ? 'strong' : 'mark');
                renderContent(node, match[1] || match[2], depth + 1);
            } else {
                try {
                    const url = new URL(match[4]);
                    node = doc.createElement('a');
                    node.href = url.href;
                    node.target = '_blank';
                    node.rel = 'noopener noreferrer';
                    renderContent(node, match[3], depth + 1);
                } catch { node = doc.createTextNode(match[0]); }
            }
            element.append(node);
            offset = match.index + match[0].length;
        }
        element.append(doc.createTextNode(text.slice(offset)));
    }
    const css = `
        .announcement-backdrop{position:fixed;inset:0;z-index:20000;background:#0008;display:flex;align-items:center;justify-content:center;padding:12px;font:14px/1.6 system-ui,sans-serif;color:#202030}
        .announcement-dialog{background:#fff;color:inherit;width:100%;max-width:660px;max-height:85vh;max-height:85dvh;border-radius:12px;box-shadow:0 12px 40px #0006;display:flex;flex-direction:column;overflow:hidden}
        .dark-theme .announcement-dialog{background:#222235;color:#eee}
        .announcement-header{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:12px 16px;border-bottom:1px solid #8885;flex-shrink:0}
        .announcement-header h2{font-size:18px;margin:0;overflow-wrap:anywhere}
        .announcement-dialog button{cursor:pointer;color:inherit;background:transparent;border:1px solid #8886;border-radius:6px;min-height:44px;padding:6px 12px;font:inherit}
        .announcement-body{overflow-y:auto;overscroll-behavior:contain;padding:16px;min-height:0}
        .announcement-item{padding:0 0 16px;margin:0 0 16px;border-bottom:1px solid #8885;overflow-wrap:anywhere}
        .announcement-item h3{font-size:16px;margin:0 0 4px;line-height:1.5}
        .announcement-item time{display:block;font-size:12px;opacity:.7;margin:0 0 10px}
        .announcement-item a{color:#4c91ed;text-decoration:underline}
        .announcement-item mark{background:#fce68b;color:#302400;border-radius:3px;padding:0 2px}
        .announcement-unread-dot{position:absolute;top:2px;right:2px;width:10px;height:10px;border-radius:50%;background:#ef4444;box-shadow:0 0 0 2px #222}
        .announcement-unread-dot[hidden]{display:none}
    `;
    function create(options = {}) {
        const fetchRef = options.fetch || root.fetch.bind(root);
        const storage = options.storage || root.localStorage;
        const getKey = options.getStorageKey || (() => 'announcementReads:v1');
        const getDocument = options.getDocument || (() => root.document);
        const getLanguage = options.getLanguage || (() => (root.I18N && root.I18N.lang) || 'en');
        const endpoint = options.endpoint || '/public/announcements';
        const docs = new Set();
        const bound = new WeakSet();
        let rows = [], read = {}, key = null, inFlight = null, lastCheck = 0, loaded = false;
        let dialog = null, failed = false, dialogLoading = false;
        function onStorage(event) {
            if (event.key === getKey()) { key = null; loadRead(); updateButtons(); }
        }
        if (root.addEventListener) root.addEventListener('storage', onStorage);
        function strings() { return labels[getLanguage()] || labels.en; }
        function loadRead() {
            const nextKey = getKey();
            if (nextKey === key) return;
            key = nextKey;
            try {
                const value = JSON.parse(storage.getItem(key) || '{}');
                read = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
            } catch { read = {}; }
        }
        function unread(row) { return read[row.id] !== row.revision; }
        function updateButtons() {
            loadRead();
            const pending = rows.some(unread), l = strings();
            for (const doc of docs) {
                if (doc.defaultView && doc.defaultView.closed) { docs.delete(doc); continue; }
                const button = doc.getElementById('announcementsButton');
                if (!button) continue;
                button.style.position = 'relative';
                button.title = pending ? l.unread : l.title;
                button.setAttribute('aria-label', button.title);
                button.setAttribute('data-custom-title', button.title);
                let dot = button.querySelector('.announcement-unread-dot');
                if (!dot) { dot = doc.createElement('span'); dot.className = 'announcement-unread-dot'; dot.setAttribute('aria-hidden', 'true'); button.append(dot); }
                dot.hidden = !pending;
            }
        }
        function markShown() {
            loadRead();
            for (const row of rows) { delete read[row.id]; read[row.id] = row.revision; }
            // Keep recent receipts for temporarily hidden announcements, with a fixed bound.
            const next = Object.fromEntries(Object.entries(read).slice(-500));
            read = next;
            try { storage.setItem(key, JSON.stringify(read)); } catch { /* Keep in-memory state. */ }
            updateButtons();
        }
        function injectStyle(doc) {
            if (doc.getElementById('announcementStyle')) return;
            const style = doc.createElement('style'); style.id = 'announcementStyle'; style.textContent = css; doc.head.append(style);
        }
        function bind(doc = getDocument()) {
            if (!doc) return;
            docs.add(doc); injectStyle(doc);
            const button = doc.getElementById('announcementsButton');
            if (button && !bound.has(button)) { bound.add(button); button.addEventListener('click', () => { void open(doc); }); }
            updateButtons();
        }
        function close() {
            if (!dialog) return;
            const current = dialog; dialog = null;
            current.doc.removeEventListener('keydown', current.onKey);
            current.backdrop.remove();
            if (current.previous && current.previous.isConnected) current.previous.focus();
        }
        function renderDialog() {
            if (!dialog) return;
            const { doc, body } = dialog, l = strings();
            body.replaceChildren();
            if (dialogLoading) { body.textContent = l.loading; return; }
            if (failed) {
                const message = doc.createElement('p'); message.setAttribute('role', 'alert'); message.textContent = l.error; body.append(message);
                const retry = doc.createElement('button'); retry.textContent = l.retry; retry.onclick = () => { void check({ force: true }); }; body.append(retry); return;
            }
            if (!loaded) { body.textContent = l.loading; return; }
            if (!rows.length) body.textContent = l.empty;
            for (const row of rows) {
                const article = doc.createElement('article'); article.className = 'announcement-item';
                const title = doc.createElement('h3'); title.textContent = row.title;
                const time = doc.createElement('time'); time.dateTime = row.published_at; time.textContent = new Date(row.published_at).toLocaleString(getLanguage());
                const content = doc.createElement('div'); renderContent(content, row.content);
                article.append(title, time, content); body.append(article);
            }
            markShown();
        }
        function show(doc = getDocument()) {
            if (!doc) return;
            close(); bind(doc);
            const l = strings(), previous = doc.activeElement;
            const backdrop = doc.createElement('div'); backdrop.className = 'announcement-backdrop';
            const panel = doc.createElement('section'); panel.className = 'announcement-dialog'; panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true'); panel.setAttribute('aria-label', l.title); panel.tabIndex = -1;
            const header = doc.createElement('div'); header.className = 'announcement-header';
            const title = doc.createElement('h2'); title.textContent = l.title;
            const closeButton = doc.createElement('button'); closeButton.textContent = '×'; closeButton.setAttribute('aria-label', l.close); closeButton.onclick = close;
            header.append(title, closeButton);
            const body = doc.createElement('div'); body.className = 'announcement-body';
            panel.append(header, body); backdrop.append(panel);
            backdrop.addEventListener('click', event => { if (event.target === backdrop) close(); });
            function onKey(event) {
                if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); close(); }
                if (event.key === 'Tab') {
                    const targets = [...panel.querySelectorAll('button:not(:disabled),a[href]')];
                    const first = targets[0], last = targets[targets.length - 1];
                    if (event.shiftKey && (doc.activeElement === first || doc.activeElement === panel)) { event.preventDefault(); last.focus(); }
                    else if (!event.shiftKey && (doc.activeElement === last || doc.activeElement === panel)) { event.preventDefault(); first.focus(); }
                }
            }
            dialog = { doc, body, backdrop, onKey, previous };
            doc.body.append(backdrop); doc.addEventListener('keydown', onKey);
            renderDialog(); closeButton.focus();
        }
        async function check({ autoPopup = false, force = false, document: doc = getDocument() } = {}) {
            bind(doc); loadRead();
            if (!inFlight && (force || !loaded || Date.now() - lastCheck >= 60000)) {
                inFlight = (async () => {
                    try {
                        const response = await fetchRef(endpoint, { cache: 'no-store', signal: AbortSignal.timeout(15000) });
                        if (!response.ok) throw new Error(`Announcement request failed: ${response.status}`);
                        const data = await response.json();
                        if (!Array.isArray(data.announcements)) throw new Error('Invalid announcement response');
                        rows = data.announcements.filter(row => row && row.visible === true && typeof row.id === 'string' && row.id.length > 0 && Number.isSafeInteger(row.revision)).slice(0, 200);
                        loaded = true; failed = false; lastCheck = Date.now();
                        updateButtons();
                    } catch (error) { failed = true; }
                })();
            }
            if (inFlight) { const pending = inFlight; await pending; if (inFlight === pending) inFlight = null; }
            if (dialog) renderDialog();
            else if (!failed && autoPopup && rows.some(row => row.popup && unread(row))) show(doc);
            return !failed;
        }
        async function open(doc = getDocument()) {
            // Always revalidate when opening so hidden or deleted announcements disappear.
            dialogLoading = true;
            show(doc);
            const opened = dialog;
            await check({ force: true, document: doc });
            dialogLoading = false;
            if (dialog === opened) renderDialog();
        }
        function destroy() { close(); docs.clear(); if (root.removeEventListener) root.removeEventListener('storage', onStorage); }
        return { bind, check, open, close, destroy, getRows: () => rows.slice(), hasUnread: () => { loadRead(); return rows.some(unread); } };
    }
    const api = { create, renderContent };
    root.SubtitleAnnouncements = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
