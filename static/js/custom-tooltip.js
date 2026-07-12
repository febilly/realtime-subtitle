(function (root) {
    'use strict';

    function create(options = {}) {
        const windowRef = options.window || root;
        const documentRef = options.document || windowRef.document;
        const ElementCtor = options.Element || windowRef.Element;
        const HTMLElementCtor = options.HTMLElement || windowRef.HTMLElement;
        const setTimeoutRef = options.setTimeout || windowRef.setTimeout.bind(windowRef);
        const clearTimeoutRef = options.clearTimeout || windowRef.clearTimeout.bind(windowRef);
        let originalSetAttribute = null;
        let originalGetAttribute = null;
        let originalRemoveAttribute = null;
        let originalTitleDescriptor = null;
        let tooltipElement = null;
        let activeTarget = null;
        const clickTimers = new Set();
        let initialized = false;

        function createTooltip() {
            tooltipElement = documentRef.createElement('div');
            tooltipElement.className = 'custom-tooltip';
            documentRef.body.appendChild(tooltipElement);
        }

        function positionTooltip(target) {
            if (!tooltipElement) return;
            const rect = target.getBoundingClientRect();
            const margin = 8;
            const gap = 8;
            const maxTooltipWidth = 350;
            tooltipElement.style.left = '0px';
            tooltipElement.style.top = '0px';
            tooltipElement.style.maxWidth = '';

            const leftSpace = Math.max(0, rect.left - margin - gap);
            const rightSpace = Math.max(0, windowRef.innerWidth - rect.right - margin - gap);
            const placeLeft = leftSpace >= rightSpace;
            const availableWidth = placeLeft ? leftSpace : rightSpace;
            tooltipElement.style.maxWidth = `${Math.max(
                1,
                Math.min(maxTooltipWidth, availableWidth),
            )}px`;

            let left;
            if (placeLeft) {
                left = rect.left - tooltipElement.offsetWidth - gap;
                if (left < margin) left = margin;
            } else {
                left = rect.right + gap;
                if (left + tooltipElement.offsetWidth > windowRef.innerWidth - margin) {
                    left = windowRef.innerWidth - tooltipElement.offsetWidth - margin;
                }
            }

            const tooltipHeight = tooltipElement.offsetHeight;
            let top = rect.top + (rect.height - tooltipHeight) / 2;
            if (top < margin) top = margin;
            if (top + tooltipHeight > windowRef.innerHeight - margin) {
                top = windowRef.innerHeight - tooltipHeight - margin;
            }
            tooltipElement.style.left = `${left}px`;
            tooltipElement.style.top = `${top}px`;
        }

        function hideTooltip() {
            if (tooltipElement) tooltipElement.classList.remove('visible');
            activeTarget = null;
        }

        function updateTooltipText(text) {
            if (!tooltipElement) createTooltip();
            if (!text) {
                hideTooltip();
                return;
            }
            tooltipElement.textContent = text;
            if (activeTarget) positionTooltip(activeTarget);
        }

        function showTooltip(target) {
            const text = target.getAttribute('data-custom-title');
            if (!text) return;
            if (!tooltipElement) createTooltip();
            tooltipElement.textContent = text;
            tooltipElement.classList.add('visible');
            positionTooltip(target);
        }

        function handleMouseOver(event) {
            const target = event.target.closest('[data-custom-title]');
            if (target) {
                if (activeTarget === target) return;
                activeTarget = target;
                showTooltip(target);
            } else if (activeTarget) {
                hideTooltip();
            }
        }

        function handleMouseOut(event) {
            if (activeTarget && !activeTarget.contains(event.relatedTarget)) hideTooltip();
        }

        function handleClick(event) {
            if (!activeTarget || !activeTarget.contains(event.target)) return;
            const target = activeTarget;
            const timer = setTimeoutRef(() => {
                clickTimers.delete(timer);
                if (activeTarget !== target) return;
                const rect = target.getBoundingClientRect();
                const visible = rect.width > 0
                    && rect.height > 0
                    && documentRef.body.contains(target);
                const text = target.getAttribute('data-custom-title');
                if (visible && text) {
                    updateTooltipText(text);
                } else {
                    hideTooltip();
                }
            }, 0);
            clickTimers.add(timer);
        }

        function installTitleProperty() {
            Object.defineProperty(HTMLElementCtor.prototype, 'title', {
                configurable: true,
                enumerable: originalTitleDescriptor ? originalTitleDescriptor.enumerable : true,
                get() {
                    return this.getAttribute('data-custom-title') || '';
                },
                set(value) {
                    if (value) {
                        this.setAttribute('data-custom-title', value);
                        originalRemoveAttribute.call(this, 'title');
                    } else {
                        this.removeAttribute('data-custom-title');
                        originalRemoveAttribute.call(this, 'title');
                    }
                    if (activeTarget === this) updateTooltipText(value);
                },
            });
        }

        function installAttributeHooks() {
            ElementCtor.prototype.setAttribute = function setAttribute(name, value) {
                if (name && name.toLowerCase() === 'title') {
                    this.setAttribute('data-custom-title', value);
                    originalRemoveAttribute.call(this, 'title');
                    if (activeTarget === this) updateTooltipText(value);
                } else {
                    originalSetAttribute.call(this, name, value);
                }
            };
            ElementCtor.prototype.getAttribute = function getAttribute(name) {
                if (name && name.toLowerCase() === 'title') {
                    return this.getAttribute('data-custom-title') || '';
                }
                return originalGetAttribute.call(this, name);
            };
            ElementCtor.prototype.removeAttribute = function removeAttribute(name) {
                if (name && name.toLowerCase() === 'title') {
                    this.removeAttribute('data-custom-title');
                    originalRemoveAttribute.call(this, 'title');
                    if (activeTarget === this) hideTooltip();
                } else {
                    originalRemoveAttribute.call(this, name);
                }
            };
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            originalSetAttribute = ElementCtor.prototype.setAttribute;
            originalGetAttribute = ElementCtor.prototype.getAttribute;
            originalRemoveAttribute = ElementCtor.prototype.removeAttribute;
            originalTitleDescriptor = Object.getOwnPropertyDescriptor(
                HTMLElementCtor.prototype,
                'title',
            );
            documentRef.querySelectorAll('[title]').forEach((element) => {
                const value = element.getAttribute('title');
                if (value) {
                    element.setAttribute('data-custom-title', value);
                    element.removeAttribute('title');
                }
            });
            installTitleProperty();
            installAttributeHooks();
            documentRef.addEventListener('mouseover', handleMouseOver, { passive: true });
            documentRef.addEventListener('mouseout', handleMouseOut, { passive: true });
            documentRef.addEventListener('click', handleClick, { passive: true });
            return true;
        }

        function destroy() {
            if (!initialized) return false;
            documentRef.removeEventListener('mouseover', handleMouseOver);
            documentRef.removeEventListener('mouseout', handleMouseOut);
            documentRef.removeEventListener('click', handleClick);
            for (const timer of clickTimers) clearTimeoutRef(timer);
            clickTimers.clear();
            ElementCtor.prototype.setAttribute = originalSetAttribute;
            ElementCtor.prototype.getAttribute = originalGetAttribute;
            ElementCtor.prototype.removeAttribute = originalRemoveAttribute;
            if (originalTitleDescriptor) {
                Object.defineProperty(HTMLElementCtor.prototype, 'title', originalTitleDescriptor);
            } else {
                delete HTMLElementCtor.prototype.title;
            }
            if (tooltipElement) tooltipElement.remove();
            tooltipElement = null;
            activeTarget = null;
            initialized = false;
            return true;
        }

        function getDebugState() {
            return {
                initialized,
                activeTarget,
                tooltipElement,
            };
        }

        return {
            destroy,
            getDebugState,
            hideTooltip,
            init,
            positionTooltip,
            showTooltip,
            updateTooltipText,
        };
    }

    const api = { create };
    root.CustomTooltip = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
