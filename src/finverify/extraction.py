"""指标抽取层 —— 表格行 → 带溯源的 FinancialMetric。

设计取舍
--------
本层是**规则驱动**的，不是 LLM 驱动。原因：

1. 中文财报的科目名高度模式化（"资产总计"、"负债和所有者权益总计"），
   规则匹配在准确性上不输 LLM，且**零成本、零延迟、完全可复现**。
2. 抽取环节引入的不确定性越小，勾稽校验的信号越干净。
   如果抽取本身有 5% 噪声，校验层的信噪比就崩了。

LLM 在本框架里的正确位置是**处理非结构化问答**，
而不是替代确定性的数字抽取。:func:`extraction_trace` 记录了抽取来源，
未来接入 LLM 时可据此对比精度。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from finverify.ingestion import Document, TableRow, normalize_text
from finverify.schemas import (
    EvidenceQuality,
    EvidenceSpan,
    FinancialMetric,
    MetricKind,
    ScopeKind,
    StatementKind,
    VerifiedNumber,
)
from finverify.scope import ScopeMap, detect_scopes

# --------------------------------------------------------------------------
# 数值解析
# --------------------------------------------------------------------------

# 负号不止 ASCII 连字符一种。真实 PDF / 不同字体里实测会拿到：
#   U+002D -   U+2011 ‑(非断行)  U+2212 −(减号)
#   U+2013 –   U+2014 —          U+FF0D －(全角，NFKC 后已是 -)
# 全角连字符与全角括号由 NFKC 折叠，剩下这几个 NFKC 不处理，必须显式列出。
# 漏掉任何一个的后果是：**负数被读成正数**，对财务工具是灾难性错误。
_MINUS_CLASS = r"\u002d\u2010\u2011\u2012\u2013\u2014\u2212\uff0d"

_NUM_RE = re.compile(rf"[{_MINUS_CLASS}]?[\d,]+(?:\.\d+)?")

# 括号负数：会计惯例 (1,234.00) = -1234.00
# 必须同时接受全角括号 —— 中文财报里全角用法非常普遍。
# 也必须允许小数点，否则 "(1,234.00)" 匹配不上。
_PAREN_NEG_RE = re.compile(rf"[（(]\s*[{_MINUS_CLASS}]?[\d,]+(?:\.\d+)?\s*[）)]")

# 中文财报常见的单位后缀，乘数表
_UNIT_SCALE = {
    "万元": 1e4,
    "亿元": 1e8,
    "千元": 1e3,
    "百万元": 1e6,
    "元": 1.0,
}

# 必须按后缀长度降序匹配 —— 否则 "万元" 会先被 "元" 命中，缩放静默失效。
_UNIT_BY_LENGTH = sorted(_UNIT_SCALE.items(), key=lambda kv: -len(kv[0]))


def parse_number(text: str) -> tuple[float, str] | None:
    """从一段文本里解析出数值与单位。

    支持：千分位、负号、括号负数 ``(1,234)``、中文单位后缀、百分号。

    Returns
    -------
    (value_in_yuan, unit) 或 None
    """
    if not text:
        return None
    # NFKC：折叠全角数字/减号/括号等兼容写法。
    # 直接调用本函数的调用方未必经过 ingestion 层规范化，这里必须自保。
    t = normalize_text(text).strip().replace(" ", "").replace(" ", "")

    if not _NUM_RE.search(t):
        return None

    negative = bool(_PAREN_NEG_RE.search(t))

    m = _NUM_RE.search(t)
    if not m:
        return None
    raw = m.group(0).replace(",", "")
    # 非 ASCII 的各种连字符 float() 解析不了，统一换成 ASCII 减号
    raw = re.sub(f"[{_MINUS_CLASS}]", "-", raw)
    try:
        val = float(raw)  # float() 已处理前置负号
    except ValueError:
        return None
    if negative:
        # 仅在括号负数场景翻转；raw 自带的负号已经生效，再取反会变成正数
        val = abs(val) * -1

    unit = "元"
    for suffix, scale in _UNIT_BY_LENGTH:
        if suffix in t:
            return val * scale, unit
    if "%" in t:
        return val, "percent"
    return val, unit


# --------------------------------------------------------------------------
# 科目名 → 指标
# --------------------------------------------------------------------------
# 顺序敏感：先匹配更长/更具体的名称，避免 "营业收入" 抢走 "营业总收入"
# 或 "净利润" 抢走 "归母净利润" 的情况。

_LABEL_PATTERNS: tuple[tuple[str, MetricKind, StatementKind], ...] = (
    # 资产负债表
    # 「负债和所有者权益总计」数值上等于资产总计，归入 TOTAL_ASSETS。
    # 注意不能归入 TOTAL_EQUITY —— 那会让资产恒等式失去意义。
    (r"负债和(?:所有者|股东)权益(?:总|合)计", MetricKind.TOTAL_ASSETS, StatementKind.BALANCE_SHEET),
    (r"(?:所有者|股东)权益(?:合|总)计", MetricKind.TOTAL_EQUITY, StatementKind.BALANCE_SHEET),
    (r"归属于[^)）]{0,12}(?:所有者|股东)权益(?:合|总)计", MetricKind.TOTAL_EQUITY, StatementKind.BALANCE_SHEET),
    (r"(?:负债合计|负债总计|总负债)", MetricKind.TOTAL_LIABILITIES, StatementKind.BALANCE_SHEET),
    (r"(?:资产总计|资产合计|总资产)", MetricKind.TOTAL_ASSETS, StatementKind.BALANCE_SHEET),

    # 利润表
    (r"营业总?收入", MetricKind.REVENUE, StatementKind.INCOME_STATEMENT),
    (r"减?[:：]?\s*营业成本", MetricKind.OPERATING_COST, StatementKind.INCOME_STATEMENT),
    (r"(?:利润总额|利润总额合计)", MetricKind.TOTAL_PROFIT, StatementKind.INCOME_STATEMENT),
    (r"减?[:：]?\s*所得税(?:费用)?", MetricKind.INCOME_TAX, StatementKind.INCOME_STATEMENT),
    (r"(?:归属于[^)）]{0,12})?净利润", MetricKind.NET_PROFIT, StatementKind.INCOME_STATEMENT),

    # 现金流量表
    (r"经营活动产生的现金流量净额", MetricKind.OPERATING_CASH_FLOW, StatementKind.CASH_FLOW),
    (r"投资活动产生的现金流量净额", MetricKind.INVESTING_CASH_FLOW, StatementKind.CASH_FLOW),
    (r"筹资活动产生的现金流量净额", MetricKind.FINANCING_CASH_FLOW, StatementKind.CASH_FLOW),
    (r"现金及现金等价物净增加额", MetricKind.NET_CASH_INCREASE, StatementKind.CASH_FLOW),
)

_COMPILED = tuple(
    (re.compile(pat), kind, stmt) for pat, kind, stmt in _LABEL_PATTERNS
)


@dataclass(slots=True)
class ExtractionTrace:
    """抽取过程的可追溯记录（未来接入 LLM 时的对比基线）。"""

    rows_seen: int = 0
    rows_matched: int = 0
    numbers_rejected: int = 0
    unmatched_labels: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.unmatched_labels is None:
            self.unmatched_labels = []

    def as_dict(self) -> dict[str, object]:
        return {
            "rows_seen": self.rows_seen,
            "rows_matched": self.rows_matched,
            "match_rate": round(
                self.rows_matched / self.rows_seen, 3
            ) if self.rows_seen else 0.0,
            "numbers_rejected": self.numbers_rejected,
            "unmatched_labels": self.unmatched_labels[:20],
        }


def classify_label(label: str) -> tuple[MetricKind, StatementKind] | None:
    """把科目名映射到 (指标, 报表类型)。"""
    if not label:
        return None
    # NFKC：部分字体会把"润"这类字反向映射成兼容码位 U+F9DD，
    # 不规范化则 `利润总额` 正则匹配不上，整行指标静默丢失。
    label = normalize_text(label)
    # 去掉序号前缀：一、二、1、2、（一）
    cleaned = re.sub(r"^[（(]?[一二三四五六七八九十\d]+[）)、.．]?", "", label.strip())
    cleaned = cleaned.replace(" ", "")
    for rx, kind, stmt in _COMPILED:
        if rx.search(cleaned):
            return kind, stmt
    return None


def extract_from_row(
    row: TableRow, period: str
) -> tuple[FinancialMetric, EvidenceSpan] | tuple[None, str]:
    """从一行表格里抽出一个指标。

    Returns
    -------
    成功 -> (FinancialMetric, EvidenceSpan)
    失败 -> (None, 原因说明)
    """
    # 找到"标签列 + 数值列"的配对
    for label_idx, cell in enumerate(row.values):
        if not cell or _NUM_RE.search(cell):
            continue
        hit = classify_label(cell)
        if not hit:
            continue
        kind, stmt = hit

        # 在该行右侧找第一个能解析成数字的单元格
        for val_idx in range(label_idx + 1, len(row.values)):
            parsed = parse_number(row.values[val_idx])
            if parsed is None:
                continue
            value, unit = parsed

            bbox = row.cell_bbox(val_idx) or row.row_bbox
            span = EvidenceSpan(
                page=row.page,
                bbox=bbox,  # type: ignore[arg-type]
                quality=EvidenceQuality.TABLE_CELL,
                snippet=f"{cell} {row.values[val_idx]}".strip(),
                cell=(row.row_index, val_idx),
            )
            metric = FinancialMetric(
                kind=kind,
                label=cell,
                number=VerifiedNumber(
                    value=value, unit=unit, period=period, evidence=span
                ),
                statement=stmt,
            )
            return metric, span
    return None, f"未匹配科目: {row.values[:2]}"


def extract_metrics(
    doc: Document,
    period: str = "2023",
    scope: ScopeKind | None = None,
) -> tuple[list[FinancialMetric], ExtractionTrace]:
    """从整份文档抽取指标。

    同一 ``(period, kind)`` 出现多次时，**保留最后一次出现** ——
    年报里常见"简表"在前、"完整表"在后，后者更权威。

    Parameters
    ----------
    scope:
        只抽取该口径下的指标。真实年报里合并报表与母公司报表的同名科目
        数值不同，**必须先按口径过滤再勾稽**，否则结果必然不平。
        为 ``None`` 时不过滤（合成样本只有单一口径，等价于全部纳入）。
    """
    trace = ExtractionTrace()
    found: dict[MetricKind, FinancialMetric] = {}
    smap: ScopeMap | None = None

    if scope is not None:
        smap = detect_scopes(doc.path)
        if not smap.marks:
            return [], trace  # 没有三表标题 -> 该口径不存在

    for table in doc.tables:
        # 按 y 坐标判定归属，而不是只看页码 —— 同一页可能同时
        # 残留上一张表的尾部与下一张表的表头
        if smap is not None and smap.scope_for(table.page, table.bbox) is not scope:
            continue
        for row in table.rows:
            if row.is_blank:
                continue
            trace.rows_seen += 1
            metric, span_or_reason = extract_from_row(row, period)
            if metric is None:
                trace.numbers_rejected += 1
                label = next((v for v in row.values if v.strip()), "")
                if label:
                    trace.unmatched_labels.append(label)
                continue
            if scope is not None:
                metric.scope = scope
            trace.rows_matched += 1
            found[metric.kind] = metric  # 后出现覆盖先出现

    return list(found.values()), trace


def dedupe(metrics: Iterable[FinancialMetric]) -> list[FinancialMetric]:
    """按 (period, kind) 去重，保留证据质量最高的一条。"""
    best: dict[tuple[str, str], FinancialMetric] = {}
    for m in metrics:
        key = (m.number.period, m.kind.value)
        cur = best.get(key)
        if cur is None or (
            m.number.evidence.quality > cur.number.evidence.quality
        ):
            best[key] = m
    return list(best.values())
