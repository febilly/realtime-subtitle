const { createPageHarness } = require('./helpers/page-harness');

describe('full-page WebSocket controller wiring', () => {
    it('opens one URL-preserving socket and routes its frames into the app controllers', async () => {
        const page = await createPageHarness({
            url: 'https://captions.example/view?room=alpha&mode=compact',
        });
        try {
            expect(page.sockets).toHaveLength(1);
            expect(page.sockets[0].url)
                .toBe('wss://captions.example/ws?room=alpha&mode=compact');

            const ipcButton = page.document.getElementById('ipcStatusButton');
            await page.emitFrame({ type: 'ipc_status', connected: true });
            expect(ipcButton.classList.contains('ipc-connected')).toBe(true);
        } finally {
            page.close();
        }
    });
});
