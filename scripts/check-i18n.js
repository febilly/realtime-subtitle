const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const CATALOG = 'static/i18n.js';
const REQUIRED_LANGUAGES = ['en', 'zh', 'ja', 'ko', 'ru', 'es', 'pt'];
// Intentional identical wording: brands, abbreviations, and natural cognates.
// Scope exceptions to both the language and exact English value so later copy
// changes do not silently inherit an old exception.
const SHARED_COPY = { provider_soniox: 'Soniox', provider_gemini: 'Gemini' };
const SAME_AS_ENGLISH = {
    zh: { api_key: 'API Key' },
    es: { error_title: 'Error', provider_local: 'Local' },
    pt: {
        provider_local: 'Local', login_method_status: 'Status',
        local_dialog_model_meta: 'Download ~{download} · VRAM ~{vram}',
    },
};

function loadMessages(source) {
    // The catalog is trusted repository code. Keep its evaluation independent
    // of browser language selection and never expose Node's require/process.
    const sandbox = { module: { exports: {} } };
    vm.runInNewContext(source, sandbox, { filename: CATALOG, timeout: 1000 });
    const messages = sandbox.module.exports.MESSAGES;
    if (!messages || typeof messages !== 'object' || Array.isArray(messages)) {
        throw new Error(`${CATALOG} must export MESSAGES`);
    }
    return messages;
}

function placeholders(text) {
    return [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort().join(',');
}

function checkMessages(messages) {
    const errors = [];
    const isTable = (table) => table && typeof table === 'object' && !Array.isArray(table);
    if (!isTable(messages.en) || !Object.keys(messages.en).length) {
        return ['[en] English reference table is missing or empty'];
    }
    const english = messages.en;
    for (const language of new Set([...REQUIRED_LANGUAGES, ...Object.keys(messages)])) {
        const table = messages[language];
        if (!isTable(table)) {
            errors.push(`[${language}] language table is missing`);
            continue;
        }
        for (const [key, reference] of Object.entries(english)) {
            if (!Object.hasOwn(table, key)) {
                errors.push(`[${language}] ${key}: missing translation`);
                continue;
            }
            const value = table[key];
            if (typeof value !== 'string' || !value.trim()) {
                errors.push(`[${language}] ${key}: translation must be a non-empty string`);
                continue;
            }
            if (typeof reference !== 'string' || !reference.trim()) {
                // The English row itself produces the diagnostic.
                continue;
            }
            if (placeholders(value) !== placeholders(reference)) {
                errors.push(`[${language}] ${key}: placeholders differ from English`);
            }
            const allowed = Object.hasOwn(SHARED_COPY, key) ? SHARED_COPY[key] : SAME_AS_ENGLISH[language]?.[key];
            if (language !== 'en' && value.trim() === reference.trim() && reference !== allowed) {
                errors.push(`[${language}] ${key}: untranslated English copy`);
            }
        }
        for (const key of Object.keys(table)) {
            if (!Object.hasOwn(english, key)) errors.push(`[${language}] ${key}: absent from English reference`);
        }
    }
    return errors;
}

function main(args = process.argv.slice(2)) {
    try {
        if (args.some((arg) => arg !== '--staged')) throw new Error('Usage: node scripts/check-i18n.js [--staged]');
        const staged = args.includes('--staged');
        const source = staged
            ? execFileSync('git', ['show', `:${CATALOG}`], { cwd: ROOT, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 })
            : fs.readFileSync(path.join(ROOT, CATALOG), 'utf8');
        const messages = loadMessages(source);
        const errors = checkMessages(messages);
        if (errors.length) {
            console.error(`Translation check failed (${staged ? 'staged' : 'working tree'} ${CATALOG}):`);
            errors.forEach((error) => console.error(`  ${error}`));
            console.error('Complete the translations and git add static/i18n.js before committing.');
            return 1;
        }
        console.log(`Translation check passed: ${Object.keys(messages).length} languages, ${Object.keys(messages.en).length} keys each (${staged ? 'staged' : 'working tree'}).`);
        return 0;
    } catch (error) {
        console.error(`Translation check failed: ${error.message}`);
        return 1;
    }
}

module.exports = { checkMessages, loadMessages, main };
if (require.main === module) process.exitCode = main();
