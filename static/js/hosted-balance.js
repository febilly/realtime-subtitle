(function (root) {
    'use strict';

    const ACTIVE_POLL_MS = 45 * 1000;
    const IDLE_POLL_MS = 5 * 60 * 1000;
    // Offer a top-up while there is still enough left to finish a conversation.
    // Waiting for the balance to hit zero only tells the user once the session
    // has already been cut off.
    const LOW_BALANCE_SECONDS = 10 * 60;

    function create(options = {}) {
        const Billing = options.Billing || (root.Hosted && root.Hosted.Billing);
        if (!Billing) throw new TypeError('HostedBalance.create requires Hosted.Billing');
        const documentRef = options.document || root.document;
        const fetchRef = typeof options.fetch === 'function'
            ? options.fetch
            : (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const now = typeof options.now === 'function' ? options.now : () => Date.now();
        const setIntervalRef = options.setInterval || root.setInterval.bind(root);
        const clearIntervalRef = options.clearInterval || root.clearInterval.bind(root);
        const getRuntimeState = typeof options.getRuntimeState === 'function'
            ? options.getRuntimeState
            : () => ({});
        const onAccountSectionChanged = typeof options.onAccountSectionChanged === 'function'
            ? options.onAccountSectionChanged
            : () => {};
        const onAccountBalanceChanged = typeof options.onAccountBalanceChanged === 'function'
            ? options.onAccountBalanceChanged
            : () => {};
        const onOpenSettings = typeof options.onOpenSettings === 'function'
            ? options.onOpenSettings
            : () => {};
        const onPurchaseCredits = typeof options.onPurchaseCredits === 'function'
            ? options.onPurchaseCredits
            : null;
        const elements = options.elements || {};
        const balanceBar = elements.balanceBar || null;
        const balanceActionItem = elements.balanceActionItem || null;
        const balanceOpenSettingsButton = elements.balanceOpenSettingsButton || null;

        let balancePollTimer = null;
        let balancePollIntervalMs = 0;
        let sessionCostTimer = null;
        let sessionAccumMs = 0;
        let sessionRunSince = null;
        let pricePerSecond = 0;
        let lastBalanceData = null;
        let balanceBaseline = null;
        let balanceBaselineSttCost = 0;
        let balanceBaselineLlmCost = 0;
        let sessionLlmCost = 0;
        let sessionHadLlmCost = false;
        let firstRedeemBonusCredits = 0;
        let firstRedeemBonusEligible = false;
        let initialized = false;

        function runtimeState() {
            const value = getRuntimeState();
            return value && typeof value === 'object' ? value : {};
        }

        function setText(id, value) {
            const element = documentRef && documentRef.getElementById(id);
            if (element) element.textContent = value;
        }

        function formatCredits(value) {
            return Billing.formatCredits(value);
        }

        function balanceBarShouldShow() {
            const current = runtimeState();
            return current.connectionMode === 'relay'
                && (!!current.backendLoggedIn || !!current.hasToken);
        }

        function balanceIsMetering() {
            return sessionRunSince != null;
        }

        function desiredBalancePollMs() {
            return balanceIsMetering() ? ACTIVE_POLL_MS : IDLE_POLL_MS;
        }

        function startBalancePolling({ immediate = true } = {}) {
            if (immediate) void fetchBalance();
            scheduleBalancePolling();
        }

        function scheduleBalancePolling() {
            if (!balanceBarShouldShow()) {
                stopBalancePolling();
                return;
            }
            const desired = desiredBalancePollMs();
            if (balancePollTimer && balancePollIntervalMs === desired) return;
            if (balancePollTimer) clearIntervalRef(balancePollTimer);
            balancePollIntervalMs = desired;
            balancePollTimer = setIntervalRef(fetchBalance, desired);
        }

        function stopBalancePolling() {
            if (balancePollTimer) {
                clearIntervalRef(balancePollTimer);
                balancePollTimer = null;
            }
            balancePollIntervalMs = 0;
        }

        function updateBalanceBarVisibility() {
            if (!balanceBar) return;
            if (balanceBarShouldShow()) {
                balanceBar.hidden = false;
                startBalancePolling();
            } else {
                balanceBar.hidden = true;
                stopBalancePolling();
            }
        }

        function freePoolLabel(period) {
            if (period === 'weekly') return t('balance_free_week');
            if (period === 'monthly') return t('balance_free_month');
            return t('balance_free_day');
        }

        function freePoolPeriodShort(period) {
            if (period === 'weekly') return t('free_period_week');
            if (period === 'monthly') return t('free_period_month');
            return t('free_period_day');
        }

        function freePoolsSummary(pools) {
            if (!Array.isArray(pools) || !pools.length) return '';
            return pools
                .map((pool) => `${freePoolPeriodShort(pool.period)} ${pool.unlimited
                    ? t('balance_free_unlimited')
                    : formatCredits(pool.max_credits)}`)
                .join(' / ');
        }

        function freePoolValue(pool) {
            if (pool.unlimited) return t('balance_free_unlimited');
            return t('balance_free_remaining', {
                remaining: formatCredits(pool.remaining),
                cap: formatCredits(pool.max_credits),
            });
        }

        function renderFreePools(container, pools) {
            if (!container) return;
            container.innerHTML = '';
            if (!Array.isArray(pools) || !pools.length) return;
            for (const pool of pools) {
                const item = documentRef.createElement('span');
                item.className = 'balance-item';
                const label = documentRef.createElement('span');
                label.className = 'balance-label';
                label.textContent = freePoolLabel(pool.period);
                const value = documentRef.createElement('span');
                value.className = 'balance-value';
                value.textContent = freePoolValue(pool);
                item.append(label, value);
                container.appendChild(item);
            }
        }

        function subscriptionPoolLabel(period) {
            if (period === 'weekly') return t('balance_subscription_week');
            if (period === 'monthly') return t('balance_subscription_month');
            return t('balance_subscription_day');
        }

        function subscriptionPoolValue(pool) {
            if (Billing.subscriptionIsUnlimited(pool)) return t('balance_free_unlimited');
            return t('balance_free_remaining', {
                remaining: formatCredits(pool.remaining_credits),
                cap: formatCredits(pool.quota_credits),
            });
        }

        function formatExpiry(value) {
            if (!value) return '';
            const parsed = new Date(value);
            if (Number.isNaN(parsed.getTime())) return '';
            return parsed.toLocaleDateString();
        }

        /**
         * Unlike the free pools this renders nothing when there is no
         * subscription: a missing free allowance is worth telling the user
         * about, not having bought a plan is not.
         */
        function renderSubscriptionPools(container, pools, { detailed = false } = {}) {
            if (!container) return;
            container.innerHTML = '';
            if (!Array.isArray(pools) || !pools.length) return;
            for (const pool of pools) {
                const item = documentRef.createElement('span');
                item.className = 'balance-item';
                const label = documentRef.createElement('span');
                label.className = 'balance-label';
                label.textContent = detailed && pool.plan_name
                    ? `${pool.plan_name} · ${subscriptionPoolLabel(pool.period)}`
                    : subscriptionPoolLabel(pool.period);
                const value = documentRef.createElement('span');
                value.className = 'balance-value';
                const expiry = detailed ? formatExpiry(pool.expires_at) : '';
                value.textContent = expiry
                    ? `${subscriptionPoolValue(pool)} (${t('balance_subscription_expires', { date: expiry })})`
                    : subscriptionPoolValue(pool);
                item.append(label, value);
                container.appendChild(item);
            }
        }

        function sttRateMultiplier() {
            const current = runtimeState();
            return Billing.sttRateMultiplier({
                translationProvider: current.translationProvider,
                uiTranslationMode: current.uiTranslationMode,
                translationUiMode: current.translationUiMode,
                sonioxNoTranslationFactor: current.sonioxNoTranslationFactor,
            });
        }

        function effectivePricePerSecond() {
            return Billing.effectivePricePerSecond(pricePerSecond, sttRateMultiplier());
        }

        function sessionElapsedMs() {
            let total = sessionAccumMs;
            if (sessionRunSince != null) total += now() - sessionRunSince;
            return total;
        }

        function estimatedSessionCost() {
            return Billing.estimatedSessionCost(
                sessionElapsedMs(),
                pricePerSecond,
                sttRateMultiplier(),
            );
        }

        /**
         * Checked against the live view so it flips mid-session, not only on
         * poll. Without a known rate there is no way to price the remaining
         * seconds, so fall back to the plain "nothing left" test.
         */
        function balanceIsLow() {
            const view = currentBalanceView();
            const rate = effectivePricePerSecond();
            if (!Number.isFinite(rate) || rate <= 0) return Billing.isAccountExhausted(view);
            return Billing.isBalanceLow(view, rate, LOW_BALANCE_SECONDS);
        }

        function canPurchaseCredits() {
            return !!onPurchaseCredits && !!runtimeState().creditsPurchaseUrl;
        }

        function currentBalanceView() {
            return Billing.currentBalanceView({
                balanceBaseline,
                lastBalanceData,
                estimatedCost: Math.max(0, estimatedSessionCost() - balanceBaselineSttCost),
                sessionLlmCost: Math.max(0, sessionLlmCost - balanceBaselineLlmCost),
            });
        }

        function reanchorBalance(data) {
            balanceBaseline = data;
            balanceBaselineSttCost = estimatedSessionCost();
            balanceBaselineLlmCost = sessionLlmCost;
        }

        function updateSessionCostDisplay() {
            const rate = effectivePricePerSecond();
            const sttCost = (sessionElapsedMs() / 1000) * rate;
            if (sessionHadLlmCost || sessionLlmCost > 0) {
                const sttString = Billing.formatSessionCost(sttCost, rate);
                const sttRounded = Number(sttString);
                const llmCost = Math.max(0, sessionLlmCost);
                const total = (Number.isFinite(sttRounded) ? sttRounded : 0) + llmCost;
                const totalString = (Math.round(total * 100) / 100).toFixed(2);
                const llmString = (Math.round(llmCost * 100) / 100).toFixed(2);
                setText('sessionValue', `${totalString} (${sttString} + LLM ${llmString})`);
            } else {
                setText('sessionValue', Billing.formatSessionCost(sttCost, rate));
            }
        }

        function renderBalanceView() {
            updateSessionCostDisplay();
            const view = currentBalanceView();
            if (!view) return;
            setText('balanceLabel', t('balance_label'));
            setText('balanceValue', formatCredits(view.prepaid_balance));
            setText('sessionLabel', t('balance_session'));
            if (balanceOpenSettingsButton) {
                balanceOpenSettingsButton.textContent = t(canPurchaseCredits() ? 'get_more_credits' : 'open_settings');
            }
            if (balanceActionItem) {
                balanceActionItem.hidden = !balanceIsLow();
            }
            renderFreePools(
                documentRef.getElementById('freePools'),
                view.free && view.free.pools,
            );
            renderSubscriptionPools(
                documentRef.getElementById('subscriptionPools'),
                view.subscriptions,
            );
            onAccountBalanceChanged();
        }

        function renderBalance(data) {
            lastBalanceData = data;
            const baselineTotal = Billing.balanceTotalRemaining(balanceBaseline);
            const nextTotal = Billing.balanceTotalRemaining(data);
            const unlimitedAllowanceExpired = !Number.isFinite(baselineTotal)
                && Number.isFinite(nextTotal);
            if (!balanceBaseline
                || estimatedSessionCost() <= 0
                || unlimitedAllowanceExpired
                || nextTotal > baselineTotal) {
                reanchorBalance(data);
            } else if (nextTotal === baselineTotal) {
                // Refresh plan metadata without forgiving cost accrued since
                // the current anchor; an equal server snapshot may simply lag
                // behind the client-side estimate.
                balanceBaseline = data;
            }
            renderBalanceView();
        }

        async function fetchBalance({ provider = null, force = false } = {}) {
            if (!force && !balanceBarShouldShow()) return false;
            try {
                const url = provider
                    ? `/account/balance?provider=${encodeURIComponent(provider)}`
                    : '/account/balance';
                const response = await fetchRef(url);
                if (!response.ok) return false;
                const data = await response.json();
                pricePerSecond = Number(data.price_per_second) || 0;
                firstRedeemBonusCredits = Math.max(
                    0,
                    Number(data.first_redeem_bonus_credits) || firstRedeemBonusCredits || 0,
                );
                firstRedeemBonusEligible = !!data.first_redeem_bonus_eligible
                    && firstRedeemBonusCredits > 0;
                renderBalance(data);
                onAccountSectionChanged();
                return true;
            } catch (error) {
                return false;
            }
        }

        async function fetchProviderBalance(provider) {
            if (!fetchRef || !['soniox', 'gemini'].includes(provider)) return null;
            try {
                const response = await fetchRef(`/account/balance?provider=${encodeURIComponent(provider)}`);
                if (!response.ok) return null;
                return await response.json();
            } catch (error) {
                return null;
            }
        }

        function sessionCostResume() {
            if (runtimeState().connectionMode !== 'relay') return;
            if (sessionRunSince == null) sessionRunSince = now();
            if (!sessionCostTimer) {
                sessionCostTimer = setIntervalRef(renderBalanceView, 1000);
            }
            renderBalanceView();
            scheduleBalancePolling();
        }

        function sessionCostPause() {
            if (sessionRunSince != null) {
                sessionAccumMs += now() - sessionRunSince;
                sessionRunSince = null;
            }
            if (sessionCostTimer) {
                clearIntervalRef(sessionCostTimer);
                sessionCostTimer = null;
            }
            renderBalanceView();
            scheduleBalancePolling();
        }

        function sessionCostReset() {
            sessionAccumMs = 0;
            sessionRunSince = null;
            sessionLlmCost = 0;
            sessionHadLlmCost = false;
            if (sessionCostTimer) {
                clearIntervalRef(sessionCostTimer);
                sessionCostTimer = null;
            }
            reanchorBalance(lastBalanceData);
            renderBalanceView();
            scheduleBalancePolling();
            void fetchBalance();
        }

        function addLlmCost(credits) {
            const amount = Number(credits);
            if (!Number.isFinite(amount) || amount <= 0) return false;
            sessionLlmCost += amount;
            sessionHadLlmCost = true;
            renderBalanceView();
            return true;
        }

        function resetFirstRedeemBonus(credits) {
            firstRedeemBonusCredits = Math.max(0, Number(credits) || 0);
            firstRedeemBonusEligible = false;
        }

        function getFirstRedeemBonus() {
            return {
                credits: firstRedeemBonusCredits,
                eligible: firstRedeemBonusEligible,
            };
        }

        function getLastBalanceData() {
            return lastBalanceData;
        }

        function getDebugState() {
            return {
                balancePollIntervalMs,
                balanceIsMetering: balanceIsMetering(),
                sessionAccumMs,
                sessionLlmCost,
                sessionHadLlmCost,
                pricePerSecond,
                firstRedeemBonusCredits,
                firstRedeemBonusEligible,
                lastBalanceData,
                balanceBaseline,
                balanceBaselineSttCost,
                balanceBaselineLlmCost,
            };
        }

        // Send the user straight to the shop when there is one; the settings
        // panel is only the fallback route to the same link.
        function handleBalanceAction() {
            if (canPurchaseCredits()) onPurchaseCredits();
            else onOpenSettings({ forced: false });
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            if (balanceOpenSettingsButton) {
                balanceOpenSettingsButton.addEventListener('click', handleBalanceAction);
            }
            return true;
        }

        function destroy() {
            stopBalancePolling();
            if (sessionCostTimer) {
                clearIntervalRef(sessionCostTimer);
                sessionCostTimer = null;
            }
            if (balanceOpenSettingsButton) {
                balanceOpenSettingsButton.removeEventListener('click', handleBalanceAction);
            }
            initialized = false;
        }

        return {
            addLlmCost,
            balanceBarShouldShow,
            currentBalanceView,
            destroy,
            fetchBalance,
            fetchProviderBalance,
            formatCredits,
            freePoolsSummary,
            getDebugState,
            getFirstRedeemBonus,
            getLastBalanceData,
            init,
            renderBalance,
            renderBalanceView,
            renderFreePools,
            renderSubscriptionPools,
            resetFirstRedeemBonus,
            scheduleBalancePolling,
            sessionCostPause,
            sessionCostReset,
            sessionCostResume,
            startBalancePolling,
            stopBalancePolling,
            updateBalanceBarVisibility,
        };
    }

    const api = { ACTIVE_POLL_MS, IDLE_POLL_MS, create };
    root.HostedBalance = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
