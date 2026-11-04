(function (root) {
    'use strict';

    const DEFAULT_REMINDER_MS = 44 * 60 * 60 * 1000;
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
        const onQuit = typeof options.onQuit === 'function' ? options.onQuit : () => {};
        const onDismissed = typeof options.onDismissed === 'function' ? options.onDismissed : () => {};
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
                    if (!updateState.updateUrl) return;
                    windowRef.open(updateState.updateUrl, '_blank', 'noopener,noreferrer');
                    // The user is off to install the new version: quit instead of
                    // carrying on with this one. The dialog stays up until we exit.
                    updateButton.disabled = true;
                    if (laterButton) laterButton.disabled = true;
                    if (directButton) directButton.disabled = true;
                    void Promise.resolve().then(onQuit).catch(() => {});
                };
            }
            if (laterButton) {
                laterButton.disabled = false;
                laterButton.textContent = t('client_update_later');
                laterButton.hidden = !!updateState.forced;
                laterButton.onclick = () => close('later');
            }
            if (directButton) {
                directButton.disabled = false;
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

        async function checked() {
            if (typeof options.onChecked === 'function') await options.onChecked();
        }

        async function ensure(optionsForCheck = {}) {
            try {
                return await ensureVersion(optionsForCheck);
            } catch (error) {
                if (!optionsForCheck.background) await checked();
                throw error;
            }
        }

        function isShowing() {
            return !!resolver;
        }

        async function ensureVersion({ candidateMode = null, background = false } = {}) {
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
            }
            if (!updateState.needed) {
                rememberCheckedVersion(updateState.latest);
                if (!background) await checked();
                return true;
            }
            if (background && updateState.forced) return true;
            // Never stack a second update dialog on one that is already up.
            if (resolver) return !updateState.forced;
            let dialogPromise;
            const display = () => {
                if (!updateState.forced) {
                    try { storage.setItem(reminderKey, String(now())); } catch (error) { /* storage unavailable */ }
                }
                rememberCheckedVersion(updateState.latest);
                dialogPromise = show(updateState);
            };
            if (!updateState.forced) {
                let lastShown = 0;
                try {
                    lastShown = parseInt(storage.getItem(reminderKey) || '0', 10) || 0;
                } catch (error) {
                    lastShown = 0;
                }
                if (!newerThanLastCheck && lastShown && now() - lastShown < reminderMs) {
                    rememberCheckedVersion(updateState.latest);
                    if (!background) await checked();
                    return true;
                }
            }
            // The update dialog has its own cadence and is not part of the shared
            // reminder cooldown. Other popups wait until it has been dismissed.
            display();
            const settled = dialogPromise.then(async (action) => {
                if (updateState.forced && action === 'direct') await onSwitchDirect();
                if (action === 'update') return action;
                await checked();
                onDismissed(action);
                return action;
            });
            if (background) {
                void settled.catch(() => {});
                return true;
            }
            const action = await settled;
            if (updateState.forced && action === 'direct') return false;
            return !updateState.forced;
        }

        function rememberCheckedVersion(version) {
            if (!version) return;
            try { storage.setItem(checkedVersionKey, version); } catch (error) { /* storage unavailable */ }
        }

        function destroy() {
            close('later');
            if (updateButton) updateButton.onclick = null;
            if (laterButton) laterButton.onclick = null;
            if (directButton) directButton.onclick = null;
        }

        return { close, destroy, ensure, getState: state, isShowing, show };
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
