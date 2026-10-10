//! Pure, width-agnostic single-line fitting used by the fixed-slot HUD rows.
//!
//! The real renderer passes a DirectWrite-derived whole-string measurement;
//! tests can pass a deterministic scalar advance so truncation edges are exact.
//!
//! Budget rule: the ellipsis is part of the line, so its own advance is reserved
//! from `max_advance` before any body text is kept. The fitted result is never
//! wider than `max_advance`:
//!
//! * `max_advance < advance('…')` -> empty string (nothing readable fits);
//! * `max_advance == advance('…')` -> only the ellipsis;
//! * otherwise body text is kept while `kept + '…'` stays within `max_advance`.
//!
//! Truncation never splits a grapheme-ish cluster (combining marks, variation
//! selectors, ZWJ joins, regional-indicator pairs, Indic virama conjuncts,
//! keycaps, emoji tag sequences, skin-tone modifiers).

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Truncation {
    /// Keep the beginning and append a trailing ellipsis.
    Trailing,
    /// Keep the newest tail and prepend a leading ellipsis.
    Leading,
}

pub fn fit_row_text(
    text: &str,
    max_advance: f32,
    direction: Truncation,
    advance: &dyn Fn(char) -> f32,
) -> String {
    fit_row_text_with_measure(text, max_advance, direction, &|candidate| {
        measure_width(candidate, advance)
    })
}

/// Fit a row using a measurement function for the complete candidate string.
/// This is used by the platform renderer so shaping, kerning and fallback are
/// included in every budget decision. The public scalar-advance helper above
/// remains available for deterministic callers and tests.
pub(crate) fn fit_row_text_with_measure(
    text: &str,
    max_advance: f32,
    direction: Truncation,
    measure: &dyn Fn(&str) -> f32,
) -> String {
    if text.is_empty() {
        return String::new();
    }
    if max_advance.is_nan() || max_advance == f32::INFINITY {
        return text.to_owned();
    }
    if max_advance == f32::NEG_INFINITY {
        return String::new();
    }
    let max = max_advance.max(0.0);
    if measure(text) <= max {
        return text.to_owned();
    }
    let ellipsis_width = measure("…").max(0.0);
    if max < ellipsis_width {
        return String::new();
    }
    let clusters = clusters(text);

    match direction {
        Truncation::Trailing => {
            let mut kept: Vec<String> = Vec::new();
            for cluster in &clusters {
                let mut candidate = kept.concat();
                candidate.push_str(cluster);
                candidate.push('…');
                if measure(&candidate) > max {
                    break;
                }
                kept.push(cluster.clone());
            }
            // Opening punctuation must not end a truncated line.
            while kept
                .last()
                .is_some_and(|cluster| is_opening_punctuation(cluster_base(cluster)))
            {
                kept.pop();
            }
            let mut out = kept.concat();
            out.push('…');
            fit_strictly_with_measure(out, kept, ellipsis_width, max, direction, measure)
        }
        Truncation::Leading => {
            let mut kept: Vec<String> = Vec::new();
            for cluster in clusters.iter().rev() {
                let mut candidate = String::from('…');
                candidate.push_str(cluster);
                for existing in kept.iter().rev() {
                    candidate.push_str(existing);
                }
                if measure(&candidate) > max {
                    break;
                }
                kept.push(cluster.clone());
            }
            kept.reverse();
            // Closing punctuation must not begin a truncated line.
            while kept
                .first()
                .is_some_and(|cluster| is_closing_punctuation(cluster_base(cluster)))
            {
                kept.remove(0);
            }
            let mut out = String::from('…');
            out.push_str(&kept.concat());
            fit_strictly_with_measure(out, kept, ellipsis_width, max, direction, measure)
        }
    }
}

/// Final guard: guarantee the returned string's measured width never exceeds
/// `max`, independent of floating-point summation order. Trims whole clusters
/// from the appropriate end until the guard holds.
fn fit_strictly_with_measure(
    mut candidate: String,
    mut kept: Vec<String>,
    ellipsis_width: f32,
    max: f32,
    direction: Truncation,
    measure: &dyn Fn(&str) -> f32,
) -> String {
    while measure(&candidate) > max && !kept.is_empty() {
        match direction {
            Truncation::Trailing => {
                kept.pop();
                candidate = kept.concat();
                candidate.push('…');
            }
            Truncation::Leading => {
                kept.remove(0);
                candidate = String::from('…');
                candidate.push_str(&kept.concat());
            }
        }
    }
    if measure(&candidate) > max {
        // Only the ellipsis can remain, and `max >= ellipsis_width` was checked.
        return if ellipsis_width <= max {
            String::from('…')
        } else {
            String::new()
        };
    }
    candidate
}

fn measure_width(text: &str, advance: &dyn Fn(char) -> f32) -> f32 {
    text.chars().map(|ch| advance(ch).max(0.0)).sum()
}

/// Split into minimal grapheme-ish clusters: a base scalar plus attached
/// combining marks, variation selectors, skin-tone modifiers, keycap marks,
/// emoji tag characters, regional-indicator pairs, ZWJ joins, and the
/// consonant following an Indic virama.
fn clusters(text: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut join_next = false;
    for ch in text.chars() {
        let attach = join_next
            || is_combining(ch)
            || (is_regional_indicator(ch) && last_cluster_is_single_regional_indicator(&out));
        if attach {
            if let Some(last) = out.last_mut() {
                last.push(ch);
                join_next = joins_following(ch);
                continue;
            }
        }
        out.push(ch.to_string());
        join_next = joins_following(ch);
    }
    out
}

fn joins_following(ch: char) -> bool {
    is_virama(ch) || ch == '\u{200D}' || ch == '\u{200C}'
}

fn last_cluster_is_single_regional_indicator(clusters: &[String]) -> bool {
    let Some(last) = clusters.last() else {
        return false;
    };
    let mut chars = last.chars();
    matches!((chars.next(), chars.next()), (Some(ch), None) if is_regional_indicator(ch))
}

fn cluster_base(cluster: &str) -> char {
    cluster
        .chars()
        .find(|ch| !is_combining(*ch))
        .or_else(|| cluster.chars().next())
        .unwrap_or('\u{0}')
}

fn is_regional_indicator(ch: char) -> bool {
    matches!(ch as u32, 0x1F1E6..=0x1F1FF)
}

fn is_virama(ch: char) -> bool {
    matches!(
        ch as u32,
        0x094D
            | 0x09CD
            | 0x0A4D
            | 0x0ACD
            | 0x0B4D
            | 0x0BCD
            | 0x0C4D
            | 0x0CCD
            | 0x0D3B
            | 0x0D3C
            | 0x0D4D
            | 0x0DCA
            | 0x0E3A
            | 0x0F84
            | 0x1039
            | 0x103A
            | 0x1714
            | 0x1734
            | 0x17D2
            | 0x1A60
            | 0x1B44
            | 0x1BAA
            | 0x1BAB
            | 0x1BF2
            | 0x1BF3
            | 0x2D7F
            | 0xA806
            | 0xA8C4
            | 0xA953
            | 0xA9C0
            | 0xAAF6
            | 0xABED
            | 0x10A3F
            | 0x11046
            | 0x11070
            | 0x1107F
            | 0x110B9
            | 0x11133
            | 0x11134
            | 0x111C0
            | 0x11235
            | 0x112EA
            | 0x1134D
            | 0x11442
            | 0x11446
            | 0x114C2
            | 0x115BF
            | 0x1163F
            | 0x116B6
            | 0x1172B
            | 0x11839
            | 0x1193D
            | 0x119E0
            | 0x11A34
            | 0x11A47
            | 0x11A99
            | 0x11C3F
            | 0x11D44
            | 0x11D45
            | 0x11D97
    )
}

fn is_combining(ch: char) -> bool {
    matches!(ch as u32,
        0x0300..=0x036F
            | 0x0483..=0x0489
            | 0x0591..=0x05BD
            | 0x05BF
            | 0x05C1..=0x05C2
            | 0x05C4..=0x05C5
            | 0x05C7
            | 0x0610..=0x061A
            | 0x064B..=0x065F
            | 0x0670
            | 0x06D6..=0x06DC
            | 0x06DF..=0x06E4
            | 0x06E7..=0x06E8
            | 0x06EA..=0x06ED
            | 0x0711
            | 0x0730..=0x074A
            | 0x07A6..=0x07B0
            | 0x07EB..=0x07F3
            | 0x0816..=0x0819
            | 0x081B..=0x0823
            | 0x0825..=0x0827
            | 0x0829..=0x082D
            | 0x0859..=0x085B
            | 0x08D3..=0x08E1
            | 0x08E3..=0x0903
            | 0x093A..=0x094F
            | 0x0951..=0x0957
            | 0x0962..=0x0963
            | 0x0981..=0x0983
            | 0x09BC
            | 0x09BE..=0x09CD
            | 0x09D7
            | 0x09E2..=0x09E3
            | 0x0A01..=0x0A03
            | 0x0A3C
            | 0x0A3E..=0x0A4D
            | 0x0A51
            | 0x0A70..=0x0A71
            | 0x0A75
            | 0x0A81..=0x0A83
            | 0x0ABC
            | 0x0ABE..=0x0ACD
            | 0x0AE2..=0x0AE3
            | 0x0B01..=0x0B03
            | 0x0B3C
            | 0x0B3E..=0x0B4D
            | 0x0B56..=0x0B57
            | 0x0B62..=0x0B63
            | 0x0B82
            | 0x0BBE..=0x0BCD
            | 0x0BD7
            | 0x0C00..=0x0C04
            | 0x0C3E..=0x0C4D
            | 0x0C55..=0x0C56
            | 0x0C62..=0x0C63
            | 0x0C81..=0x0C83
            | 0x0CBC
            | 0x0CBE..=0x0CCD
            | 0x0CD5..=0x0CD6
            | 0x0CE2..=0x0CE3
            | 0x0D00..=0x0D03
            | 0x0D3E..=0x0D4D
            | 0x0D57
            | 0x0D62..=0x0D63
            | 0x0D82..=0x0D83
            | 0x0DCA
            | 0x0DCF..=0x0DDF
            | 0x0DF2..=0x0DF3
            | 0x0E31
            | 0x0E34..=0x0E3A
            | 0x0E47..=0x0E4E
            | 0x0EB1
            | 0x0EB4..=0x0EBC
            | 0x0EC8..=0x0ECD
            | 0x0F18..=0x0F19
            | 0x0F35
            | 0x0F37
            | 0x0F39
            | 0x0F71..=0x0F84
            | 0x0F86..=0x0F87
            | 0x0F8D..=0x0FBC
            | 0x0FC6
            | 0x102B..=0x103E
            | 0x1056..=0x1059
            | 0x105E..=0x1060
            | 0x1062..=0x1064
            | 0x1067..=0x106D
            | 0x1071..=0x1074
            | 0x1082..=0x108D
            | 0x108F
            | 0x109A..=0x109D
            | 0x135D..=0x135F
            | 0x1712..=0x1714
            | 0x1732..=0x1734
            | 0x1752..=0x1753
            | 0x1772..=0x1773
            | 0x17B4..=0x17D3
            | 0x17DD
            | 0x180B..=0x180D
            | 0x1885..=0x1886
            | 0x18A9
            | 0x1920..=0x192B
            | 0x1930..=0x193B
            | 0x1A17..=0x1A1B
            | 0x1A55..=0x1A7F
            | 0x1AB0..=0x1AFF
            | 0x1B00..=0x1B04
            | 0x1B34..=0x1B44
            | 0x1B6B..=0x1B73
            | 0x1B80..=0x1B82
            | 0x1BA1..=0x1BAD
            | 0x1BE6..=0x1BF3
            | 0x1C24..=0x1C37
            | 0x1CD0..=0x1CD2
            | 0x1CD4..=0x1CE8
            | 0x1CED
            | 0x1CF4
            | 0x1CF7..=0x1CF9
            | 0x1DC0..=0x1DFF
            | 0x200C
            | 0x200D
            | 0x20D0..=0x20F0
            | 0x2CEF..=0x2CF1
            | 0x2D7F
            | 0x2DE0..=0x2DFF
            | 0x302A..=0x302F
            | 0x3099..=0x309A
            | 0xA66F..=0xA672
            | 0xA674..=0xA67D
            | 0xA69E..=0xA69F
            | 0xA6F0..=0xA6F1
            | 0xA802
            | 0xA806
            | 0xA80B
            | 0xA823..=0xA827
            | 0xA880..=0xA881
            | 0xA8B4..=0xA8C5
            | 0xA8E0..=0xA8F1
            | 0xA926..=0xA92D
            | 0xA947..=0xA953
            | 0xA980..=0xA983
            | 0xA9B3..=0xA9C0
            | 0xA9E5
            | 0xAA29..=0xAA36
            | 0xAA43
            | 0xAA4C..=0xAA4D
            | 0xAA7B..=0xAA7D
            | 0xAAB0
            | 0xAAB2..=0xAAB4
            | 0xAAB7..=0xAAB8
            | 0xAABE..=0xAABF
            | 0xAAC1
            | 0xAAEB..=0xAAEF
            | 0xAAF5..=0xAAF6
            | 0xABE3..=0xABEA
            | 0xABEC..=0xABED
            | 0xFB1E
            | 0xFE00..=0xFE0F
            | 0xFE20..=0xFE2F
            | 0x101FD
            | 0x102E0
            | 0x10376..=0x1037A
            | 0x10A01..=0x10A0F
            | 0x10A38..=0x10A3F
            | 0x10AE5..=0x10AE6
            | 0x11000..=0x11002
            | 0x11038..=0x11046
            | 0x1107F..=0x11082
            | 0x110B0..=0x110BA
            | 0x11100..=0x11102
            | 0x11127..=0x11134
            | 0x11145..=0x11146
            | 0x11173
            | 0x11180..=0x11182
            | 0x111B3..=0x111C0
            | 0x1122C..=0x11237
            | 0x1123E
            | 0x112DF..=0x112EA
            | 0x11300..=0x11303
            | 0x1133B..=0x1134D
            | 0x11357
            | 0x11362..=0x11374
            | 0x11435..=0x11446
            | 0x114B0..=0x114C3
            | 0x115AF..=0x115C0
            | 0x11630..=0x11640
            | 0x116AB..=0x116B7
            | 0x1171D..=0x1172B
            | 0x1182C..=0x1183A
            | 0x11930..=0x1193E
            | 0x119D1..=0x119E0
            | 0x119E4
            | 0x11A01..=0x11A0A
            | 0x11A33..=0x11A39
            | 0x11A3B..=0x11A3F
            | 0x11A47
            | 0x11A51..=0x11A5B
            | 0x11A8A..=0x11A99
            | 0x11C2F..=0x11C3F
            | 0x11CA9..=0x11CB6
            | 0x11D31..=0x11D45
            | 0x11D47
            | 0x11D8A..=0x11D97
            | 0x11EF3..=0x11EF6
            | 0x16AF0..=0x16AF4
            | 0x16B30..=0x16B36
            | 0x16F51..=0x16F92
            | 0x1BC9D..=0x1BC9E
            | 0x1D165..=0x1D169
            | 0x1D16D..=0x1D172
            | 0x1D17B..=0x1D182
            | 0x1D185..=0x1D18B
            | 0x1D1AA..=0x1D1AD
            | 0x1D242..=0x1D244
            | 0x1DA00..=0x1DA36
            | 0x1DA3B..=0x1DA6C
            | 0x1DA75
            | 0x1DA84
            | 0x1DA9B..=0x1DA9F
            | 0x1DAA1..=0x1DAAF
            | 0x1E000..=0x1E02A
            | 0x1E130..=0x1E136
            | 0x1E2EC..=0x1E2EF
            | 0x1E8D0..=0x1E8D6
            | 0x1E944..=0x1E94A
            | 0x1F3FB..=0x1F3FF
            | 0xE0020..=0xE007F
            | 0xE0100..=0xE01EF
    )
}

fn is_opening_punctuation(ch: char) -> bool {
    matches!(
        ch,
        '（' | '【' | '「' | '『' | '〈' | '《' | '〔' | '〖' | '［' | '｛' | '“' | '‘'
    )
}

fn is_closing_punctuation(ch: char) -> bool {
    matches!(
        ch,
        '）' | '】' | '」' | '』' | '〉' | '》' | '〕' | '〗' | '］' | '｝' | '”' | '’'
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unit_advance() -> impl Fn(char) -> f32 {
        |_ch| 1.0
    }

    fn zero_combining_advance() -> impl Fn(char) -> f32 {
        |ch| if is_combining(ch) { 0.0 } else { 1.0 }
    }

    /// Strict invariant: the returned string's measured width must never exceed
    /// the budget. No tolerance is applied.
    fn assert_within(result: &str, budget: f32, advance: &dyn Fn(char) -> f32) {
        let width: f32 = result.chars().map(|ch| advance(ch).max(0.0)).sum();
        assert!(
            width <= budget,
            "result {result:?} width {width} exceeds budget {budget}"
        );
    }

    #[test]
    fn text_within_budget_is_returned_unchanged() {
        let advance = unit_advance();
        assert_eq!(
            fit_row_text("你好", 5.0, Truncation::Trailing, &advance),
            "你好"
        );
        assert_eq!(
            fit_row_text("hello", 5.0, Truncation::Leading, &advance),
            "hello"
        );
    }

    #[test]
    fn empty_text_stays_empty() {
        let advance = unit_advance();
        for budget in [0.0f32, 0.5, 3.0, 100.0] {
            assert_eq!(fit_row_text("", budget, Truncation::Trailing, &advance), "");
            assert_eq!(fit_row_text("", budget, Truncation::Leading, &advance), "");
        }
    }

    #[test]
    fn trailing_truncation_reserves_ellipsis_width() {
        let advance = unit_advance();
        let result = fit_row_text("abcdefgh", 3.0, Truncation::Trailing, &advance);
        assert_eq!(result, "ab…");
        assert_within(&result, 3.0, &advance);
    }

    #[test]
    fn leading_truncation_reserves_ellipsis_width() {
        let advance = unit_advance();
        let result = fit_row_text("abcdefgh", 3.0, Truncation::Leading, &advance);
        assert_eq!(result, "…gh");
        assert_within(&result, 3.0, &advance);
    }

    #[test]
    fn zero_budget_returns_empty_because_ellipsis_does_not_fit() {
        let advance = unit_advance();
        assert_eq!(fit_row_text("abc", 0.0, Truncation::Trailing, &advance), "");
        assert_eq!(fit_row_text("abc", 0.0, Truncation::Leading, &advance), "");
    }

    #[test]
    fn budget_smaller_than_ellipsis_returns_empty() {
        let advance = unit_advance();
        assert_eq!(fit_row_text("abc", 0.5, Truncation::Trailing, &advance), "");
        assert_eq!(fit_row_text("abc", 0.5, Truncation::Leading, &advance), "");
    }

    #[test]
    fn budget_equal_to_ellipsis_returns_only_ellipsis() {
        let advance = unit_advance();
        for direction in [Truncation::Trailing, Truncation::Leading] {
            let result = fit_row_text("abc", 1.0, direction, &advance);
            assert_eq!(result, "…");
            assert_within(&result, 1.0, &advance);
        }
    }

    #[test]
    fn wider_ellipsis_shrinks_the_body_budget() {
        let advance = |ch: char| if ch == '…' { 2.0 } else { 1.0 };
        let trailing = fit_row_text("abcdef", 4.0, Truncation::Trailing, &advance);
        assert_eq!(trailing, "ab…");
        assert_within(&trailing, 4.0, &advance);
        let leading = fit_row_text("abcdef", 4.0, Truncation::Leading, &advance);
        assert_eq!(leading, "…ef");
        assert_within(&leading, 4.0, &advance);
    }

    #[test]
    fn fractional_budgets_never_overflow() {
        let advance = unit_advance();
        for budget in [0.1f32, 0.9, 1.5, 2.25, 2.5, 3.75, 4.5] {
            let trailing = fit_row_text("abcdefgh", budget, Truncation::Trailing, &advance);
            assert_within(&trailing, budget, &advance);
            let leading = fit_row_text("abcdefgh", budget, Truncation::Leading, &advance);
            assert_within(&leading, budget, &advance);
        }
    }

    #[test]
    fn cjk_prohibition_never_strands_punctuation_at_a_truncated_edge() {
        let advance = unit_advance();
        let trailing = fit_row_text("你好（世界", 3.0, Truncation::Trailing, &advance);
        assert_eq!(trailing, "你好…");
        assert_within(&trailing, 3.0, &advance);
        let leading = fit_row_text("世界）好你", 3.0, Truncation::Leading, &advance);
        assert_eq!(leading, "…好你");
        assert_within(&leading, 3.0, &advance);
    }

    #[test]
    fn pure_punctuation_never_overflows() {
        let advance = unit_advance();
        for budget in [0.0f32, 0.5, 1.0, 2.0, 3.0, 5.0] {
            let trailing = fit_row_text("。。。", budget, Truncation::Trailing, &advance);
            assert_within(&trailing, budget, &advance);
            let leading = fit_row_text("。。。", budget, Truncation::Leading, &advance);
            assert_within(&leading, budget, &advance);
        }
    }

    #[test]
    fn mixed_cjk_and_latin_never_overflows() {
        let advance = zero_combining_advance();
        for budget in [0.0f32, 1.0, 2.5, 4.0, 9.0] {
            let trailing = fit_row_text("你好ab中文", budget, Truncation::Trailing, &advance);
            assert_within(&trailing, budget, &advance);
            let leading = fit_row_text("你好ab中文", budget, Truncation::Leading, &advance);
            assert_within(&leading, budget, &advance);
        }
    }

    #[test]
    fn multibyte_scalars_are_never_byte_split() {
        let advance = unit_advance();
        let trailing = fit_row_text("日本語X", 3.0, Truncation::Trailing, &advance);
        assert_eq!(trailing, "日本…");
        let leading = fit_row_text("日本語X", 3.0, Truncation::Leading, &advance);
        assert_eq!(leading, "…語X");
    }

    #[test]
    fn regional_indicator_flags_are_not_split() {
        let advance = unit_advance();
        // JP + US = four regional indicators, two clusters of two.
        let result = fit_row_text("🇯🇵🇺🇸", 3.0, Truncation::Trailing, &advance);
        assert_eq!(result, "🇯🇵…");
        assert_within(&result, 3.0, &advance);
        assert_eq!(
            result.chars().filter(|c| is_regional_indicator(*c)).count() % 2,
            0
        );
    }

    #[test]
    fn indic_virama_conjuncts_are_not_split() {
        let advance = zero_combining_advance();
        // Devanagari ka + virama + ssa, then ma + x.
        let result = fit_row_text("क\u{094D}षमx", 3.0, Truncation::Trailing, &advance);
        assert_eq!(result, "क\u{094D}ष…");
        assert_within(&result, 3.0, &advance);
        // Bengali ka + virama + ssa.
        let bengali = fit_row_text("ক\u{09CD}ষমx", 3.0, Truncation::Trailing, &advance);
        assert_eq!(bengali, "ক\u{09CD}ষ…");
        assert_within(&bengali, 3.0, &advance);
    }

    #[test]
    fn keycap_sequences_are_not_split() {
        let advance = zero_combining_advance();
        // digit + variation selector + combining enclosing keycap, then base chars.
        let result = fit_row_text("1\u{FE0F}\u{20E3}xyz", 2.0, Truncation::Trailing, &advance);
        assert_eq!(result, "1\u{FE0F}\u{20E3}…");
        assert_within(&result, 2.0, &advance);
    }

    #[test]
    fn emoji_tag_sequences_are_not_split() {
        let advance = zero_combining_advance();
        // black flag + gb eng tag letters + cancel tag, then base chars.
        let result = fit_row_text(
            "\u{1F3F4}\u{E0067}\u{E0062}\u{E007F}xy",
            2.0,
            Truncation::Trailing,
            &advance,
        );
        assert_eq!(result, "\u{1F3F4}\u{E0067}\u{E0062}\u{E007F}…");
        assert_within(&result, 2.0, &advance);
    }

    #[test]
    fn zwj_emoji_sequences_are_not_broken_mid_cluster() {
        let advance = zero_combining_advance();
        let text = "\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}xy";
        let result = fit_row_text(text, 4.0, Truncation::Trailing, &advance);
        assert_eq!(result, "\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}…");
        assert_within(&result, 4.0, &advance);
    }

    #[test]
    fn combining_marks_stay_with_their_base() {
        let advance = zero_combining_advance();
        let result = fit_row_text("e\u{0301}xyz", 2.0, Truncation::Trailing, &advance);
        assert_eq!(result, "e\u{0301}…");
        assert_within(&result, 2.0, &advance);
    }

    #[test]
    fn strict_budget_invariant_across_corpora() {
        let advance = zero_combining_advance();
        let corpora = [
            "",
            "a",
            "abcdefgh",
            "你好（世界）",
            "你好ab中文",
            "日本語X",
            "🇯🇵🇺🇸",
            "क\u{094D}षम",
            "1\u{FE0F}\u{20E3}x",
            "\u{1F3F4}\u{E0067}\u{E0062}\u{E007F}x",
            "\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}xy",
            "e\u{0301}xyz",
            "。。。",
        ];
        let budgets = [
            0.0f32, 0.25, 0.5, 0.9, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 8.0, 50.0,
        ];
        for text in corpora {
            let total: f32 = text.chars().map(|ch| advance(ch).max(0.0)).sum();
            for budget in budgets {
                for direction in [Truncation::Trailing, Truncation::Leading] {
                    let result = fit_row_text(text, budget, direction, &advance);
                    assert_within(&result, budget, &advance);
                    if budget >= total && !text.is_empty() {
                        assert_eq!(result, text, "within budget must be unchanged");
                    }
                }
            }
        }
    }
}
