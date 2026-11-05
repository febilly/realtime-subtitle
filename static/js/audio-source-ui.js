(function (root) {
    'use strict';

    function create(options = {}) {
        const document = options.document || root.document;
        const window = options.window || root;
        const fetch = options.fetch || root.fetch.bind(root);
        const t = options.t || ((key) => key);
        const button = options.button;
        const ui = (options.settingsUI || root.SettingsUI).create({ document, window });
        let settings = null;
        let draft = null;
        let panel = null;
        let busy = false;
        let generation = 0;
        let pickers = [];
        let lists = {};
        let captureError = '';

        const clone = (value) => JSON.parse(JSON.stringify(value));
        const locked = () => options.isLocked && options.isLocked();
        const node = (tag, className, text) => {
            const result = document.createElement(tag);
            result.className = className;
            if (text) result.textContent = text;
            return result;
        };

        async function request(url, init) {
            const response = await fetch(url, init);
            const data = await response.json();
            if (!response.ok || data.status === 'error') throw new Error(data.message || `HTTP ${response.status}`);
            return data;
        }

        function accept(data) {
            captureError = data.capture_error || '';
            settings = data.settings || {
                desktop_enabled: data.source !== 'microphone', microphone_enabled: ['microphone', 'mix'].includes(data.source),
                desktop_mode: 'output', output_device_id: '', microphone_device_id: '', window: null, remember: false,
            };
            if (typeof options.onApplied === 'function') options.onApplied(data.source);
        }

        async function fetchState() {
            try {
                accept(await request('/audio-source'));
                return true;
            } catch (error) {
                (options.console || root.console).error('Failed to fetch audio capture settings:', error);
                return false;
            }
        }

        function closePickers() {
            pickers.forEach((picker) => picker.close());
            pickers = [];
        }

        function hide() {
            if (busy) return false;
            generation += 1;
            closePickers();
            if (panel) panel.remove();
            panel = null;
            draft = null;
            document.removeEventListener('mousedown', outside, true);
            document.removeEventListener('keydown', escape, true);
            window.removeEventListener('resize', position, true);
            button.setAttribute('aria-expanded', 'false');
            button.focus();
            return true;
        }

        function outside(event) {
            if (panel && !panel.contains(event.target) && !button.contains(event.target)
                && !event.target.closest('.lang-select-menu')) hide();
        }

        function escape(event) {
            if (event.key === 'Escape') {
                if (document.querySelector('.lang-select-menu')) closePickers();
                else hide();
            }
        }

        function position() {
            if (!panel) return;
            ui.positionPopoverAtBaseline(panel, (baseline) => {
                const contents = baseline.querySelectorAll('.audio-source-options');
                contents[0].hidden = false;
                contents[1].hidden = true;
                // Reference: desktop output capture on, microphone off.
                // The window-mode explanation and validation are extra content.
                contents[0].querySelectorAll('.field-hint').forEach((hint) => hint.remove());
                baseline.querySelector('.audio-source-error').hidden = true;
            });
        }

        function error(key) {
            const message = panel.querySelector('.audio-source-error');
            message.textContent = t(key);
            message.hidden = false;
        }

        function picker(host, entries, value, onChange, disabled = false) {
            const select = ui.buildCustomSelect(entries, { value, onChange, disabled });
            pickers.push(select);
            host.appendChild(select);
        }

        function devicePicker(host, key, data) {
            const entries = [{ value: '', label: data.default?.name
                ? t('microphone_device_default', { name: data.default.name }) : t('microphone_device_default_unknown') },
            ...(data.devices || []).map((item) => ({ value: item.id, label: item.name }))];
            if (draft[key] && !entries.some((entry) => entry.value === draft[key])) {
                entries.push({ value: draft[key], label: t('audio_device_missing') });
            }
            picker(host, entries, draft[key], (value) => { draft[key] = value; }, !data.available);
        }

        function section(labelKey, enabledKey, buildContents) {
            const box = node('section', 'settings-card audio-source-section');
            const row = node('div', 'settings-row');
            const label = node('span', 'settings-row-text', t(labelKey));
            label.id = `audio-${enabledKey}-label`;
            const toggle = ui.buildToggleSwitch({ value: String(draft[enabledKey]), onChange: (value) => {
                draft[enabledKey] = value === 'true';
                render();
            } });
            toggle.id = `audio-${enabledKey}`;
            toggle.setAttribute('aria-labelledby', label.id);
            row.append(label, toggle);
            box.appendChild(row);
            const content = node('div', 'audio-source-options');
            content.hidden = !draft[enabledKey];
            buildContents(content);
            box.appendChild(content);
            return box;
        }

        function render() {
            const focusedId = panel.contains(document.activeElement) ? document.activeElement.id : '';
            closePickers();
            panel.replaceChildren();
            panel.appendChild(node('h2', 'lang-popover-title', t('audio_capture_title')));
            panel.appendChild(section('audio_desktop_capture', 'desktop_enabled', (content) => {
                const modes = node('div', 'segmented-control translation-mode-control');
                for (const [mode, label] of [['output', 'audio_output_mode'], ['window', 'audio_window_mode']]) {
                    const option = node('button', 'segmented-option', t(label));
                    option.type = 'button';
                    option.dataset.desktopMode = mode;
                    option.id = `audio-desktop-mode-${mode}`;
                    option.classList.toggle('selected', draft.desktop_mode === mode);
                    option.setAttribute('aria-pressed', String(draft.desktop_mode === mode));
                    option.addEventListener('click', () => { draft.desktop_mode = mode; render(); });
                    modes.appendChild(option);
                }
                content.appendChild(modes);
                if (draft.desktop_mode === 'output') devicePicker(content, 'output_device_id', lists.outputs);
                else {
                    const entries = [{ value: '', label: t('audio_choose_window') },
                        ...lists.windows.windows.map((item, index) => ({ value: String(index), label: item.title }))];
                    const selected = lists.windows.windows.findIndex((item) => item.hwnd === draft.window?.hwnd
                        && item.created === draft.window?.created);
                    picker(content, entries, selected < 0 ? '' : String(selected), (value) => {
                        draft.window = value === '' ? null : lists.windows.windows[Number(value)];
                    }, !lists.windows.available);
                    content.appendChild(node('p', 'field-hint', t(lists.windows.available ? 'audio_window_hint' : 'audio_window_unsupported')));
                }
            }));
            panel.appendChild(section('audio_microphone_capture', 'microphone_enabled', (content) => {
                devicePicker(content, 'microphone_device_id', lists.microphones);
            }));
            const remember = node('div', 'settings-row popover-remember');
            const rememberText = node('span', 'settings-row-text', t('remember_my_settings'));
            rememberText.id = 'rememberAudioSettingsLabel';
            const rememberSwitch = ui.buildToggleSwitch({ value: String(draft.remember),
                onChange: (value) => { draft.remember = value === 'true'; } });
            rememberSwitch.id = 'rememberAudioSettings';
            rememberSwitch.setAttribute('aria-labelledby', rememberText.id);
            remember.append(rememberText, rememberSwitch);
            panel.appendChild(remember);
            const message = node('p', 'field-hint audio-source-error');
            message.setAttribute('role', 'alert');
            message.hidden = true;
            panel.appendChild(message);
            const actions = node('div', 'lang-popover-actions');
            const refresh = node('button', 'secondary-button', t('audio_refresh_sources'));
            refresh.type = 'button';
            refresh.id = 'refreshAudioSources';
            refresh.addEventListener('click', () => { void refreshLists(); });
            const cancel = node('button', 'secondary-button', t('cancel'));
            cancel.type = 'button';
            cancel.addEventListener('click', hide);
            const apply = node('button', 'primary-button', t('apply'));
            apply.type = 'button';
            apply.id = 'applyAudioSettings';
            apply.addEventListener('click', () => { void applyDraft(); });
            actions.append(refresh, cancel, apply);
            panel.appendChild(actions);
            if (focusedId) document.getElementById(focusedId)?.focus();
        }

        async function refreshLists() {
            const token = ++generation;
            const empty = { available: false, devices: [], windows: [] };
            const results = await Promise.allSettled(['/output-devices', '/microphones', '/audio-windows'].map((url) => request(url)));
            if (!panel || token !== generation) return;
            [lists.outputs, lists.microphones, lists.windows] = results.map((result) => result.status === 'fulfilled' ? result.value : { ...empty });
            lists.windows.windows ||= [];
            render();
            if (results.some((result) => result.status === 'rejected')) error('audio_sources_load_failed');
            else if (captureError || (draft.desktop_enabled && draft.desktop_mode === 'window'
                && !lists.windows.windows.some((item) => item.hwnd === draft.window?.hwnd && item.created === draft.window?.created))) {
                error('audio_window_missing');
            }
        }

        async function show() {
            if (locked() || busy) return false;
            if (panel) return hide();
            const token = ++generation;
            if (!await fetchState() || token !== generation || locked()) return false;
            draft = clone(settings);
            panel = node('div', 'lang-popover audio-source-popover');
            panel.setAttribute('role', 'dialog');
            panel.setAttribute('aria-label', t('audio_capture_title'));
            lists = { outputs: {}, microphones: {}, windows: { available: false, windows: [] } };
            document.body.appendChild(panel);
            render();
            position();
            button.setAttribute('aria-expanded', 'true');
            document.addEventListener('mousedown', outside, true);
            document.addEventListener('keydown', escape, true);
            window.addEventListener('resize', position, true);
            panel.querySelector('button').focus();
            await refreshLists();
            return true;
        }

        async function applyDraft() {
            if (busy || !draft || locked()) return false;
            if (!draft.desktop_enabled && !draft.microphone_enabled) {
                error('audio_source_required');
                return false;
            }
            if (draft.desktop_enabled && draft.desktop_mode === 'window' && !draft.window) {
                error('audio_choose_window');
                return false;
            }
            busy = true;
            panel.querySelectorAll('button, input').forEach((element) => { element.disabled = true; });
            try {
                accept(await request('/audio-source', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: draft }),
                }));
                busy = false;
                hide();
                return true;
            } catch (failure) {
                render();
                error(failure.message === 'Selected audio window is unavailable' ? 'audio_window_missing' : 'audio_apply_failed');
                return false;
            } finally {
                busy = false;
            }
        }

        return { show, hide, fetchState, applyDraft, getDraft: () => draft, isOpen: () => !!panel };
    }

    root.AudioSourceUI = { create };
    if (typeof module !== 'undefined') module.exports = { create };
})(typeof window !== 'undefined' ? window : globalThis);
