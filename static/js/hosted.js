(function (root) {
    'use strict';

    function formatRate(value) {
        const number = Number(value);
        if (!Number.isFinite(number)) return '—';
        if (number === 0) return '0';
        if (number < 1) return parseFloat(number.toPrecision(2)).toString();
        return (Math.round(number * 100) / 100).toString();
    }

    function versionParts(version) {
        const raw = String(version || '').trim().replace(/^v/i, '');
        if (!raw) return null;
        const parts = raw.split(/[.+_-]/).map((part) => {
            const match = part.match(/^\d+/);
            return match ? Number(match[0]) : 0;
        });
        if (!parts.length || (parts.every((part) => part === 0) && !/^0(?:[.+_-]0)*$/.test(raw))) {
            return null;
        }
        while (parts.length < 3) parts.push(0);
        return parts;
    }

    function compareVersions(leftVersion, rightVersion) {
        const left = versionParts(leftVersion);
        const right = versionParts(rightVersion);
        if (!left || !right) return 0;
        const length = Math.max(left.length, right.length);
        for (let index = 0; index < length; index += 1) {
            const difference = (left[index] || 0) - (right[index] || 0);
            if (difference !== 0) return difference < 0 ? -1 : 1;
        }
        return 0;
    }

    function formatCredits(value) {
        if (value === null || value === undefined) return '—';
        const number = Number(value);
        if (!Number.isFinite(number)) return '—';
        return (Math.round(number * 100) / 100).toString();
    }

    function hasPositiveCredits(value) {
        return Number(value || 0) > 0;
    }

    function hasUsableFreePool(free) {
        const pools = free && Array.isArray(free.pools) ? free.pools : [];
        return pools.some((pool) => pool.unlimited || Number(pool.remaining || 0) > 0);
    }

    function hasUsableSubscription(subscriptions) {
        return Array.isArray(subscriptions)
            && subscriptions.some((subscription) => Number(subscription.remaining_credits || 0) > 0);
    }

    function isAccountExhausted(data) {
        if (!data) return false;
        return !hasPositiveCredits(data.prepaid_balance)
            && !hasUsableFreePool(data.free)
            && !hasUsableSubscription(data.subscriptions);
    }

    function balanceTotalRemaining(data) {
        if (!data) return 0;
        let total = Math.max(0, Number(data.prepaid_balance || 0));
        const pools = data.free && Array.isArray(data.free.pools) ? data.free.pools : [];
        for (const pool of pools) {
            if (!pool.unlimited) total += Math.max(0, Number(pool.remaining || 0));
        }
        return total;
    }

    function hasAtLeastCredits(data, minimum) {
        if (!data) return false;
        const required = Math.max(0, Number(minimum) || 0);
        const pools = data.free && Array.isArray(data.free.pools) ? data.free.pools : [];
        if (pools.some((pool) => pool.unlimited)) return true;
        let total = balanceTotalRemaining(data);
        const subscriptions = Array.isArray(data.subscriptions) ? data.subscriptions : [];
        for (const subscription of subscriptions) {
            total += Math.max(0, Number(subscription.remaining_credits || 0));
        }
        return total >= required;
    }

    /**
     * Whether the account has less than `seconds` of listening left at the
     * given rate. Measured in time rather than credits so the threshold means
     * the same thing across providers, whose per-second rates differ several
     * fold. An unlimited free pool is never low.
     */
    function isBalanceLow(data, pricePerSecond, seconds) {
        if (!data) return false;
        const rate = Number(pricePerSecond);
        if (!Number.isFinite(rate) || rate <= 0) return false;
        return !hasAtLeastCredits(data, rate * Math.max(0, Number(seconds) || 0));
    }

    function sttRateMultiplier({
        translationProvider,
        uiTranslationMode,
        translationUiMode,
        sonioxNoTranslationFactor,
    } = {}) {
        if (
            translationProvider === 'soniox'
            && (uiTranslationMode === 'none' || translationUiMode === 'accurate')
        ) {
            const factor = Number(sonioxNoTranslationFactor);
            if (Number.isFinite(factor) && factor > 0) return factor;
        }
        return 1;
    }

    function effectivePricePerSecond(pricePerSecond, multiplier = 1) {
        return Number(pricePerSecond) * Number(multiplier);
    }

    function estimatedSessionCost(elapsedMs, pricePerSecond, multiplier = 1) {
        const price = effectivePricePerSecond(pricePerSecond, multiplier);
        if (!Number.isFinite(price) || price <= 0) return 0;
        return Math.max(0, Math.round(Number(elapsedMs || 0) / 1000) * price);
    }

    function applyEstimatedDeduction(data, estimatedCost) {
        if (!data) return data;
        let remaining = Math.max(0, Number(estimatedCost) || 0);
        const output = Object.assign({}, data);
        if (data.free && Array.isArray(data.free.pools)) {
            const pools = data.free.pools.map((pool) => Object.assign({}, pool));
            for (const pool of pools) {
                if (remaining <= 0) break;
                if (pool.unlimited) {
                    remaining = 0;
                    break;
                }
                const available = Math.max(0, Number(pool.remaining || 0));
                const taken = Math.min(available, remaining);
                if (taken > 0) {
                    pool.remaining = available - taken;
                    remaining -= taken;
                }
            }
            output.free = Object.assign({}, data.free, { pools });
        }
        if (remaining > 0) {
            const prepaid = Math.max(0, Number(data.prepaid_balance || 0));
            output.prepaid_balance = Math.max(0, prepaid - remaining);
        }
        return output;
    }

    function currentBalanceView({
        balanceBaseline,
        lastBalanceData,
        estimatedCost = 0,
        sessionLlmCost = 0,
    } = {}) {
        const base = balanceBaseline || lastBalanceData;
        if (!base) return null;
        const view = applyEstimatedDeduction(base, estimatedCost);
        if (view && sessionLlmCost > 0) {
            const prepaid = Math.max(0, Number(view.prepaid_balance || 0));
            view.prepaid_balance = Math.max(0, prepaid - sessionLlmCost);
        }
        return view;
    }

    function formatSessionCost(cost, pricePerSecond) {
        const price = Number(pricePerSecond);
        if (!Number.isFinite(price) || price <= 0) return formatCredits(cost);
        const roundedCost = Math.round(cost / price) * price;
        const priceString = price.toString();
        const dotIndex = priceString.indexOf('.');
        const decimals = dotIndex >= 0 ? priceString.length - dotIndex - 1 : 0;
        return Number(roundedCost.toFixed(Math.max(decimals, 0))).toString();
    }

    function createController(actions = {}) {
        const call = (name, ...args) => (typeof actions[name] === 'function'
            ? actions[name](...args)
            : undefined);

        async function startup() {
            call('preopenHostedLoginIfNeeded');
            await call('fetchUiConfig');
            call('refreshPreopenedHostedLogin');
            await call('maybeRunFirstLaunchFlow');
            await call('ensureHostedVersionAllowed');
            call('refreshPreopenedHostedLogin');
            await call('syncProviderFromStorage');
            await call('fetchLlmRefineStatus');
            call('fetchApiKeyStatus');
            call('fetchOscTranslationStatus');
            call('maybeForceOpenSettings');
            call('updateBalanceBarVisibility');
            await call('maybeShowInviteReminder');
            void call('startTicketNotifications');
            call('connect');
        }

        function handleSessionFrame(frame) {
            const type = frame && frame.type;
            if (type === 'recognition_paused') {
                if (frame.paused) call('sessionCostPause');
                return true;
            }
            if (type === 'session_connected') {
                if (!call('isPaused')) call('sessionCostResume');
                return true;
            }
            if (type === 'session_idle' || type === 'session_disconnected') {
                call('sessionCostPause');
                return true;
            }
            return false;
        }

        return { startup, handleSessionFrame };
    }

    const Billing = {
        formatRate,
        versionParts,
        compareVersions,
        formatCredits,
        hasPositiveCredits,
        hasUsableFreePool,
        hasUsableSubscription,
        isAccountExhausted,
        isBalanceLow,
        balanceTotalRemaining,
        hasAtLeastCredits,
        sttRateMultiplier,
        effectivePricePerSecond,
        estimatedSessionCost,
        applyEstimatedDeduction,
        currentBalanceView,
        formatSessionCost,
    };
    const api = { Billing, createController };
    root.Hosted = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
