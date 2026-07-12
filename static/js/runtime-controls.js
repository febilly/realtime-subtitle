(function (root) {
    'use strict';

    const DISPLAY_MODES = ['both', 'original', 'translation'];
    const AUDIO_SOURCES = ['system', 'microphone', 'mix'];

    function getNextDisplayMode(mode) {
        if (mode === 'both') return 'original';
        if (mode === 'original') return 'translation';
        return 'both';
    }

    function normalizeAudioSource(source) {
        const value = String(source || '').trim().toLowerCase();
        return AUDIO_SOURCES.includes(value) ? value : 'system';
    }

    function getNextAudioSource(source) {
        const current = normalizeAudioSource(source);
        const index = AUDIO_SOURCES.indexOf(current);
        return AUDIO_SOURCES[(index + 1) % AUDIO_SOURCES.length];
    }

    function create(options = {}) {
        const elements = options.elements || {};
        const displayModeButton = elements.displayModeButton || options.displayModeButton || null;
        const pauseButton = elements.pauseButton || options.pauseButton || null;
        const pauseIcon = elements.pauseIcon || options.pauseIcon || null;
        const audioSourceButton = elements.audioSourceButton || options.audioSourceButton || null;
        const audioSourceIcon = elements.audioSourceIcon || options.audioSourceIcon || null;
        const overlayButton = elements.overlayButton || options.overlayButton || null;
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const updateState = typeof options.updateState === 'function' ? options.updateState : () => {};
        const fetchRef = typeof options.fetch === 'function'
            ? options.fetch
            : (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const storage = options.storage || root.localStorage;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const setControlIcon = typeof options.setControlIcon === 'function' ? options.setControlIcon : () => {};
        const renderSubtitles = typeof options.renderSubtitles === 'function' ? options.renderSubtitles : () => {};
        const sessionCostPause = typeof options.sessionCostPause === 'function' ? options.sessionCostPause : () => {};
        const consoleRef = options.console || root.console || { log() {}, warn() {}, error() {} };
        let initialized = false;
        let handlers = null;

        function state() {
            const current = getState();
            return current && typeof current === 'object' ? current : {};
        }

        function updatePauseButtonUi() {
            if (!pauseButton || !pauseIcon) return;
            const paused = !!state().isPaused;
            setControlIcon(pauseIcon, paused ? 'play' : 'pause');
            pauseButton.title = paused ? t('resume') : t('pause');
            pauseButton.classList.toggle('is-paused', paused);
        }

        function updateDisplayModeButton() {
            if (!displayModeButton) return;
            const displayMode = state().displayMode;
            let nextKey;
            let currentKey;
            if (displayMode === 'both') {
                nextKey = 'display_mode_original';
                currentKey = 'display_mode_both';
            } else if (displayMode === 'original') {
                nextKey = 'display_mode_translation';
                currentKey = 'display_mode_original';
            } else {
                nextKey = 'display_mode_both';
                currentKey = 'display_mode_translation';
            }
            const currentName = t(currentKey);
            const nextName = t(nextKey);
            displayModeButton.title = t('display_mode_format', { current: currentName, next: nextName });
        }

        function updateAudioSourceButton() {
            if (!audioSourceButton || !audioSourceIcon) return;
            const audioSource = normalizeAudioSource(state().audioSource);
            updateState({ audioSource });
            let nextKey;
            let currentKey;
            if (audioSource === 'microphone') {
                setControlIcon(audioSourceIcon, 'mic');
                nextKey = 'audio_to_mix_val';
                currentKey = 'audio_source_microphone';
            } else if (audioSource === 'mix') {
                setControlIcon(audioSourceIcon, 'blend');
                nextKey = 'audio_to_system_val';
                currentKey = 'audio_source_mix';
            } else {
                setControlIcon(audioSourceIcon, 'volume-2');
                nextKey = 'audio_to_mic_val';
                currentKey = 'audio_source_system';
            }
            const currentName = t(currentKey);
            const nextName = t(nextKey);
            audioSourceButton.title = t('audio_source_format', { current: currentName, next: nextName });
        }

        function updateOverlayButton() {
            if (!overlayButton) return;
            const overlayOpen = !!state().overlayOpen;
            overlayButton.title = overlayOpen ? t('overlay_close') : t('overlay_open');
            overlayButton.classList.toggle('active', overlayOpen);
        }

        function cycleDisplayMode() {
            const displayMode = getNextDisplayMode(state().displayMode);
            updateState({ displayMode });
            storage.setItem('displayMode', displayMode);
            updateDisplayModeButton();
            renderSubtitles();
            consoleRef.log(`Display mode switched to: ${displayMode}`);
            return displayMode;
        }

        async function togglePause() {
            const current = state();
            if (current.lockManualControls || !fetchRef) return false;
            try {
                if (current.isPaused) {
                    const response = await fetchRef('/resume', { method: 'POST' });
                    if (response.ok) {
                        updateState({ isPaused: false });
                        updatePauseButtonUi();
                        consoleRef.log('Recognition resumed');
                        return true;
                    }
                } else {
                    const response = await fetchRef('/pause', { method: 'POST' });
                    if (response.ok) {
                        updateState({ isPaused: true });
                        updatePauseButtonUi();
                        consoleRef.log('Recognition paused');
                        sessionCostPause();
                        return true;
                    }
                }
            } catch (error) {
                consoleRef.error('Error toggling pause state:', error);
            }
            return false;
        }

        async function fetchInitialAudioSource() {
            try {
                const stored = storage.getItem('audioSource');
                updateState({ audioSource: normalizeAudioSource(stored) });
                updateAudioSourceButton();
            } catch (storageError) {
                consoleRef.warn('Unable to access stored audio source preference:', storageError);
            }

            if (!fetchRef) return false;
            try {
                const response = await fetchRef('/audio-source');
                if (!response.ok) return false;
                const data = await response.json();
                if (data && typeof data.source === 'string') {
                    const audioSource = normalizeAudioSource(data.source);
                    updateState({ audioSource });
                    updateAudioSourceButton();
                    try {
                        storage.setItem('audioSource', audioSource);
                    } catch (persistError) {
                        consoleRef.warn('Unable to persist audio source preference:', persistError);
                    }
                }
                return true;
            } catch (error) {
                consoleRef.error('Failed to fetch current audio source:', error);
                return false;
            }
        }

        async function switchAudioSource() {
            const current = state();
            if (current.lockManualControls || !fetchRef) return false;
            const nextSource = getNextAudioSource(current.audioSource);
            try {
                const response = await fetchRef('/audio-source', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ source: nextSource }),
                });
                let result = null;
                try {
                    result = await response.json();
                } catch (parseError) {
                    consoleRef.error('Failed to parse audio source response:', parseError);
                }
                if (response.ok && result && result.source) {
                    const audioSource = normalizeAudioSource(result.source);
                    updateState({ audioSource });
                    updateAudioSourceButton();
                    storage.setItem('audioSource', audioSource);
                    if (result.message) {
                        consoleRef.log(result.message);
                    } else {
                        consoleRef.log(`Audio source switched to ${audioSource}`);
                    }
                    return true;
                }
                const message = (result && result.message) || `Server responded with status ${response.status}`;
                consoleRef.error('Failed to switch audio source:', message);
            } catch (error) {
                consoleRef.error('Error switching audio source:', error);
            }
            return false;
        }

        async function refreshOverlayState() {
            if (!overlayButton || !fetchRef) return false;
            try {
                const response = await fetchRef('/overlay');
                const result = await response.json();
                if (!result || result.available === false) {
                    overlayButton.style.display = 'none';
                    return false;
                }
                updateState({ overlayOpen: !!result.open });
                updateOverlayButton();
                return true;
            } catch (error) {
                consoleRef.error('Failed to query overlay state:', error);
                return false;
            }
        }

        async function toggleOverlay() {
            if (state().lockManualControls || !fetchRef) return false;
            try {
                const response = await fetchRef('/overlay', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'toggle' }),
                });
                const result = await response.json();
                if (result && result.available === false) {
                    if (overlayButton) overlayButton.style.display = 'none';
                    return false;
                }
                updateState({ overlayOpen: !!(result && result.open) });
                updateOverlayButton();
                return true;
            } catch (error) {
                consoleRef.error('Error toggling subtitle overlay:', error);
                return false;
            }
        }

        function syncPauseState(paused) {
            updateState({ isPaused: !!paused });
            updatePauseButtonUi();
        }

        function syncOverlayState(open) {
            updateState({ overlayOpen: !!open });
            updateOverlayButton();
        }

        function init(initOptions = {}) {
            if (initialized) return false;
            initialized = true;
            handlers = {
                displayMode: () => cycleDisplayMode(),
                pause: () => { void togglePause(); },
                audioSource: () => { void switchAudioSource(); },
                overlay: () => { void toggleOverlay(); },
            };
            if (displayModeButton) displayModeButton.addEventListener('click', handlers.displayMode);
            if (pauseButton) pauseButton.addEventListener('click', handlers.pause);
            if (audioSourceButton) audioSourceButton.addEventListener('click', handlers.audioSource);
            if (overlayButton) overlayButton.addEventListener('click', handlers.overlay);
            updateDisplayModeButton();
            updatePauseButtonUi();
            updateAudioSourceButton();
            updateOverlayButton();
            if (overlayButton && initOptions.refreshOverlay !== false) void refreshOverlayState();
            return true;
        }

        function destroy() {
            if (!initialized || !handlers) return false;
            if (displayModeButton) displayModeButton.removeEventListener('click', handlers.displayMode);
            if (pauseButton) pauseButton.removeEventListener('click', handlers.pause);
            if (audioSourceButton) audioSourceButton.removeEventListener('click', handlers.audioSource);
            if (overlayButton) overlayButton.removeEventListener('click', handlers.overlay);
            initialized = false;
            handlers = null;
            return true;
        }

        return {
            init,
            destroy,
            cycleDisplayMode,
            togglePause,
            fetchInitialAudioSource,
            switchAudioSource,
            refreshOverlayState,
            toggleOverlay,
            syncPauseState,
            syncOverlayState,
            updatePauseButtonUi,
            updateDisplayModeButton,
            updateAudioSourceButton,
            updateOverlayButton,
        };
    }

    const api = {
        DISPLAY_MODES,
        AUDIO_SOURCES,
        getNextDisplayMode,
        normalizeAudioSource,
        getNextAudioSource,
        create,
    };
    root.RuntimeControls = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
