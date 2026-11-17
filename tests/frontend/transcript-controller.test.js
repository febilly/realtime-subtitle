const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const FeaturePacks = require('../../static/js/feature-packs');
const TranscriptController = require('../../static/js/transcript-controller');
const { createPageHarness, flushTasks } = require('./helpers/page-harness');

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

function buildTranscriptDom() {
    const dom = new JSDOM(`<!doctype html>
        <button id="exportTranscriptButton" hidden disabled></button>
        <section id="transcriptSettingsSection" hidden>
            <h2 id="transcriptSettingsLabel"></h2>
            <div>
                <label id="transcriptAutoSaveField">
                    <span id="transcriptAutoSaveLabel"></span>
                    <span id="transcriptAutoSavePicker"></span>
                </label>
                <div id="transcriptFormatField">
                    <span id="transcriptFormatLabel"></span>
                    <div id="transcriptFormatPicker"></div>
                </div>
                <div id="transcriptFolderField">
                    <span id="transcriptFolderLabel"></span>
                    <p id="transcriptFolderPath"></p>
                    <div id="transcriptFolderActions">
                        <button id="transcriptChangeFolderButton" type="button"></button>
                        <button id="transcriptOpenFolderButton" type="button"></button>
                    </div>
                    <p id="transcriptDesktopHint" hidden></p>
                </div>
            </div>
        </section>
    `);
    doms.push(dom);
    return dom;
}

afterEach(() => {
    while (doms.length) doms.pop().window.close();
});

describe('TranscriptController registration and visibility', () => {
    it('gates exportTranscriptButton and transcriptSettingsSection under the transcript feature pack', () => {
        const transcriptPack = FeaturePacks.PACKS.find((pack) => pack.id === 'transcript');
        expect(transcriptPack).toBeDefined();
        expect(transcriptPack.elementIds).toEqual([
            'exportTranscriptButton',
            'transcriptSettingsSection',
        ]);
    });

    it('includes transcriptSettings.v1 in local-store-sync allowlist', () => {
        const syncSrc = fs.readFileSync(
            path.resolve(__dirname, '../../static/local-store-sync.js'),
            'utf8',
        );
        expect(syncSrc).toContain("'transcriptSettings.v1': true");
    });

    it('shows export button and folder actions only in desktop main window when transcript pack is enabled', () => {
        const dom = buildTranscriptDom();
        const doc = dom.window.document;
        const storage = createMemoryStorage();
        let packEnabled = false;
        const ctrl = TranscriptController.create({
            document: doc,
            storage,
            fetch: vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }),
            t: (key) => key,
            userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            isPackEnabled: () => packEnabled,
        });
        ctrl.init();

        const exportBtn = doc.getElementById('exportTranscriptButton');
        const settingsSection = doc.getElementById('transcriptSettingsSection');
        const folderActions = doc.getElementById('transcriptFolderActions');
        const desktopHint = doc.getElementById('transcriptDesktopHint');

        // Pack off, desktop not yet confirmed -> both hidden
        expect(exportBtn.hidden).toBe(true);
        expect(settingsSection.hidden).toBe(true);
        expect(folderActions.hidden).toBe(true);
        expect(desktopHint.hidden).toBe(false);

        // Enable pack without desktop_available -> settings visible, export still hidden
        packEnabled = true;
        ctrl.setPackEnabled(true);
        expect(settingsSection.hidden).toBe(false);
        expect(exportBtn.hidden).toBe(true);
        expect(folderActions.hidden).toBe(true);
        expect(desktopHint.hidden).toBe(false);

        // Confirm desktop main window via /ui-config payload -> export and folder actions appear
        ctrl.applyConfig({
            transcript_desktop_available: true,
            transcript_status: { has_entries: false, count: 0, pack_enabled: true, run_active: false },
            transcript_settings: { auto_save: false, format: 'srt', directory: 'C:\\Docs\\Transcripts' },
        });
        expect(exportBtn.hidden).toBe(false);
        expect(exportBtn.disabled).toBe(true);
        expect(folderActions.hidden).toBe(false);
        expect(desktopHint.hidden).toBe(true);
        expect(doc.getElementById('transcriptFolderPath').textContent).toBe('C:\\Docs\\Transcripts');

        // transcript_status with has_entries: true enables the export button
        ctrl.applyStatus({ has_entries: true, count: 2, pack_enabled: true, run_active: true });
        expect(exportBtn.disabled).toBe(false);

        // Disabling the pack hides both export button and settings section again
        ctrl.setPackEnabled(false);
        expect(exportBtn.hidden).toBe(true);
        expect(settingsSection.hidden).toBe(true);
    });

    it('hides export button and folder actions for mobile user agents even if desktop_available is true', () => {
        const dom = buildTranscriptDom();
        const doc = dom.window.document;
        const ctrl = TranscriptController.create({
            document: doc,
            storage: createMemoryStorage(),
            fetch: vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }),
            t: (key) => key,
            userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148',
            isPackEnabled: () => true,
        });
        ctrl.init();
        ctrl.applyConfig({
            transcript_desktop_available: true,
            transcript_status: { has_entries: true, count: 1, pack_enabled: true, run_active: true },
            transcript_settings: { auto_save: false, format: 'srt', directory: 'C:\\Transcripts' },
        });

        expect(ctrl.isDesktopMainWindow()).toBe(false);
        expect(doc.getElementById('transcriptSettingsSection').hidden).toBe(false);
        expect(doc.getElementById('exportTranscriptButton').hidden).toBe(true);
        expect(doc.getElementById('transcriptFolderActions').hidden).toBe(true);
        expect(doc.getElementById('transcriptDesktopHint').hidden).toBe(false);
        expect(doc.getElementById('transcriptDesktopHint').textContent).toBe('transcript_desktop_only_hint');
    });
});

describe('TranscriptController settings persistence and actions', () => {
    it('persists autoSave and format toggles in transcriptSettings.v1 and syncs with backend', async () => {
        const dom = buildTranscriptDom();
        const doc = dom.window.document;
        const storage = createMemoryStorage();
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                status: 'ok',
                settings: { auto_save: true, format: 'txt', directory: 'C:\\DefaultDir' },
            }),
        });

        const ctrl = TranscriptController.create({
            document: doc,
            storage,
            fetch: fetchMock,
            t: (key) => key,
            userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            isPackEnabled: () => true,
        });
        ctrl.init();
        ctrl.applyConfig({
            transcript_desktop_available: true,
            transcript_settings: { auto_save: false, format: 'srt', directory: 'C:\\DefaultDir' },
        });

        // No stored settings yet -> syncToBackend is a no-op
        await expect(ctrl.syncToBackend()).resolves.toBe(false);
        expect(fetchMock).not.toHaveBeenCalled();

        // Toggle auto-save checkbox
        const autoSaveCheckbox = doc.querySelector('#transcriptAutoSavePicker input[type="checkbox"]');
        autoSaveCheckbox.checked = true;
        autoSaveCheckbox.dispatchEvent(new dom.window.Event('change'));
        await flushTasks(4);

        expect(JSON.parse(storage.getItem('transcriptSettings.v1'))).toEqual({
            autoSave: true,
            format: 'srt',
            directory: '',
        });
        expect(fetchMock).toHaveBeenCalledWith('/transcript/settings', expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ auto_save: true, format: 'srt' }),
        }));

        // Switch format to txt
        const formatSelect = doc.querySelector('#transcriptFormatPicker select');
        formatSelect.value = 'txt';
        formatSelect.dispatchEvent(new dom.window.Event('change'));
        await flushTasks(4);

        expect(JSON.parse(storage.getItem('transcriptSettings.v1'))).toEqual({
            autoSave: true,
            format: 'txt',
            directory: '',
        });
        expect(fetchMock).toHaveBeenLastCalledWith('/transcript/settings', expect.objectContaining({
            method: 'POST',
            body: JSON.stringify({ auto_save: true, format: 'txt' }),
        }));
    });

    it('handles manual export, change folder, and open folder actions with toasts', async () => {
        const dom = buildTranscriptDom();
        const doc = dom.window.document;
        const storage = createMemoryStorage();
        const showToast = vi.fn();
        const fetchMock = vi.fn(async (url) => {
            if (url === '/transcript/export') {
                return { ok: true, json: async () => ({ status: 'ok', path: 'C:\\Out\\run.srt' }) };
            }
            if (url === '/transcript/folder') {
                return {
                    ok: true,
                    json: async () => ({ status: 'ok', cancelled: false, directory: 'D:\\Picked' }),
                };
            }
            if (url === '/transcript/open-folder') {
                return { ok: true, json: async () => ({ status: 'ok', directory: 'D:\\Picked' }) };
            }
            return { ok: true, json: async () => ({}) };
        });

        const ctrl = TranscriptController.create({
            document: doc,
            storage,
            fetch: fetchMock,
            t: (key, vars) => (vars && vars.path ? `${key}:${vars.path}` : key),
            showToast,
            userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            isPackEnabled: () => true,
        });
        ctrl.init();
        ctrl.applyConfig({
            transcript_desktop_available: true,
            transcript_status: { has_entries: true, count: 1, pack_enabled: true, run_active: false },
            transcript_settings: { auto_save: false, format: 'srt', directory: 'C:\\DefaultDir' },
        });

        await expect(ctrl.handleExportClick()).resolves.toBe(true);
        expect(showToast).toHaveBeenCalledWith('transcript_export_success:C:\\Out\\run.srt', false);

        await expect(ctrl.handleChangeFolderClick()).resolves.toBe(true);
        expect(doc.getElementById('transcriptFolderPath').textContent).toBe('D:\\Picked');
        expect(JSON.parse(storage.getItem('transcriptSettings.v1')).directory).toBe('D:\\Picked');

        await expect(ctrl.handleOpenFolderClick()).resolves.toBe(true);
        expect(fetchMock).toHaveBeenCalledWith('/transcript/open-folder', { method: 'POST' });
    });
});

describe('full-page transcript wiring', () => {
    it('wires transcript pack, ui-config desktop flag, and transcript_status WebSocket frames in index.html + app.js', async () => {
        const page = await createPageHarness({
            localStorage: {
                'I18N.lang': 'en',
                'featurePacks.v1': JSON.stringify({
                    version: 1,
                    confirmed: true,
                    isUpgrade: false,
                    enabled: { vrchat: false, learning: false, obs: false, transcript: true },
                }),
            },
            uiConfig: {
                transcript_desktop_available: true,
                transcript_status: {
                    has_entries: false,
                    count: 0,
                    pack_enabled: true,
                    run_active: false,
                },
                transcript_settings: {
                    auto_save: false,
                    format: 'srt',
                    directory: 'C:\\Users\\Test\\Documents\\Realtime Subtitle\\Transcripts',
                },
            },
        });
        try {
            await flushTasks(20);
            const doc = page.document;
            const exportBtn = doc.getElementById('exportTranscriptButton');
            const section = doc.getElementById('transcriptSettingsSection');
            const folderPath = doc.getElementById('transcriptFolderPath');

            expect(section.hidden).toBe(false);
            expect(exportBtn.hidden).toBe(false);
            expect(exportBtn.disabled).toBe(true);
            expect(folderPath.textContent).toBe('C:\\Users\\Test\\Documents\\Realtime Subtitle\\Transcripts');

            // Deliver transcript_status frame via live WebSocket
            await page.emitFrame({
                type: 'transcript_status',
                has_entries: true,
                count: 3,
                pack_enabled: true,
                run_active: true,
            });
            await flushTasks(8);
            expect(exportBtn.disabled).toBe(false);

            // Backend feature_packs frame disabling transcript hides UI elements
            await page.emitFrame({
                type: 'feature_packs',
                packs: { vrchat: false, learning: false, obs: false, transcript: false },
            });
            await flushTasks(8);
            expect(section.hidden).toBe(true);
            expect(exportBtn.hidden).toBe(true);
        } finally {
            page.close();
        }
    });
});
