(function (root) {
    'use strict';

    const DEFAULT_REMINDER_MS = 20 * 60 * 60 * 1000;
    const DEFAULT_REMINDER_KEY = 'clientUpdateReminderLastShown';
    const DEFAULT_CHECKED_VERSION_KEY = 'clientUpdateLastCheckedVersion';

    function resolveUpdateState(input = {}, Billing) {
        const mode = input.mode;
        if (!input.relayAvailable || mode !== 'relay') {
            return { needed: false, forced: false };
        }
        const current = input.currentVersion || '0.1.0';
        const latest = input.latestVersion || '';
        const minimum = input.minimumVersion || '';
        const belowMinimum = !!minimum && Billing.compareVersions(current, minimum) < 0;
        const belowLatest = !!latest && Billing.compareVersions(current, latest) < 0;
        return {
            needed: belowMinimum || belowLatest,
            forced: belowMinimum,
            current,
            latest: latest || minimum || '',
            minimum,
            updateUrl: input.updateUrl || '',
            notes: input.notes || '',
        };
    }

    function create(options = {}) {
        const Billing = options.Billing || (root.Hosted && root.Hosted.Billing);
        if (!Billing) throw new TypeError('HostedUpdate.create requires Hosted.Billing');
        const windowRef = options.window || root;
        const storage = options.storage || root.localStorage;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const showConfirm = typeof options.showConfirm === 'function'
            ? options.showConfirm
            : () => Promise.resolve(false);
        const now = typeof options.now === 'function' ? options.now : () => Date.now();
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const onSwitchDirect = typeof options.onSwitchDirect === 'function'
            ? options.onSwitchDirect
            : () => {};
        const reminderMs = options.reminderMs || DEFAULT_REMINDER_MS;
        const reminderKey = options.reminderKey || DEFAULT_REMINDER_KEY;
        const checkedVersionKey = options.checkedVersionKey || DEFAULT_CHECKED_VERSION_KEY;
        const elements = options.elements || {};
        const overlay = elements.overlay || null;
        const dialog = elements.dialog || null;
        const title = elements.title || null;
        const body = elements.body || null;
        const currentLabel = elements.currentLabel || null;
        const latestLabel = elements.latestLabel || null;
        const minimumLabel = elements.minimumLabel || null;
        const currentValue = elements.currentValue || null;
        const latestValue = elements.latestValue || null;
        const minimumValue = elements.minimumValue || null;
        const notes = elements.notes || null;
        const noUrl = elements.noUrl || null;
        const directButton = elements.directButton || null;
        const laterButton = elements.laterButton || null;
        const updateButton = elements.updateButton || null;
        let resolver = null;

        function state(modeOverride = null) {
            const current = getState() || {};
            return resolveUpdateState({
                relayAvailable: current.relayAvailable,
                mode: modeOverride || current.connectionMode,
                currentVersion: current.currentVersion,
                latestVersion: current.latestVersion,
                minimumVersion: current.minimumVersion,
                updateUrl: current.updateUrl,
                notes: current.notes,
            }, Billing);
        }

        function close(result) {
            if (overlay) overlay.hidden = true;
            if (dialog) dialog.hidden = true;
            if (resolver) {
                const resolve = resolver;
                resolver = null;
                resolve(result);
            }
        }

        function show(updateState) {
            if (!dialog || !overlay) {
                return Promise.resolve(updateState.forced ? 'direct' : 'later');
            }
            if (resolver) close('later');
            if (title) title.textContent = t(updateState.forced
                ? 'client_update_title_required'
                : 'client_update_title_optional');
            if (body) body.textContent = t(updateState.forced
                ? 'client_update_body_required'
                : 'client_update_body_optional');
            if (currentLabel) currentLabel.textContent = t('client_update_current');
            if (latestLabel) latestLabel.textContent = t('client_update_latest');
            if (minimumLabel) minimumLabel.textContent = t('client_update_minimum');
            if (currentValue) currentValue.textContent = updateState.current || '—';
            if (latestValue) latestValue.textContent = updateState.latest || '—';
            if (minimumValue) minimumValue.textContent = updateState.minimum || '—';
            if (notes) {
                notes.textContent = updateState.notes || '';
                notes.hidden = !updateState.notes;
            }
            if (noUrl) {
                noUrl.textContent = updateState.updateUrl ? '' : t('client_update_no_url');
                noUrl.hidden = !!updateState.updateUrl;
            }
            if (updateButton) {
                updateButton.textContent = t('client_update_button');
                updateButton.disabled = !updateState.updateUrl;
                updateButton.onclick = () => {
                    if (updateState.updateUrl) {
                        windowRef.open(updateState.updateUrl, '_blank', 'noopener,noreferrer');
                    }
                };
            }
            if (laterButton) {
                laterButton.textContent = t('client_update_later');
                laterButton.hidden = !!updateState.forced;
                laterButton.onclick = () => close('later');
            }
            if (directButton) {
                directButton.textContent = t('client_update_direct');
                directButton.hidden = !updateState.forced;
                directButton.onclick = async () => {
                    overlay.hidden = true;
                    dialog.hidden = true;
                    const confirmed = await showConfirm(t('client_update_direct_confirm'), {
                        okLabel: t('client_update_direct_confirm_ok'),
                        cancelLabel: t('client_update_direct_confirm_cancel'),
                        danger: true,
                    });
                    if (confirmed) {
                        close('direct');
                        return;
                    }
                    overlay.hidden = false;
                    dialog.hidden = false;
                };
            }
            overlay.hidden = false;
            dialog.hidden = false;
            return new Promise((resolve) => { resolver = resolve; });
        }

        async function ensure({ candidateMode = null } = {}) {
            const updateState = state(candidateMode);
            let newerThanLastCheck = false;
            if (updateState.latest) {
                let lastCheckedVersion = '';
                try {
                    lastCheckedVersion = String(storage.getItem(checkedVersionKey) || '').trim();
                } catch (error) {
                    lastCheckedVersion = '';
                }
                newerThanLastCheck = !!lastCheckedVersion
                    && Billing.compareVersions(updateState.latest, lastCheckedVersion) > 0;
                try {
                    storage.setItem(checkedVersionKey, updateState.latest);
                } catch (error) {
                    // Preserve the browser's storage-failure fallback.
                }
            }
            if (!updateState.needed) return true;
            if (!updateState.forced) {
                let lastShown = 0;
                try {
                    lastShown = parseInt(storage.getItem(reminderKey) || '0', 10) || 0;
                } catch (error) {
                    lastShown = 0;
                }
                if (!newerThanLastCheck && lastShown && now() - lastShown < reminderMs) {
                    return true;
                }
                try {
                    storage.setItem(reminderKey, String(now()));
                } catch (error) {
                    // Preserve the browser's storage-failure fallback.
                }
            }
            const action = await show(updateState);
            if (updateState.forced && action === 'direct') {
                onSwitchDirect();
                return false;
            }
            return !updateState.forced;
        }

        function destroy() {
            close('later');
            if (updateButton) updateButton.onclick = null;
            if (laterButton) laterButton.onclick = null;
            if (directButton) directButton.onclick = null;
        }

        return { close, destroy, ensure, getState: state, show };
    }

    const api = {
        DEFAULT_REMINDER_MS,
        DEFAULT_REMINDER_KEY,
        DEFAULT_CHECKED_VERSION_KEY,
        resolveUpdateState,
        create,
    };
    root.HostedUpdate = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
