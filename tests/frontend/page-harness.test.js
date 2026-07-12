const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createPageHarness } = require('./helpers/page-harness');
const { DEFAULT_INPUT, generateGolden, readFrames } = require('../../scripts/generate-frontend-golden');

const pages = [];

afterEach(() => {
    while (pages.length) pages.pop().close();
});

describe('full-page frontend harness', () => {
    it('loads index scripts and accepts recorded WebSocket frames', async () => {
        const page = await createPageHarness();
        pages.push(page);

        expect(page.document.getElementById('subtitleContainer')).not.toBeNull();
        expect(page.sockets).toHaveLength(1);
        await page.emitFrame(readFrames(DEFAULT_INPUT)[0]);
        expect(page.html()).toContain('Hello.');
        expect(page.html()).toContain('你好。');
    });

    it('writes one HTML snapshot per frame', async () => {
        const output = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'frontend-golden-')), 'out.jsonl');
        await generateGolden(DEFAULT_INPUT, output);
        const snapshots = readFrames(output);

        expect(snapshots).toHaveLength(3);
        expect(snapshots[0].html).toContain('Hello.');
        expect(snapshots[1].html).toContain('您好。');
        // `clear` resets model state but deliberately leaves the last DOM in place
        // until the restarted stream renders its first frame.
        expect(snapshots[2].html).toContain('您好。');
    });
});
