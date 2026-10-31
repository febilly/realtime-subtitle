(function (root) {
    'use strict';

    // Local model downloads asked for before a switch: the first switch to the
    // local provider, and a switch to 快速 / 混合 while Hy-MT2 is missing.
    // A switch only goes through once every chosen download has finished;
    // cancel or failure keeps the previous provider / mode.

    const PROGRESS_POLL_MS = 500;
    // VRAM thresholds in GiB (what drivers report), each a little under the
    // round number so a nominal 8 / 16 GB card still passes.
    const GIB = 1024 ** 3;
    const VRAM_MIN_TOTAL = 7.8 * GIB;
    const VRAM_MIN_FREE = 3.8 * GIB;
    const VRAM_GAME_TOTAL = 15.8 * GIB;

    function formatGb(bytes) {
        const value = Number(bytes) || 0;
        return `${(value / 1e9).toFixed(1)} GB`;
    }

    // VRAM in GiB, labelled GB like Task Manager does.
    function formatVram(bytes) {
        const value = Number(bytes) || 0;
        return `${(value / GIB).toFixed(1)} GB`;
    }

    // Whether the local provider runs Hy-MT2 with this UI state: 准确 needs no
    // local model, but only while an LLM is there to do the translating.
    function needsMachineTranslation({ uiTranslationMode, translationUiMode, llmAvailable }) {
        if (uiTranslationMode === 'none') return false;
        return !(llmAvailable && translationUiMode === 'accurate');
    }

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const fetchRef = typeof options.fetch === 'function'
            ? options.fetch
            : (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const setTimer = options.setTimeout || root.setTimeout.bind(root);
        const clearTimer = options.clearTimeout || root.clearTimeout.bind(root);
        let overlay = null;
        let dialog = null;
        let pollTimer = null;

        async function fetchStatus() {
            const response = await fetchRef('/local-inference/status');
            const data = await response.json();
            if (!response.ok) throw new Error(data.message || 'status failed');
            return data;
        }

        function modelInfo(status) {
            const models = (status && status.models) || {};
            const asr = (models.asr && models.asr['qwen3-asr']) || {};
            const mt = (models.translation && models.translation.hymt2) || {};
            return { asr, mt };
        }

        function element(tag, className, text) {
            const node = documentRef.createElement(tag);
            if (className) node.className = className;
            if (text !== undefined) node.textContent = text;
            return node;
        }

        function close() {
            if (pollTimer !== null) clearTimer(pollTimer);
            pollTimer = null;
            if (overlay) overlay.remove();
            if (dialog) dialog.remove();
            overlay = null;
            dialog = null;
        }

        function open(title) {
            close();
            overlay = element('div', 'confirm-overlay');
            dialog = element('div', 'confirm-dialog local-model-dialog');
            dialog.setAttribute('role', 'dialog');
            dialog.setAttribute('aria-modal', 'true');
            dialog.appendChild(element('h2', 'local-model-dialog-title', title));
            const body = element('div', 'local-model-dialog-body');
            const actions = element('div', 'confirm-actions local-model-dialog-actions');
            dialog.appendChild(body);
            dialog.appendChild(actions);
            documentRef.body.appendChild(overlay);
            documentRef.body.appendChild(dialog);
            return { body, actions };
        }

        function button(label, className, onClick) {
            const node = element('button', className, label);
            node.type = 'button';
            node.addEventListener('click', onClick);
            return node;
        }

        function modelRow(name, model, badge) {
            const row = element('div', 'local-model-dialog-row');
            const head = element('div', 'local-model-dialog-row-head');
            head.appendChild(element('strong', '', name));
            head.appendChild(element('span', 'local-model-dialog-badge', badge));
            row.appendChild(head);
            row.appendChild(element('div', 'field-hint', t('local_dialog_model_meta', {
                download: formatGb(model.estimated_bytes),
                vram: formatGb(model.estimated_vram_bytes),
            })));
            return row;
        }

        // The card the models will load on; older servers send no
        // selected_device, so fall back to the first card with free memory.
        function selectedGpu(status) {
            if (status && status.selected_device !== undefined) return status.selected_device;
            const devices = Array.isArray(status && status.devices) ? status.devices : [];
            return devices.find((device) => Number(device && device.free_bytes) > 0) || null;
        }

        function gpuName(gpu) {
            return String(gpu.description || gpu.name || 'GPU');
        }

        function gpuLine(status) {
            const gpu = selectedGpu(status);
            if (!gpu || !(Number(gpu.free_bytes) > 0)) return null;
            return element('p', 'field-hint', t('local_dialog_gpu_free', {
                gpu: gpuName(gpu),
                free: formatVram(gpu.free_bytes),
            }));
        }

        // Warnings shown only when something falls short: free disk space for
        // downloadBytes, and the selected card's total / free VRAM.
        function resourceWarnings(status, downloadBytes) {
            const warnings = [];
            const models = (status && status.models) || {};
            const diskFree = models.disk_free_bytes;
            if (typeof diskFree === 'number' && diskFree < downloadBytes) {
                warnings.push(element('p', 'local-model-dialog-warning', t('local_dialog_disk_low', {
                    need: formatGb(downloadBytes),
                    free: formatGb(diskFree),
                    dir: String(models.models_dir || ''),
                })));
            }
            const gpu = selectedGpu(status);
            const total = Number(gpu && gpu.total_bytes) || 0;
            if (total > 0) {
                const free = Number(gpu.free_bytes) || 0;
                if (total < VRAM_MIN_TOTAL || (free > 0 && free < VRAM_MIN_FREE)) {
                    warnings.push(element('p', 'local-model-dialog-warning', t('local_dialog_vram_low', {
                        gpu: gpuName(gpu), total: formatVram(total), free: formatVram(free),
                    })));
                } else if (total < VRAM_GAME_TOTAL) {
                    warnings.push(element('p', 'local-model-dialog-warning', t('local_dialog_vram_no_game', {
                        gpu: gpuName(gpu), total: formatVram(total),
                    })));
                }
            }
            return warnings;
        }

        function stageLabel(stage) {
            if (!stage) return t('local_download_preparing');
            return t(`local_download_stage_${String(stage).replace(/-/g, '_')}`);
        }

        // Runs the downloads in order inside the open dialog. Resolves true
        // when all finished, false when the user cancelled; failures show a
        // retry / close choice and resolve false on close.
        function runDownloads(view, components) {
            return new Promise((resolve) => {
                let cancelled = false;
                const progressText = element('p', 'local-model-dialog-progress', t('local_download_preparing'));
                const bar = element('progress', 'local-model-dialog-bar');
                bar.max = 1;
                bar.removeAttribute('value');

                function showProgress() {
                    view.body.replaceChildren(progressText, bar);
                    view.actions.replaceChildren(button(t('local_dialog_cancel_download'), 'secondary-button', () => {
                        cancelled = true;
                        void fetchRef('/local-inference/download/cancel', { method: 'POST' }).catch(() => {});
                    }));
                }

                function poll() {
                    pollTimer = setTimer(async () => {
                        pollTimer = null;
                        try {
                            const response = await fetchRef('/local-inference/download/progress');
                            const data = await response.json();
                            const download = (data && data.download) || {};
                            if (download.active) {
                                const total = Number(download.total) || 0;
                                const done = Number(download.done) || 0;
                                let text = stageLabel(download.stage);
                                if (total > 0) {
                                    bar.value = Math.min(1, done / total);
                                    text += ` · ${formatGb(done)} / ${formatGb(total)}`;
                                } else {
                                    bar.removeAttribute('value');
                                }
                                progressText.textContent = text;
                            }
                        } catch (error) {
                            // Progress is display only.
                        }
                        if (dialog) poll();
                    }, PROGRESS_POLL_MS);
                }

                async function downloadAll() {
                    showProgress();
                    poll();
                    try {
                        for (const component of components) {
                            if (cancelled) {
                                resolve(false);
                                return;
                            }
                            const response = await fetchRef('/local-inference/download', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ component }),
                            });
                            const data = await response.json().catch(() => ({}));
                            if (!response.ok) {
                                if (data.code === 'download_cancelled' || cancelled) {
                                    resolve(false);
                                    return;
                                }
                                throw new Error(data.message || 'download failed');
                            }
                        }
                        resolve(true);
                    } catch (error) {
                        if (pollTimer !== null) clearTimer(pollTimer);
                        pollTimer = null;
                        view.body.replaceChildren(element('p', 'local-model-dialog-error', t('local_dialog_failed', {
                            error: String(error && error.message ? error.message : error),
                        })));
                        view.actions.replaceChildren(
                            button(t('local_dialog_close'), 'secondary-button', () => resolve(false)),
                            button(t('local_dialog_retry'), 'primary-button', () => { void downloadAll(); }),
                        );
                    }
                }

                void downloadAll();
            });
        }

        // Before switching the provider to local. Resolves
        //   { ok: false }                          cancelled / failed: keep the old provider
        //   { ok: true, skippedTranslation: bool } switch; skippedTranslation means
        //                                          Hy-MT2 is still missing by choice
        async function confirmLocalProvider({ previousProvider, llmAvailable, needsTranslation } = {}) {
            let status;
            try {
                status = await fetchStatus();
            } catch (error) {
                return { ok: true, skippedTranslation: false };
            }
            const { asr, mt } = modelInfo(status);
            const asrMissing = !asr.ready;
            const mtMissing = !mt.model_ready && needsTranslation !== false;
            if (!asrMissing && !mtMissing) return { ok: true, skippedTranslation: false };

            const view = open(asrMissing ? t('local_dialog_title') : t('local_dialog_mt_title'));
            const intro = [element('p', 'confirm-message', t('local_dialog_body'))];
            if (asrMissing) intro.push(modelRow(t('local_dialog_asr_name'), asr, t('local_dialog_required')));
            if (mtMissing) intro.push(modelRow(t('local_dialog_mt_name'), mt, t('local_dialog_mt_usage')));
            const gpu = gpuLine(status);
            if (gpu) intro.push(gpu);
            intro.push(...resourceWarnings(status,
                (asrMissing ? Number(asr.estimated_bytes) || 0 : 0)
                + (mtMissing ? Number(mt.estimated_bytes) || 0 : 0)));
            if (mtMissing) {
                intro.push(element('p', 'field-hint', t(
                    llmAvailable ? 'local_dialog_asr_only_llm' : 'local_dialog_asr_only_no_llm',
                )));
            }
            view.body.replaceChildren(...intro);

            const choice = await new Promise((resolve) => {
                const buttons = [button(
                    t('local_dialog_cancel_keep', { provider: previousProvider || '' }),
                    'secondary-button',
                    () => resolve(null),
                )];
                if (asrMissing && mtMissing) {
                    buttons.push(button(t('local_dialog_download_asr', {
                        size: formatGb(asr.estimated_bytes),
                    }), 'secondary-button', () => resolve(['qwen3-asr'])));
                    buttons.push(button(t('local_dialog_download_both', {
                        size: formatGb((Number(asr.estimated_bytes) || 0) + (Number(mt.estimated_bytes) || 0)),
                    }), 'primary-button', () => resolve(['qwen3-asr', 'hymt2'])));
                } else if (asrMissing) {
                    buttons.push(button(t('local_dialog_download_mt', {
                        size: formatGb(asr.estimated_bytes),
                    }), 'primary-button', () => resolve(['qwen3-asr'])));
                } else {
                    buttons.push(button(t(
                        llmAvailable ? 'local_dialog_skip_mt_llm' : 'local_dialog_skip_mt_no_llm',
                    ), 'secondary-button', () => resolve([])));
                    buttons.push(button(t('local_dialog_download_mt', {
                        size: formatGb(mt.estimated_bytes),
                    }), 'primary-button', () => resolve(['hymt2'])));
                }
                view.actions.replaceChildren(...buttons);
            });
            if (choice === null) {
                close();
                return { ok: false };
            }
            const finished = choice.length ? await runDownloads(view, choice) : true;
            close();
            if (!finished) return { ok: false };
            return { ok: true, skippedTranslation: mtMissing && !choice.includes('hymt2') };
        }

        // Before selecting 快速 / 混合 on the local provider. Resolves true when
        // Hy-MT2 is (now) downloaded.
        async function confirmTranslationModel({ modeLabel, previousModeLabel } = {}) {
            let status;
            try {
                status = await fetchStatus();
            } catch (error) {
                return true;
            }
            const { mt } = modelInfo(status);
            if (mt.model_ready) return true;
            const view = open(t('local_dialog_mt_title'));
            const intro = [
                element('p', 'confirm-message', t('local_dialog_mt_body', { mode: modeLabel || '' })),
                modelRow(t('local_dialog_mt_name'), mt, t('local_dialog_mt_usage')),
            ];
            const gpu = gpuLine(status);
            if (gpu) intro.push(gpu);
            intro.push(...resourceWarnings(status, Number(mt.estimated_bytes) || 0));
            view.body.replaceChildren(...intro);
            const confirmed = await new Promise((resolve) => {
                view.actions.replaceChildren(
                    button(t('local_dialog_cancel_mode', { mode: previousModeLabel || '' }),
                        'secondary-button', () => resolve(false)),
                    button(t('local_dialog_download_mt', { size: formatGb(mt.estimated_bytes) }),
                        'primary-button', () => resolve(true)),
                );
            });
            const finished = confirmed ? await runDownloads(view, ['hymt2']) : false;
            close();
            return finished;
        }

        return {
            close,
            confirmLocalProvider,
            confirmTranslationModel,
            isOpen: () => !!dialog,
        };
    }

    const api = { create, formatGb, needsMachineTranslation };
    root.LocalModelDialog = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
