"""强类型金融数据结构 —— 每个数字都必须携带出处。

设计要点
--------
1. ``FinancialMetric.value`` 不是裸 float，而是 :class:`VerifiedNumber`，
   它同时携带数值、单位、期间和 :class:`EvidenceSpan`。
2. ``EvidenceSpan.page`` + ``EvidenceSpan.bbox`` 让结论可以被点开验证。
3. ``EvidenceQuality`` 用五级评分过滤低质量证据 —— 没有出处就不用。
"""

from __future__ import annotations

import enum
import sys
from dataclasses import dataclass, field


class EvidenceQuality(enum.IntEnum):
    """证据质量分级。低于 ``PARAPHRASE`` 的证据默认丢弃。"""

    DIRECT_QUOTE = 5
    """原文逐字命中（JSON-LD 结构化披露 / XBRL 标签）"""

    TABLE_CELL = 4
    """表格单元格精确命中（行列坐标锁定）"""

    PARAPHRASE = 3
    """语义近义命中，数值来自上下文推断"""

    INFERRED = 2
    """跨文档/跨页推断得到"""

    UNVERIFIED = 1
    """无出处 —— 默认拒绝使用"""


if sys.version_info >= (3, 11):
    StrEnum = enum.StrEnum
else:  # pragma: no cover — Python 3.10 回退

    class StrEnum(str, enum.Enum):
        """3.10 上的等价实现。

        3.11 才引入 enum.StrEnum，而本项目声明支持 3.10。
        3.10 的 `class X(str, Enum)` 里 `str(x)` 会返回 "X.VALUE"，
        因此必须显式覆写 __str__ 才能与 3.11+ 行为一致 ——
        否则同一份报告在不同 Python 版本上会渲染出不同字符串。
        """

        def __str__(self) -> str:
            return str(self.value)


class StatementKind(StrEnum):
    """报表类型。"""

    BALANCE_SHEET = "balance_sheet"
    """资产负债表"""

    INCOME_STATEMENT = "income_statement"
    """利润表"""

    CASH_FLOW = "cash_flow"
    """现金流量表"""

    NOTES = "notes"
    """附注"""


class MetricKind(StrEnum):
    """指标标识。参与勾稽校验的指标必须有明确枚举值。"""

    # 资产负债表
    TOTAL_ASSETS = "total_assets"
    TOTAL_LIABILITIES = "total_liabilities"
    TOTAL_EQUITY = "total_equity"

    # 利润表
    REVENUE = "revenue"
    OPERATING_COST = "operating_cost"
    OPERATING_PROFIT = "operating_profit"
    TOTAL_PROFIT = "total_profit"
    INCOME_TAX = "income_tax"
    NET_PROFIT = "net_profit"

    # 现金流量表
    OPERATING_CASH_FLOW = "operating_cash_flow"
    INVESTING_CASH_FLOW = "investing_cash_flow"
    FINANCING_CASH_FLOW = "financing_cash_flow"
    NET_CASH_INCREASE = "net_cash_increase"
    CASH_EOP = "cash_eop"

    # 其他
    UNKNOWN = "unknown"


class VerificationStatus(StrEnum):
    """校验结论。刻意使用三态而非二值 —— 承认证据不足是正常状态。"""

    PASS = "PASS"
    """证据充分且通过全部勾稽校验"""

    FAIL = "FAIL"
    """存在明确矛盾（数值冲突 / 勾稽不成立）"""

    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    """证据不足或存在歧义，需人工介入"""


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    """一个可回溯到原文的坐标。

    Attributes
    ----------
    page:
        1-based 页码（与 PDF 阅读器一致）
    bbox:
        ``(x0, y0, x1, y1)``，PDF 用户坐标系，左下角为原点。
        表头可额外提供 ``cell`` 标记行列位置。
    quality:
        证据质量等级
    snippet:
        原文片段，用于前端展示与人工复核
    """

    page: int
    bbox: tuple[float, float, float, float]
    quality: EvidenceQuality
    snippet: str
    cell: tuple[int, int] | None = None
    """表格单元格坐标 ``(row, col)``，非表格证据为 None。"""

    @property
    def is_trustworthy(self) -> bool:
        """是否达到可采信阈值（``PARAPHRASE`` 及以上）。"""
        return self.quality >= EvidenceQuality.PARAPHRASE

    def __str__(self) -> str:
        loc = f"p{self.page}"
        if self.cell is not None:
            loc += f" r{self.cell[0]}c{self.cell[1]}"
        return f"{loc} @{self.quality.name}"


@dataclass(frozen=True, slots=True)
class VerifiedNumber:
    """一个自带出处的数字。

    这是本框架的核心类型 —— 裸 ``float`` 在这里不存在。
    """

    value: float
    unit: str
    """金额单位统一归一到 ``元``；比率类用 ``"ratio"`` / ``"percent"``。"""

    period: str
    """报告期，如 ``"2023"`` / ``"2023H1"`` / ``"2023Q4"``。"""

    evidence: EvidenceSpan

    def __post_init__(self) -> None:
        if not self.evidence.is_trustworthy:
            raise ValueError(
                f"证据质量 {self.evidence.quality.name} 低于阈值，"
                f"拒绝构造 VerifiedNumber（page={self.evidence.page}）"
            )

    def __str__(self) -> str:
        return f"{self.value:,.2f} {self.unit} @ {self.evidence}"


@dataclass(slots=True)
class FinancialMetric:
    """一条带出处的财务指标。"""

    kind: MetricKind
    label: str
    """中文显示名，如 ``"营业收入"``。"""

    number: VerifiedNumber
    statement: StatementKind

    extra: dict[str, str] = field(default_factory=dict)
    """附加披露信息，如币种、会计准则。"""

    def __str__(self) -> str:
        return f"{self.label}({self.kind.value}) = {self.number}"
