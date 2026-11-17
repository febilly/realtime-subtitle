(function (root) {
    'use strict';

    const STORAGE_KEY = 'featurePacks.v1';

    const EXISTING_INSTALL_KEYS = Object.freeze([
        'subtitleServer.v1',
        'providerSettings.v1',
        'uiTranslationMode',
        'translationUiMode',
        'segmentMode',
        'displayMode',
        'subtitleFlowDirection',
        'autoRestartEnabled',
        'sleepOnSilenceEnabled',
        'interruptRepairEnabled',
        'oscSensitiveFilterEnabled',
        'oscSensitiveFilterNoticeDisabled',
        'bottomSafeAreaEnabled',
        'useBundledCjkFont',
        'theme',
        'audioSource',
        'vrOverlayEnabled',
        'vrOverlayAttachMode',
        'translationLanguagePreferences.v1',
        'clientUpdateReminderLastShown',
        'clientUpdateLastCheckedVersion',
        'inviteRewardReminderFirstSeen',
        'inviteRewardReminderLastShown',
    ]);

    const PACKS = Object.freeze([
        Object.freeze({
            id: 'vrchat',
            nameKey: 'pack_vrchat_name',
            descKey: 'pack_vrchat_desc',
            elementIds: Object.freeze([
                'oscTranslationButton',
                'vrOverlayButton',
                'oscSettingsSection',
                'vrOverlaySection',
            ]),
        }),
        Object.freeze({
            id: 'learning',
            nameKey: 'pack_learning_name',
            descKey: 'pack_learning_desc',
            elementIds: Object.freeze([
                'furiganaButton',
                'learningSettingsSection',
            ]),
        }),
        Object.freeze({
            id: 'obs',
            nameKey: 'pack_obs_name',
            descKey: 'pack_obs_desc',
            elementIds: Object.freeze([
                'obsCopyLinkButton',
                'obsSettingsSection',
            ]),
        }),
        Object.freeze({
            id: 'transcript',
            nameKey: 'pack_transcript_name',
            descKey: 'pack_transcript_desc',
            elementIds: Object.freeze([
                'exportTranscriptButton',
                'transcriptSettingsSection',
            ]),
        }),
    ]);

    const PACK_IDS = Object.freeze(PACKS.map((pack) => pack.id));
    const DEFAULT_UPGRADE_ENABLED = Object.freeze({
        vrchat: true,
        learning: true,
        obs: false,
        transcript: false,
    });
    const DEFAULT_NEW_ENABLED = Object.freeze({
        vrchat: false,
        learning: false,
        obs: false,
        transcript: false,
    });

    function detectExistingInstall(storage) {
        if (!storage || typeof storage.getItem !== 'function') return false;
        for (const key of EXISTING_INSTALL_KEYS) {
            try {
                if (storage.getItem(key) !== null) return true;
            } catch (_) {
                // ignore storage read errors
            }
        }
        return false;
    }

    function normalizeEnabledMap(raw, fallback) {
        const result = {};
        const base = fallback || DEFAULT_NEW_ENABLED;
        const source = raw && typeof raw === 'object' ? raw : {};
        for (const id of PACK_IDS) {
            result[id] = typeof source[id] === 'boolean' ? source[id] : !!base[id];
        }
        return result;
    }

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const storage = options.storage || root.localStorage;
        const fetchImpl = typeof options.fetch === 'function'
            ? options.fetch
            : (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const buildToggleSwitch = typeof options.buildToggleSwitch === 'function'
            ? options.buildToggleSwitch
            : null;
        const getRuntimeState = typeof options.getRuntimeState === 'function'
            ? options.getRuntimeState
            : () => ({});
        const logger = options.console || root.console || { warn() {}, error() {} };
        const hooks = options.hooks && typeof options.hooks === 'object' ? options.hooks : {};
        const elements = options.elements || {};

        let state = loadOrInitializeState();
        let pickerOpen = false;
        let pickerSelection = {};
        let pickerResolve = null;

        function runtimeState() {
            const value = getRuntimeState();
            return value && typeof value === 'object' ? value : {};
        }

        function saveState(nextState) {
            state = {
                version: 1,
                confirmed: !!nextState.confirmed,
                isUpgrade: !!nextState.isUpgrade,
                enabled: normalizeEnabledMap(nextState.enabled, DEFAULT_NEW_ENABLED),
            };
            if (storage && typeof storage.setItem === 'function') {
                try {
                    storage.setItem(STORAGE_KEY, JSON.stringify(state));
                } catch (error) {
                    logger.warn('Failed to persist feature packs state:', error);
                }
            }
            return state;
        }

        function loadOrInitializeState() {
            let raw = null;
            if (storage && typeof storage.getItem === 'function') {
                try {
                    raw = storage.getItem(STORAGE_KEY);
                } catch (_) {
                    raw = null;
                }
            }
            if (raw) {
                try {
                    const parsed = JSON.parse(raw);
                    if (parsed && typeof parsed === 'object') {
                        const isUpgrade = !!parsed.isUpgrade;
                        const fallback = isUpgrade && !parsed.confirmed
                            ? DEFAULT_UPGRADE_ENABLED
                            : DEFAULT_NEW_ENABLED;
                        return {
                            version: 1,
                            confirmed: !!parsed.confirmed,
                            isUpgrade,
                            enabled: normalizeEnabledMap(parsed.enabled, fallback),
                        };
                    }
                } catch (_) {
                    // fall through to re-initialize
                }
            }
            const isUpgrade = detectExistingInstall(storage);
            const initial = {
                version: 1,
                confirmed: false,
                isUpgrade,
                enabled: normalizeEnabledMap(null, isUpgrade ? DEFAULT_UPGRADE_ENABLED : DEFAULT_NEW_ENABLED),
            };
            if (storage && typeof storage.setItem === 'function') {
                try {
                    storage.setItem(STORAGE_KEY, JSON.stringify(initial));
                } catch (_) {
                    // ignore
                }
            }
            return initial;
        }

        function isEnabled(packId) {
            return !!state.enabled[packId];
        }

        function getEnabledMap() {
            return { ...state.enabled };
        }

        function isConfirmed() {
            return !!state.confirmed;
        }

        function isUpgrade() {
            return !!state.isUpgrade;
        }

        function applyPackVisibility(pack) {
            if (!documentRef) return;
            const enabled = isEnabled(pack.id);
            for (const id of pack.elementIds) {
                const el = documentRef.getElementById(id);
                if (el) el.hidden = !enabled;
            }
        }

        function invokeHook(packId, enabled) {
            const entry = hooks[packId];
            if (!entry || typeof entry !== 'object') return;
            const fn = enabled ? entry.onEnable : entry.onDisable;
            if (typeof fn === 'function') {
                try {
                    fn(enabled);
                } catch (error) {
                    logger.error(`Feature pack hook failed for ${packId}:`, error);
                }
            }
        }

        async function syncToBackend() {
            if (!fetchImpl) return false;
            try {
                const response = await fetchImpl('/feature-packs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ packs: getEnabledMap() }),
                });
                return !!response.ok;
            } catch (error) {
                logger.warn('Failed to sync feature packs to backend:', error);
                return false;
            }
        }

        function applyAll({ sync = true, invokeHooks = true } = {}) {
            for (const pack of PACKS) {
                applyPackVisibility(pack);
                if (invokeHooks) {
                    invokeHook(pack.id, isEnabled(pack.id));
                }
            }
            renderSettingsSection();
            if (sync) {
                void syncToBackend();
            }
        }

        function applyBackendPacks(packs) {
            if (!packs || typeof packs !== 'object') return false;
            let changed = false;
            const nextEnabled = { ...state.enabled };
            for (const pack of PACKS) {
                if (typeof packs[pack.id] === 'boolean' && nextEnabled[pack.id] !== packs[pack.id]) {
                    nextEnabled[pack.id] = packs[pack.id];
                    changed = true;
                }
            }
            if (!changed) return false;
            saveState({
                ...state,
                enabled: nextEnabled,
            });
            for (const pack of PACKS) {
                applyPackVisibility(pack);
                invokeHook(pack.id, isEnabled(pack.id));
            }
            renderSettingsSection();
            return true;
        }

        function setEnabled(packId, nextValue, { sync = true } = {}) {
            const pack = PACKS.find((item) => item.id === packId);
            if (!pack) return false;
            const enabled = !!nextValue;
            if (state.enabled[packId] === enabled && state.confirmed) {
                return false;
            }
            const nextEnabled = { ...state.enabled, [packId]: enabled };
            saveState({
                ...state,
                confirmed: true,
                enabled: nextEnabled,
            });
            applyPackVisibility(pack);
            invokeHook(pack.id, enabled);
            renderSettingsSection();
            if (sync) {
                void syncToBackend();
            }
            return true;
        }

        function confirmSelection(selectedMap, { sync = true } = {}) {
            const nextEnabled = {};
            for (const id of PACK_IDS) {
                nextEnabled[id] = !!(selectedMap && selectedMap[id]);
            }
            saveState({
                ...state,
                confirmed: true,
                enabled: nextEnabled,
            });
            applyAll({ sync, invokeHooks: true });
            return getEnabledMap();
        }

        function renderSettingsSection() {
            const titleEl = elements.settingsSectionLabel
                || (documentRef && documentRef.getElementById('featurePacksLabel'));
            if (titleEl) titleEl.textContent = t('feature_packs_section');
            const listEl = elements.settingsList
                || (documentRef && documentRef.getElementById('featurePacksList'));
            if (!listEl || !documentRef) return;
            listEl.innerHTML = '';
            for (const pack of PACKS) {
                const enabled = isEnabled(pack.id);
                const row = documentRef.createElement('label');
                row.className = 'settings-item settings-row';
                row.dataset.packId = pack.id;

                const textWrap = documentRef.createElement('span');
                textWrap.className = 'settings-row-text';

                const nameEl = documentRef.createElement('span');
                nameEl.className = 'feature-pack-settings-name';
                nameEl.textContent = t(pack.nameKey);

                const descEl = documentRef.createElement('span');
                descEl.className = 'field-hint feature-pack-settings-desc';
                descEl.textContent = t(pack.descKey);

                textWrap.appendChild(nameEl);
                textWrap.appendChild(descEl);
                row.appendChild(textWrap);

                if (buildToggleSwitch) {
                    const toggle = buildToggleSwitch({
                        value: enabled ? 'true' : 'false',
                        onValue: 'true',
                        offValue: 'false',
                        onChange: (nextValue) => {
                            setEnabled(pack.id, nextValue === 'true');
                        },
                    });
                    toggle.setAttribute('aria-label', t(pack.nameKey));
                    row.appendChild(toggle);
                } else {
                    const checkbox = documentRef.createElement('input');
                    checkbox.type = 'checkbox';
                    checkbox.checked = enabled;
                    checkbox.setAttribute('aria-label', t(pack.nameKey));
                    checkbox.addEventListener('change', () => {
                        setEnabled(pack.id, checkbox.checked);
                    });
                    row.appendChild(checkbox);
                }
                listEl.appendChild(row);
            }
        }

        function getPickerElements() {
            const overlay = elements.pickerOverlay
                || (documentRef && documentRef.getElementById('featurePackOverlay'));
            const dialog = elements.pickerDialog
                || (documentRef && documentRef.getElementById('featurePackDialog'));
            const title = elements.pickerTitle
                || (documentRef && documentRef.getElementById('featurePackTitle'));
            const hint = elements.pickerHint
                || (documentRef && documentRef.getElementById('featurePackHint'));
            const list = elements.pickerList
                || (documentRef && documentRef.getElementById('featurePackList'));
            const confirmButton = elements.pickerConfirmButton
                || (documentRef && documentRef.getElementById('featurePackConfirmButton'));
            return { overlay, dialog, title, hint, list, confirmButton };
        }

        function renderPickerCards() {
            const { title, hint, list, confirmButton } = getPickerElements();
            if (title) title.textContent = t('feature_packs_picker_title');
            if (hint) hint.textContent = t('feature_packs_picker_hint');
            if (confirmButton) confirmButton.textContent = t('feature_packs_picker_confirm');
            if (!list || !documentRef) return;

            list.innerHTML = '';
            for (const pack of PACKS) {
                const selected = !!pickerSelection[pack.id];
                const card = documentRef.createElement('label');
                card.className = 'feature-pack-card';
                if (selected) card.classList.add('is-selected');
                card.dataset.packId = pack.id;

                const checkbox = documentRef.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.className = 'feature-pack-checkbox';
                checkbox.checked = selected;
                checkbox.value = pack.id;
                checkbox.addEventListener('change', () => {
                    pickerSelection[pack.id] = checkbox.checked;
                    card.classList.toggle('is-selected', checkbox.checked);
                });

                const content = documentRef.createElement('span');
                content.className = 'feature-pack-card-text';

                const nameEl = documentRef.createElement('span');
                nameEl.className = 'mode-card-title';
                nameEl.textContent = t(pack.nameKey);

                const descEl = documentRef.createElement('span');
                descEl.className = 'mode-card-desc';
                descEl.textContent = t(pack.descKey);

                content.appendChild(nameEl);
                content.appendChild(descEl);
                card.appendChild(checkbox);
                card.appendChild(content);
                list.appendChild(card);
            }
        }

        function closePicker(result) {
            const { overlay, dialog } = getPickerElements();
            if (overlay) overlay.hidden = true;
            if (dialog) dialog.hidden = true;
            pickerOpen = false;
            const resolve = pickerResolve;
            pickerResolve = null;
            if (typeof resolve === 'function') {
                resolve(result);
            }
        }

        function openPicker() {
            const { overlay, dialog, confirmButton } = getPickerElements();
            if (!overlay || !dialog) {
                return Promise.resolve(getEnabledMap());
            }
            if (pickerOpen && pickerResolve) {
                return new Promise((resolve) => {
                    const prev = pickerResolve;
                    pickerResolve = (val) => {
                        prev(val);
                        resolve(val);
                    };
                });
            }
            pickerOpen = true;
            pickerSelection = state.isUpgrade
                ? normalizeEnabledMap(state.enabled, DEFAULT_UPGRADE_ENABLED)
                : normalizeEnabledMap(null, DEFAULT_NEW_ENABLED);
            renderPickerCards();
            overlay.hidden = false;
            dialog.hidden = false;

            return new Promise((resolve) => {
                pickerResolve = resolve;
                if (confirmButton) {
                    confirmButton.onclick = () => {
                        const result = confirmSelection(pickerSelection);
                        closePicker(result);
                    };
                }
            });
        }

        function canAutoShowPicker() {
            const runtime = runtimeState();
            if (runtime.lockManualControls) return false;
            if (state.confirmed) return false;
            return true;
        }

        async function maybeShowStartupPicker() {
            if (!canAutoShowPicker()) return false;
            const runtime = runtimeState();
            if (runtime.relayAvailable) {
                const loggedIn = !!(runtime.backendLoggedIn || runtime.signedIn);
                const directMode = runtime.connectionMode === 'direct';
                if (!loggedIn && !directMode) {
                    return false;
                }
            }
            await openPicker();
            return true;
        }

        async function maybeShowAfterLogin() {
            if (!canAutoShowPicker()) return false;
            await openPicker();
            return true;
        }

        async function maybeShowOnModeEnter(mode) {
            if (!canAutoShowPicker()) return false;
            if (mode !== 'direct') return false;
            await openPicker();
            return true;
        }

        function applyI18n() {
            renderSettingsSection();
            if (pickerOpen) {
                renderPickerCards();
            }
        }

        return {
            PACKS,
            PACK_IDS,
            STORAGE_KEY,
            applyAll,
            applyBackendPacks,
            applyI18n,
            confirmSelection,
            getEnabledMap,
            isConfirmed,
            isEnabled,
            isPickerOpen: () => pickerOpen,
            isUpgrade,
            maybeShowAfterLogin,
            maybeShowOnModeEnter,
            maybeShowStartupPicker,
            openPicker,
            renderSettingsSection,
            setEnabled,
            syncToBackend,
        };
    }

    const api = {
        EXISTING_INSTALL_KEYS,
        PACKS,
        PACK_IDS,
        STORAGE_KEY,
        create,
        detectExistingInstall,
    };
    root.FeaturePacks = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
