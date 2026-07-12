(function (root) {
    'use strict';

    const STAR_FILLED_SVG = '<svg class="star-icon filled" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="currentColor"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>';
    const STAR_EMPTY_SVG = '<svg class="star-icon empty" viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>';

    function normalizeTranslationMode(mode) {
        const value = String(mode || '').trim().toLowerCase();
        return ['none', 'one_way', 'two_way'].includes(value) ? value : 'one_way';
    }

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const windowRef = options.window || root;
        const storage = options.storage || root.localStorage;
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const getState = typeof options.getState === 'function' ? options.getState : () => ({});
        const updateState = typeof options.updateState === 'function' ? options.updateState : () => {};
        const getLanguages = typeof options.getLanguages === 'function' ? options.getLanguages : () => [];
        const button = options.button || null;
        let popover = null;
        let popoverOpen = false;
        let popoverCleanup = null;
        let draft = null;
        let selectMenu = null;
        let activePicker = null;
        let initialized = false;
        let buttonHandler = null;

        function languages() {
            const value = getLanguages();
            return Array.isArray(value) ? value : [];
        }

        function getLanguageDisplayName(code) {
            const normalized = String(code || '').trim().toLowerCase();
            const info = languages().find((lang) => String(lang.code).toLowerCase() === normalized);
            return info ? `${info.en} - ${info.native}` : String(code || '');
        }

        function firstSupportedCode() {
            const first = languages()[0];
            return first && first.code ? first.code : 'en';
        }

        function coerceSupportedLanguageCode(code, fallback = 'en') {
            const desired = String(code || '').trim().toLowerCase();
            const fallbackCode = String(fallback || '').trim().toLowerCase();
            const desiredMatch = languages().find((lang) => String(lang.code).toLowerCase() === desired);
            if (desiredMatch) return desiredMatch.code;
            const fallbackMatch = languages().find((lang) => String(lang.code).toLowerCase() === fallbackCode);
            return fallbackMatch ? fallbackMatch.code : firstSupportedCode();
        }

        function ensureSelectMenu() {
            if (selectMenu) return selectMenu;
            selectMenu = documentRef.createElement('div');
            selectMenu.className = 'lang-select-menu';
            selectMenu.hidden = true;
            selectMenu.setAttribute('role', 'listbox');
            documentRef.body.appendChild(selectMenu);
            return selectMenu;
        }

        function setPickerValue(picker, code) {
            if (!picker) return;
            const selected = coerceSupportedLanguageCode(code, picker.dataset.fallback || 'en');
            picker.dataset.value = selected;
            const label = picker.querySelector('.lang-picker-label');
            if (label) label.textContent = getLanguageDisplayName(selected);
        }

        function positionSelectMenu(picker, menu) {
            const trigger = picker.querySelector('.lang-picker-button') || picker;
            const rect = trigger.getBoundingClientRect();
            const gap = 6;
            const viewportPadding = 8;
            const menuWidth = Math.max(220, Math.round(rect.width));
            menu.style.maxHeight = '';
            const naturalHeight = menu.offsetHeight;
            const maxHeight = Math.min(260, Math.max(160, windowRef.innerHeight - 2 * viewportPadding));
            const spaceBelow = windowRef.innerHeight - rect.bottom - viewportPadding;
            const spaceAbove = rect.top - viewportPadding;
            const openUp = spaceBelow < 170 && spaceAbove > spaceBelow;
            const menuHeight = Math.min(
                maxHeight,
                openUp ? Math.max(120, spaceAbove - gap) : Math.max(120, spaceBelow - gap),
            );
            const actualHeight = Math.min(menuHeight, naturalHeight);
            const left = Math.min(
                Math.max(viewportPadding, rect.left),
                Math.max(viewportPadding, windowRef.innerWidth - viewportPadding - menuWidth),
            );
            const top = openUp
                ? Math.max(viewportPadding, rect.top - gap - actualHeight)
                : Math.min(windowRef.innerHeight - viewportPadding - actualHeight, rect.bottom + gap);
            menu.style.left = `${Math.round(left)}px`;
            menu.style.top = `${Math.round(top)}px`;
            menu.style.width = `${Math.round(menuWidth)}px`;
            menu.style.maxHeight = `${Math.round(menuHeight)}px`;
        }

        function getFavoriteLanguages() {
            try {
                const stored = storage.getItem('favoriteLanguages');
                if (stored) {
                    const parsed = JSON.parse(stored);
                    if (Array.isArray(parsed)) return parsed.map((code) => String(code).toLowerCase());
                }
            } catch (error) {
                if (root.console) root.console.warn('Failed to parse favorite languages:', error);
            }
            const state = getState();
            const favorites = [];
            const current = [
                state.currentTranslationTargetLang,
                state.backendTargetLang1,
                state.backendTargetLang2,
            ].filter(Boolean).map((code) => String(code).toLowerCase());
            for (const code of current) if (!favorites.includes(code)) favorites.push(code);
            for (const seed of ['zh-hans', 'zh', 'en', 'ja', 'ko']) {
                if (!favorites.includes(seed) && languages().some((lang) => String(lang.code).toLowerCase() === seed)) {
                    favorites.push(seed);
                }
            }
            return favorites;
        }

        function saveFavoriteLanguages(favorites) {
            try {
                storage.setItem('favoriteLanguages', JSON.stringify(favorites));
            } catch (error) {
                if (root.console) root.console.warn('Failed to save favorite languages:', error);
            }
        }

        function findOption(menu, normalizedCode, section) {
            if (!menu) return null;
            for (const row of menu.querySelectorAll('.lang-select-option[data-code]')) {
                if (String(row.dataset.code || '').toLowerCase() !== normalizedCode) continue;
                if (section && row.dataset.section !== section) continue;
                return row;
            }
            return null;
        }

        function toggleFavoriteLanguage(code, anchorRow = null) {
            const normalized = String(code).toLowerCase();
            const favorites = getFavoriteLanguages();
            const index = favorites.indexOf(normalized);
            if (index >= 0) favorites.splice(index, 1);
            else favorites.push(normalized);
            saveFavoriteLanguages(favorites);
            if (selectMenu && !selectMenu.hidden && activePicker) {
                const section = anchorRow ? anchorRow.dataset.section : null;
                const anchorTop = anchorRow ? anchorRow.getBoundingClientRect().top : null;
                renderSelectMenu(activePicker);
                const updated = findOption(selectMenu, normalized, section);
                if (updated && anchorTop !== null) {
                    selectMenu.scrollTop += updated.getBoundingClientRect().top - anchorTop;
                }
            }
        }

        function createOptionRow(lang, picker, section = 'all') {
            const selected = lang.code === picker.value;
            const favorited = getFavoriteLanguages().includes(String(lang.code).toLowerCase());
            const option = documentRef.createElement('div');
            option.className = 'lang-select-option';
            option.dataset.code = lang.code;
            option.dataset.section = section;
            const selectButton = documentRef.createElement('button');
            selectButton.type = 'button';
            selectButton.className = 'lang-select-option-btn';
            selectButton.classList.toggle('selected', selected);
            selectButton.setAttribute('role', 'option');
            selectButton.setAttribute('aria-selected', selected ? 'true' : 'false');
            selectButton.textContent = `${lang.en} - ${lang.native}`;
            selectButton.addEventListener('click', () => {
                setPickerValue(picker, lang.code);
                closeSelectMenu();
                picker.dispatchEvent(new windowRef.Event('change'));
            });
            const favoriteButton = documentRef.createElement('button');
            favoriteButton.type = 'button';
            favoriteButton.className = 'lang-favorite-btn';
            favoriteButton.innerHTML = favorited ? STAR_FILLED_SVG : STAR_EMPTY_SVG;
            favoriteButton.title = favorited ? t('unfavorite') : t('favorite');
            favoriteButton.addEventListener('click', (event) => {
                event.stopPropagation();
                toggleFavoriteLanguage(lang.code, option);
            });
            option.append(selectButton, favoriteButton);
            return option;
        }

        function renderSelectMenu(picker) {
            const menu = ensureSelectMenu();
            menu.innerHTML = '';
            const favoriteCodes = getFavoriteLanguages();
            const favoriteLanguages = languages().filter(
                (lang) => favoriteCodes.includes(String(lang.code).toLowerCase()),
            );
            if (favoriteLanguages.length) {
                const header = documentRef.createElement('div');
                header.className = 'lang-select-section-title';
                header.textContent = t('favorites_section_title');
                menu.appendChild(header);
                favoriteLanguages.forEach((lang) => menu.appendChild(createOptionRow(lang, picker, 'favorites')));
                const divider = documentRef.createElement('div');
                divider.className = 'lang-select-divider';
                menu.appendChild(divider);
                const allHeader = documentRef.createElement('div');
                allHeader.className = 'lang-select-section-title';
                allHeader.textContent = t('all_languages_section_title');
                menu.appendChild(allHeader);
            }
            languages().forEach((lang) => menu.appendChild(createOptionRow(lang, picker, 'all')));
        }

        function openSelectMenu(picker) {
            const menu = ensureSelectMenu();
            activePicker = picker;
            renderSelectMenu(picker);
            picker.classList.add('open');
            const trigger = picker.querySelector('.lang-picker-button');
            if (trigger) trigger.setAttribute('aria-expanded', 'true');
            menu.hidden = false;
            positionSelectMenu(picker, menu);
            const selected = menu.querySelector('.lang-select-option-btn.selected');
            if (selected && typeof selected.scrollIntoView === 'function') selected.scrollIntoView({ block: 'nearest' });
        }

        function closeSelectMenu() {
            if (activePicker) {
                activePicker.classList.remove('open');
                const trigger = activePicker.querySelector('.lang-picker-button');
                if (trigger) trigger.setAttribute('aria-expanded', 'false');
            }
            activePicker = null;
            if (selectMenu) {
                selectMenu.hidden = true;
                selectMenu.innerHTML = '';
            }
        }

        function buildPicker(selectedCode, fallbackCode = 'en') {
            const picker = documentRef.createElement('div');
            picker.className = 'lang-picker';
            picker.dataset.fallback = fallbackCode;
            Object.defineProperty(picker, 'value', {
                get: () => picker.dataset.value || '',
                set: (value) => setPickerValue(picker, value),
            });
            const trigger = documentRef.createElement('button');
            trigger.type = 'button';
            trigger.className = 'lang-picker-button';
            trigger.setAttribute('aria-haspopup', 'listbox');
            trigger.setAttribute('aria-expanded', 'false');
            const label = documentRef.createElement('span');
            label.className = 'lang-picker-label';
            const chevron = documentRef.createElement('span');
            chevron.className = 'lang-picker-chevron';
            chevron.setAttribute('aria-hidden', 'true');
            trigger.append(label, chevron);
            trigger.addEventListener('click', () => {
                if (activePicker === picker && selectMenu && !selectMenu.hidden) closeSelectMenu();
                else {
                    closeSelectMenu();
                    openSelectMenu(picker);
                }
            });
            picker.appendChild(trigger);
            setPickerValue(picker, selectedCode);
            return picker;
        }

        function createDraft() {
            const state = getState();
            let mode = normalizeTranslationMode(
                state.uiTranslationMode || state.backendTranslationMode || 'one_way',
            );
            if (mode === 'two_way' && !state.twoWaySupported) mode = 'one_way';
            return {
                mode,
                targetLang: coerceSupportedLanguageCode(
                    state.currentTranslationTargetLang,
                    state.defaultTranslationTargetLang,
                ),
                targetLang1: coerceSupportedLanguageCode(state.backendTargetLang1, 'en'),
                targetLang2: coerceSupportedLanguageCode(state.backendTargetLang2, 'zh'),
            };
        }

        function currentDraft() {
            if (!draft) draft = createDraft();
            return draft;
        }

        function selectMode(mode) {
            if (mode === 'two_way' && !getState().twoWaySupported) return;
            currentDraft().mode = normalizeTranslationMode(mode);
            updateSelection();
            refreshSections();
        }

        function refreshSections() {
            if (!popover) return;
            const state = getState();
            const value = currentDraft();
            const twoWayOption = popover.querySelector('[data-mode="two_way"]');
            if (twoWayOption) twoWayOption.hidden = !state.twoWaySupported;
            const mode = value.mode === 'two_way' && !state.twoWaySupported ? 'one_way' : value.mode;
            const oneWayBox = popover.querySelector('.lang-popover-oneway');
            const twoWayBox = popover.querySelector('.lang-popover-twoway');
            if (oneWayBox) oneWayBox.hidden = mode !== 'one_way';
            if (twoWayBox) twoWayBox.hidden = mode !== 'two_way';
            const target = popover.querySelector('.lang-picker[data-role="target"]');
            if (target) target.value = coerceSupportedLanguageCode(value.targetLang, state.defaultTranslationTargetLang);
            const first = popover.querySelector('.lang-picker[data-role="langA"]');
            if (first) first.value = coerceSupportedLanguageCode(value.targetLang1, 'en');
            const second = popover.querySelector('.lang-picker[data-role="langB"]');
            if (second) second.value = coerceSupportedLanguageCode(value.targetLang2, 'zh');
        }

        function ensurePopover() {
            if (popover) return popover;
            const state = getState();
            popover = documentRef.createElement('div');
            popover.className = 'lang-popover';
            popover.style.display = 'none';
            const title = documentRef.createElement('h2');
            title.className = 'lang-popover-title';
            title.textContent = t('translation_panel_title');
            popover.appendChild(title);
            const modeControl = documentRef.createElement('div');
            modeControl.className = 'segmented-control translation-mode-control';
            [['none', 'translate_mode_none'], ['one_way', 'translate_mode_one_way'], ['two_way', 'translate_mode_two_way']]
                .forEach(([mode, labelKey]) => {
                    const option = documentRef.createElement('button');
                    option.type = 'button';
                    option.className = 'segmented-option';
                    option.dataset.mode = mode;
                    const label = documentRef.createElement('span');
                    label.textContent = t(labelKey);
                    option.appendChild(label);
                    option.addEventListener('click', () => selectMode(mode));
                    modeControl.appendChild(option);
                });
            popover.appendChild(modeControl);
            const oneWayBox = documentRef.createElement('div');
            oneWayBox.className = 'lang-popover-oneway';
            const targetField = documentRef.createElement('label');
            targetField.className = 'lang-twoway-field';
            const targetLabel = documentRef.createElement('span');
            targetLabel.textContent = t('target_language');
            const target = buildPicker(currentDraft().targetLang, state.defaultTranslationTargetLang);
            target.dataset.role = 'target';
            target.addEventListener('change', () => { currentDraft().targetLang = target.value; });
            targetField.append(targetLabel, target);
            oneWayBox.appendChild(targetField);
            popover.appendChild(oneWayBox);
            const twoWayBox = documentRef.createElement('div');
            twoWayBox.className = 'lang-popover-twoway';
            const makeField = (labelKey, role, fallback, key) => {
                const field = documentRef.createElement('label');
                field.className = 'lang-twoway-field';
                const label = documentRef.createElement('span');
                label.textContent = t(labelKey);
                const picker = buildPicker(currentDraft()[key], fallback);
                picker.dataset.role = role;
                picker.addEventListener('change', () => { currentDraft()[key] = picker.value; });
                field.append(label, picker);
                return field;
            };
            twoWayBox.append(
                makeField('language_a', 'langA', 'en', 'targetLang1'),
                makeField('language_b', 'langB', 'zh', 'targetLang2'),
            );
            popover.appendChild(twoWayBox);
            const actions = documentRef.createElement('div');
            actions.className = 'lang-popover-actions';
            const cancel = documentRef.createElement('button');
            cancel.type = 'button';
            cancel.className = 'secondary-button';
            cancel.textContent = t('cancel');
            cancel.addEventListener('click', hide);
            const apply = documentRef.createElement('button');
            apply.type = 'button';
            apply.className = 'primary-button';
            apply.textContent = t('apply');
            apply.addEventListener('click', applyDraft);
            actions.append(cancel, apply);
            popover.appendChild(actions);
            documentRef.body.appendChild(popover);
            return popover;
        }

        function updateSelection() {
            if (!popover) return;
            for (const modeButton of popover.querySelectorAll('.translation-mode-control .segmented-option')) {
                const selected = modeButton.dataset.mode === currentDraft().mode;
                modeButton.classList.toggle('selected', selected);
                modeButton.setAttribute('aria-pressed', selected ? 'true' : 'false');
            }
            refreshSections();
        }

        function applyDraft() {
            const value = currentDraft();
            const state = getState();
            const nextMode = value.mode === 'two_way' && !state.twoWaySupported ? 'one_way' : value.mode;
            if (nextMode === 'none') {
                const changed = state.uiTranslationMode !== 'none' || state.suppressTranslationDisplay;
                options.setUiTranslationMode('none');
                hide();
                if (state.translationProvider === 'gemini') options.renderSubtitles();
                else if (changed) void options.restartRecognition({ translationMode: 'none' });
                return true;
            }
            if (nextMode === 'two_way') {
                const first = coerceSupportedLanguageCode(value.targetLang1, 'en');
                const second = coerceSupportedLanguageCode(value.targetLang2, 'zh');
                if (!first || !second || first === second) return false;
                const changed = state.uiTranslationMode !== 'two_way'
                    || state.suppressTranslationDisplay
                    || first !== state.backendTargetLang1
                    || second !== state.backendTargetLang2;
                updateState({ backendTargetLang1: first, backendTargetLang2: second });
                options.setUiTranslationMode('two_way');
                hide();
                if (changed) void options.restartRecognition({
                    translationMode: 'two_way', targetLang1: first, targetLang2: second,
                });
                return true;
            }
            const selected = coerceSupportedLanguageCode(value.targetLang, state.defaultTranslationTargetLang);
            const changed = state.uiTranslationMode !== 'one_way'
                || state.suppressTranslationDisplay
                || selected !== state.currentTranslationTargetLang;
            updateState({ currentTranslationTargetLang: selected });
            options.setUiTranslationMode('one_way');
            hide();
            if (changed) void options.restartRecognition({ translationMode: 'one_way', targetLang: selected });
            return true;
        }

        function show() {
            if (!button) return false;
            if (popoverOpen) return true;
            const element = ensurePopover();
            draft = createDraft();
            updateSelection();
            element.style.display = 'block';
            popoverOpen = true;
            const onMouseDown = (event) => {
                const target = event.target;
                if (!target || (popover && popover.contains(target))
                    || (selectMenu && selectMenu.contains(target)) || button.contains(target)) return;
                hide();
            };
            const onKeyDown = (event) => {
                if (event.key !== 'Escape') return;
                if (selectMenu && !selectMenu.hidden) closeSelectMenu();
                else hide();
            };
            documentRef.addEventListener('mousedown', onMouseDown, true);
            documentRef.addEventListener('keydown', onKeyDown, true);
            popoverCleanup = () => {
                documentRef.removeEventListener('mousedown', onMouseDown, true);
                documentRef.removeEventListener('keydown', onKeyDown, true);
            };
            return true;
        }

        function hide() {
            if (!popoverOpen) return false;
            popoverOpen = false;
            if (popover) popover.style.display = 'none';
            closeSelectMenu();
            draft = null;
            if (popoverCleanup) popoverCleanup();
            popoverCleanup = null;
            return true;
        }

        function init() {
            if (initialized || !button) return false;
            initialized = true;
            buttonHandler = () => {
                if (getState().lockManualControls) return;
                if (popoverOpen) hide();
                else show();
            };
            button.addEventListener('click', buttonHandler);
            return true;
        }

        function invalidate() {
            hide();
            closeSelectMenu();
            if (popover) popover.remove();
            if (selectMenu) selectMenu.remove();
            popover = null;
            selectMenu = null;
            draft = null;
        }

        function destroy() {
            invalidate();
            if (button && buttonHandler) button.removeEventListener('click', buttonHandler);
            buttonHandler = null;
            initialized = false;
        }

        function setDraft(value) {
            draft = { ...currentDraft(), ...value };
            if (popover) updateSelection();
        }

        return {
            init,
            destroy,
            invalidate,
            show,
            hide,
            isOpen: () => popoverOpen,
            closeSelectMenu,
            buildPicker,
            getLanguageDisplayName,
            coerceSupportedLanguageCode,
            getFavoriteLanguages,
            toggleFavoriteLanguage,
            createDraft,
            getDraft: currentDraft,
            setDraft,
            applyDraft,
        };
    }

    const api = { create, normalizeTranslationMode };
    root.LanguageUI = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
