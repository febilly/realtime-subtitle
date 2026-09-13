//! Pure, width-agnostic single-line fitting used by the fixed-slot HUD rows.
//!
//! The real renderer passes a DirectWrite-derived advance function; tests pass a
//! deterministic advance so truncation edges are exact.
//!
//! Budget rule: the ellipsis is part of the line, so its own advance is reserved
//! from `max_advance` before any body text is kept. The fitted result is never
//! wider than `max_advance`. When `max_advance` cannot even fit the ellipsis,
//! the result is empty because no readable representation fits.

const WIDTH_EPSILON: f32 = 1e-3;

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
    if text.is_empty() {
        return String::new();
    }
    let max = max_advance.max(0.0);
    if measure_width(text, advance) <= max + WIDTH_EPSILON {
        return text.to_owned();
    }
    let ellipsis_width = advance('…').max(0.0);
    if max + WIDTH_EPSILON < ellipsis_width {
        return String::new();
    }
    let body_budget = (max - ellipsis_width).max(0.0);
    let clusters = clusters(text);

    match direction {
        Truncation::Trailing => {
            let mut kept: Vec<String> = Vec::new();
            let mut width = 0.0f32;
            for cluster in &clusters {
                let cluster_width = measure_width(cluster, advance);
                if width + cluster_width > body_budget + WIDTH_EPSILON {
                    break;
                }
                width += cluster_width;
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
            out
        }
        Truncation::Leading => {
            let mut kept: Vec<String> = Vec::new();
            let mut width = 0.0f32;
            for cluster in clusters.iter().rev() {
                let cluster_width = measure_width(cluster, advance);
                if width + cluster_width > body_budget + WIDTH_EPSILON {
                    break;
                }
                width += cluster_width;
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
            out
        }
    }
}

fn measure_width(text: &str, advance: &dyn Fn(char) -> f32) -> f32 {
    text.chars().map(|ch| advance(ch).max(0.0)).sum()
}

/// Split into minimal grapheme-ish clusters: a base scalar plus attached
/// combining marks, variation selectors, skin-tone modifiers, and ZWJ joins.
fn clusters(text: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut join_next = false;
    for ch in text.chars() {
        let attach = join_next || is_combining(ch);
        if attach {
            if let Some(last) = out.last_mut() {
                last.push(ch);
                join_next = ch == '\u{200D}';
                continue;
            }
        }
        out.push(ch.to_string());
        join_next = ch == '\u{200D}';
    }
    out
}

fn cluster_base(cluster: &str) -> char {
    cluster
        .chars()
        .find(|ch| !is_combining(*ch))
        .or_else(|| cluster.chars().next())
        .unwrap_or('\u{0}')
}

fn is_combining(ch: char) -> bool {
    matches!(ch as u32,
        0x0300..=0x036F
            | 0x0483..=0x0489
            | 0x0591..=0x05BD
            | 0x05BF
            | 0x05C1..=0x05C2
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
            | 0x0900..=0x0903
            | 0x093A..=0x094F
            | 0x0951..=0x0957
            | 0x0962..=0x0963
            | 0x1AB0..=0x1AFF
            | 0x1DC0..=0x1DFF
            | 0x20D0..=0x20FF
            | 0x200D
            | 0xFE00..=0xFE0F
            | 0xFE20..=0xFE2F
            | 0x1F3FB..=0x1F3FF
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

    fn assert_within_budget(result: &str, budget: f32) {
        let advance = unit_advance();
        let width = measure_width(result, &advance);
        assert!(
            width <= budget + WIDTH_EPSILON,
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
        assert_eq!(fit_row_text("", 0.0, Truncation::Trailing, &advance), "");
        assert_eq!(fit_row_text("", 10.0, Truncation::Leading, &advance), "");
    }

    #[test]
    fn trailing_truncation_reserves_ellipsis_width() {
        let advance = unit_advance();
        let result = fit_row_text("abcdefgh", 3.0, Truncation::Trailing, &advance);
        assert_eq!(result, "ab…");
        assert_within_budget(&result, 3.0);
    }

    #[test]
    fn leading_truncation_reserves_ellipsis_width() {
        let advance = unit_advance();
        let result = fit_row_text("abcdefgh", 3.0, Truncation::Leading, &advance);
        assert_eq!(result, "…gh");
        assert_within_budget(&result, 3.0);
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
    fn budget_exactly_ellipsis_returns_only_ellipsis() {
        let advance = unit_advance();
        let trailing = fit_row_text("abc", 1.0, Truncation::Trailing, &advance);
        assert_eq!(trailing, "…");
        assert_within_budget(&trailing, 1.0);
        let leading = fit_row_text("abc", 1.0, Truncation::Leading, &advance);
        assert_eq!(leading, "…");
        assert_within_budget(&leading, 1.0);
    }

    #[test]
    fn wider_ellipsis_shrinks_the_body_budget() {
        let advance = |ch: char| if ch == '…' { 2.0 } else { 1.0 };
        let result = fit_row_text("abcdef", 4.0, Truncation::Trailing, &advance);
        assert_eq!(result, "ab…");
        assert_within_budget_with(&result, 4.0, &advance);
        let leading = fit_row_text("abcdef", 4.0, Truncation::Leading, &advance);
        assert_eq!(leading, "…ef");
        assert_within_budget_with(&leading, 4.0, &advance);
    }

    #[test]
    fn cjk_prohibition_never_strands_punctuation_at_a_truncated_edge() {
        let advance = unit_advance();
        let trailing = fit_row_text("你好（世界", 3.0, Truncation::Trailing, &advance);
        assert_eq!(trailing, "你好…");
        assert_within_budget(&trailing, 3.0);
        let leading = fit_row_text("世界）好你", 3.0, Truncation::Leading, &advance);
        assert_eq!(leading, "…好你");
        assert_within_budget(&leading, 3.0);
    }

    #[test]
    fn pure_punctuation_never_overflows() {
        let advance = unit_advance();
        for budget in [0.0f32, 0.5, 1.0, 2.0, 3.0, 5.0] {
            let trailing = fit_row_text("。。。", budget, Truncation::Trailing, &advance);
            assert_within_budget(&trailing, budget);
            let leading = fit_row_text("。。。", budget, Truncation::Leading, &advance);
            assert_within_budget(&leading, budget);
        }
    }

    #[test]
    fn mixed_cjk_and_latin_stays_within_budget() {
        let advance = |ch: char| if is_combining(ch) { 0.0 } else { 1.0 };
        for budget in [0.0f32, 1.0, 2.5, 4.0, 9.0] {
            let trailing = fit_row_text("你好ab中文", budget, Truncation::Trailing, &advance);
            assert_within_budget_with(&trailing, budget, &advance);
            let leading = fit_row_text("你好ab中文", budget, Truncation::Leading, &advance);
            assert_within_budget_with(&leading, budget, &advance);
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
    fn emoji_are_kept_whole() {
        let advance = unit_advance();
        let result = fit_row_text("😀😀😀x", 3.0, Truncation::Trailing, &advance);
        assert_eq!(result, "😀😀…");
        assert_within_budget(&result, 3.0);
        let leading = fit_row_text("😀😀😀x", 3.0, Truncation::Leading, &advance);
        assert_eq!(leading, "…😀x");
        assert_within_budget(&leading, 3.0);
    }

    #[test]
    fn combining_marks_stay_with_their_base() {
        let advance = |ch: char| if is_combining(ch) { 0.0 } else { 1.0 };
        // "e" + U+0301 (combining acute) is one cluster; keep it whole.
        let result = fit_row_text("e\u{0301}xyz", 2.0, Truncation::Trailing, &advance);
        assert_eq!(result, "e\u{0301}…");
        assert_within_budget_with(&result, 2.0, &advance);
    }

    #[test]
    fn zwj_emoji_sequences_are_not_broken_mid_cluster() {
        let advance = |ch: char| if ch == '\u{200D}' { 0.0 } else { 1.0 };
        // family sequence: man + ZWJ + woman + ZWJ + girl, then two base chars.
        let text = "\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}xy";
        let result = fit_row_text(text, 4.0, Truncation::Trailing, &advance);
        assert_eq!(result, "\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}…");
        assert_within_budget_with(&result, 4.0, &advance);
    }

    fn assert_within_budget_with(result: &str, budget: f32, advance: &dyn Fn(char) -> f32) {
        let width = measure_width(result, advance);
        assert!(
            width <= budget + WIDTH_EPSILON,
            "result {result:?} width {width} exceeds budget {budget}"
        );
    }
}
