import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock dependencies before importing the target module
MOCK_MODULES = {
    "config": MagicMock(),
    "soniox_client": MagicMock(),
    "audio_capture": MagicMock(),
    "osc_manager": MagicMock(),
    "llm_client": MagicMock(),
}

with patch.dict(sys.modules, MOCK_MODULES):
    from soniox_session import normalize_east_asian_translation_spacing

class TestSonioxSession(unittest.TestCase):
    def test_normalize_east_asian_translation_spacing_empty(self):
        """Test with empty or None values."""
        self.assertEqual(normalize_east_asian_translation_spacing(None), "")
        self.assertEqual(normalize_east_asian_translation_spacing(""), "")

    def test_normalize_east_asian_translation_spacing_non_string(self):
        """Test with non-string values."""
        self.assertEqual(normalize_east_asian_translation_spacing(123), "123")
        self.assertEqual(normalize_east_asian_translation_spacing(12.34), "12.34")

    def test_normalize_east_asian_translation_spacing_no_cjk(self):
        """Test with regular strings that should not be modified."""
        self.assertEqual(normalize_east_asian_translation_spacing("Hello world!"), "Hello world!")
        self.assertEqual(normalize_east_asian_translation_spacing("  Spaces  "), "  Spaces  ")

    def test_normalize_east_asian_translation_spacing_cjk(self):
        """Test with East Asian text where spacing should be removed."""
        # Chinese characters separated by spaces
        self.assertEqual(normalize_east_asian_translation_spacing("你 好 世 界"), "你好世界")
        self.assertEqual(normalize_east_asian_translation_spacing("你好  世界"), "你好世界")

        # Punctuation
        self.assertEqual(normalize_east_asian_translation_spacing("你好！ 世界"), "你好！世界")
        self.assertEqual(normalize_east_asian_translation_spacing("你好 ， 世界"), "你好，世界")
        self.assertEqual(normalize_east_asian_translation_spacing("「 你好 」"), "「你好」")

        # Japanese characters
        self.assertEqual(normalize_east_asian_translation_spacing("こんにちは 世界"), "こんにちは世界")
        self.assertEqual(normalize_east_asian_translation_spacing("テスト です"), "テストです")

    def test_normalize_east_asian_translation_spacing_mixed(self):
        """Test with mixed CJK and non-CJK text."""
        # Spaces between CJK and English might or might not be stripped depending on the regex.
        # The EAST_ASIAN_TIGHT_SPACING_CLASS only includes CJK ranges, so space between CJK and English shouldn't be stripped.
        self.assertEqual(normalize_east_asian_translation_spacing("你好 World"), "你好 World")
        self.assertEqual(normalize_east_asian_translation_spacing("Hello 世界"), "Hello 世界")

if __name__ == '__main__':
    unittest.main()
