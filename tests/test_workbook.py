import json
import os
import tempfile
import unittest

from soroban import Workbook
from soroban.values import Err


CSV_TEXT = """产品,数量,单价,金额
苹果,3,5.5,=B2*C2
"梨, 大",2,8,=B3*C3
"""


class BasicTest(unittest.TestCase):
    def setUp(self):
        self.wb = Workbook()
        self.wb.update({"A1": "10", "A2": "=A1*2", "A3": "文本"})

    def test_read(self):
        self.assertEqual(self.wb.get("A1"), 10.0)
        self.assertEqual(self.wb.get("A2"), 20.0)
        self.assertEqual(self.wb.text("A2"), "20")
        self.assertEqual(self.wb.raw("A2"), "=A1*2")
        self.assertEqual(self.wb.formula("A2"), "=A1*2")
        self.assertIsNone(self.wb.formula("A1"))

    def test_missing_cell(self):
        self.assertIsNone(self.wb.get("Z9"))
        self.assertEqual(self.wb.raw("Z9"), "")
        self.assertEqual(self.wb.text("Z9"), "")

    def test_clear(self):
        self.wb.clear("A2")
        self.assertEqual(self.wb.raw("A2"), "")
        self.assertIsNone(self.wb.get("A2"))

    def test_bad_address(self):
        with self.assertRaises(ValueError):
            self.wb.set("不是地址", "1")

    def test_unknown_sheet(self):
        with self.assertRaises(ValueError):
            self.wb.set("不存在!A1", "1")

    def test_case_insensitive_sheet(self):
        self.wb.set("sheet1!B1", "5")
        self.assertEqual(self.wb.get("SHEET1!B1"), 5.0)


class SheetTest(unittest.TestCase):
    def test_names(self):
        wb = Workbook(["经营", "贷款"])
        self.assertEqual(wb.sheet_names, ["经营", "贷款"])
        self.assertEqual(wb.default_sheet, "经营")

    def test_add_sheet(self):
        wb = Workbook()
        wb.add_sheet("新表")
        self.assertIn("新表", wb.sheet_names)
        with self.assertRaises(ValueError):
            wb.add_sheet("新表")

    def test_duplicate_on_construction(self):
        with self.assertRaises(ValueError):
            Workbook(["A", "A"])

    def test_bounds(self):
        wb = Workbook()
        self.assertEqual(wb.sheet().bounds(), (-1, -1))
        wb.update({"C4": "1"})
        self.assertEqual(wb.sheet().bounds(), (3, 2))


class JsonTest(unittest.TestCase):
    def test_round_trip(self):
        wb = Workbook(["经营"])
        wb.update({"A1": "10", "A2": "=A1*2", "B1": "文本"})
        clone = Workbook.from_json(wb.to_json())
        self.assertEqual(clone.get("A2"), 20.0)
        self.assertEqual(clone.raw("A2"), "=A1*2")
        self.assertEqual(clone.raw("B1"), "文本")
        self.assertEqual(clone.sheet_names, ["经营"])

    def test_dict_is_sorted(self):
        wb = Workbook()
        wb.update({"B2": "2", "A1": "1"})
        cells = wb.to_dict()["sheets"]["Sheet1"]
        self.assertEqual(list(cells), ["A1", "B2"])

    def test_chinese_is_not_escaped(self):
        wb = Workbook(["经营"])
        wb.set("A1", "收入")
        text = wb.to_json()
        self.assertIn("经营", text)
        self.assertIn("收入", text)
        self.assertNotIn("\\u7ecf", text)

    def test_bad_payload(self):
        with self.assertRaises(ValueError):
            Workbook.from_dict({"sheets": {}})
        with self.assertRaises(ValueError):
            Workbook.from_dict({"sheets": {"S": {"??": "1"}}})

    def test_formula_errors_survive_round_trip(self):
        wb = Workbook()
        wb.update({"A1": "=1+"})
        clone = Workbook.from_json(wb.to_json())
        self.assertEqual(clone.get("A1"), Err("#PARSE!"))


class CsvTest(unittest.TestCase):
    def test_load(self):
        wb = Workbook()
        wb.load_csv(CSV_TEXT)
        self.assertEqual(wb.get("D2"), 16.5)
        self.assertEqual(wb.get("D3"), 16.0)
        self.assertEqual(wb.raw("A3"), "梨, 大")

    def test_export_values(self):
        wb = Workbook()
        wb.load_csv(CSV_TEXT)
        text = wb.to_csv()
        lines = text.strip().splitlines()
        self.assertEqual(lines[0], "产品,数量,单价,金额")
        self.assertEqual(lines[1], "苹果,3,5.5,16.5")
        self.assertIn('"梨, 大"', lines[2])

    def test_export_formulas(self):
        wb = Workbook()
        wb.load_csv(CSV_TEXT)
        text = wb.to_csv(formulas=True)
        self.assertIn("=B2*C2", text)

    def test_empty_cells_are_skipped(self):
        wb = Workbook()
        wb.load_csv("a,,c\n1,,3\n")
        self.assertIsNone(wb.get("B1"))
        self.assertEqual(wb.get("C2"), 3.0)

    def test_empty_workbook(self):
        self.assertEqual(Workbook().to_csv(), "")


class FileTest(unittest.TestCase):
    def test_json_file(self):
        wb = Workbook()
        wb.update({"A1": "1", "A2": "=A1+1"})
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "book.json")
            wb.save(path)
            clone = Workbook.load(path)
        self.assertEqual(clone.get("A2"), 2.0)

    def test_csv_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "book.csv")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(CSV_TEXT)
            wb = Workbook.load(path)
        self.assertEqual(wb.get("D2"), 16.5)

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            Workbook.load("不存在的文件.json")


class CopyTest(unittest.TestCase):
    def test_copy_is_independent(self):
        wb = Workbook()
        wb.update({"A1": "1", "A2": "=A1+1"})
        clone = wb.copy()
        clone.set("A1", "10")
        self.assertEqual(wb.get("A2"), 2.0)
        self.assertEqual(clone.get("A2"), 11.0)

    def test_copy_keeps_flags(self):
        wb = Workbook(iterative=True, max_iterations=7)
        wb.update({"A1": "=(A1+10)/2"})
        clone = wb.copy()
        self.assertTrue(clone.engine.iterative)
        self.assertEqual(clone.engine.max_iterations, 7)


class BatchTest(unittest.TestCase):
    def test_update_recalculates_once(self):
        wb = Workbook()
        stats = wb.update({"A1": "1", "A2": "=A1+1", "A3": "=A2+1"})
        self.assertEqual(wb.get("A3"), 3.0)
        self.assertFalse(stats.full)

    def test_update_with_none_clears(self):
        wb = Workbook()
        wb.update({"A1": "1"})
        wb.update({"A1": None})
        self.assertIsNone(wb.get("A1"))


if __name__ == "__main__":
    unittest.main()
