(function (root) {
    'use strict';

    const SPEC_MAP_MAX = 80;

    function normalizeSentenceId(value) {
        if (value === null || value === undefined) return '';
        return String(value).trim();
    }

    function normalizeSource(value) {
        return String(value || '').trim();
    }

    function normalizeLanguage(value) {
        return String(value || '').trim().toLowerCase();
    }

    function removeSentenceTokens(tokens, sentenceId) {
        const sourceTokens = Array.isArray(tokens) ? tokens : [];
        const id = normalizeSentenceId(sentenceId);
        if (!id) return { tokens: sourceTokens, removed: false };

        const kept = [];
        let removed = false;
        for (const token of sourceTokens) {
            if (token && token.llm_sentence_id && String(token.llm_sentence_id) === id) {
                removed = true;
                continue;
            }
            kept.push(token);
        }

        if (!removed) return { tokens: sourceTokens, removed: false };

        // Preserve the current app.js behavior: only separators made invalid by
        // the removal are discarded. Other token objects and their order remain
        // untouched.
        for (let index = kept.length - 1; index >= 0; index -= 1) {
            const token = kept[index];
            if (!token || !token.is_separator) continue;
            const previous = kept[index - 1];
            const next = kept[index + 1];
            if (!previous || !next || (previous.is_separator && next.is_separator)) {
                kept.splice(index, 1);
            }
        }

        return { tokens: kept, removed: true };
    }

    function createRefineState(options = {}) {
        const configuredMax = Number(options.specMapMax);
        const specMapMax = Number.isFinite(configuredMax) && configuredMax > 0
            ? Math.floor(configuredMax)
            : SPEC_MAP_MAX;

        const refinedResults = new Map();
        const confirmedSentenceIds = new Set();
        const retractedSentenceIds = new Set();
        const translationOverrides = new Map();
        const translationLanguages = new Map();
        const specTranslations = new Map();
        const specPendingSources = new Map();

        function trimSpecMap(map) {
            while (map.size > specMapMax) {
                map.delete(map.keys().next().value);
            }
        }

        function lookup(sentenceId) {
            const id = normalizeSentenceId(sentenceId);
            return {
                sentenceId: id,
                refinedTranslation: id ? refinedResults.get(id) : undefined,
                confirmed: !!id && confirmedSentenceIds.has(id),
                retracted: !!id && retractedSentenceIds.has(id),
                translationOverride: id ? translationOverrides.get(id) : undefined,
                translationLanguage: id ? translationLanguages.get(id) : undefined,
            };
        }

        function applyRefineResult(data, applyOptions = {}) {
            if (!data) return false;

            const source = normalizeSource(data.source);
            const sentenceId = normalizeSentenceId(data.sentence_id);
            const refinedTranslation = normalizeSource(data.refined_translation);
            const targetLanguage = normalizeLanguage(data.target_lang);
            const noChange = !!data.no_change;
            const translateMode = !!applyOptions.translateMode;

            if (!source) return false;
            if (sentenceId && retractedSentenceIds.has(sentenceId)) return false;

            // A finalized result makes the speculative pending marker obsolete.
            specPendingSources.delete(source);

            if (!noChange && refinedTranslation && sentenceId) {
                refinedResults.set(sentenceId, refinedTranslation);
                if (translateMode) {
                    translationOverrides.set(sentenceId, refinedTranslation);
                    if (targetLanguage) translationLanguages.set(sentenceId, targetLanguage);
                }
            } else if (noChange && sentenceId) {
                confirmedSentenceIds.add(sentenceId);
            }

            return true;
        }

        function markRetracted(sentenceId) {
            const id = normalizeSentenceId(sentenceId);
            if (!id) return false;
            retractedSentenceIds.add(id);
            refinedResults.delete(id);
            confirmedSentenceIds.delete(id);
            translationOverrides.delete(id);
            translationLanguages.delete(id);
            return true;
        }

        function markSpecPending(source, targetLanguage) {
            const normalizedSource = normalizeSource(source);
            if (!normalizedSource) return false;
            specPendingSources.set(normalizedSource, normalizeLanguage(targetLanguage));
            trimSpecMap(specPendingSources);
            return true;
        }

        function applySpecTranslation(data) {
            if (!data) return false;
            const source = normalizeSource(data.source);
            const translation = normalizeSource(data.translation);
            const language = normalizeLanguage(data.target_lang);
            if (!source) return false;

            specPendingSources.delete(source);
            if (translation) {
                specTranslations.set(source, { text: translation, lang: language });
                trimSpecMap(specTranslations);
            }
            return true;
        }

        function setSpecTranslation(source, translation, targetLanguage) {
            return applySpecTranslation({ source, translation, target_lang: targetLanguage });
        }

        function getSpecTranslation(source) {
            return specTranslations.get(normalizeSource(source));
        }

        function isSpecPending(source) {
            return specPendingSources.has(normalizeSource(source));
        }

        function getSpecPendingLanguage(source) {
            return specPendingSources.get(normalizeSource(source));
        }

        function clear() {
            refinedResults.clear();
            confirmedSentenceIds.clear();
            retractedSentenceIds.clear();
            translationOverrides.clear();
            translationLanguages.clear();
            specTranslations.clear();
            specPendingSources.clear();
        }

        return {
            applyRefineResult,
            markRetracted,
            cleanupSentenceCaches: markRetracted,
            removeSentenceTokens,
            clear,
            lookup,
            getRefinedTranslation: (sentenceId) => lookup(sentenceId).refinedTranslation,
            isConfirmed: (sentenceId) => lookup(sentenceId).confirmed,
            isRetracted: (sentenceId) => lookup(sentenceId).retracted,
            getTranslationOverride: (sentenceId) => lookup(sentenceId).translationOverride,
            getTranslationLanguage: (sentenceId) => lookup(sentenceId).translationLanguage,
            markSpecPending,
            applySpecTranslation,
            setSpecTranslation,
            getSpecTranslation,
            isSpecPending,
            getSpecPendingLanguage,
        };
    }

    const api = { SPEC_MAP_MAX, createRefineState, removeSentenceTokens };
    root.RefineState = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
