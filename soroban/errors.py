"""错误码与内部异常。

对外（单元格里）错误是一种**值**：用户能在 ISERROR、IFERROR 里观察它。
对内（函数实现里）用异常传播更省事：任何一步失败就直接抛出，
最外层统一转成错误值写回单元格。两条路径都从这里定义的错误码出发。
"""

from __future__ import annotations

#: Excel 里常见的七种错误，加上本引擎的两种扩展
ERROR_CODES = {
    "#NULL!": "区域没有交集",
    "#DIV/0!": "除数为零",
    "#VALUE!": "类型不匹配",
    "#REF!": "引用无效",
    "#NAME?": "未知的函数或名称",
    "#NUM!": "数值超出定义域",
    "#N/A": "找不到匹配项",
    "#CYCLE!": "循环引用",
    "#PARSE!": "公式语法错误",
}


class FormulaError(Exception):
    """引擎内部用来中断计算的异常，携带一个错误码。"""

    def __init__(self, code: str = "#VALUE!", message: str = "") -> None:
        self.code = code if code in ERROR_CODES else "#VALUE!"
        self.message = message or ERROR_CODES[self.code]
        super().__init__(f"{self.code} {self.message}".strip())

    def to_value(self):
        """转成可以存进单元格的错误值。"""
        from .values import Err

        return Err(self.code, self.message)


def parse_error(message: str, position: int | None = None) -> FormulaError:
    if position is not None:
        message = f"{message}（第 {position + 1} 个字符）"
    return FormulaError("#PARSE!", message)
