"""合成年报样本生成器。

为什么不直接用真实年报
----------------------
1. 可复现 —— 任何人跑都得到同样的数字，benchmark 才有意义。
2. 无版权与合规风险。
3. 可以**故意注入勾稽错误**，用来演示幻觉检测能力，
   而真实财报里找不到这种可控的负样本。

生成的是一份结构接近真实披露文件的三表 PDF：资产负债表 / 利润表 / 现金流量表。
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from finverify.schemas import MetricKind

# --------------------------------------------------------------------------
# 合成公司的财务数据（元）
# --------------------------------------------------------------------------
# 设计原则：三张表内部**完全勾稽**，除非在 injection 中显式注入错误。
# 这样"通过"是默认状态，demo 的翻转点才清晰。

@dataclass(frozen=True, slots=True)
class AnnualReportData:
    """一份年度财务数据。"""

    company: str = "示范科技股份有限公司"
    stock_code: str = "600000"
    period: str = "2023"

    revenue: float = 4_820_000_000.00
    operating_cost: float = 3_016_000_000.00
    operating_expenses: float = 1_105_000_000.00
    total_profit: float = 612_000_000.00
    income_tax: float = 91_800_000.00

    total_assets: float = 9_450_000_000.00
    total_liabilities: float = 4_535_000_000.00

    operating_cash_flow: float = 705_000_000.00
    investing_cash_flow: float = -880_000_000.00
    financing_cash_flow: float = 240_000_000.00

    injected_error: tuple[MetricKind, float] | None = None
    """注入的幻觉：(指标, 错误值)。用于演示勾稽引擎的检出能力。"""

    extra: dict[str, str] = field(default_factory=dict)

    # -- 派生量（全部由基础科目推导，保证勾稽自洽） ------------------
    @property
    def total_equity(self) -> float:
        return self.total_assets - self.total_liabilities

    @property
    def net_profit(self) -> float:
        return self.total_profit - self.income_tax

    @property
    def net_cash_increase(self) -> float:
        return self.operating_cash_flow + self.investing_cash_flow + self.financing_cash_flow

    def value_of(self, kind: MetricKind) -> float:
        base = {
            MetricKind.REVENUE: self.revenue,
            MetricKind.OPERATING_COST: self.operating_cost,
            MetricKind.TOTAL_PROFIT: self.total_profit,
            MetricKind.INCOME_TAX: self.income_tax,
            MetricKind.TOTAL_ASSETS: self.total_assets,
            MetricKind.TOTAL_LIABILITIES: self.total_liabilities,
            MetricKind.TOTAL_EQUITY: self.total_equity,
            MetricKind.NET_PROFIT: self.net_profit,
            MetricKind.OPERATING_CASH_FLOW: self.operating_cash_flow,
            MetricKind.INVESTING_CASH_FLOW: self.investing_cash_flow,
            MetricKind.FINANCING_CASH_FLOW: self.financing_cash_flow,
            MetricKind.NET_CASH_INCREASE: self.net_cash_increase,
        }.get(kind)
        if base is None:
            raise KeyError(f"样本未定义指标 {kind}")
        if self.injected_error and self.injected_error[0] == kind:
            return self.injected_error[1]
        return base


# --------------------------------------------------------------------------
# PDF 排版
# --------------------------------------------------------------------------

PAGE_W, PAGE_H = 595.0, 842.0  # A4
MARGIN_X = 56.0
LINE_H = 17.0
FONT_SIZE = 9.5


# --------------------------------------------------------------------------
# 字体
# --------------------------------------------------------------------------
# PyMuPDF 内置的 base-14 字体（courier / helvetica）不含 CJK 字形，
# 中文会渲染成空白或乱码。必须加载系统 TTF/TTC。

_FONT_CANDIDATES = (
    ("C:/Windows/Fonts/msyh.ttc", "雅黑"),
    ("C:/Windows/Fonts/simsun.ttc", "宋体"),
    ("C:/Windows/Fonts/simhei.ttf", "黑体"),
)


def _pick_fontfile() -> tuple[str, str]:
    for path, label in _FONT_CANDIDATES:
        if Path(path).exists():
            return path, label
    raise RuntimeError(
        "未找到可用的中文字体。合成样本需要 CJK 字形，"
        "请安装 Microsoft YaHei / SimSun / SimHei 之一。"
    )


_FONTFILE, _FONTNAME = _pick_fontfile()
_FONT = pymupdf.Font(fontfile=_FONTFILE)
_FONT_ALIAS = "FVS"


@dataclass(slots=True)
class _Table:
    title: str
    headers: tuple[str, ...]
    rows: list[tuple[tuple[str, ...], ...]]
    """rows -> [(第 N 列文字, 第 N 列数值), ...]"""


def _fmt(v: float) -> str:
    """按财务惯例格式化：千分位、两位小数。"""
    return f"{v:,.2f}"


def _measure(text: str, size: float) -> float:
    """用字体对象测量文本宽度（比例字距，比例中文字体必须如此）。"""
    return _FONT.text_length(text, fontsize=size)


def _render_table(page: pymupdf.Page, y: float, tbl: _Table) -> float:
    """把一张表画到 page 上，返回下一个可用 y 坐标。

    数值列右对齐到 ``right_x``，保证列边界稳定，便于后续 ``find_tables()`` 切分。
    """
    col_x = [MARGIN_X, MARGIN_X + 300]
    right_x = PAGE_W - MARGIN_X

    page.insert_font(fontname=_FONT_ALIAS, fontfile=_FONTFILE)

    page.insert_text((MARGIN_X, y), tbl.title,
                     fontname=_FONT_ALIAS, fontsize=11.5)
    y += LINE_H * 1.8

    for i, h in enumerate(tbl.headers):
        page.insert_text((col_x[i], y), h,
                         fontname=_FONT_ALIAS, fontsize=FONT_SIZE)
    y += LINE_H
    page.draw_line(
        pymupdf.Point(MARGIN_X, y - 4), pymupdf.Point(right_x, y - 4), width=0.6
    )
    y += 7

    for row in tbl.rows:
        for i, cell in enumerate(row):
            # 最后一列是数值，右对齐
            x = (
                right_x - _measure(cell, FONT_SIZE)
                if i == len(row) - 1
                else col_x[i]
            )
            page.insert_text((x, y), cell,
                             fontname=_FONT_ALIAS, fontsize=FONT_SIZE)
        y += LINE_H

    page.draw_line(
        pymupdf.Point(MARGIN_X, y - 4), pymupdf.Point(right_x, y - 4), width=0.6
    )
    return y + LINE_H * 1.3


def build_sample_pdf(
    out_path: str | Path, data: AnnualReportData | None = None
) -> Path:
    """生成合成年报 PDF。

    注入 ``injected_error`` 时，该科目数值被改写，从而破坏勾稽关系 ——
    这正是用来演示幻觉检测的负样本。
    """
    d = data or AnnualReportData()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open()

    # ---------------- 封面 ----------------
    p0 = doc.new_page(width=PAGE_W, height=PAGE_H)
    p0.insert_font(fontname=_FONT_ALIAS, fontfile=_FONTFILE)
    y = MARGIN_X * 2.2
    p0.insert_text((MARGIN_X, y), d.company, fontname=_FONT_ALIAS, fontsize=20)
    y += 40
    p0.insert_text((MARGIN_X, y), "2023 年年度报告（合成样本）",
                   fontname=_FONT_ALIAS, fontsize=14)
    y += 30
    for line in (
        f"股票代码：{d.stock_code}",
        f"报告期：{d.period} 年 1 月 1 日 至 {d.period} 年 12 月 31 日",
        "货币单位：人民币元",
        "",
        "本文件由 finverify 合成生成，仅用于测试与演示。",
        "其中的公司、数据、编号均为虚构，不代表任何真实主体。",
    ):
        p0.insert_text((MARGIN_X, y), line, fontname=_FONT_ALIAS, fontsize=10)
        y += 16

    if d.injected_error:
        kind, _ = d.injected_error
        y += 24
        note = (
            "【负样本说明】本文件的"
            f"{kind.value} 已被注入错误数值，用于验证勾稽引擎的检出能力。"
        )
        p0.insert_text((MARGIN_X, y), note, fontname=_FONT_ALIAS, fontsize=9)
        y += 14
        p0.insert_text(
            (MARGIN_X, y),
            "该行在视觉上与其他行完全一致，没有任何可辨识的异常。",
            fontname=_FONT_ALIAS, fontsize=9,
        )

    # ---------------- 合并资产负债表 ----------------
    bs = [
        ("资产总计", _fmt(d.value_of(MetricKind.TOTAL_ASSETS))),
        ("负债合计", _fmt(d.value_of(MetricKind.TOTAL_LIABILITIES))),
        ("所有者权益合计", _fmt(d.value_of(MetricKind.TOTAL_EQUITY))),
        ("负债和所有者权益总计", _fmt(d.total_assets)),
    ]
    p1 = doc.new_page(width=PAGE_W, height=PAGE_H)
    _render_table(
        p1, MARGIN_X * 1.8,
        _Table("合并资产负债表（简表）", ("项目", "期末余额"), bs),
    )

    is_rows = [
        ("一、营业收入", _fmt(d.value_of(MetricKind.REVENUE))),
        ("减：营业成本", _fmt(d.value_of(MetricKind.OPERATING_COST))),
        ("税金及费用合计", _fmt(d.operating_expenses)),
        ("二、营业利润", _fmt(d.revenue - d.operating_cost - d.operating_expenses)),
        ("三、利润总额", _fmt(d.value_of(MetricKind.TOTAL_PROFIT))),
        ("减：所得税费用", _fmt(d.value_of(MetricKind.INCOME_TAX))),
        ("四、净利润", _fmt(d.value_of(MetricKind.NET_PROFIT))),
    ]
    p2 = doc.new_page(width=PAGE_W, height=PAGE_H)
    _render_table(
        p2, MARGIN_X * 1.8,
        _Table("合并利润表（简表）", ("项目", "本期金额"), is_rows),
    )

    cf_rows = [
        ("经营活动产生的现金流量净额",
         _fmt(d.value_of(MetricKind.OPERATING_CASH_FLOW))),
        ("投资活动产生的现金流量净额",
         _fmt(d.value_of(MetricKind.INVESTING_CASH_FLOW))),
        ("筹资活动产生的现金流量净额",
         _fmt(d.value_of(MetricKind.FINANCING_CASH_FLOW))),
        ("现金及现金等价物净增加额",
         _fmt(d.value_of(MetricKind.NET_CASH_INCREASE))),
    ]
    p3 = doc.new_page(width=PAGE_W, height=PAGE_H)
    _render_table(
        p3, MARGIN_X * 1.8,
        _Table("合并现金流量表（简表）", ("项目", "本期金额"), cf_rows),
    )

    # 中文字体体积很大（msyh.ttc 约 19MB），必须子集化后再保存，
    # 否则样本文件会膨胀到不可接受的大小。子集化失败不应阻断生成。
    with contextlib.suppress(Exception):
        doc.subset_fonts(verbose=False)

    doc.save(str(out), garbage=4, deflate=True)
    doc.close()
    return out


def default_samples_dir(base: str | Path = "samples") -> Path:
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p
