const { createPageHarness } = require('./helpers/page-harness');

describe('full-page runtime frame controller wiring', () => {
    it('applies and persists backend subtitle font preference frames', async () => {
        const page = await createPageHarness();
        try {
            await page.emitFrame({
                type: 'subtitle_font_preference',
                use_bundled_cjk_fonts: true,
            });
            expect(page.document.body.classList.contains('use-bundled-cjk-fonts')).toBe(true);
            expect(page.window.localStorage.getItem('useBundledCjkFont')).toBe('true');

            await page.emitFrame({
                type: 'subtitle_font_preference',
                use_bundled_cjk_fonts: false,
            });
            expect(page.document.body.classList.contains('use-bundled-cjk-fonts')).toBe(false);
            expect(page.window.localStorage.getItem('useBundledCjkFont')).toBe('false');
        } finally {
            page.close();
        }
    });

    it('routes overlay visibility frames through runtime presentation', async () => {
        const page = await createPageHarness();
        try {
            const button = page.document.getElementById('overlayButton');

            await page.emitFrame({ type: 'overlay_visibility', visible: true });
            expect(button.classList.contains('active')).toBe(true);
            expect(button.title).toBe('Close subtitle overlay window');

            await page.emitFrame({ type: 'overlay_visibility', visible: false });
            expect(button.classList.contains('active')).toBe(false);
            expect(button.title).toBe('Open subtitle overlay window');
        } finally {
            page.close();
        }
    });
});
