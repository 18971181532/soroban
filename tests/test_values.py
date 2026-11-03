import math
import unittest

from soroban.errors import FormulaError
from soroban.values import (
    Err,
    compare,
    display,
    equal,
    format_number,
    is_number,
    to_bool,
    to_number,
    to_text,
)


class NumberTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(to_number(3), 3.0)
        self.assertEqual(to_number(True), 1.0)
        self.assertEqual(to_number(False), 0.0)
        self.assertEqual(to_number(None), 0.0)

    def test_text(self):
        self.assertEqual(to_number(" 12.5 "), 12.5)
        self.assertEqual(to_number("1e3"), 1000.0)
        self.assertAlmostEqual(to_number("5%"), 0.05)
        self.assertEqual(to_number("+2"), 2.0)

    def test_bad_text(self):
        for text in ("", "abc", "1,000", "12px", "%"):
            with self.assertRaises(FormulaError) as caught:
                to_number(text)
            self.assertEqual(caught.exception.code, "#VALUE!")

    def test_error_propagates(self):
        with self.assertRaises(FormulaError) as caught:
            to_number(Err("#DIV/0!"))
        self.assertEqual(caught.exception.code, "#DIV/0!")

    def test_nan_is_rejected(self):
        with self.assertRaises(FormulaError):
            to_number(float("nan"))


class TextTest(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(to_text(3.0), "3")
        self.assertEqual(to_text(3.5), "3.5")
        self.assertEqual(to_text(1 / 3), "0.3333333333")

    def test_other_types(self):
        self.assertEqual(to_text(None), "")
        self.assertEqual(to_text(True), "TRUE")
        self.assertEqual(to_text(False), "FALSE")
        self.assertEqual(to_text("abc"), "abc")

    def test_error_raises(self):
        with self.assertRaises(FormulaError):
            to_text(Err("#N/A"))


class BoolTest(unittest.TestCase):
    def test_values(self):
        self.assertIs(to_bool(True), True)
        self.assertIs(to_bool(0), False)
        self.assertIs(to_bool(2), True)
        self.assertIs(to_bool("true"), True)
        self.assertIs(to_bool("FALSE"), False)
        self.assertIs(to_bool(None), False)

    def test_bad(self):
        with self.assertRaises(FormulaError):
            to_bool("yes")


class CompareTest(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(compare(1, 2), -1)
        self.assertEqual(compare(2, 2), 0)
        self.assertEqual(compare(3, 2), 1)

    def test_text_is_case_insensitive(self):
        self.assertEqual(compare("A", "a"), 0)
        self.assertEqual(compare("a", "b"), -1)

    def test_type_order(self):
        self.assertEqual(compare(100, "a"), -1)
        self.assertEqual(compare("a", True), -1)
        self.assertEqual(compare(True, "a"), 1)

    def test_blank_adapts(self):
        self.assertEqual(compare(None, 0), 0)
        self.assertEqual(compare(None, ""), 0)
        self.assertEqual(compare(None, False), 0)
        self.assertEqual(compare(None, None), 0)

    def test_error(self):
        with self.assertRaises(FormulaError):
            compare(Err("#N/A"), 1)

    def test_equal(self):
        self.assertTrue(equal("A", "a"))
        self.assertFalse(equal(1, "1"))


class DisplayTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(display(None), "")
        self.assertEqual(display(Err("#VALUE!")), "#VALUE!")
        self.assertEqual(display(True), "TRUE")
        self.assertEqual(display(2.0), "2")
        self.assertEqual(display(2.5), "2.5")
        self.assertEqual(display("文本"), "文本")

    def test_float_noise_is_trimmed(self):
        self.assertEqual(format_number(0.1 + 0.2), "0.3")
        self.assertEqual(format_number(1 / 3), "0.3333333333")

    def test_large_integers(self):
        self.assertEqual(format_number(1e16), "1e+16")


class ErrorValueTest(unittest.TestCase):
    def test_equality_by_code(self):
        self.assertEqual(Err("#N/A", "一"), Err("#N/A", "二"))
        self.assertNotEqual(Err("#N/A"), Err("#VALUE!"))

    def test_hash(self):
        self.assertEqual(len({Err("#N/A"), Err("#N/A")}), 1)

    def test_repr_and_str(self):
        self.assertEqual(str(Err("#DIV/0!")), "#DIV/0!")
        self.assertIn("Err", repr(Err("#DIV/0!")))


class NumberPredicateTest(unittest.TestCase):
    def test_bool_is_not_a_number(self):
        self.assertFalse(is_number(True))
        self.assertTrue(is_number(0))
        self.assertFalse(is_number("1"))


if __name__ == "__main__":
    unittest.main()
