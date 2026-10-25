(function (root) {
    'use strict';

    // Frontend visualization modes for LLM refine edits (mirrors config.py
    // LLM_REFINE_DIFF_MODE):
    // - "off":                 show the refined translation as-is, no diff
    // - "additions":           word/char diff, added text highlighted in green
    // - "additions_deletions": like "additions" but deleted text is also shown in
    //                          red with strikethrough
    // - "two_lines":           no inline diff; the full original translation on one
    //                          line (red background), the refined one on the next
    //                          line (green background)
    const DIFF_MODES = ['off', 'additions', 'additions_deletions', 'two_lines'];

    function normalizeMode(mode) {
        const value = String(mode == null ? '' : mode).trim().toLowerCase();
        return DIFF_MODES.indexOf(value) !== -1 ? value : 'off';
    }

    function create(options = {}) {
        const escapeHtml = typeof options.escapeHtml === 'function'
            ? options.escapeHtml
            : (text) => String(text == null ? '' : text);

        function containsCjkOrJapanese(text) {
            // Han (CJK ideographs), Hiragana, Katakana.
            // We intentionally do NOT include Hangul here; Korean generally benefits from word-level diff.
            const value = (text || '').toString();
            return /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/.test(value);
        }

        function shouldUseCharDiff(original, refined) {
            return containsCjkOrJapanese(original) || containsCjkOrJapanese(refined);
        }

        function tokenizeForDiff(text, mode) {
            // Tokenize for alignment while ignoring whitespace differences.
            // mode: 'char' | 'word'
            const value = (text || '').toString();
            const out = [];

            if (mode === 'char') {
                for (let idx = 0; idx < value.length; idx++) {
                    const ch = value[idx];
                    if (/\s/.test(ch)) {
                        continue;
                    }
                    out.push({ text: ch, start: idx, end: idx + 1 });
                }
                return out;
            }

            // Word-level tokenization for non-CJK languages.
            // We align on words; punctuation becomes its own token.
            // Uses Unicode character properties (modern Chromium / modern browsers).
            const wordRe = /[\p{L}\p{N}]+(?:[’'\-][\p{L}\p{N}]+)*/gu;
            let idx = 0;
            while (idx < value.length) {
                const ch = value[idx];
                if (/\s/.test(ch)) {
                    idx++;
                    continue;
                }

                wordRe.lastIndex = idx;
                const m = wordRe.exec(value);
                if (m && m.index === idx) {
                    const w = m[0] || '';
                    out.push({ text: w, start: idx, end: idx + w.length });
                    idx += w.length;
                    continue;
                }

                // Punctuation / symbol as a single-character token.
                out.push({ text: ch, start: idx, end: idx + 1 });
                idx++;
            }

            return out;
        }

        function renderInlineDiff(original, refined, showDeletions) {
            const a = (original || '').toString();
            const b = (refined || '').toString();

            // Guardrails: LCS is O(n*m). Keep it safe.
            if (a.length > 12000 || b.length > 12000) {
                return escapeHtml(b);
            }

            const mode = shouldUseCharDiff(a, b) ? 'char' : 'word';
            const A = tokenizeForDiff(a, mode);
            const B = tokenizeForDiff(b, mode);

            const n = A.length;
            const m = B.length;
            if (n === 0 && m === 0) {
                return escapeHtml(b);
            }

            // If this would be too expensive, skip highlighting.
            if (n * m > 400000) {
                return escapeHtml(b);
            }

            // LCS DP table (typed arrays for lower overhead).
            const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
            for (let i = 1; i <= n; i++) {
                const ai = A[i - 1].text;
                const row = dp[i];
                const prevRow = dp[i - 1];
                for (let j = 1; j <= m; j++) {
                    if (ai === B[j - 1].text) {
                        row[j] = prevRow[j - 1] + 1;
                    } else {
                        const up = prevRow[j];
                        const left = row[j - 1];
                        row[j] = up >= left ? up : left;
                    }
                }
            }

            const ops = [];
            let i = n;
            let j = m;
            while (i > 0 || j > 0) {
                if (i > 0 && j > 0 && A[i - 1].text === B[j - 1].text) {
                    ops.push({ type: 'eq', start: B[j - 1].start, end: B[j - 1].end });
                    i--;
                    j--;
                } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
                    ops.push({ type: 'ins', start: B[j - 1].start, end: B[j - 1].end });
                    j--;
                } else {
                    ops.push({ type: 'del', text: A[i - 1].text });
                    i--;
                }
            }
            ops.reverse();

            const parts = [];
            const pushDel = (text) => {
                if (!showDeletions) return;
                if (!text) return;
                parts.push(`<span class="llm-diff-del">${escapeHtml(text)}</span>`);
            };
            const pushIns = (text) => {
                if (!text) return;
                parts.push(`<span class="llm-diff-ins">${escapeHtml(text)}</span>`);
            };

            let refinedPos = 0;
            let delBuffer = '';
            let insBuffer = '';

            const isWordChar = (s) => {
                if (!s) return false;
                try {
                    return /[\p{L}\p{N}]/u.test(s);
                } catch (e) {
                    return /[A-Za-z0-9]/.test(s);
                }
            };

            const appendDeletedToken = (tokenText) => {
                if (!showDeletions) {
                    return;
                }
                const t = (tokenText || '').toString();
                if (!t) return;
                if (mode === 'word' && delBuffer) {
                    const last = delBuffer[delBuffer.length - 1];
                    const first = t[0];
                    if (isWordChar(last) && isWordChar(first)) {
                        delBuffer += ' ';
                    }
                }
                delBuffer += t;
            };

            const flushDel = () => {
                if (!showDeletions) {
                    delBuffer = '';
                    return;
                }
                if (delBuffer) {
                    pushDel(delBuffer);
                    delBuffer = '';
                }
            };
            const flushIns = () => {
                if (insBuffer) {
                    pushIns(insBuffer);
                    insBuffer = '';
                }
            };

            for (const op of ops) {
                if (op.type !== 'ins') {
                    flushIns();
                }
                if (op.type !== 'del') {
                    flushDel();
                }

                if (op.type === 'del') {
                    // Deleted non-whitespace characters are shown in red with strikethrough.
                    if (showDeletions) {
                        appendDeletedToken(op.text);
                    }
                    continue;
                }

                const start = op.start;
                const end = op.end;
                if (typeof start !== 'number' || typeof end !== 'number' || start < 0 || end < start || end > b.length) {
                    continue;
                }

                // Important: when we ignore whitespace in the alignment, two consecutive non-whitespace insertions
                // may still be separated by whitespace in the refined string (e.g. inserted multi-word phrase).
                // If we buffer insertions across that gap, we'd output the whitespace *before* the buffered letters,
                // which breaks languages that use spaces (English, etc.).
                if (op.type === 'ins' && start > refinedPos) {
                    flushIns();
                }

                // Always output refined whitespace (and any other chars between aligned non-ws chars) as plain.
                if (start > refinedPos) {
                    parts.push(escapeHtml(b.slice(refinedPos, start)));
                }

                const tokenText = b.slice(start, end);
                if (op.type === 'eq') {
                    parts.push(escapeHtml(tokenText));
                } else if (op.type === 'ins') {
                    // Inserted non-whitespace characters are shown in green.
                    insBuffer += tokenText;
                }

                refinedPos = end;
            }

            flushIns();
            flushDel();

            if (refinedPos < b.length) {
                parts.push(escapeHtml(b.slice(refinedPos)));
            }

            return parts.join('');
        }

        function renderTwoLines(original, refined) {
            const a = (original || '').toString();
            const b = (refined || '').toString();
            return `<span class="llm-diff-line-old">${escapeHtml(a)}</span>`
                + `<span class="llm-diff-line-new">${escapeHtml(b)}</span>`;
        }

        function render(original, refined, mode) {
            const normalizedMode = normalizeMode(mode);
            if (normalizedMode === 'off') {
                return escapeHtml(refined);
            }
            if (normalizedMode === 'two_lines') {
                return renderTwoLines(original, refined);
            }
            return renderInlineDiff(
                original,
                refined,
                normalizedMode === 'additions_deletions',
            );
        }

        return { render };
    }

    const api = { create, normalizeMode, DIFF_MODES };
    root.TranslationDiff = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
