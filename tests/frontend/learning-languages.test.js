const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const LearningLanguages = require('../../static/js/learning-languages');
const { createPageHarness } = require('./helpers/page-harness');

const doms = [];

function createMemoryStorage(initial = {}) {
    const store = new Map(Object.entries(initial).map(([k, v]) => [k, String(v)]));
    return {
        getItem: (key) => (store.has(key) ? store.get(key) : null),
        setItem: (key, value) => {
            store.set(key, String(value));
        },
        removeItem: (key) => {
            store.delete(key);
        },
        _map: store,
    };
}

function buildSettingsDom() {
    const dom = new JSDOM(`<!doctype html>
        <div id="learningSettingsSection">
            <label id="learningSettingsLabel"></label>
            <div id="learningLanguagesList"></div>
        </div>
    `);
    doms.push(dom);
    return dom;
}

afterEach(() => {
    while (doms.length) doms.pop().window.close();
});

describe('LearningLanguages defaults and persistence', () => {
    it.each([
        ['zh', { ja: true, zh: false, ko: true }],
        ['zh-CN', { ja: true, zh: false, ko: true }],
        ['ja', { ja: false, zh: true, ko: true }],
        ['ja-JP', { ja: false, zh: true, ko: true }],
        ['ko', { ja: true, zh: true, ko: false }],
        ['ko-KR', { ja: true, zh: true, ko: false }],
        ['en', { ja: true, zh: true, ko: true }],
        ['ru', { ja: true, zh: true, ko: true }],
        ['es', { ja: true, zh: true, ko: true }],
        ['pt', { ja: true, zh: true, ko: true }],
    ])('computes UI-language defaults for %s', (uiLang, expected) => {
        expect(LearningLanguages.getDefaultMap(uiLang)).toEqual(expected);
    });

    it('includes learningLanguages.v1 in local-store-sync allowlist', () => {
        const syncSrc = fs.readFileSync(
            path.resolve(__dirname, '../../static/local-store-sync.js'),
            'utf8',
        );
        expect(syncSrc).toContain("'learningLanguages.v1': true");
    });

    it('renders 3 toggles, handles cmn alias, persists changes, and syncs to backend', async () => {
        const dom = buildSettingsDom();
        const storage = createMemoryStorage();
        const fetchMock = vi.fn().mockResolvedValue({ ok: true });
        const onChange = vi.fn();
        let uiLang = 'zh';
        const ctrl = LearningLanguages.create({
            document: dom.window.document,
            storage,
            fetch: fetchMock,
            getUiLanguage: () => uiLang,
            t: (k) => k,
            onChange,
        });

        expect(ctrl.getEnabledMap()).toEqual({ ja: true, zh: false, ko: true });
        expect(ctrl.isLanguageEnabled('cmn')).toBe(false);

        ctrl.applyI18n();
        const rows = [...dom.window.document.querySelectorAll('#learningLanguagesList .settings-row')];
        expect(rows.map((r) => r.dataset.learningLang)).toEqual(['ja', 'zh', 'ko']);
        expect(rows.map((r) => r.querySelector('input[type="checkbox"]').checked)).toEqual([true, false, true]);

        // Unstored defaults follow UI language switch
        uiLang = 'ko';
        ctrl.applyI18n();
        expect(ctrl.getEnabledMap()).toEqual({ ja: true, zh: true, ko: false });

        // Uninitialized backend payload must not override UI defaults
        expect(ctrl.applyBackendLanguages({ ja: true, zh: true, ko: true }, { initialized: false })).toBe(false);
        expect(ctrl.getEnabledMap()).toEqual({ ja: true, zh: true, ko: false });

        // User toggle persists & syncs
        expect(ctrl.setLanguageEnabled('ko', true)).toBe(true);
        expect(ctrl.getEnabledMap()).toEqual({ ja: true, zh: true, ko: true });
        expect(JSON.parse(storage.getItem('learningLanguages.v1'))).toEqual({ ja: true, zh: true, ko: true });
        expect(onChange).toHaveBeenCalledWith({ ja: true, zh: true, ko: true });
        expect(fetchMock).toHaveBeenCalledWith('/learning-languages', expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ languages: { ja: true, zh: true, ko: true } }),
        }));

        // UI language change after explicit storage preserves saved choice
        uiLang = 'zh';
        ctrl.applyI18n();
        expect(ctrl.getEnabledMap()).toEqual({ ja: true, zh: true, ko: true });

        // Initialized backend WS frame overrides and re-renders
        expect(ctrl.applyBackendLanguages({ ja: false, zh: true, ko: false }, { initialized: true })).toBe(true);
        expect(ctrl.getEnabledMap()).toEqual({ ja: false, zh: true, ko: false });
        expect(ctrl.applyBackendLanguages({ ja: false, zh: true, ko: false }, { initialized: true })).toBe(false);
    });
});

describe('full-page learning languages wiring', () => {
    it('gates zh/ko/ja subtitles via learning settings toggles and master furigana button, and exposes __overlayFurigana(text, lang)', async () => {
        const page = await createPageHarness({
            localStorage: {
                'I18N.lang': 'en',
                'featurePacks.v1': JSON.stringify({
                    version: 1,
                    confirmed: true,
                    isUpgrade: false,
                    enabled: { vrchat: false, learning: true, obs: false, transcript: false },
                }),
            },
            sessionStorage: {
                furiganaEnabled: 'true',
            },
        });
        try {
            const doc = page.document;
            expect(doc.getElementById('learningSettingsSection').hidden).toBe(false);
            const rows = [...doc.querySelectorAll('#learningLanguagesList .settings-row')];
            expect(rows.map((r) => r.dataset.learningLang)).toEqual(['ja', 'zh', 'ko']);

            // __overlayFurigana supports zh, ko, ja, cmn, defaults empty/null to ja, and returns [] for unsupported
            expect(page.window.__overlayFurigana('你好', 'zh')).toEqual({
                ready: true,
                pairs: [['你', 'nǐ'], ['好', 'hǎo']],
            });
            expect(page.window.__overlayFurigana('你好', 'cmn')).toEqual({
                ready: true,
                pairs: [['你', 'nǐ'], ['好', 'hǎo']],
            });
            expect(page.window.__overlayFurigana('한국어', 'ko')).toEqual({
                ready: true,
                pairs: [['한국어', 'hangugeo']],
            });
            expect(page.window.__overlayFurigana('Hello', 'en')).toEqual({
                ready: true,
                pairs: [],
            });
            // Empty/null lang falls back to 'ja' (kuromoji stub in harness resolves synchronously on readyTokenizer or returns ready:false before build)
            expect(page.window.__overlayFurigana('', null)).toEqual({
                ready: true,
                pairs: [],
            });

            // Send Chinese original + Japanese translation via WebSocket
            await page.emitFrame({
                type: 'update',
                final_tokens: [
                    { text: '你好', is_final: true, speaker: '1', language: 'zh', translation_status: 'original', llm_sentence_id: 's1' },
                    { text: 'こんにちは', is_final: true, speaker: '1', language: 'ja', translation_status: 'translation', llm_sentence_id: 's1' },
                ],
                non_final_tokens: [],
            });
            await page.flush(10);

            let orig = doc.querySelector('.original-line');
            let trans = doc.querySelector('.subtitle-line:not(.original-line)');
            expect(orig.classList.contains('subtitle-line--pinyin')).toBe(true);
            expect(orig.innerHTML).toContain('<ruby>你<rp>(</rp><rt>nǐ</rt><rp>)</rp></ruby>');
            expect(trans.innerHTML).not.toContain('<ruby>');

            // Toggle Chinese pinyin switch OFF in settings -> removes ruby from Chinese line
            const zhSwitch = doc.querySelector('#learningLanguagesList [data-learning-lang="zh"] .settings-switch');
            zhSwitch.click();
            await page.flush(10);
            orig = doc.querySelector('.original-line');
            expect(orig.innerHTML).not.toContain('<ruby>');
            expect(JSON.parse(page.window.localStorage.getItem('learningLanguages.v1')).zh).toBe(false);

            // Toggle Chinese back ON -> restores ruby; then master 「あ」 button OFF -> hides all ruby
            const zhSwitchOn = doc.querySelector('#learningLanguagesList [data-learning-lang="zh"] .settings-switch');
            zhSwitchOn.click();
            await page.flush(10);
            expect(doc.querySelector('.original-line').innerHTML).toContain('<ruby>');

            doc.getElementById('furiganaButton').click();
            await page.flush(10);
            expect(doc.querySelector('.original-line').innerHTML).not.toContain('<ruby>');
        } finally {
            page.close();
        }
    });
});
