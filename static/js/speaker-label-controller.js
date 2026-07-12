(function (root) {
    'use strict';

    function create(options = {}) {
        const fetchImpl = options.fetch || root.fetch;
        const container = options.container;
        const logger = options.console || root.console || { error() {} };
        const getRuntimeState = typeof options.getRuntimeState === 'function'
            ? options.getRuntimeState
            : () => ({});
        const getStoredPreference = typeof options.getStoredPreference === 'function'
            ? options.getStoredPreference
            : () => null;
        const renderPicker = typeof options.renderPicker === 'function'
            ? options.renderPicker
            : () => {};
        const renderSubtitles = typeof options.renderSubtitles === 'function'
            ? options.renderSubtitles
            : () => {};

        if (typeof fetchImpl !== 'function') {
            throw new TypeError('SpeakerLabelController.create requires fetch');
        }
        if (!container || !container.classList) {
            throw new TypeError('SpeakerLabelController.create requires a container');
        }

        let diarizationEnabled = true;
        let labelsHidden = false;

        function applyVisibility() {
            container.classList.toggle('hide-speaker-labels', labelsHidden);
            return labelsHidden;
        }

        async function setHidden(hidden) {
            if (getRuntimeState().lockManualControls) return false;
            try {
                const response = await fetchImpl('/speaker-labels', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ hide_speaker_labels: !!hidden }),
                });
                if (!response.ok) {
                    logger.error('Failed to set speaker labels');
                    return false;
                }
                const data = await response.json().catch(() => ({}));
                labelsHidden = typeof data.hide_speaker_labels === 'boolean'
                    ? data.hide_speaker_labels
                    : !!hidden;
                applyVisibility();
                renderPicker();
                renderSubtitles();
                return true;
            } catch (error) {
                logger.error('Error setting speaker labels:', error);
                return false;
            }
        }

        function applyBackendConfig(data = {}) {
            if (typeof data.speaker_diarization_enabled === 'boolean') {
                diarizationEnabled = data.speaker_diarization_enabled;
            }
            if (typeof data.hide_speaker_labels === 'boolean') {
                labelsHidden = data.hide_speaker_labels;
            }
            const stored = getStoredPreference();
            const runtime = getRuntimeState() || {};
            if (
                !runtime.lockManualControls
                && runtime.translationProvider === 'soniox'
                && stored !== null
            ) {
                labelsHidden = stored;
                if (data.hide_speaker_labels !== stored) void setHidden(stored);
            }
            applyVisibility();
            return { diarizationEnabled, labelsHidden };
        }

        function handleBackendChanged(data = {}) {
            if (typeof data.hide_speaker_labels === 'boolean') {
                labelsHidden = data.hide_speaker_labels;
            } else if (typeof data.enabled === 'boolean') {
                labelsHidden = !data.enabled;
            }
            applyVisibility();
            renderPicker();
            renderSubtitles();
            return labelsHidden;
        }

        function getState() {
            return { diarizationEnabled, labelsHidden };
        }

        return {
            applyBackendConfig,
            applyVisibility,
            getState,
            handleBackendChanged,
            isDiarizationEnabled: () => diarizationEnabled,
            isHidden: () => labelsHidden,
            setHidden,
        };
    }

    const api = { create };
    root.SpeakerLabelController = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
