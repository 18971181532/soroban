# 示例工作簿

三个文件，覆盖「带公式的 JSON」「纯数据的 CSV」两种输入，以及 README 里出现的所有命令。

## quarterly.json —— 两表联动

`明细` 是六个季度销售额的原始数据，`汇总` 是跨表引用算出来的指标。

- `明细!G2` 用 `INDEX + MATCH + MAX` 找出卖得最好的季度：`=INDEX($B$1:$E$1,MATCH(MAX(B2:E2),B2:E2,0))`
- `汇总!B2` 跨表取数：`=明细!B6`
- `汇总!C3` 算环比：`=C2/B2-1`
- `汇总!B5` 按阈值判定：`=IF(B2>=750,"达标","未达标")`

```bash
python run.py run examples/quarterly.json
python run.py run examples/quarterly.json --sheet 汇总 --formulas
python run.py audit examples/quarterly.json "汇总!B2"
```

## loan.json —— 财务函数与反向求解

等额本息房贷：本金 30 万、年利率 4.9%、20 年。`B6` 是月供 `=PMT(B4/12,B5*12,-B3)`，`B8` 是总利息，右下角还有一张五档利率的对照表。

```bash
python run.py run examples/loan.json
python run.py goal examples/loan.json --target 贷款!B6 --variable 贷款!B4 --value 1800
python run.py sensitivity examples/loan.json --target 贷款!B6 --variable 贷款!B4 \
    --values 0.039,0.049,0.059
```

`goal` 的结果默认不写回文件，想保存就加 `--save out.json`。

## sales.csv —— 从 CSV 起步

六个月的销量与单价，`金额` 列是公式 `=B2*C2`。CSV 里的 `=...` 会被当成公式解析，所以纯数据文件也能直接带计算。

```bash
python run.py run examples/sales.csv
python run.py calc "=SUM(B2:B7)"      # 单条表达式，不需要文件
```

## 自己造一个

```python
from soroban import Workbook

wb = Workbook(["预算"])
wb.update({
    "A1": "项目", "B1": "金额",
    "A2": "服务器", "B2": "1200",
    "A3": "域名", "B3": "80",
    "A4": "合计", "B4": "=SUM(B2:B3)",
})
wb.save("budget.json")          # .json 保留公式，.csv 只存值
print(wb.get("预算!B4"))        # 1280.0
```

JSON 格式本身很直白，一层工作表名、一层地址：

```json
{
  "version": 1,
  "sheets": {
    "预算": { "A2": "服务器", "B2": "1200", "B4": "=SUM(B2:B3)" }
  }
}
```
