const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createPageHarness } = require('./helpers/page-harness');
const {
    DEFAULT_INPUT,
    assertGoldenFilesEqual,
    checkGolden,
    compareGoldenFiles,
    generateGolden,
    readFrames,
} = require('../../scripts/generate-frontend-golden');

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

    it('configures hybrid and accurate rendering deterministically', async () => {
        const frame = readFrames(DEFAULT_INPUT)[0];
        const hybrid = await createPageHarness({ translationUiMode: 'hybrid' });
        pages.push(hybrid);
        await hybrid.emitFrame(frame);

        const accurate = await createPageHarness({ translationUiMode: 'accurate' });
        pages.push(accurate);
        await accurate.emitFrame(frame);

        expect(hybrid.translationUiMode).toBe('hybrid');
        expect(hybrid.html()).toContain('你好。');
        expect(accurate.translationUiMode).toBe('accurate');
        expect(accurate.html()).not.toContain('你好。');
        expect(accurate.html()).toContain('placeholder');
    });

    it('disables real auto-restart side effects by default', async () => {
        const page = await createPageHarness();
        pages.push(page);

        expect(page.window.localStorage.getItem('autoRestartEnabled')).toBe('false');
        page.sockets[0].close();
        await page.flush(2);

        const requestedPaths = page.fetchCalls.map(([url]) => new URL(String(url), 'http://localhost/').pathname);
        expect(requestedPaths).not.toContain('/restart');
    });

    it('compares and checks golden files byte-for-byte', async () => {
        const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'frontend-golden-compare-'));
        const expected = path.join(tempDir, 'expected.jsonl');
        const actual = path.join(tempDir, 'actual.jsonl');
        await generateGolden(DEFAULT_INPUT, expected);
        fs.copyFileSync(expected, actual);

        await expect(compareGoldenFiles(expected, actual)).resolves.toMatchObject({ equal: true });
        await expect(checkGolden(expected, DEFAULT_INPUT)).resolves.toBe(path.resolve(expected));

        fs.appendFileSync(actual, ' ', 'utf8');
        await expect(compareGoldenFiles(expected, actual)).resolves.toMatchObject({
            equal: false,
            offset: fs.statSync(expected).size,
        });
        await expect(assertGoldenFilesEqual(expected, actual)).rejects.toThrow('Golden mismatch at byte');
    });
});
