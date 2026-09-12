//! Pure, width-agnostic single-line fitting used by the fixed-slot HUD rows.
//!
//! The real renderer passes a DirectWrite-derived advance function; tests pass a
//! constant advance so truncation edges are deterministic.

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
    let max = max_advance.max(0.0);
    let total: f32 = text.chars().map(|ch| advance(ch).max(0.0)).sum();
    if total <= max {
        return text.to_owned();
    }
    match direction {
        Truncation::Trailing => {
            let mut kept = String::new();
            let mut width = 0.0f32;
            for ch in text.chars() {
                let w = advance(ch).max(0.0);
                if width + w > max {
                    break;
                }
                width += w;
                kept.push(ch);
            }
            // Opening punctuation must not end a truncated line.
            while kept.chars().last().is_some_and(is_opening_punctuation) {
                kept.pop();
            }
            kept.push('…');
            kept
        }
        Truncation::Leading => {
            let mut kept: Vec<char> = Vec::new();
            let mut width = 0.0f32;
            for ch in text.chars().rev() {
                let w = advance(ch).max(0.0);
                if width + w > max {
                    break;
                }
                width += w;
                kept.push(ch);
            }
            kept.reverse();
            // Closing punctuation must not begin a truncated line.
            while kept.first().copied().is_some_and(is_closing_punctuation) {
                kept.remove(0);
            }
            let mut out = String::from('…');
            out.extend(kept);
            out
        }
    }
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
    fn trailing_truncation_keeps_the_beginning_and_appends_ellipsis() {
        let advance = unit_advance();
        assert_eq!(
            fit_row_text("abcdefgh", 3.0, Truncation::Trailing, &advance),
            "abc…"
        );
    }

    #[test]
    fn leading_truncation_keeps_the_newest_tail_and_prepends_ellipsis() {
        let advance = unit_advance();
        assert_eq!(
            fit_row_text("abcdefgh", 3.0, Truncation::Leading, &advance),
            "…fgh"
        );
    }

    #[test]
    fn cjk_prohibition_never_strands_punctuation_at_a_truncated_edge() {
        let advance = unit_advance();
        assert_eq!(
            fit_row_text("你好（世界", 3.0, Truncation::Trailing, &advance),
            "你好…"
        );
        assert_eq!(
            fit_row_text("世界）好你", 3.0, Truncation::Leading, &advance),
            "…好你"
        );
    }
}
