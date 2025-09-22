import unittest
from utils import add_scaled_after_title

class TestAddScaledAfterTitle(unittest.TestCase):
    def test_with_precision_80s(self):
        src = '- %(title).80s [%(id).20s].%(ext)s'
        expected = '- %(title).80s.scaled [%(id).20s].%(ext)s'
        self.assertEqual(add_scaled_after_title(src), expected)

    def test_with_precision_40s(self):
        src = 'prefix_%(title).40s_suffix.%(ext)s'
        expected = 'prefix_%(title).40s.scaled_suffix.%(ext)s'
        self.assertEqual(add_scaled_after_title(src), expected)

    def test_without_precision(self):
        src = '%(title)s.%(ext)s'
        expected = '%(title)s.scaled.%(ext)s'
        self.assertEqual(add_scaled_after_title(src), expected)

    def test_already_scaled_is_idempotent(self):
        src = '%(title).80s.scaled.%(ext)s'
        expected = '%(title).80s.scaled.%(ext)s'
        self.assertEqual(add_scaled_after_title(src), expected)

    def test_does_not_touch_other_placeholders(self):
        src = '%(id).20s - %(title).80s - %(ext)s'
        expected = '%(id).20s - %(title).80s.scaled - %(ext)s'
        self.assertEqual(add_scaled_after_title(src), expected)

    def test_no_title_placeholder(self):
        src = '%(id).20s - %(ext)s'
        expected = '%(id).20s - %(ext)s'
        self.assertEqual(add_scaled_after_title(src), expected)

    def test_multiple_title_occurrences_all_updated(self):
        src = '%(title)s | %(title).80s | %(ext)s'
        expected = '%(title)s.scaled | %(title).80s.scaled | %(ext)s'
        self.assertEqual(add_scaled_after_title(src), expected)

    def test_dict_with_precision_80s(self):
        src = {'default': '%(title).80s %(ext)s', 'chapter': '%(title)s - %(section_title)s %(ext)s'}
        expected = {'default': '%(title).80s.scaled %(ext)s', 'chapter': '%(title)s.scaled - %(section_title)s %(ext)s'}
        self.assertEqual(add_scaled_after_title(src), expected)

    def test_dict_with_precision_40s(self):
        src = {'default': 'prefix_%(title).40s_suffix.%(ext)s', 'chapter': 'prefix_%(title).40s_suffix - %(section_title)s.%(ext)s'}
        expected = {'default': 'prefix_%(title).40s.scaled_suffix.%(ext)s', 'chapter': 'prefix_%(title).40s.scaled_suffix - %(section_title)s.%(ext)s'}
        self.assertEqual(add_scaled_after_title(src), expected)

    def test_dct_without_precision(self):
        src = {'default': '%(title)s.%(ext)s', 'chapter': '%(title)s.%(section_title)s.%(ext)s'}
        expected = {'default': '%(title)s.scaled.%(ext)s', 'chapter': '%(title)s.scaled.%(section_title)s.%(ext)s'}
        self.assertEqual(add_scaled_after_title(src), expected)

    def test_dict_no_title_placeholder(self):
        src = {'default': '%(id).20s - %(ext)s', 'chapter': '%(id).20s - %(section_title)s.%(ext)s'}
        expected = {'default': '%(id).20s - %(ext)s', 'chapter': '%(id).20s - %(section_title)s.%(ext)s'}
        self.assertEqual(add_scaled_after_title(src), expected)

if __name__ == '__main__':
    unittest.main()
