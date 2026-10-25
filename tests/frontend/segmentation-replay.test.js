// Frontend half of the segmentation replay suite: the backend output of each
// anonymized real recording (tests/fixtures/segmentation-replay/*.expected.jsonl,
// kept in sync by tests/test_segmentation_replay.py) replayed through the real
// frontend session and render model.
const fs = require('node:fs');
const path = require('node:path');
const {
    findChangedFinalLines,
    findSplitBoundaryMismatches,
} = require('./helpers/segmentation-invariants');

const FIXTURE_DIR = path.join(__dirname, '..', 'fixtures', 'segmentation-replay');
const fixtures = fs.readdirSync(FIXTURE_DIR)
    .filter((name) => name.endsWith('.expected.jsonl'))
    .map((name) => name.replace('.expected.jsonl', ''));

function readFrames(name) {
    return fs.readFileSync(path.join(FIXTURE_DIR, `${name}.expected.jsonl`), 'utf8')
        .split(/\r?\n/)
        .filter(Boolean)
        .map((line) => JSON.parse(line));
}

describe('segmentation replay of real recordings', () => {
    it('has fixtures', () => {
        expect(fixtures.length).toBeGreaterThan(0);
    });

    describe.each(fixtures)('%s', (name) => {
        const frames = readFrames(name);

        it('never changes or drops a completed final line', () => {
            expect(findChangedFinalLines(frames)).toEqual([]);
        });

        it('breaks every final line where the non-final preview split it', () => {
            expect(findSplitBoundaryMismatches(frames).rejoined).toEqual([]);
        });
    });
});
