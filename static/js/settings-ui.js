(function (root) {
    'use strict';

    const ALL_THEMES = ['dark', 'light', 'chroma'];
    const THEME_ICONS = { dark: 'moon', light: 'sun', chroma: 'sparkles' };

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const windowRef = options.window || root;
        const storage = options.storage || root.localStorage;
        const actions = options.actions || {};
        const listeners = [];
        let initialized = false;

        function getAvailableThemes(enableChromaTheme = false) {
            return enableChromaTheme ? [...ALL_THEMES] : ['dark', 'light'];
        }

        function applyTheme(theme, applyOptions = {}) {
            const available = getAvailableThemes(!!applyOptions.enableChromaTheme);
            const normalized = available.includes(theme) ? theme : 'dark';
            documentRef.body.classList.remove('dark-theme', 'chroma-theme');
            if (normalized === 'dark') documentRef.body.classList.add('dark-theme');
            if (normalized === 'chroma') documentRef.body.classList.add('chroma-theme');
            if (applyOptions.themeIcon && typeof applyOptions.setControlIcon === 'function') {
                applyOptions.setControlIcon(applyOptions.themeIcon, THEME_ICONS[normalized]);
            }
            if (applyOptions.persist !== false) {
                try { storage.setItem('theme', normalized); } catch (error) { /* ignore */ }
            }
            if (typeof applyOptions.onApplied === 'function') applyOptions.onApplied(normalized);
            return normalized;
        }

        function setPanelOpen(panel, overlay, open) {
            if (panel) {
                panel.hidden = !open;
                panel.setAttribute('aria-hidden', open ? 'false' : 'true');
            }
            if (overlay) overlay.hidden = !open;
        }

        function positionDropdownMenu(trigger, menu) {
            const rect = trigger.getBoundingClientRect();
            const gap = 6;
            const viewportPadding = 8;
            const menuWidth = Math.max(rect.width, 180);
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

        function buildCustomSelect(selectOptions, { value = null, onChange = null, disabled = false } = {}) {
            const picker = documentRef.createElement('div');
            picker.className = 'lang-picker';
            let currentValue = value;
            let menu = null;
            const trigger = documentRef.createElement('button');
            trigger.type = 'button';
            trigger.className = 'lang-picker-button';
            trigger.setAttribute('aria-haspopup', 'listbox');
            trigger.setAttribute('aria-expanded', 'false');
            if (disabled) {
                trigger.disabled = true;
                picker.classList.add('disabled');
            }
            const label = documentRef.createElement('span');
            label.className = 'lang-picker-label';
            const chevron = documentRef.createElement('span');
            chevron.className = 'lang-picker-chevron';
            chevron.setAttribute('aria-hidden', 'true');
            trigger.append(label, chevron);
            picker.appendChild(trigger);

            const renderLabel = () => {
                const match = selectOptions.find((entry) => entry.value === currentValue);
                label.textContent = match ? match.label : '';
            };
            const reposition = () => { if (menu) positionDropdownMenu(trigger, menu); };
            const onScroll = (event) => {
                if (menu && event.target && menu.contains(event.target)) return;
                const target = event.target;
                if (
                    target === windowRef || target === documentRef || target === documentRef.body
                    || target === documentRef.documentElement
                    || (target && typeof target.contains === 'function' && target.contains(picker))
                ) reposition();
            };
            const onMouseDown = (event) => {
                if (!picker.contains(event.target) && !(menu && menu.contains(event.target))) close();
            };
            const onKeyDown = (event) => { if (event.key === 'Escape') close(); };
            const close = () => {
                if (!menu) return;
                menu.remove();
                menu = null;
                picker.classList.remove('open');
                trigger.setAttribute('aria-expanded', 'false');
                documentRef.removeEventListener('mousedown', onMouseDown, true);
                documentRef.removeEventListener('keydown', onKeyDown, true);
                windowRef.removeEventListener('resize', reposition, true);
                windowRef.removeEventListener('scroll', onScroll, true);
            };
            const open = () => {
                if (menu || disabled) return;
                menu = documentRef.createElement('div');
                menu.className = 'lang-select-menu';
                menu.setAttribute('role', 'listbox');
                for (const entry of selectOptions) {
                    const option = documentRef.createElement('button');
                    option.type = 'button';
                    option.className = 'lang-select-option';
                    option.setAttribute('role', 'option');
                    option.textContent = entry.label;
                    const selected = entry.value === currentValue;
                    option.classList.toggle('selected', selected);
                    option.setAttribute('aria-selected', selected ? 'true' : 'false');
                    option.addEventListener('click', () => {
                        const changed = currentValue !== entry.value;
                        currentValue = entry.value;
                        renderLabel();
                        close();
                        if (changed && typeof onChange === 'function') onChange(currentValue);
                    });
                    menu.appendChild(option);
                }
                documentRef.body.appendChild(menu);
                picker.classList.add('open');
                trigger.setAttribute('aria-expanded', 'true');
                positionDropdownMenu(trigger, menu);
                const selected = menu.querySelector('.lang-select-option.selected');
                if (selected && typeof selected.scrollIntoView === 'function') {
                    selected.scrollIntoView({ block: 'nearest' });
                }
                documentRef.addEventListener('mousedown', onMouseDown, true);
                documentRef.addEventListener('keydown', onKeyDown, true);
                windowRef.addEventListener('resize', reposition, true);
                windowRef.addEventListener('scroll', onScroll, true);
            };
            trigger.addEventListener('click', () => { if (menu) close(); else open(); });
            Object.defineProperty(picker, 'value', {
                get: () => currentValue,
                set: (next) => { currentValue = next; renderLabel(); },
            });
            picker.close = close;
            renderLabel();
            return picker;
        }

        function bind(target, event, handler) {
            if (!target || typeof handler !== 'function') return;
            target.addEventListener(event, handler);
            listeners.push([target, event, handler]);
        }

        function init(elements = {}) {
            if (initialized) return false;
            initialized = true;
            bind(elements.settingsButton, 'click', actions.openSettings);
            bind(elements.closeButton, 'click', actions.closeSettings);
            bind(elements.cancelButton, 'click', actions.closeSettings);
            bind(elements.backButton, 'click', actions.returnToModeChooser);
            bind(elements.resetButton, 'click', actions.handleResetAll);
            bind(elements.overlay, 'click', actions.closeSettings);
            bind(elements.form, 'submit', actions.handleSettingsSave);
            return true;
        }

        function destroy() {
            while (listeners.length) {
                const [target, event, handler] = listeners.pop();
                target.removeEventListener(event, handler);
            }
            initialized = false;
        }

        return {
            getAvailableThemes,
            applyTheme,
            setPanelOpen,
            positionDropdownMenu,
            buildCustomSelect,
            init,
            destroy,
        };
    }

    const api = { ALL_THEMES, THEME_ICONS, create };
    root.SettingsUI = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
