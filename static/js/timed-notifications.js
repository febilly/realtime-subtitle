(function (root) {
    'use strict';

    function create(options = {}) {
        const windowRef = options.window || root;
        const fetchRef = options.fetch || root.fetch.bind(root);
        const showToast = options.showToast;
        const t = options.t || ((key) => key);
        const renderServerNotice = options.renderServerNotice || (() => {});
        const now = options.now || (() => Date.now());
        const tasks = new Map();
        let blockedUntil = 0;
        let ready = false;
        let busy = null;
        let timer = null;
        let starShownThisLaunch = false;
        let starDisabledThisLaunch = false;
        let pendingStarDisable = false;

        function showStarThanks() {
            showToast(t('local_model_star_thanks'), false, { timeoutMs: 6000 });
        }

        async function post(payload) {
            const response = await fetchRef('/timed-notifications', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!response.ok) throw new Error('Notification state unavailable');
            return response.json();
        }

        async function tryShow(kind, display, extra = {}) {
            if (!ready || now() < blockedUntil) return false;
            const result = await post({ kind, ...extra });
            if (!result.shown) {
                if (result.retry_after_seconds) blockedUntil = now() + result.retry_after_seconds * 1000;
                return false;
            }
            blockedUntil = now() + (Number(result.retry_after_seconds) || 0) * 1000;
            // The backend is authoritative, including when another window has
            // already claimed this slot. Individual cadences advance on display.
            display(result);
            return true;
        }

        async function serverNotice(notice, noticeId) {
            if (!noticeId) return false;
            if (options.canRenderServerNotice && !options.canRenderServerNotice(notice)) return false;
            return tryShow('server', () => renderServerNotice(notice), { notice_id: noticeId });
        }

        async function refresh({ runTasks = true } = {}) {
            try {
                if (pendingStarDisable) {
                    await post({ disable_star: true });
                    pendingStarDisable = false;
                }
                const response = await fetchRef('/timed-notifications');
                if (!response.ok) return false;
                const state = await response.json();
                ready = true;
                blockedUntil = Math.max(blockedUntil, now() + (Number(state.retry_after_seconds) || 0) * 1000);
                // Server notices take the startup slot before any local task.
                for (const item of state.notices || []) {
                    await serverNotice(item.notice, item.notice_id);
                }
                if (!runTasks || now() < blockedUntil) return true;
                for (const task of tasks.values()) {
                    await task();
                    if (now() < blockedUntil) return true;
                }
                const starDue = state.star_mode === 'always'
                    ? !starShownThisLaunch
                    : state.star_mode !== 'never' && !state.star_disabled
                        && !starDisabledThisLaunch && state.usage_days >= 7;
                if (starDue) {
                    await tryShow('local_star', (result) => {
                        starShownThisLaunch = true;
                        const actions = [{
                            label: t('local_model_star_link'),
                            onAction: () => {
                                showStarThanks();
                                windowRef.open('https://github.com/febilly/realtime-subtitle', '_blank', 'noopener,noreferrer');
                            },
                        }];
                        if (result.star_shown_count >= 2) {
                            actions.push({
                                label: t('local_model_already_starred'),
                                onAction: () => {
                                    showStarThanks();
                                    starDisabledThisLaunch = true;
                                    pendingStarDisable = true;
                                    void post({ disable_star: true }).then(() => {
                                        pendingStarDisable = false;
                                    }).catch(() => {});
                                },
                            });
                        }
                        showToast(t('local_model_star_reminder'), false, {
                            persistent: true,
                            actions,
                            actionSeparator: ' · ',
                        });
                    });
                }
                return true;
            } catch (error) {
                // Fail closed; individual cadences survive IO errors.
                return false;
            }
        }

        function tick(optionsForRefresh) {
            if (!busy) busy = refresh(optionsForRefresh).finally(() => { busy = null; });
            return busy;
        }

        function start() {
            if (timer) return;
            void tick();
            timer = root.setInterval(() => { void tick(); }, 60000);
        }

        function destroy() {
            if (timer) root.clearInterval(timer);
            timer = null;
            tasks.clear();
        }

        return {
            add: (key, task) => tasks.set(key, task),
            canShow: () => ready && now() >= blockedUntil,
            prepare: () => tick({ runTasks: false }),
            serverNotice,
            start,
            tick,
            tryShow,
            destroy,
        };
    }

    const api = { create };
    root.TimedNotifications = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
