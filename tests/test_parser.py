import unittest

from soroban.errors import FormulaError
from soroban.lexer import tokenize
from soroban.parser import (
    BinaryNode,
    CallNode,
    RefNode,
    function_names,
    parse_formula,
    references,
    unparse,
)
from soroban.values import Err

from .support import calc, calc_error


class LexerTest(unittest.TestCase):
    def test_numbers(self):
        kinds = [token.kind for token in tokenize("1 2.5 .5 1e3 1E-2")]
        self.assertEqual(kinds, ["num"] * 5 + ["eof"])

    def test_string_doubled_quote(self):
        tokens = tokenize('"a""b"')
        self.assertEqual(tokens[0].text, 'a"b')

    def test_quoted_sheet(self):
        tokens = tokenize("'销售 表'!A1")
        self.assertEqual(tokens[0].kind, "qname")
        self.assertEqual(tokens[0].text, "销售 表")
        self.assertEqual(tokens[1].kind, "!")
        self.assertEqual(tokens[2].kind, "ident")

    def test_unclosed_string(self):
        with self.assertRaises(FormulaError):
            tokenize('"abc')

    def test_unknown_character(self):
        with self.assertRaises(FormulaError):
            tokenize("1 @ 2")


class PrecedenceTest(unittest.TestCase):
    def test_multiply_before_add(self):
        self.assertEqual(calc("=1+2*3"), 7.0)

    def test_parentheses(self):
        self.assertEqual(calc("=(1+2)*3"), 9.0)

    def test_unary_minus_binds_tighter_than_power(self):
        # 与 Excel 一致：-2^2 = 4
        self.assertEqual(calc("=-2^2"), 4.0)

    def test_power_is_right_associative(self):
        self.assertEqual(calc("=2^3^2"), 512.0)

    def test_negative_exponent(self):
        self.assertEqual(calc("=2^-2"), 0.25)

    def test_percent_postfix(self):
        self.assertEqual(calc("=50%"), 0.5)
        self.assertEqual(calc("=200*10%"), 20.0)

    def test_concat_binds_looser_than_add(self):
        self.assertEqual(calc('="x"&1+2'), "x3")

    def test_comparison(self):
        self.assertIs(calc("=1<2"), True)
        self.assertIs(calc("=2<=2"), True)
        self.assertIs(calc("=2<>2"), False)

    def test_comparison_chain_is_left_to_right(self):
        # 1<2 → TRUE，TRUE<3 → FALSE（逻辑值排在数字之后）
        self.assertIs(calc("=1<2<3"), False)


class StructureTest(unittest.TestCase):
    def test_function_call(self):
        ast = parse_formula("=SUM(A1:A2, 3)")
        self.assertIsInstance(ast, CallNode)
        self.assertEqual(ast.name, "SUM")
        self.assertEqual(len(ast.args), 2)

    def test_lookalike_function_name(self):
        # LOG10 既像 LOG 列第 10 行，也像函数；有括号时按函数处理
        ast = parse_formula("=LOG10(100)")
        self.assertIsInstance(ast, CallNode)
        self.assertEqual(ast.name, "LOG10")
        bare = parse_formula("=LOG10")
        self.assertIsInstance(bare, RefNode)

    def test_sheet_qualified_ref(self):
        ast = parse_formula("='销售 表'!B2")
        self.assertIsInstance(ast, RefNode)
        assert isinstance(ast, RefNode)
        self.assertEqual(ast.ref.sheet, "销售 表")
        self.assertEqual((ast.ref.row, ast.ref.col), (1, 1))

    def test_range_inherits_sheet(self):
        ast = parse_formula("=SUM(数据!A1:B2)")
        assert isinstance(ast, CallNode)
        arg = ast.args[0]
        self.assertEqual(arg.range.sheet, "数据")

    def test_references_lists(self):
        _, ranges = references(parse_formula("=SUM(A1:B2)+C3"))
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0].a1, "A1:B2")

    def test_function_names(self):
        names = function_names(parse_formula("=IF(SUM(A1:A2)>0,ROUND(1.5,0),0)"))
        self.assertEqual(names, ["IF", "SUM", "ROUND"])

    def test_blank_arguments(self):
        ast = parse_formula("=IF(A1,,3)")
        self.assertEqual(len(ast.args), 3)
        self.assertEqual(unparse(ast.args[1]), "")


class ParseErrorTest(unittest.TestCase):
    def test_incomplete(self):
        for text in ("=1+", "=SUM(1", "=A1:", "=*2", "=()"):
            with self.assertRaises(FormulaError) as caught:
                parse_formula(text)
            self.assertEqual(caught.exception.code, "#PARSE!")

    def test_trailing_garbage(self):
        with self.assertRaises(FormulaError):
            parse_formula("=1 2")

    def test_unknown_name_in_expression(self):
        with self.assertRaises(FormulaError):
            parse_formula("=foo+1")

    def test_unknown_function_is_a_value_error(self):
        value = calc("=NOPE(1)")
        self.assertIsInstance(value, Err)
        self.assertEqual(value.code, "#NAME?")

    def test_empty_formula(self):
        with self.assertRaises(FormulaError):
            parse_formula("=")


class UnparseTest(unittest.TestCase):
    def test_round_trip(self):
        for text in (
            "1+2*3",
            "(1+2)*3",
            "2^(3^2)",
            "SUM(A1:B2)*2",
            'IF(B4>1000,B4*0.1,"高")',
            "'销售 表'!$B$2+经营!C3",
            "ROUND(-2.5,0)",
        ):
            ast = parse_formula("=" + text)
            again = parse_formula("=" + unparse(ast))
            self.assertEqual(unparse(again), unparse(ast))

    def test_parentheses_are_preserved_when_needed(self):
        self.assertEqual(unparse(parse_formula("=(1+2)*3")), "(1+2)*3")

    def test_sheet_name_is_quoted(self):
        text = unparse(parse_formula("='销售 表'!A1"))
        self.assertEqual(text, "'销售 表'!A1")


class LiteralTest(unittest.TestCase):
    def test_number(self):
        self.assertEqual(calc("=1.5"), 1.5)

    def test_string_escape(self):
        self.assertEqual(calc('="a""b"'), 'a"b')

    def test_boolean(self):
        self.assertIs(calc("=TRUE"), True)

    def test_error_value_is_not_fatal(self):
        self.assertEqual(calc_error("=1/0").code, "#DIV/0!")


if __name__ == "__main__":
    unittest.main()
