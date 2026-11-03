import unittest

from soroban.values import Err

from .support import calc, calc_error, numeric


TABLE = {
    "A1": "1", "B1": "一",
    "A2": "2", "B2": "二",
    "A3": "3", "B3": "三",
    "D1": "10", "D2": "20", "D3": "30",
}


class MathTest(unittest.TestCase):
    def test_sum_and_product(self):
        self.assertEqual(calc("=SUM(1,2,3)"), 6.0)
        self.assertEqual(calc("=SUM(A1:A3)", TABLE), 6.0)
        self.assertEqual(calc("=PRODUCT(2,3,4)"), 24.0)
        self.assertEqual(calc("=SUMSQ(1,2,3)"), 14.0)

    def test_sum_ignores_text_in_range_but_not_direct(self):
        cells = {"A1": "1", "A2": "文本", "A3": "3"}
        self.assertEqual(calc("=SUM(A1:A3)", cells), 4.0)
        self.assertEqual(calc_error('=SUM("文本")').code, "#VALUE!")

    def test_sum_coerces_direct_values(self):
        self.assertEqual(calc('=SUM("3",TRUE,2)'), 6.0)

    def test_sumproduct(self):
        self.assertEqual(calc("=SUMPRODUCT(A1:A3,D1:D3)", TABLE), 140.0)

    def test_abs_sign_int(self):
        self.assertEqual(calc("=ABS(-3)"), 3.0)
        self.assertEqual(calc("=SIGN(-9)"), -1.0)
        self.assertEqual(calc("=SIGN(0)"), 0.0)
        self.assertEqual(calc("=INT(-2.5)"), -3.0)

    def test_rounding(self):
        self.assertEqual(calc("=ROUND(2.675,2)"), 2.68)
        self.assertEqual(calc("=ROUND(-2.5,0)"), -3.0)
        self.assertEqual(calc("=ROUNDUP(3.141,2)"), 3.15)
        self.assertEqual(calc("=ROUNDDOWN(-3.19,1)"), -3.1)
        self.assertEqual(calc("=TRUNC(3.99)"), 3.0)

    def test_mod_follows_divisor_sign(self):
        self.assertEqual(calc("=MOD(-7,3)"), 2.0)
        self.assertEqual(calc("=MOD(7,-3)"), -2.0)
        self.assertEqual(calc_error("=MOD(1,0)").code, "#DIV/0!")

    def test_powers_and_roots(self):
        self.assertEqual(calc("=POWER(2,10)"), 1024.0)
        self.assertEqual(calc("=SQRT(9)"), 3.0)
        self.assertEqual(calc_error("=SQRT(-1)").code, "#NUM!")
        self.assertEqual(calc("=EXP(0)"), 1.0)
        self.assertAlmostEqual(calc("=LN(EXP(1))"), 1.0, places=12)
        self.assertAlmostEqual(calc("=LOG(8,2)"), 3.0, places=12)
        self.assertEqual(calc("=LOG10(1000)"), 3.0)

    def test_ceiling_floor(self):
        self.assertEqual(calc("=CEILING(2.1,1)"), 3.0)
        self.assertEqual(calc("=CEILING(2.1,0.5)"), 2.5)
        self.assertEqual(calc("=FLOOR(2.9,1)"), 2.0)

    def test_integer_helpers(self):
        self.assertEqual(calc("=GCD(12,18)"), 6.0)
        self.assertEqual(calc("=LCM(4,6)"), 12.0)
        self.assertEqual(calc("=FACT(5)"), 120.0)
        self.assertEqual(calc("=COMBIN(10,3)"), 120.0)
        self.assertEqual(calc_error("=FACT(-1)").code, "#NUM!")


class StatsTest(unittest.TestCase):
    def test_min_max_average(self):
        self.assertEqual(calc("=MIN(D1:D3)", TABLE), 10.0)
        self.assertEqual(calc("=MAX(D1:D3)", TABLE), 30.0)
        self.assertEqual(calc("=AVERAGE(D1:D3)", TABLE), 20.0)
        self.assertEqual(calc("=MIN()"), 0.0)
        self.assertEqual(calc_error("=AVERAGE()").code, "#DIV/0!")

    def test_median_and_quartile_like(self):
        self.assertEqual(calc("=MEDIAN(3,1,4,1,5)"), 3.0)
        self.assertEqual(calc("=MEDIAN(1,2,3,4)"), 2.5)
        self.assertEqual(calc("=LARGE(D1:D3,2)", TABLE), 20.0)
        self.assertEqual(calc("=SMALL(D1:D3,1)", TABLE), 10.0)
        self.assertEqual(calc_error("=LARGE(D1:D3,9)", TABLE).code, "#NUM!")

    def test_dispersion(self):
        self.assertAlmostEqual(calc("=STDEV(2,4,4,4,5,5,7,9)"), 2.138089935, places=9)
        self.assertAlmostEqual(calc("=STDEVP(2,4,4,4,5,5,7,9)"), 2.0, places=9)
        self.assertAlmostEqual(calc("=VAR(2,4)"), 2.0, places=9)

    def test_counting(self):
        cells = {"A1": "1", "A2": "文本", "A3": "3", "A4": ""}
        self.assertEqual(calc("=COUNT(A1:A4)", cells), 2.0)
        self.assertEqual(calc("=COUNTA(A1:A4)", cells), 3.0)
        self.assertEqual(calc("=COUNTBLANK(A1:A4)", cells), 1.0)

    def test_conditional_aggregates(self):
        cells = {"A1": "1", "A2": "10", "A3": "5", "B1": "2", "B2": "4", "B3": "6"}
        self.assertEqual(calc('=COUNTIF(A1:A3,">=5")', cells), 2.0)
        self.assertEqual(calc("=SUMIF(A1:A3,\">=5\",B1:B3)", cells), 10.0)
        self.assertEqual(calc("=AVERAGEIF(A1:A3,\">=5\",B1:B3)", cells), 5.0)

    def test_wildcard_criteria(self):
        cells = {"A1": "apple", "A2": "apricot", "A3": "banana"}
        self.assertEqual(calc('=COUNTIF(A1:A3,"a*")', cells), 2.0)
        self.assertEqual(calc('=COUNTIF(A1:A3,"?????")', cells), 1.0)
        self.assertEqual(calc('=SUMIF(A1:A3,"<>apple",B1:B3)',
                             {**cells, "B1": "1", "B2": "2", "B3": "3"}), 5.0)


class LogicTest(unittest.TestCase):
    def test_if(self):
        self.assertEqual(calc("=IF(TRUE,1,2)"), 1.0)
        self.assertEqual(calc("=IF(FALSE,1,2)"), 2.0)
        self.assertIs(calc("=IF(FALSE,1)"), False)

    def test_if_is_lazy(self):
        # 没走到的分支即使会出错也不该冒泡
        self.assertEqual(calc("=IF(TRUE,1,1/0)"), 1.0)
        self.assertEqual(calc("=IFERROR(1/0,42)"), 42.0)
        self.assertEqual(calc("=IFNA(1/0,42)"), Err("#DIV/0!"))

    def test_and_or_not_xor(self):
        self.assertIs(calc("=AND(TRUE,1,2)"), True)
        self.assertIs(calc("=AND(TRUE,0)"), False)
        self.assertIs(calc("=OR(FALSE,0,3)"), True)
        self.assertIs(calc("=NOT(0)"), True)
        self.assertIs(calc("=XOR(TRUE,TRUE,TRUE)"), True)

    def test_ifs_and_switch(self):
        self.assertEqual(calc("=IFS(FALSE,1,TRUE,2)"), 2.0)
        self.assertEqual(calc('=SWITCH(2,1,"一",2,"二","其他")'), "二")
        self.assertEqual(calc('=SWITCH(9,1,"一","其他")'), "其他")

    def test_na(self):
        self.assertEqual(calc_error("=NA()").code, "#N/A")


class TextFunctionTest(unittest.TestCase):
    def test_len_left_right_mid(self):
        self.assertEqual(calc('=LEN("中文abc")'), 5.0)
        self.assertEqual(calc('=LEFT("abcdef",3)'), "abc")
        self.assertEqual(calc('=RIGHT("abcdef",2)'), "ef")
        self.assertEqual(calc('=MID("abcdef",2,3)'), "bcd")
        self.assertEqual(calc('=MID("abc",9,2)'), "")

    def test_case_and_trim(self):
        self.assertEqual(calc('=UPPER("aBc")'), "ABC")
        self.assertEqual(calc('=LOWER("aBc")'), "abc")
        self.assertEqual(calc('=PROPER("hello WORLD")'), "Hello World")
        self.assertEqual(calc('=TRIM("  a   b  ")'), "a b")

    def test_concat(self):
        self.assertEqual(calc('=CONCAT("a",1,TRUE)'), "a1TRUE")
        self.assertEqual(calc('=CONCATENATE("a","-","b")'), "a-b")
        self.assertEqual(calc('=TEXTJOIN("-",TRUE,A1:A3)',
                             {"A1": "a", "A2": "", "A3": "c"}), "a-c")

    def test_substitute_and_replace(self):
        self.assertEqual(calc('=SUBSTITUTE("a-b-c","-","+")'), "a+b+c")
        self.assertEqual(calc('=SUBSTITUTE("a-b-c","-","+",2)'), "a-b+c")
        self.assertEqual(calc('=REPLACE("abcdef",2,3,"X")'), "aXef")

    def test_find_and_search(self):
        self.assertEqual(calc('=FIND("b","aBc")'), Err("#VALUE!"))
        self.assertEqual(calc('=SEARCH("b","aBc")'), 2.0)
        self.assertEqual(calc('=FIND("b","abc")'), 2.0)
        self.assertEqual(calc_error('=FIND("z","abc")').code, "#VALUE!")

    def test_rept_value_exact(self):
        self.assertEqual(calc('=REPT("ab",3)'), "ababab")
        self.assertEqual(calc('=VALUE("12.5")'), 12.5)
        self.assertIs(calc('=EXACT("a","A")'), False)

    def test_char_code(self):
        self.assertEqual(calc("=CHAR(65)"), "A")
        self.assertEqual(calc('=CODE("A")'), 65.0)
        self.assertEqual(calc_error("=CHAR(0)").code, "#VALUE!")

    def test_t_and_n(self):
        self.assertEqual(calc('=T("abc")'), "abc")
        self.assertEqual(calc("=T(5)"), "")
        self.assertEqual(calc("=N(TRUE)"), 1.0)
        self.assertEqual(calc('=N("abc")'), 0.0)


class LookupTest(unittest.TestCase):
    def test_vlookup_exact(self):
        self.assertEqual(calc("=VLOOKUP(2,A1:B3,2,FALSE)", TABLE), "二")
        self.assertEqual(calc("=VLOOKUP(1,A1:B3,2,0)", TABLE), "一")

    def test_vlookup_approximate(self):
        self.assertEqual(calc("=VLOOKUP(2.5,A1:B3,2,TRUE)", TABLE), "二")

    def test_vlookup_errors(self):
        self.assertEqual(calc_error("=VLOOKUP(9,A1:B3,2,FALSE)", TABLE).code, "#N/A")
        self.assertEqual(calc_error("=VLOOKUP(1,A1:B3,3,FALSE)", TABLE).code, "#REF!")

    def test_hlookup(self):
        cells = {"A1": "1", "B1": "2", "C1": "3", "A2": "一", "B2": "二", "C2": "三"}
        self.assertEqual(calc("=HLOOKUP(2,A1:C2,2,FALSE)", cells), "二")

    def test_index(self):
        self.assertEqual(calc("=INDEX(A1:B3,3,2)", TABLE), "三")
        self.assertEqual(calc_error("=INDEX(A1:B3,9,1)", TABLE).code, "#REF!")

    def test_index_single_row_uses_column_number(self):
        # 单行区域只给一个序号时，Excel 把它当列号
        row = {"A1": "一", "B1": "二", "C1": "三"}
        self.assertEqual(calc("=INDEX(A1:C1,2)", row), "二")
        self.assertEqual(calc("=INDEX(A1:C1,1,3)", row), "三")
        self.assertEqual(calc_error("=INDEX(A1:C1,4)", row).code, "#REF!")

    def test_index_single_column_uses_row_number(self):
        self.assertEqual(calc("=INDEX(A1:A3,2)", TABLE), 2.0)
        self.assertEqual(calc_error("=INDEX(A1:A3,4)", TABLE).code, "#REF!")

    def test_index_reverse_lookup(self):
        values = {"A1": "一", "B1": "二", "C1": "三",
                  "A2": "1", "B2": "5", "C2": "3"}
        self.assertEqual(calc("=INDEX(A1:C1,MATCH(MAX(A2:C2),A2:C2,0))", values), "二")

    def test_match(self):
        self.assertEqual(calc("=MATCH(2,A1:A3,0)", TABLE), 2.0)
        self.assertEqual(calc("=MATCH(2.5,A1:A3,1)", TABLE), 2.0)
        self.assertEqual(calc_error("=MATCH(9,A1:A3,0)", TABLE).code, "#N/A")

    def test_choose(self):
        self.assertEqual(calc('=CHOOSE(2,"a","b","c")'), "b")
        self.assertEqual(calc_error('=CHOOSE(5,"a")').code, "#VALUE!")

    def test_row_column(self):
        self.assertEqual(calc("=ROW(B7)"), 7.0)
        self.assertEqual(calc("=COLUMN(B7)"), 2.0)
        self.assertEqual(calc("=ROWS(A1:B3)"), 3.0)
        self.assertEqual(calc("=COLUMNS(A1:B3)"), 2.0)


class DateTest(unittest.TestCase):
    def test_serial(self):
        self.assertEqual(calc("=DATE(2024,3,1)"), 45352.0)

    def test_parts(self):
        self.assertEqual(calc("=YEAR(DATE(2024,3,1))"), 2024.0)
        self.assertEqual(calc("=MONTH(DATE(2024,3,1))"), 3.0)
        self.assertEqual(calc("=DAY(DATE(2024,3,1))"), 1.0)

    def test_month_overflow(self):
        self.assertEqual(calc("=MONTH(DATE(2024,13,1))"), 1.0)
        self.assertEqual(calc("=YEAR(DATE(2024,13,1))"), 2025.0)

    def test_edate_clamps(self):
        value = calc("=EDATE(DATE(2024,1,31),1)")
        self.assertEqual(calc(f"=DAY({int(value)})"), 29.0)
        self.assertEqual(calc(f"=MONTH({int(value)})"), 2.0)

    def test_eomonth(self):
        value = int(calc("=EOMONTH(DATE(2024,1,15),1)"))
        self.assertEqual(calc(f"=DAY({value})"), 29.0)

    def test_weekday(self):
        self.assertEqual(calc("=WEEKDAY(DATE(2024,3,3))"), 1.0)   # 周日
        self.assertEqual(calc("=WEEKDAY(DATE(2024,3,4))"), 2.0)   # 周一
        self.assertEqual(calc("=WEEKDAY(DATE(2024,3,4),2)"), 1.0)

    def test_days(self):
        self.assertEqual(calc("=DAYS(DATE(2024,3,1),DATE(2024,2,1))"), 29.0)

    def test_datevalue(self):
        self.assertEqual(calc('=DATEVALUE("2024-03-01")'), 45352.0)
        self.assertEqual(calc_error('=DATEVALUE("不是日期")').code, "#VALUE!")

    def test_text_formats_date(self):
        self.assertEqual(calc('=TEXT(DATE(2024,3,1),"yyyy-mm-dd")'), "2024-03-01")


class FinancialTest(unittest.TestCase):
    def test_pmt(self):
        self.assertAlmostEqual(calc("=PMT(0.1/12,12,-1000)"), 87.91588723, places=6)

    def test_fv_and_pv(self):
        self.assertAlmostEqual(calc("=FV(0.05/12,120,-100,-1000)"), 17175.237442, places=5)
        self.assertAlmostEqual(calc("=PV(0.05/12,120,-100,0)"), 9428.135033, places=5)

    def test_pv_pmt_round_trip(self):
        # 把 PMT 的结果代回 PV，应该回到本金
        cells = {"A1": "=0.05/12", "A2": "120", "A3": "5000"}
        cells["A4"] = "=PMT(A1,A2,-A3)"
        cells["A5"] = "=PV(A1,A2,-A4)"
        self.assertAlmostEqual(calc("=A5", cells), 5000.0, places=6)

    def test_npv(self):
        self.assertAlmostEqual(calc("=NPV(0.1,100,100)"), 173.553719, places=6)

    def test_irr(self):
        cells = {"A1": "-100", "A2": "30", "A3": "40", "A4": "50"}
        self.assertAlmostEqual(calc("=IRR(A1:A4)", cells), 0.0889633947, places=7)

    def test_sln(self):
        self.assertEqual(calc("=SLN(10000,1000,5)"), 1800.0)

    def test_bad_rate(self):
        self.assertEqual(calc_error("=NPV(-1,100)").code, "#NUM!")


class InfoTest(unittest.TestCase):
    def test_is_functions(self):
        cells = {"A1": "1", "A2": "x"}
        self.assertIs(calc("=ISBLANK(A3)", cells), True)
        self.assertIs(calc("=ISBLANK(A1)", cells), False)
        self.assertIs(calc("=ISNUMBER(A1)", cells), True)
        self.assertIs(calc("=ISTEXT(A2)", cells), True)
        self.assertIs(calc("=ISLOGICAL(TRUE)"), True)
        self.assertIs(calc("=ISEVEN(4)"), True)
        self.assertIs(calc("=ISODD(4)"), False)

    def test_error_predicates(self):
        self.assertIs(calc("=ISERROR(1/0)"), True)
        self.assertIs(calc("=ISNA(NA())"), True)
        self.assertIs(calc("=ISERR(NA())"), False)


class TextFormatTest(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(calc('=TEXT(12.345,"0.00")'), "12.35")
        self.assertEqual(calc('=TEXT(1234.5,"#,##0.00")'), "1,234.50")
        self.assertEqual(calc('=TEXT(0.4567,"0.0%")'), "45.7%")

    def test_unknown_pattern_falls_back(self):
        self.assertEqual(calc('=TEXT(3.5,"general")'), "3.5")


if __name__ == "__main__":
    unittest.main()
