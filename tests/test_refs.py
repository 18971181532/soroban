import unittest

from soroban.refs import (
    CellRef,
    RangeRef,
    col_to_letters,
    letters_to_col,
    parse_cell,
    parse_range,
    quote_sheet,
    split_sheet,
)


class ColumnLettersTest(unittest.TestCase):
    def test_start_and_end_of_alphabet(self):
        self.assertEqual(col_to_letters(0), "A")
        self.assertEqual(col_to_letters(25), "Z")
        self.assertEqual(col_to_letters(26), "AA")
        self.assertEqual(col_to_letters(51), "AZ")
        self.assertEqual(col_to_letters(52), "BA")
        self.assertEqual(col_to_letters(701), "ZZ")
        self.assertEqual(col_to_letters(702), "AAA")
        self.assertEqual(col_to_letters(16383), "XFD")

    def test_round_trip(self):
        for col in (0, 1, 25, 26, 27, 700, 701, 702, 12345, 16383):
            self.assertEqual(letters_to_col(col_to_letters(col)), col)

    def test_invalid_letters(self):
        self.assertIsNone(letters_to_col(""))
        self.assertIsNone(letters_to_col("A1"))
        self.assertIsNone(letters_to_col("ABCD"))
        self.assertIsNone(letters_to_col("XFE"))
        self.assertIsNone(letters_to_col("ZZZ"))


class ParseCellTest(unittest.TestCase):
    def test_simple(self):
        ref = parse_cell("B3")
        self.assertIsNotNone(ref)
        assert ref is not None
        self.assertEqual((ref.row, ref.col), (2, 1))
        self.assertEqual(ref.a1, "B3")
        self.assertFalse(ref.abs_row or ref.abs_col)

    def test_absolute_flags(self):
        ref = parse_cell("$B$7")
        assert ref is not None
        self.assertTrue(ref.abs_row)
        self.assertTrue(ref.abs_col)
        self.assertEqual(ref.absolute, "$B$7")
        self.assertEqual((ref.row, ref.col), (6, 1))

    def test_mixed_absolute(self):
        ref = parse_cell("B$7")
        assert ref is not None
        self.assertTrue(ref.abs_row)
        self.assertFalse(ref.abs_col)
        self.assertEqual(ref.absolute, "B$7")

    def test_limits(self):
        self.assertIsNotNone(parse_cell("A1"))
        self.assertIsNotNone(parse_cell("XFD1048576"))
        self.assertIsNone(parse_cell("A0"))
        self.assertIsNone(parse_cell("A1048577"))
        self.assertIsNone(parse_cell("XFE1"))
        self.assertIsNone(parse_cell("1A"))
        self.assertIsNone(parse_cell(""))

    def test_sheet_prefix_is_not_part_of_cell(self):
        self.assertIsNone(parse_cell("Sheet1!A1"))
        ref = parse_cell("A1", sheet="数据")
        assert ref is not None
        self.assertEqual(ref.key(), ("数据", 0, 0))


class ParseRangeTest(unittest.TestCase):
    def test_normal(self):
        rng = parse_range("A1:B3")
        assert rng is not None
        self.assertEqual((rng.row1, rng.col1, rng.row2, rng.col2), (0, 0, 2, 1))
        self.assertEqual(rng.height, 3)
        self.assertEqual(rng.width, 2)
        self.assertEqual(rng.size, 6)
        self.assertEqual(rng.a1, "A1:B3")

    def test_reversed_corners_are_normalised(self):
        rng = parse_range("B3:A1")
        assert rng is not None
        self.assertEqual(rng.a1, "A1:B3")

    def test_single_cell_range(self):
        rng = parse_range("C4:C4")
        assert rng is not None
        self.assertEqual(rng.size, 1)
        self.assertEqual(list(rng.cells()), [(3, 2)])

    def test_invalid(self):
        self.assertIsNone(parse_range("A1"))
        self.assertIsNone(parse_range("A1:B2:C3"))
        self.assertIsNone(parse_range("A1:2B"))

    def test_contains(self):
        rng = parse_range("B2:C3")
        assert rng is not None
        self.assertTrue(rng.contains(1, 1))
        self.assertTrue(rng.contains(2, 2))
        self.assertFalse(rng.contains(0, 0))
        self.assertFalse(rng.contains(3, 1))


class SheetNameTest(unittest.TestCase):
    def test_split_plain(self):
        self.assertEqual(split_sheet("A1"), (None, "A1"))

    def test_split_qualified(self):
        self.assertEqual(split_sheet("经营!B4"), ("经营", "B4"))

    def test_split_quoted(self):
        self.assertEqual(split_sheet("'销售 表'!A1"), ("销售 表", "A1"))

    def test_quote_when_needed(self):
        self.assertEqual(quote_sheet("经营"), "经营")
        self.assertEqual(quote_sheet("销售 表"), "'销售 表'")
        self.assertEqual(quote_sheet("a-b"), "'a-b'")

    def test_escaped_quote(self):
        self.assertEqual(split_sheet("'It''s'!A1"), ("It's", "A1"))


class RefKeyTest(unittest.TestCase):
    def test_key_uses_default_sheet(self):
        ref = parse_cell("B2")
        assert ref is not None
        self.assertEqual(ref.key(), ("Sheet1", 1, 1))
        self.assertEqual(ref.key("默认"), ("默认", 1, 1))

    def test_range_with_sheet(self):
        rng = RangeRef("S", 0, 0, 1, 1)
        self.assertEqual(str(rng), "S!A1:B2")
        self.assertEqual(rng.with_sheet("T").sheet, "T")


if __name__ == "__main__":
    unittest.main()
