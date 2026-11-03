import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from soroban.cli import main


def run(*argv):
    buffer = StringIO()
    with redirect_stdout(buffer):
        code = main(list(argv))
    return code, buffer.getvalue()


class CalcTest(unittest.TestCase):
    def test_simple(self):
        code, output = run("calc", "=1+2")
        self.assertEqual(code, 0)
        self.assertIn("3", output)

    def test_several_expressions(self):
        code, output = run("calc", "=SUM(1,2,3)", '=UPPER("ab")')
        self.assertEqual(code, 0)
        self.assertIn("6", output)
        self.assertIn("AB", output)

    def test_error_sets_exit_code(self):
        code, output = run("calc", "=1/0")
        self.assertEqual(code, 1)
        self.assertIn("#DIV/0!", output)

    def test_parse_error(self):
        code, output = run("calc", "=1+")
        self.assertEqual(code, 1)
        self.assertIn("#PARSE!", output)


class FileCommandTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.folder.name, "book.json")
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "sheets": {"Sheet1": {
                "A1": "3", "B1": "4", "C1": "=A1*B1", "A2": "合计",
            }}}, handle)

    def tearDown(self):
        self.folder.cleanup()

    def test_run_table(self):
        code, output = run("run", self.path)
        self.assertEqual(code, 0)
        self.assertIn("12", output)
        self.assertIn("合计", output)

    def test_run_formulas(self):
        _, output = run("run", self.path, "--formulas")
        self.assertIn("=A1*B1", output)

    def test_run_json(self):
        code, output = run("run", self.path, "--json")
        payload = json.loads(output)
        self.assertEqual(payload["sheet"], "Sheet1")
        self.assertTrue(any(cell["ref"] == "C1" for cell in payload["cells"]))

    def test_audit(self):
        code, output = run("audit", self.path, "C1")
        self.assertEqual(code, 0)
        self.assertIn("=A1*B1", output)
        self.assertIn("12", output)

    def test_goal(self):
        code, output = run("goal", self.path, "--target", "C1",
                           "--variable", "A1", "--value", "24")
        self.assertEqual(code, 0)
        self.assertIn("目标搜索", output)
        self.assertIn("8", output)

    def test_sensitivity(self):
        code, output = run("sensitivity", self.path, "--target", "C1",
                           "--variable", "A1", "--values", "1,2,5")
        self.assertEqual(code, 0)
        self.assertIn("4", output)
        self.assertIn("20", output)

    def test_functions_filter(self):
        code, output = run("functions", "--filter", "PMT")
        self.assertEqual(code, 0)
        self.assertIn("PMT", output)
        self.assertIn("等额本息", output)

    def test_missing_file(self):
        with self.assertRaises(SystemExit):
            run("run", os.path.join(self.folder.name, "nope.json"))


if __name__ == "__main__":
    unittest.main()
