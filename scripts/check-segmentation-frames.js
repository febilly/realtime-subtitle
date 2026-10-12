// Frontend half of the segmentation audit: replay backend frames written by
// `python tools/segmentation_replay.py audit ... --frames-dir DIR` through
// the real frontend model and report invariant violations.
//
//   node scripts/check-segmentation-frames.js DIR/*.frames.jsonl [--verbose]
const fs = require('node:fs');
const path = require('node:path');
const {
    findChangedFinalLines,
    findSplitBoundaryMismatches,
} = require('../tests/frontend/helpers/segmentation-invariants');

function readFrames(file) {
    const rows = fs.readFileSync(file, 'utf8').split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
    const meta = rows.length && rows[0].type === 'replay_meta' ? rows.shift() : null;
    return { meta, frames: rows };
}

const verbose = process.argv.includes('--verbose');
const files = process.argv.slice(2).filter((arg) => arg !== '--verbose');
let failing = 0;
for (const file of files) {
    const { meta, frames } = readFrames(file);
    const changed = findChangedFinalLines(frames);
    const { rejoined, lateSplits } = findSplitBoundaryMismatches(frames);
    const config = meta && meta.config ? `${meta.config.translation}:${meta.config.target}` : '';
    const lateByType = {};
    lateSplits.forEach((item) => { lateByType[item.separator] = (lateByType[item.separator] || 0) + 1; });
    const bad = changed.length + rejoined.length;
    if (bad) failing += 1;
    console.log(
        `${path.basename(file)} ${config} changed_final_lines=${changed.length} `
        + `rejoined=${rejoined.length} late_splits=${JSON.stringify(lateByType)}`,
    );
    if (verbose) {
        changed.slice(0, 5).forEach((item) => console.log('   changed', JSON.stringify(item)));
        rejoined.slice(0, 5).forEach((item) => console.log('   rejoined', JSON.stringify(item)));
        lateSplits.slice(0, 5).forEach((item) => console.log('   late', JSON.stringify(item)));
    }
}
process.exitCode = failing ? 1 : 0;
