(function (root) {
    'use strict';

    const STORAGE_KEY = 'learningLanguages.v1';
    const LANGUAGES = Object.freeze([
        Object.freeze({ code: 'ja', labelKey: 'learning_ja_furigana' }),
        Object.freeze({ code: 'zh', labelKey: 'learning_zh_pinyin' }),
        Object.freeze({ code: 'ko', labelKey: 'learning_ko_romanization' }),
    ]);
    const LANGUAGE_CODES = Object.freeze(LANGUAGES.map((entry) => entry.code));

    function normalizeUiLangCode(uiLang) {
        return String(uiLang || 'en')
            .trim()
            .toLowerCase()
            .replace(/_/g, '-')
            .split('-')[0];
    }

    function getDefaultMap(uiLang) {
        const code = normalizeUiLangCode(uiLang);
        return {
            ja: code !== 'ja',
            zh: code !== 'zh',
            ko: code !== 'ko',
        };
    }

    function normalizeLanguageMap(raw, fallback) {
        const base = fallback || getDefaultMap('en');
        const source = raw && typeof raw === 'object' ? raw : {};
        const result = {};
        for (const code of LANGUAGE_CODES) {
            result[code] = typeof source[code] === 'boolean' ? source[code] : !!base[code];
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
        const getUiLanguage = typeof options.getUiLanguage === 'function'
            ? options.getUiLanguage
            : () => (root.I18N && root.I18N.lang) || 'en';
        const buildToggleSwitch = typeof options.buildToggleSwitch === 'function'
            ? options.buildToggleSwitch
            : null;
        const onChange = typeof options.onChange === 'function' ? options.onChange : () => {};
        const logger = options.console || root.console || { warn() {}, error() {} };
        const elements = options.elements || {};

        let hasExplicitStored = false;
        let state = loadInitialState();
        let draft = { ...state };
        const rowControls = new Map();

        function isDirtyMap() {
            return LANGUAGE_CODES.some((code) => draft[code] !== state[code]);
        }

        function isSectionVisible() {
            const section = elements.settingsSection
                || (documentRef && documentRef.getElementById('learningSettingsSection'));
            return !section || !section.hidden;
        }

        function hasPendingChanges() {
            return isSectionVisible() && isDirtyMap();
        }

        function getDraft() {
            return { ...draft };
        }

        function setDraftLanguage(lang, nextValue) {
            const code = normalizeUiLangCode(lang);
            if (!LANGUAGE_CODES.includes(code)) return false;
            draft[code] = !!nextValue;
            return true;
        }

        function resetDraft() {
            draft = { ...state };
            renderSettingsSection();
            return getDraft();
        }

        async function commitDraft({ sync = true } = {}) {
            if (!isDirtyMap()) return false;
            saveState(draft);
            draft = { ...state };
            renderSettingsSection();
            onChange(getEnabledMap());
            if (sync) {
                await syncToBackend();
            }
            return true;
        }

        function loadInitialState() {
            const defaults = getDefaultMap(getUiLanguage());
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
                        const hasAnyBool = LANGUAGE_CODES.some((code) => typeof parsed[code] === 'boolean');
                        if (hasAnyBool) {
                            hasExplicitStored = true;
                            return normalizeLanguageMap(parsed, defaults);
                        }
                    }
                } catch (_) {
                    // ignore corrupt storage
                }
            }
            return defaults;
        }

        function saveState(nextMap) {
            state = normalizeLanguageMap(nextMap, getDefaultMap(getUiLanguage()));
            hasExplicitStored = true;
            if (storage && typeof storage.setItem === 'function') {
                try {
                    storage.setItem(STORAGE_KEY, JSON.stringify(state));
                } catch (error) {
                    logger.warn('Failed to persist learning languages state:', error);
                }
            }
            return { ...state };
        }

        function getEnabledMap() {
            return { ...state };
        }

        function isLanguageEnabled(lang) {
            const code = normalizeUiLangCode(lang);
            if (code === 'cmn') return !!state.zh;
            if (!LANGUAGE_CODES.includes(code)) return false;
            return !!state[code];
        }

        async function syncToBackend() {
            if (!fetchImpl) return false;
            try {
                const response = await fetchImpl('/learning-languages', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ languages: getEnabledMap() }),
                });
                return !!response.ok;
            } catch (error) {
                logger.warn('Failed to sync learning languages to backend:', error);
                return false;
            }
        }

        function setLanguageEnabled(lang, nextValue, { sync = true } = {}) {
            const code = normalizeUiLangCode(lang);
            if (!LANGUAGE_CODES.includes(code)) return false;
            const enabled = !!nextValue;
            if (state[code] === enabled && hasExplicitStored) {
                return false;
            }
            saveState({ ...state, [code]: enabled });
            draft = { ...state };
            renderSettingsSection();
            onChange(getEnabledMap());
            if (sync) {
                void syncToBackend();
            }
            return true;
        }

        function applyBackendLanguages(languages, { initialized = true } = {}) {
            if (!languages || typeof languages !== 'object') return false;
            if (!initialized && !hasExplicitStored) return false;
            let changed = false;
            const next = { ...state };
            for (const code of LANGUAGE_CODES) {
                if (typeof languages[code] === 'boolean' && next[code] !== languages[code]) {
                    next[code] = languages[code];
                    changed = true;
                }
            }
            if (!changed) return false;
            const wasDirty = isDirtyMap();
            saveState(next);
            if (!wasDirty) {
                draft = { ...state };
            }
            renderSettingsSection();
            onChange(getEnabledMap());
            return true;
        }

        function renderSettingsSection() {
            const titleEl = elements.settingsSectionLabel
                || (documentRef && documentRef.getElementById('learningSettingsLabel'));
            if (titleEl) titleEl.textContent = t('learning_settings_section');
            const listEl = elements.settingsList
                || (documentRef && documentRef.getElementById('learningLanguagesList'));
            if (!listEl || !documentRef) return;

            const expectedKind = buildToggleSwitch ? 'switch' : 'checkbox';
            const canUpdateInPlace = LANGUAGES.every((entry) => {
                const cached = rowControls.get(entry.code);
                return cached && cached.kind === expectedKind && cached.row.parentNode === listEl;
            });

            if (canUpdateInPlace) {
                for (const entry of LANGUAGES) {
                    const enabled = !!draft[entry.code];
                    const cached = rowControls.get(entry.code);
                    const labelText = t(entry.labelKey);
                    cached.nameEl.textContent = labelText;
                    cached.control.setAttribute('aria-label', labelText);
                    if (cached.kind === 'switch') {
                        cached.control.value = enabled ? 'true' : 'false';
                    } else {
                        cached.control.checked = enabled;
                    }
                }
                return;
            }

            rowControls.clear();
            listEl.innerHTML = '';
            for (const entry of LANGUAGES) {
                const enabled = !!draft[entry.code];
                const row = documentRef.createElement('label');
                row.className = 'settings-item settings-row';
                row.dataset.learningLang = entry.code;

                const nameEl = documentRef.createElement('span');
                nameEl.className = 'settings-row-text';
                nameEl.textContent = t(entry.labelKey);
                row.appendChild(nameEl);

                let control;
                if (buildToggleSwitch) {
                    control = buildToggleSwitch({
                        value: enabled ? 'true' : 'false',
                        onValue: 'true',
                        offValue: 'false',
                        onChange: (nextValue) => {
                            setDraftLanguage(entry.code, nextValue === 'true');
                        },
                    });
                    control.setAttribute('aria-label', t(entry.labelKey));
                    row.appendChild(control);
                } else {
                    control = documentRef.createElement('input');
                    control.type = 'checkbox';
                    control.checked = enabled;
                    control.setAttribute('aria-label', t(entry.labelKey));
                    control.addEventListener('change', () => {
                        setDraftLanguage(entry.code, control.checked);
                    });
                    row.appendChild(control);
                }
                rowControls.set(entry.code, {
                    row,
                    nameEl,
                    control,
                    kind: expectedKind,
                });
                listEl.appendChild(row);
            }
        }

        function applyI18n() {
            if (!hasExplicitStored) {
                const wasDirty = isDirtyMap();
                state = getDefaultMap(getUiLanguage());
                if (!wasDirty) {
                    draft = { ...state };
                }
            }
            renderSettingsSection();
        }

        return {
            LANGUAGES,
            LANGUAGE_CODES,
            STORAGE_KEY,
            applyBackendLanguages,
            applyI18n,
            commitDraft,
            getDraft,
            getEnabledMap,
            hasPendingChanges,
            isLanguageEnabled,
            renderSettingsSection,
            resetDraft,
            setLanguageEnabled,
            syncToBackend,
        };
    }

    const api = {
        LANGUAGES,
        LANGUAGE_CODES,
        STORAGE_KEY,
        getDefaultMap,
        create,
    };
    root.LearningLanguages = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
