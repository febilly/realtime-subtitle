/*
 * Cross-instance localStorage bridge.
 *
 * Browser localStorage is partitioned per origin (scheme+host+port). When a
 * second app instance launches it binds a different dynamic port, so its page
 * starts with an empty localStorage and loses the saved login/settings.
 *
 * This shim makes the backend's shared file (local_store.py, exposed at
 * /local-store) the cross-instance source of truth, while localStorage stays
 * the live in-page store everything else reads:
 *   - On load: hydrate localStorage from the shared file (file wins), and seed
 *     the file from any keys that only exist locally (first-run migration).
 *   - On change: write-through every setItem/removeItem/clear to the file.
 *
 * Must run BEFORE i18n.js / app.js, which read localStorage synchronously at
 * top level — hence the synchronous XHR here and the <script> ordering.
 */
(function () {
    'use strict';

    var ls = window.localStorage;
    if (!ls) {
        return;
    }

    // Storage objects are Web IDL exotic objects, so assigning methods directly
    // on the localStorage instance is not reliable. Patch the shared prototype
    // and guard by object identity so sessionStorage remains local-only.
    var storagePrototype = Object.getPrototypeOf(ls);
    var nativeSet = storagePrototype.setItem;
    var nativeRemove = storagePrototype.removeItem;
    var nativeClear = storagePrototype.clear;

    // Only these keys are shared across instances: API key / provider config
    // (providerSettings.v1), server address + login auth (subtitleServer.v1),
    // the LLM refine/translate mode, automatic sleep, the invite-reminder cadence,
    // and the optional bundled CJK font toggle.
    // Everything else (theme,
    // segment/display/subtitle-flow mode, audio source, auto-restart, …) stays per-instance.
    // Keep in sync with the constants in app.js.
    var ALLOWLIST = {
        'providerSettings.v1': true,
        'subtitleServer.v1': true,
        'llmTranslationMode': true,
        'llmRefineMode': true,
        'llmRefineEnabled': true,
        'sleepOnSilenceEnabled': true,
        'inviteRewardReminderLastShown': true,
        'useBundledCjkFont': true,
    };

    function isShared(key) {
        return typeof key === 'string' && key in ALLOWLIST;
    }

    // ---- 1. Hydrate from the shared file (synchronous, before app.js) ----
    var fileStore = {};
    try {
        var xhr = new XMLHttpRequest();
        xhr.open('GET', '/local-store', false); // synchronous on purpose
        xhr.send(null);
        if (xhr.status >= 200 && xhr.status < 300) {
            var parsed = JSON.parse(xhr.responseText);
            if (parsed && parsed.store && typeof parsed.store === 'object') {
                fileStore = parsed.store;
            }
        }
    } catch (e) {
        // Backend unreachable or older build: fall back to local-only behaviour.
    }

    // File wins for keys it holds.
    var fileKeys = Object.create(null);
    Object.keys(fileStore).forEach(function (key) {
        fileKeys[key] = true;
        if (!isShared(key)) {
            return;
        }
        try {
            nativeSet.call(ls, key, String(fileStore[key]));
        } catch (e) { /* quota / serialization — ignore */ }
    });

    // First-run migration: push keys that exist only in this localStorage up to
    // the shared file so existing users keep their settings.
    var seed = {};
    var hasSeed = false;
    try {
        for (var i = 0; i < ls.length; i++) {
            var k = ls.key(i);
            if (k && isShared(k) && !(k in fileKeys)) {
                seed[k] = ls.getItem(k);
                hasSeed = true;
            }
        }
    } catch (e) { /* ignore */ }
    if (hasSeed) {
        post({ set: seed }, false);
    }

    // ---- 2. Write-through wrappers ----
    function post(body, sync) {
        var json = JSON.stringify(body);
        if (sync) {
            try {
                var x = new XMLHttpRequest();
                x.open('POST', '/local-store', false);
                x.setRequestHeader('Content-Type', 'application/json');
                x.send(json);
            } catch (e) { /* ignore */ }
            return;
        }
        try {
            // keepalive so the write still lands if the page is navigating away
            // (e.g. reset-all triggers a shutdown right after).
            fetch('/local-store', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: json,
                keepalive: true,
            }).catch(function () {});
        } catch (e) { /* ignore */ }
    }

    storagePrototype.setItem = function (key, value) {
        nativeSet.call(this, key, value);
        if (this === ls && isShared(key)) {
            var u = {};
            u[key] = value;
            post({ set: u }, false);
        }
    };

    storagePrototype.removeItem = function (key) {
        nativeRemove.call(this, key);
        if (this === ls && isShared(key)) {
            post({ remove: [key] }, false);
        }
    };

    storagePrototype.clear = function () {
        nativeClear.call(this);
        if (this !== ls) return;
        // Synchronous: the reset-all flow calls /shutdown immediately after, so
        // make sure the shared file is emptied before the backend exits.
        post({ clear: true }, true);
    };
})();
