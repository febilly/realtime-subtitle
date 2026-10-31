const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');
const helper = fs.readFileSync(path.join(__dirname, '../../static/js/content-i18n.js'), 'utf8');
const announcements = fs.readFileSync(path.join(__dirname, '../../static/js/announcements.js'), 'utf8');

describe('server-supplied multilingual text', () => {
    it('resolves updates and plan names with English, source and legacy fallbacks', () => {
        const context = {};
        vm.runInNewContext(helper, context);
        const i18n = context.ContentI18n;
        const text = { source: 'zh', texts: { zh: '更新说明', en: 'Release notes', ko: '업데이트 안내' } };
        expect(i18n.localized(text, 'ko')).toBe('업데이트 안내');
        expect(i18n.localized(text, 'pt')).toBe('Release notes');
        expect(i18n.localized(undefined, 'ja', 'Old client text')).toBe('Old client text');
        expect(i18n.localizedField({ plan_name: 'Old name', i18n: { plan_name: text } }, 'plan_name', 'en')).toBe('Release notes');
    });
    it('renders the selected announcement language through the safe rich-text renderer', async () => {
        const dom = new JSDOM('<button id="announcementsButton"></button>', { url: 'http://localhost/', runScripts: 'outside-only' });
        dom.window.eval(helper);
        dom.window.eval(announcements);
        const client = dom.window.SubtitleAnnouncements.create({
            getDocument: () => dom.window.document,
            getLanguage: () => 'ru',
            storage: dom.window.localStorage,
            fetch: async () => ({ ok: true, json: async () => ({ announcements: [{ id: 'notice-1', title: '旧标题', content: '旧正文', visible: true, revision: 1, published_at: '2026-11-01T00:00:00Z', i18n: { title: { source: 'zh', texts: { zh: '旧标题', ru: 'Объявление' } }, content: { source: 'zh', texts: { ru: '**Текст** <script>bad()</script>' } } } }] }) }),
        });
        client.bind(dom.window.document);
        await client.open();
        expect(dom.window.document.querySelector('h3').textContent).toBe('Объявление');
        expect(dom.window.document.querySelector('strong').textContent).toBe('Текст');
        expect(dom.window.document.querySelector('script')).toBeNull();
        expect(dom.window.document.body.textContent).toContain('<script>bad()</script>');
        client.destroy();
        dom.window.close();
    });
});
