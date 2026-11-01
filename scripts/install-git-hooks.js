const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
function git(args) {
    return spawnSync('git', args, { cwd: root, encoding: 'utf8' });
}

const repo = git(['rev-parse', '--show-toplevel']);
if (repo.status !== 0 || path.resolve(repo.stdout.trim()) !== root) {
    console.log('Git hook setup skipped: this package is not a Git checkout.');
} else {
    const existing = git(['config', '--get', 'core.hooksPath']);
    const configured = existing.stdout.trim();
    const hookDir = git(['rev-parse', '--git-path', 'hooks']);
    const hook = path.resolve(root, hookDir.stdout.trim(), 'pre-commit');
    if ((configured && configured !== '.githooks') || (!configured && fs.existsSync(hook))) {
        console.error('Existing Git hooks detected. Integrate the translation check before changing core.hooksPath.');
        process.exitCode = 1;
    } else {
        fs.chmodSync(path.join(root, '.githooks', 'pre-commit'), 0o755);
        const result = git(['config', '--local', 'core.hooksPath', '.githooks']);
        if (result.status !== 0) {
            console.error(result.stderr || 'Unable to install Git hooks.');
            process.exitCode = 1;
        } else {
            console.log('Git translation pre-commit check enabled (.githooks).');
        }
    }
}
