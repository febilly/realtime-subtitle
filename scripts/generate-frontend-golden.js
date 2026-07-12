const fs = require('node:fs');
const path = require('node:path');
const { createPageHarness } = require('../tests/frontend/helpers/page-harness');

const REPO_ROOT = path.resolve(__dirname, '..');
const DEFAULT_INPUT = path.join(REPO_ROOT, 'tests', 'fixtures', 'frontend-frames-smoke.jsonl');

function readFrames(filename) {
    return fs.readFileSync(filename, 'utf8')
        .split(/\r?\n/)
        .filter((line) => line.trim())
        .map((line, index) => {
            try {
                return JSON.parse(line);
            } catch (error) {
                throw new Error(`Invalid JSON on ${filename}:${index + 1}: ${error.message}`);
            }
        });
}

async function generateGolden(inputFile = DEFAULT_INPUT, outputFile = null) {
    const frames = readFrames(path.resolve(inputFile));
    const outputDir = path.join(REPO_ROOT, 'logs', 'frontend-golden');
    fs.mkdirSync(outputDir, { recursive: true });
    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..+/, '').replace('T', '_');
    const destination = outputFile
        ? path.resolve(outputFile)
        : path.join(outputDir, `golden_${stamp}.jsonl`);
    const page = await createPageHarness();
    const snapshots = [];
    try {
        for (const [index, frame] of frames.entries()) {
            await page.emitFrame(frame);
            snapshots.push(JSON.stringify({ index, type: frame.type, html: page.html() }));
        }
    } finally {
        page.close();
    }
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.writeFileSync(destination, `${snapshots.join('\n')}\n`, 'utf8');
    return destination;
}

if (require.main === module) {
    generateGolden(process.argv[2] || DEFAULT_INPUT, process.argv[3] || null)
        .then((destination) => process.stdout.write(`${destination}\n`))
        .catch((error) => {
            process.stderr.write(`${error.stack || error}\n`);
            process.exitCode = 1;
        });
}

module.exports = { DEFAULT_INPUT, generateGolden, readFrames };
