const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const { createPageHarness } = require('../tests/frontend/helpers/page-harness');

const REPO_ROOT = path.resolve(__dirname, '..');
const DEFAULT_INPUT = path.join(REPO_ROOT, 'tests', 'fixtures', 'frontend-frames-smoke.jsonl');
const COMPARE_CHUNK_SIZE = 64 * 1024;

function parseFrameLine(line, filename, lineNumber) {
    try {
        return JSON.parse(line);
    } catch (error) {
        throw new Error(`Invalid JSON on ${filename}:${lineNumber}: ${error.message}`);
    }
}

function readFrames(filename) {
    return fs.readFileSync(filename, 'utf8')
        .split(/\r?\n/)
        .flatMap((line, index) => line.trim()
            ? [parseFrameLine(line, filename, index + 1)]
            : []);
}

async function* streamFrames(filename) {
    const source = fs.createReadStream(filename, { encoding: 'utf8' });
    const lines = readline.createInterface({ input: source, crlfDelay: Infinity });
    let lineNumber = 0;
    try {
        for await (const line of lines) {
            lineNumber += 1;
            if (line.trim()) yield parseFrameLine(line, filename, lineNumber);
        }
    } finally {
        lines.close();
        source.destroy();
    }
}

function defaultGoldenDestination() {
    const outputDir = path.join(REPO_ROOT, 'logs', 'frontend-golden');
    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..+/, '').replace('T', '_');
    return path.join(outputDir, `golden_${stamp}_${process.pid}_${Date.now()}.jsonl`);
}

async function generateGolden(inputFile = DEFAULT_INPUT, outputFile = null, options = {}) {
    const input = path.resolve(inputFile);
    const destination = outputFile ? path.resolve(outputFile) : defaultGoldenDestination();
    if (input === destination) {
        throw new Error('Golden output must be different from the input frame recording');
    }

    fs.mkdirSync(path.dirname(destination), { recursive: true });
    const output = await fs.promises.open(destination, 'w');
    let page = null;
    try {
        page = await createPageHarness(options.harness || options);
        let index = 0;
        for await (const frame of streamFrames(input)) {
            await page.emitFrame(frame);
            const snapshot = { index, type: frame.type, html: page.html() };
            await output.write(`${JSON.stringify(snapshot)}\n`);
            index += 1;
        }
    } finally {
        if (page) page.close();
        await output.close();
    }
    return destination;
}

async function compareGoldenFiles(expectedFile, actualFile) {
    const expected = path.resolve(expectedFile);
    const actual = path.resolve(actualFile);
    const [expectedStat, actualStat] = await Promise.all([
        fs.promises.stat(expected),
        fs.promises.stat(actual),
    ]);
    const expectedHandle = await fs.promises.open(expected, 'r');
    let actualHandle;
    try {
        actualHandle = await fs.promises.open(actual, 'r');
    } catch (error) {
        await expectedHandle.close();
        throw error;
    }
    const expectedBuffer = Buffer.allocUnsafe(COMPARE_CHUNK_SIZE);
    const actualBuffer = Buffer.allocUnsafe(COMPARE_CHUNK_SIZE);
    let offset = 0;
    try {
        while (true) {
            const [left, right] = await Promise.all([
                expectedHandle.read(expectedBuffer, 0, COMPARE_CHUNK_SIZE, offset),
                actualHandle.read(actualBuffer, 0, COMPARE_CHUNK_SIZE, offset),
            ]);
            const sharedLength = Math.min(left.bytesRead, right.bytesRead);
            for (let index = 0; index < sharedLength; index += 1) {
                if (expectedBuffer[index] !== actualBuffer[index]) {
                    return {
                        equal: false,
                        offset: offset + index,
                        expectedSize: expectedStat.size,
                        actualSize: actualStat.size,
                    };
                }
            }
            if (left.bytesRead !== right.bytesRead) {
                return {
                    equal: false,
                    offset: offset + sharedLength,
                    expectedSize: expectedStat.size,
                    actualSize: actualStat.size,
                };
            }
            if (left.bytesRead === 0) {
                return {
                    equal: true,
                    offset: null,
                    expectedSize: expectedStat.size,
                    actualSize: actualStat.size,
                };
            }
            offset += left.bytesRead;
        }
    } finally {
        await Promise.all([expectedHandle.close(), actualHandle.close()]);
    }
}

async function assertGoldenFilesEqual(expectedFile, actualFile) {
    const comparison = await compareGoldenFiles(expectedFile, actualFile);
    if (!comparison.equal) {
        throw new Error(
            `Golden mismatch at byte ${comparison.offset} `
            + `(expected ${comparison.expectedSize} bytes, actual ${comparison.actualSize} bytes)`
        );
    }
    return comparison;
}

async function checkGolden(expectedFile, inputFile = DEFAULT_INPUT, options = {}) {
    const tempDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), 'frontend-golden-check-'));
    const actualFile = path.join(tempDir, 'actual.jsonl');
    try {
        await generateGolden(inputFile, actualFile, options);
        await assertGoldenFilesEqual(expectedFile, actualFile);
        return path.resolve(expectedFile);
    } finally {
        await fs.promises.unlink(actualFile).catch(() => {});
        await fs.promises.rmdir(tempDir).catch(() => {});
    }
}

function extractModeOption(argv) {
    const args = [...argv];
    let translationUiMode = 'hybrid';
    for (let index = 0; index < args.length; index += 1) {
        if (args[index] === '--mode') {
            if (!args[index + 1]) throw new Error('--mode requires fast, hybrid, or accurate');
            translationUiMode = args[index + 1];
            args.splice(index, 2);
            index -= 1;
        } else if (args[index].startsWith('--mode=')) {
            translationUiMode = args[index].slice('--mode='.length);
            args.splice(index, 1);
            index -= 1;
        }
    }
    return { args, options: { translationUiMode } };
}

function usage() {
    return [
        'Usage:',
        '  node scripts/generate-frontend-golden.js generate [frames.jsonl] [output.jsonl] [--mode fast|hybrid|accurate]',
        '  node scripts/generate-frontend-golden.js compare <expected.jsonl> <actual.jsonl>',
        '  node scripts/generate-frontend-golden.js check <expected.jsonl> [frames.jsonl] [--mode fast|hybrid|accurate]',
        '  node scripts/generate-frontend-golden.js [frames.jsonl] [output.jsonl]  # legacy generate form',
    ].join('\n');
}

async function runCli(argv = process.argv.slice(2)) {
    const parsed = extractModeOption(argv);
    const args = parsed.args;
    const command = ['generate', 'compare', 'check'].includes(args[0]) ? args.shift() : 'legacy-generate';

    if (command === 'compare') {
        if (args.length !== 2) throw new Error(`${usage()}\n\ncompare requires expected and actual files`);
        await assertGoldenFilesEqual(args[0], args[1]);
        process.stdout.write(`Golden files match: ${path.resolve(args[0])}\n`);
        return;
    }
    if (command === 'check') {
        if (!args[0] || args.length > 2) throw new Error(`${usage()}\n\ncheck requires an expected file`);
        const expected = await checkGolden(args[0], args[1] || DEFAULT_INPUT, parsed.options);
        process.stdout.write(`Golden check passed: ${expected}\n`);
        return;
    }
    if (args.length > 2) throw new Error(usage());
    const destination = await generateGolden(args[0] || DEFAULT_INPUT, args[1] || null, parsed.options);
    process.stdout.write(`${destination}\n`);
}

if (require.main === module) {
    runCli().catch((error) => {
        process.stderr.write(`${error.stack || error}\n`);
        process.exitCode = 1;
    });
}

module.exports = {
    DEFAULT_INPUT,
    assertGoldenFilesEqual,
    checkGolden,
    compareGoldenFiles,
    generateGolden,
    readFrames,
    runCli,
    streamFrames,
};
