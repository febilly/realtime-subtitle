(function (root) {
    'use strict';

    const SPRITE_URL = 'icons/lucide-sprite.svg';

    function set(iconElement, iconName) {
        if (!iconElement || !iconName) return false;
        const useElement = iconElement.querySelector('use');
        if (!useElement) return false;
        const href = `${SPRITE_URL}#${iconName}`;
        useElement.setAttribute('href', href);
        useElement.setAttributeNS('http://www.w3.org/1999/xlink', 'href', href);
        return true;
    }

    const api = { SPRITE_URL, set };
    root.ControlIcon = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
