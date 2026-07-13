(function (root) {
    'use strict';

    const INVITE_REMINDER_COOLDOWN_MS = 7 * 24 * 60 * 60 * 1000;
    const INVITE_REMINDER_STORAGE_KEY = 'inviteRewardReminderLastShown';
    const TICKET_UNREAD_POLL_INTERVAL_MS = 60 * 1000;

    function create(options = {}) {
        const documentRef = options.document || root.document;
        const windowRef = options.window || root;
        const navigatorRef = options.navigator || root.navigator || {};
        const storage = options.storage || root.localStorage;
        const now = typeof options.now === 'function' ? options.now : () => Date.now();
        const signedInAtLaunch = !!options.signedInAtLaunch;
        const fetchRef = typeof options.fetch === 'function'
            ? options.fetch
            : (typeof root.fetch === 'function' ? root.fetch.bind(root) : null);
        const t = typeof options.t === 'function' ? options.t : (key) => key;
        const localizeBackendMessage = typeof options.localizeBackendMessage === 'function'
            ? options.localizeBackendMessage
            : (message) => String(message || '');
        const showConfirm = typeof options.showConfirm === 'function'
            ? options.showConfirm
            : (message) => Promise.resolve(windowRef.confirm(message));
        const rankLabel = typeof options.rankLabel === 'function'
            ? options.rankLabel
            : (rank) => String(rank || '').replace(/_/g, ' ');
        const getRuntimeState = typeof options.getRuntimeState === 'function'
            ? options.getRuntimeState
            : () => ({});
        const loadServerSettings = options.loadServerSettings || (() => ({}));
        const saveServerSettings = options.saveServerSettings || (() => {});
        const balance = options.balance || {};
        const actions = options.actions || {};
        const elements = options.elements || {};
        const redeemButton = elements.redeemButton || null;
        const redeemInput = elements.redeemInput || null;
        const redeemPasteButton = elements.redeemPasteButton || null;
        const reLoginButton = elements.reLoginButton || null;
        const logoutButton = elements.logoutButton || null;
        const copyInviteButton = elements.copyInviteButton || null;
        const openUserWebButton = elements.openUserWebButton || null;

        let initialized = false;
        let ticketUnreadCheckPromise = null;
        let ticketUnreadPollTimer = null;
        let lastNotifiedTicketUnreadSignature = '';
        const listeners = [];

        function runtimeState() {
            const value = getRuntimeState();
            return value && typeof value === 'object' ? value : {};
        }

        function call(name, ...args) {
            return typeof actions[name] === 'function' ? actions[name](...args) : undefined;
        }

        function balanceCall(name, ...args) {
            return typeof balance[name] === 'function' ? balance[name](...args) : undefined;
        }

        function formatCredits(value) {
            const formatted = balanceCall('formatCredits', value);
            return formatted == null ? String(value == null ? '' : value) : formatted;
        }

        function isSignedIn(server = loadServerSettings()) {
            return !!runtimeState().backendLoggedIn || !!server.token;
        }

        function successfulInviteCount(data) {
            if (!data || typeof data !== 'object') return null;
            const numericKeys = [
                'successful_invite_count', 'successful_invites', 'invite_count',
                'invited_count', 'invited_users_count', 'referral_count',
                'referred_users_count', 'registered_invitees', 'invitee_count',
            ];
            for (const key of numericKeys) {
                const value = data[key];
                if ((typeof value === 'number' || typeof value === 'string')
                    && value !== '' && Number.isFinite(Number(value))) {
                    return Math.max(0, Number(value));
                }
            }
            const arrayKeys = [
                'successful_invites', 'invited_users', 'referred_users', 'referrals', 'invitees',
            ];
            for (const key of arrayKeys) {
                if (Array.isArray(data[key])) return data[key].length;
            }
            if (data.stats && typeof data.stats === 'object') return successfulInviteCount(data.stats);
            return null;
        }

        async function maybeShowInviteReminder() {
            if (!signedInAtLaunch || !isSignedIn()) return false;
            let lastShown = 0;
            try {
                lastShown = Number(storage && storage.getItem(INVITE_REMINDER_STORAGE_KEY)) || 0;
            } catch (error) {
                lastShown = 0;
            }
            if (now() - lastShown < INVITE_REMINDER_COOLDOWN_MS) return false;
            try {
                const response = await fetchRef('/account/invite');
                if (!response.ok) return false;
                const data = await response.json().catch(() => ({}));
                if (successfulInviteCount(data) !== 0) return false;
                call('showToast', t('invite_reward_reminder'), false, {
                    timeoutMs: 12000,
                    actionLabel: t('open_settings'),
                    onAction: () => call('openSettings', { forced: false }),
                });
                try {
                    if (storage) storage.setItem(INVITE_REMINDER_STORAGE_KEY, String(now()));
                } catch (error) {
                    // The reminder still works for this launch when storage is unavailable.
                }
                return true;
            } catch (error) {
                return false;
            }
        }

        async function openUserWeb(nextPath = '') {
            const suffix = nextPath ? `?next=${encodeURIComponent(nextPath)}` : '';
            try {
                const response = await fetchRef(`/account/web-login-url${suffix}`);
                const data = await response.json().catch(() => ({}));
                const url = data && data.url;
                if (!response.ok || !url) {
                    const message = data && (data.detail || data.message);
                    call('showToast', localizeBackendMessage(message || t('account_open_web_failed')), true);
                    return false;
                }
                try {
                    windowRef.open(url, '_blank', 'noopener,noreferrer');
                } catch (error) {
                    if (navigatorRef.clipboard) {
                        navigatorRef.clipboard.writeText(url).catch(() => {});
                    }
                    call('showToast', url);
                }
                return true;
            } catch (error) {
                call('showToast', String(error), true);
                return false;
            }
        }

        function ticketUnreadSignature(data) {
            const tickets = Array.isArray(data && data.tickets) ? data.tickets : [];
            const entries = tickets.map((ticket) => [
                String((ticket && ticket.id) || ''),
                String((ticket && ticket.read_cursor) || ''),
                String((ticket && ticket.unread_type) || ''),
                Number((ticket && ticket.unread_count) || 0),
            ]).sort((left, right) => left[0].localeCompare(right[0]));
            return JSON.stringify(entries.length ? entries : [
                Number((data && data.admin_initiated_count) || 0),
                Number((data && data.admin_reply_count) || 0),
                Number((data && data.unread_activity_count) || 0),
            ]);
        }

        async function checkTicketUnread() {
            if (!isSignedIn()) return false;
            try {
                const response = await fetchRef('/account/tickets/unread-summary');
                const data = await response.json().catch(() => ({}));
                if (!response.ok || !data || typeof data !== 'object') return false;
                const unreadCount = Number(data.unread_activity_count) || 0;
                if (unreadCount <= 0) {
                    lastNotifiedTicketUnreadSignature = '';
                    return false;
                }
                const signature = ticketUnreadSignature(data);
                if (signature === lastNotifiedTicketUnreadSignature) return false;
                lastNotifiedTicketUnreadSignature = signature;
                const initiatedCount = Number(data.admin_initiated_count) || 0;
                const replyCount = Number(data.admin_reply_count) || 0;
                const reminderKey = initiatedCount > 0 && replyCount > 0
                    ? 'ticket_unread_mixed_reminder'
                    : initiatedCount > 0
                        ? 'ticket_admin_initiated_reminder'
                        : 'ticket_reply_reminder';
                call('showToast', t(reminderKey), false, {
                    timeoutMs: 10000,
                    actionLabel: t('open_tickets'),
                    onAction: () => { void openUserWeb('/tickets'); },
                });
                return true;
            } catch (error) {
                return false;
            }
        }

        function maybeShowTicketUnreadReminder() {
            if (!isSignedIn()) return Promise.resolve(false);
            if (!ticketUnreadCheckPromise) {
                ticketUnreadCheckPromise = checkTicketUnread().finally(() => {
                    ticketUnreadCheckPromise = null;
                });
            }
            return ticketUnreadCheckPromise;
        }

        function startTicketUnreadPolling() {
            if (ticketUnreadPollTimer === null && typeof windowRef.setInterval === 'function') {
                ticketUnreadPollTimer = windowRef.setInterval(() => {
                    void maybeShowTicketUnreadReminder();
                }, TICKET_UNREAD_POLL_INTERVAL_MS);
            }
            return maybeShowTicketUnreadReminder();
        }

        function updateBalance() {
            const balanceHint = elements.balanceHint || null;
            const poolsBox = elements.freePools || null;
            const server = loadServerSettings();
            const signedIn = isSignedIn(server);
            const view = balanceCall('currentBalanceView');
            if (balanceHint) {
                if (signedIn && view && view.prepaid_balance != null) {
                    balanceHint.textContent = t('account_balance', {
                        balance: formatCredits(view.prepaid_balance),
                    });
                    balanceHint.hidden = false;
                } else {
                    balanceHint.textContent = '';
                    balanceHint.hidden = true;
                }
            }
            if (poolsBox) {
                const pools = signedIn && view && view.free ? view.free.pools : null;
                balanceCall('renderFreePools', poolsBox, pools);
            }
        }

        function updateSection() {
            const current = runtimeState();
            const server = loadServerSettings();
            const serverHint = elements.serverHint || null;
            const identityHint = elements.identityHint || null;
            const purchaseHint = elements.purchaseHint || null;
            const purchaseLink = elements.purchaseLink || null;
            const firstBonusHint = elements.firstBonusHint || null;
            const firstBonus = balanceCall('getFirstRedeemBonus') || {};
            if (serverHint) {
                serverHint.textContent = current.relayServerUrl
                    ? t('account_server', { url: current.relayServerUrl })
                    : '';
            }
            if (identityHint) {
                if (isSignedIn(server)) {
                    const name = server.displayName || '—';
                    const rank = rankLabel(server.trustRank) || '—';
                    identityHint.textContent = t('account_identity', { name, rank });
                } else {
                    identityHint.textContent = t('account_not_signed_in');
                }
            }
            if (purchaseHint && purchaseLink) {
                if (current.creditsPurchaseUrl) {
                    purchaseLink.href = current.creditsPurchaseUrl;
                    purchaseLink.textContent = t('account_purchase_credits');
                    purchaseHint.hidden = false;
                } else {
                    purchaseLink.removeAttribute('href');
                    purchaseHint.hidden = true;
                }
            }
            if (firstBonusHint) {
                const showFirstBonus = isSignedIn(server)
                    && firstBonus.eligible
                    && Number(firstBonus.credits) > 0;
                firstBonusHint.textContent = showFirstBonus
                    ? t('account_first_redeem_bonus', { credits: formatCredits(firstBonus.credits) })
                    : '';
                firstBonusHint.hidden = !showFirstBonus;
            }
            updateBalance();
        }

        async function handleRedeem() {
            const code = String((redeemInput && redeemInput.value) || '').trim();
            if (!code) return;
            try {
                const response = await fetchRef('/account/redeem', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ code }),
                });
                const data = await response.json().catch(() => ({}));
                if (response.ok && data && data.success) {
                    if (redeemInput) redeemInput.value = '';
                    call('showToast', t('account_redeem_success', {
                        credits: formatCredits(data.granted_credits),
                        balance: formatCredits(data.new_balance),
                    }));
                    balanceCall('resetFirstRedeemBonus', data.first_redeem_bonus_credits);
                    updateSection();
                    void balanceCall('fetchBalance');
                } else {
                    const message = data && (data.detail || data.message);
                    call('showToast', localizeBackendMessage(message || t('connection_error_try_again')), true);
                }
            } catch (error) {
                call('showToast', String(error), true);
            }
        }

        async function handleCopyInvite() {
            if (copyInviteButton) copyInviteButton.disabled = true;
            try {
                const response = await fetchRef('/account/invite');
                const data = await response.json().catch(() => ({}));
                const link = data && data.invite_link;
                if (!response.ok || !link) {
                    const message = data && (data.detail || data.message);
                    call('showToast', localizeBackendMessage(message || t('account_invite_failed')), true);
                    return;
                }
                try {
                    await navigatorRef.clipboard.writeText(link);
                    call('showToast', t('account_invite_copied'));
                } catch (error) {
                    call('showToast', link);
                }
            } catch (error) {
                call('showToast', String(error), true);
            } finally {
                if (copyInviteButton) copyInviteButton.disabled = false;
            }
        }

        async function handleOpenUserWeb() {
            if (openUserWebButton) openUserWebButton.disabled = true;
            try {
                await openUserWeb();
            } finally {
                if (openUserWebButton) openUserWebButton.disabled = false;
            }
        }

        async function handleLogout() {
            const confirmed = await showConfirm(t('account_logout_confirm'), {
                okLabel: t('account_logout'),
                cancelLabel: t('cancel'),
                danger: true,
            });
            if (!confirmed) return false;
            try {
                await fetchRef('/account/logout', { method: 'POST' });
            } catch (error) {
                // Local credentials must still be cleared when the relay is unreachable.
            }
            const server = loadServerSettings();
            server.token = '';
            server.displayName = '';
            server.trustRank = '';
            saveServerSettings(server);
            call('setBackendLoggedIn', false);
            call('resetBootGuard');
            updateSection();
            balanceCall('updateBalanceBarVisibility');
            call('hideSettingsPanel');
            call('openLogin', { forced: true });
            return true;
        }

        function bind(element, type, listener) {
            if (!element) return;
            element.addEventListener(type, listener);
            listeners.push([element, type, listener]);
        }

        function init() {
            if (initialized) return false;
            initialized = true;
            bind(redeemButton, 'click', () => { void handleRedeem(); });
            bind(redeemPasteButton, 'click', async (event) => {
                event.preventDefault();
                try {
                    const text = await navigatorRef.clipboard.readText();
                    if (redeemInput) {
                        redeemInput.value = String(text || '').trim();
                        redeemInput.focus();
                    }
                } catch (error) {
                    // Clipboard read can be denied; manual paste remains available.
                }
            });
            bind(copyInviteButton, 'click', () => { void handleCopyInvite(); });
            bind(openUserWebButton, 'click', () => { void handleOpenUserWeb(); });
            bind(reLoginButton, 'click', () => {
                call('hideSettingsPanel');
                call('openLogin', { forced: false });
            });
            bind(logoutButton, 'click', () => { void handleLogout(); });
            return true;
        }

        function destroy() {
            for (const [element, type, listener] of listeners.splice(0)) {
                element.removeEventListener(type, listener);
            }
            if (ticketUnreadPollTimer !== null && typeof windowRef.clearInterval === 'function') {
                windowRef.clearInterval(ticketUnreadPollTimer);
                ticketUnreadPollTimer = null;
            }
            initialized = false;
        }

        return {
            destroy,
            handleCopyInvite,
            handleLogout,
            handleOpenUserWeb,
            handleRedeem,
            init,
            isSignedIn,
            maybeShowInviteReminder,
            maybeShowTicketUnreadReminder,
            startTicketUnreadPolling,
            successfulInviteCount,
            updateBalance,
            updateSection,
        };
    }

    const api = {
        INVITE_REMINDER_COOLDOWN_MS,
        INVITE_REMINDER_STORAGE_KEY,
        TICKET_UNREAD_POLL_INTERVAL_MS,
        create,
    };
    root.HostedAccount = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
