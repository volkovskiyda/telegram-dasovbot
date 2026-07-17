import unittest

from info import sizeof_fmt


class TestSizeofFmt(unittest.TestCase):
    def test_zero_is_not_available(self):
        self.assertEqual(sizeof_fmt(0), 'N/A')

    def test_bytes(self):
        self.assertEqual(sizeof_fmt(512), '512.0B')

    def test_kibibytes(self):
        self.assertEqual(sizeof_fmt(1024), '1.0KiB')

    def test_fractional_kibibytes(self):
        self.assertEqual(sizeof_fmt(1536), '1.5KiB')

    def test_gibibytes(self):
        self.assertEqual(sizeof_fmt(1024 ** 3), '1.0GiB')

    def test_negative_value_keeps_sign(self):
        self.assertEqual(sizeof_fmt(-512), '-512.0B')

    def test_huge_value_uses_yobibytes(self):
        self.assertEqual(sizeof_fmt(1024 ** 8), '1.0YiB')


if __name__ == '__main__':
    unittest.main()
