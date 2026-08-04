const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

describe('frontend i18n', () => {
    it('provides Portuguese copy for interrupted sentence repair', () => {
        const dom = new JSDOM('', { runScripts: 'outside-only' });
        Object.defineProperty(dom.window.navigator, 'languages', {
            configurable: true,
            value: ['pt-BR'],
        });
        const source = fs.readFileSync(
            path.resolve(__dirname, '..', '..', 'static', 'i18n.js'),
            'utf8',
        );

        dom.window.eval(source);

        expect(dom.window.I18N.lang).toBe('pt');
        expect(dom.window.I18N.t('interrupt_repair_setting')).toBe('Reconectar frases interrompidas');
        expect(dom.window.I18N.t('interrupt_repair_enabled')).toBe('Ativada');
        expect(dom.window.I18N.t('interrupt_repair_disabled')).toBe('Desativada');
        expect(dom.window.I18N.t('backend_interrupt_repair_disabled')).toContain('configuração do servidor');
        dom.window.close();
    });
});
