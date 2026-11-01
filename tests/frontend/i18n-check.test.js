const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { checkMessages, loadMessages } = require('../../scripts/check-i18n');

const ROOT = path.resolve(__dirname, '../..');

function messages() {
    const greetings = {
        en: 'Hello {name}', zh: '你好 {name}', ja: 'こんにちは {name}',
        ko: '안녕 {name}', ru: 'Привет {name}', es: 'Hola {name}', pt: 'Olá {name}',
    };
    return Object.fromEntries(Object.entries(greetings).map(([language, greeting]) => [
        language, { greeting, provider_soniox: 'Soniox' },
    ]));
}

describe('translation completeness gate', () => {
    it('accepts the real catalog without hiding missing entries behind English fallback', () => {
        const catalog = loadMessages(fs.readFileSync(path.join(ROOT, 'static/i18n.js'), 'utf8'));
        expect(checkMessages(catalog)).toEqual([]);
    });

    it('rejects missing tables, including deletion of an entire supported language', () => {
        const catalog = messages();
        delete catalog.ja;
        expect(checkMessages(catalog)).toContain('[ja] language table is missing');
        delete catalog.en;
        expect(checkMessages(catalog)).toEqual(['[en] English reference table is missing or empty']);
    });

    it('rejects missing keys in existing and newly added language tables', () => {
        const catalog = messages();
        delete catalog.ja.greeting;
        catalog.th = {};
        expect(checkMessages(catalog)).toContain('[ja] greeting: missing translation');
        expect(checkMessages(catalog)).toContain('[th] greeting: missing translation');
    });

    it.each(['', '  ', null, 42])('rejects empty or invalid text: %s', (value) => {
        const catalog = messages();
        catalog.ko.greeting = value;
        expect(checkMessages(catalog)).toContain('[ko] greeting: translation must be a non-empty string');
    });

    it('checks placeholder names and counts while allowing reordered placeholders', () => {
        const catalog = messages();
        catalog.en.greeting = 'Hello {name} at {time}';
        catalog.ja.greeting = '{time} にこんにちは {name}';
        expect(checkMessages(catalog)).not.toContain('[ja] greeting: placeholders differ from English');
        catalog.ja.greeting = 'こんにちは {user}';
        expect(checkMessages(catalog)).toContain('[ja] greeting: placeholders differ from English');
        catalog.ja.greeting = 'こんにちは {name} {time} {name}';
        expect(checkMessages(catalog)).toContain('[ja] greeting: placeholders differ from English');
    });

    it('rejects untranslated English while preserving exact brand exceptions', () => {
        const catalog = messages();
        expect(checkMessages(catalog)).toEqual([]);
        catalog.ru.greeting = ` ${catalog.en.greeting} `;
        expect(checkMessages(catalog)).toContain('[ru] greeting: untranslated English copy');
        catalog.en.provider_soniox = catalog.ru.provider_soniox = 'Changed English sentence';
        expect(checkMessages(catalog)).toContain('[ru] provider_soniox: untranslated English copy');
    });

    it('rejects unexpected keys and malformed catalogs', () => {
        const catalog = messages();
        catalog.pt.typo = 'Texto';
        expect(checkMessages(catalog)).toContain('[pt] typo: absent from English reference');
        expect(() => loadMessages('module.exports = {};')).toThrow('must export MESSAGES');
        expect(() => loadMessages('module.exports = {')).toThrow();
    });
});

function withRepository(callback) {
    const parent = path.join(ROOT, 'build', 'i18n-hook-tests');
    fs.mkdirSync(parent, { recursive: true });
    const repo = fs.mkdtempSync(path.join(parent, 'repo-'));
    const run = (command, args) => spawnSync(command, args, { cwd: repo, encoding: 'utf8', timeout: 15000 });
    const git = (...args) => run('git', ['-c', 'core.fsmonitor=false', ...args]);
    try {
        expect(git('init', '-q').status).toBe(0);
        for (const [key, value] of [
            ['user.name', 'Translation hook test'], ['user.email', 'hook-test@example.invalid'],
            ['core.autocrlf', 'false'], ['commit.gpgsign', 'false'],
        ]) expect(git('config', '--local', key, value).status).toBe(0);
        for (const file of ['scripts/check-i18n.js', 'scripts/install-git-hooks.js', '.githooks/pre-commit', '.gitattributes']) {
            fs.mkdirSync(path.dirname(path.join(repo, file)), { recursive: true });
            fs.copyFileSync(path.join(ROOT, file), path.join(repo, file));
        }
        fs.mkdirSync(path.join(repo, 'static'));
        const write = (catalog) => fs.writeFileSync(path.join(repo, 'static/i18n.js'),
            `module.exports = { MESSAGES: ${JSON.stringify(catalog)} };\n`);
        callback({ repo, run, git, write });
    } finally {
        // Never recursively remove anything outside this test's own temp root.
        if (path.dirname(repo) !== parent) throw new Error('Unsafe test cleanup path');
        fs.rmSync(repo, { recursive: true, force: true, maxRetries: 2, retryDelay: 50 });
    }
}

describe('Git pre-commit integration', () => {
    it('blocks incomplete staged translations even when the working copy is fixed', () => {
        withRepository(({ repo, run, git, write }) => {
            expect(git('config', '--local', 'core.autocrlf', 'true').status).toBe(0);
            const installed = run(process.execPath, ['scripts/install-git-hooks.js']);
            expect(installed.stderr).toBe('');
            expect(installed.status).toBe(0);
            expect(git('config', '--local', '--get', 'core.hooksPath').stdout.trim()).toBe('.githooks');
            const incomplete = messages();
            delete incomplete.ja.greeting;
            write(incomplete);
            expect(git('add', '.').status).toBe(0);
            const blocked = git('commit', '-qm', 'Missing translation');
            expect(blocked.status).not.toBe(0);
            expect(blocked.stderr).toContain('[ja] greeting: missing translation');

            write(messages());
            const stillBlocked = git('commit', '-qm', 'Unstaged fix');
            expect(stillBlocked.status).not.toBe(0);
            expect(stillBlocked.stderr).toContain('[ja] greeting: missing translation');

            expect(git('add', 'static/i18n.js').status).toBe(0);
            const accepted = git('commit', '-qm', 'Complete translations');
            expect(accepted.stdout + accepted.stderr).toContain('Translation check passed');
            expect(accepted.status).toBe(0);
            expect(git('checkout-index', '--force', '.githooks/pre-commit').status).toBe(0);
            expect(fs.readFileSync(path.join(repo, '.githooks/pre-commit'), 'utf8')).not.toContain('\r');

            // An unstaged regression must not block an unrelated valid commit.
            write(incomplete);
            fs.writeFileSync(path.join(repo, 'note.txt'), 'Another change\n');
            expect(git('add', 'note.txt').status).toBe(0);
            expect(git('commit', '-qm', 'Valid staged contents').status).toBe(0);
        });
    }, 30000);

    it('preserves an existing custom hooks path', () => {
        withRepository(({ run, git }) => {
            expect(git('config', '--local', 'core.hooksPath', 'custom-hooks').status).toBe(0);
            const result = run(process.execPath, ['scripts/install-git-hooks.js']);
            expect(result.status).toBe(1);
            expect(result.stderr).toContain('Existing Git hooks detected');
            expect(git('config', '--local', '--get', 'core.hooksPath').stdout.trim()).toBe('custom-hooks');
        });
    });
});
