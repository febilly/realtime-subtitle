const { Billing, createController } = require('../../static/js/hosted');

describe('Hosted.Billing versions and formatting', () => {
    it('compares normalized version segments and preserves invalid-version behavior', () => {
        expect(Billing.compareVersions('v4.1.3', '4.1.2')).toBe(1);
        expect(Billing.compareVersions('4.1', '4.1.0')).toBe(0);
        expect(Billing.compareVersions('4.1.0-beta', '4.1.0')).toBe(0);
        expect(Billing.compareVersions('4.1.0.2', '4.1.0.1')).toBe(1);
        expect(Billing.compareVersions('not-a-version', '4.1.0')).toBe(0);
    });

    it('formats credits and fractional rates at current precision', () => {
        expect(Billing.formatCredits(null)).toBe('—');
        expect(Billing.formatCredits(1.236)).toBe('1.24');
        expect(Billing.formatRate(0.01234)).toBe('0.012');
        expect(Billing.formatRate(12.345)).toBe('12.35');
    });
});

describe('Hosted.Billing metering', () => {
    it.each([
        [{ translationProvider: 'soniox', uiTranslationMode: 'none', translationUiMode: 'hybrid', sonioxNoTranslationFactor: 0.6 }, 0.6],
        [{ translationProvider: 'soniox', uiTranslationMode: 'one_way', translationUiMode: 'accurate', sonioxNoTranslationFactor: 0.5 }, 0.5],
        [{ translationProvider: 'soniox', uiTranslationMode: 'one_way', translationUiMode: 'hybrid', sonioxNoTranslationFactor: 0.5 }, 1],
        [{ translationProvider: 'gemini', uiTranslationMode: 'none', translationUiMode: 'accurate', sonioxNoTranslationFactor: 0.5 }, 1],
        [{ translationProvider: 'soniox', uiTranslationMode: 'none', translationUiMode: 'fast', sonioxNoTranslationFactor: 0 }, 1],
    ])('computes the STT multiplier without mixing the two mode dimensions', (input, expected) => {
        expect(Billing.sttRateMultiplier(input)).toBe(expected);
    });

    it('rounds elapsed time to whole provider billing ticks', () => {
        expect(Billing.estimatedSessionCost(1499, 2, 0.5)).toBe(1);
        expect(Billing.estimatedSessionCost(1500, 2, 0.5)).toBe(2);
        expect(Billing.estimatedSessionCost(-1000, 2)).toBe(0);
        expect(Billing.estimatedSessionCost(1000, 0)).toBe(0);
    });

    it('spends free pools, then subscription quota, then prepaid, without mutating input', () => {
        const input = {
            prepaid_balance: 10,
            free: { pools: [{ remaining: 2 }, { remaining: 3 }] },
            subscriptions: [{ remaining_credits: 50, quota_credits: 50 }],
        };
        const output = Billing.applyEstimatedDeduction(input, 7);
        expect(output).toEqual({
            // The subscription covers the 2 credits the free pools could not,
            // so the prepaid balance is untouched.
            prepaid_balance: 10,
            free: { pools: [{ remaining: 0 }, { remaining: 0 }] },
            subscriptions: [{ remaining_credits: 48, quota_credits: 50 }],
        });
        expect(input.free.pools.map((pool) => pool.remaining)).toEqual([2, 3]);
        expect(input.subscriptions[0].remaining_credits).toBe(50);
    });

    it('falls through to prepaid once free and subscription quota are both spent', () => {
        const output = Billing.applyEstimatedDeduction({
            prepaid_balance: 10,
            free: { pools: [{ remaining: 2 }] },
            subscriptions: [{ remaining_credits: 3, quota_credits: 20 }],
        }, 9);
        expect(output.free.pools[0].remaining).toBe(0);
        expect(output.subscriptions[0].remaining_credits).toBe(0);
        expect(output.prepaid_balance).toBe(6);
    });

    it('treats an unlimited subscription pool as inexhaustible, not as empty', () => {
        // The server reports an unlimited pool as a -1 quota with null remaining.
        const unlimited = {
            prepaid_balance: 0,
            free: { pools: [] },
            subscriptions: [{ remaining_credits: null, quota_credits: -1 }],
        };
        expect(Billing.isAccountExhausted(unlimited)).toBe(false);
        expect(Billing.balanceTotalRemaining(unlimited)).toBe(Infinity);
        expect(Billing.hasAtLeastCredits(unlimited, 9999)).toBe(true);
        expect(Billing.applyEstimatedDeduction(unlimited, 9999).prepaid_balance).toBe(0);
    });

    it('lets an unlimited pool absorb the remainder and then deducts LLM only from prepaid', () => {
        const base = {
            prepaid_balance: 10,
            free: { pools: [{ remaining: 1 }, { remaining: 0, unlimited: true }] },
        };
        const view = Billing.currentBalanceView({
            lastBalanceData: base,
            estimatedCost: 20,
            sessionLlmCost: 2.5,
        });
        expect(view.free.pools[0].remaining).toBe(0);
        expect(view.prepaid_balance).toBe(7.5);
    });

    it('checks all spendable balance sources for exhaustion', () => {
        expect(Billing.isAccountExhausted({ prepaid_balance: 0, free: { pools: [] }, subscriptions: [] })).toBe(true);
        expect(Billing.isAccountExhausted({ prepaid_balance: 0, free: { pools: [{ unlimited: true }] } })).toBe(false);
        expect(Billing.isAccountExhausted({ prepaid_balance: 0, subscriptions: [{ remaining_credits: 1 }] })).toBe(false);
        expect(Billing.balanceTotalRemaining({ prepaid_balance: 2, free: { pools: [{ remaining: 3 }] } })).toBe(5);
        expect(Billing.balanceTotalRemaining({
            prepaid_balance: 2,
            free: { pools: [{ remaining: 3 }] },
            subscriptions: [{ remaining_credits: 4, quota_credits: 10 }],
        })).toBe(9);
        // An unlimited pool has no total to report; saying 5 would let a caller
        // comparing two totals conclude the account is running low.
        expect(Billing.balanceTotalRemaining({ prepaid_balance: 2, free: { pools: [{ remaining: 3 }, { unlimited: true }] } })).toBe(Infinity);
    });

    it('checks the recovery threshold across prepaid, free, and subscription quota', () => {
        expect(Billing.hasAtLeastCredits({
            prepaid_balance: 5,
            free: { pools: [{ remaining: 4 }] },
            subscriptions: [{ remaining_credits: 11 }],
        }, 20)).toBe(true);
        expect(Billing.hasAtLeastCredits({
            prepaid_balance: 5,
            free: { pools: [{ remaining: 4 }] },
            subscriptions: [{ remaining_credits: 10.99 }],
        }, 20)).toBe(false);
        expect(Billing.hasAtLeastCredits({
            prepaid_balance: 0,
            free: { pools: [{ unlimited: true }] },
        }, 20)).toBe(true);
    });

    it('prices the remaining balance as listening time when judging it low', () => {
        const tenMinutes = 600;
        // At 1 credit/sec, 500 credits is 8 minutes and 900 is 15.
        expect(Billing.isBalanceLow({ prepaid_balance: 500, free: { pools: [] } }, 1, tenMinutes)).toBe(true);
        expect(Billing.isBalanceLow({ prepaid_balance: 900, free: { pools: [] } }, 1, tenMinutes)).toBe(false);
        // The same 500 credits is over half an hour at Gemini's cheaper rate.
        expect(Billing.isBalanceLow({ prepaid_balance: 500, free: { pools: [] } }, 0.25, tenMinutes)).toBe(false);
    });

    it('counts free pools and subscriptions toward the low-balance threshold', () => {
        const tenMinutes = 600;
        expect(Billing.isBalanceLow({
            prepaid_balance: 300,
            free: { pools: [{ remaining: 400 }] },
            subscriptions: [{ remaining_credits: 400 }],
        }, 1, tenMinutes)).toBe(false);
        expect(Billing.isBalanceLow({ prepaid_balance: 0, free: { pools: [{ unlimited: true }] } }, 1, tenMinutes)).toBe(false);
    });

    it('reports no low balance when the rate or data is unusable', () => {
        expect(Billing.isBalanceLow(null, 1, 600)).toBe(false);
        expect(Billing.isBalanceLow({ prepaid_balance: 0 }, 0, 600)).toBe(false);
        expect(Billing.isBalanceLow({ prepaid_balance: 0 }, Number.NaN, 600)).toBe(false);
    });

    it('locks the current scientific-notation formatting edge for a separate fix', () => {
        expect(Billing.formatSessionCost(3e-7, 1e-7)).toBe('0');
    });
});

describe('Hosted controller orchestration', () => {
    it('preserves the startup dependency order', async () => {
        const order = [];
        const names = [
            'preopenHostedLoginIfNeeded', 'fetchUiConfig', 'refreshPreopenedHostedLogin',
            'maybeRunFirstLaunchFlow', 'ensureHostedVersionAllowed', 'syncProviderFromStorage',
            'fetchLlmRefineStatus', 'fetchApiKeyStatus', 'fetchOscTranslationStatus',
            'maybeForceOpenSettings', 'updateBalanceBarVisibility', 'maybeShowInviteReminder',
            'startTicketNotifications', 'connect',
        ];
        const actions = Object.fromEntries(names.map((name) => [name, () => { order.push(name); }]));
        await createController(actions).startup();
        expect(order).toEqual([
            'preopenHostedLoginIfNeeded', 'fetchUiConfig', 'refreshPreopenedHostedLogin',
            'maybeRunFirstLaunchFlow', 'ensureHostedVersionAllowed', 'refreshPreopenedHostedLogin',
            'syncProviderFromStorage', 'fetchLlmRefineStatus', 'fetchApiKeyStatus',
            'fetchOscTranslationStatus', 'maybeForceOpenSettings', 'updateBalanceBarVisibility',
            'maybeShowInviteReminder', 'startTicketNotifications', 'connect',
        ]);
    });

    it('meters only recognition-session lifecycle frames', () => {
        const resume = vi.fn();
        const pause = vi.fn();
        let paused = false;
        const controller = createController({
            sessionCostResume: resume,
            sessionCostPause: pause,
            isPaused: () => paused,
        });
        expect(controller.handleSessionFrame({ type: 'socket_open' })).toBe(false);
        expect(controller.handleSessionFrame({ type: 'session_connected' })).toBe(true);
        paused = true;
        controller.handleSessionFrame({ type: 'session_connected' });
        controller.handleSessionFrame({ type: 'recognition_paused', paused: true });
        controller.handleSessionFrame({ type: 'session_idle' });
        controller.handleSessionFrame({ type: 'session_disconnected' });
        expect(resume).toHaveBeenCalledOnce();
        expect(pause).toHaveBeenCalledTimes(3);
    });
});
