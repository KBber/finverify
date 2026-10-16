"""溯源索引 —— 把"每个数字都能点开"这件事变成可查询的接口。

前端只需要拿 :class:`ProvenanceIndex`，就能为任意一条结论渲染
"查看原文"跳转，定位到具体页码与坐标。
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from finverify.schemas import EvidenceQuality, EvidenceSpan, FinancialMetric


class ProvenanceIndex:
    """指标的溯源索引。

    Examples
    --------
    >>> idx = ProvenanceIndex(metrics)
    >>> span = idx.locate(MetricKind.REVENUE, period="2023")
    >>> print(span.page)          # 142
    >>> idx.to_markdown(MetricKind.REVENUE, "2023")
    """

    def __init__(self, metrics: Iterable[FinancialMetric] = ()) -> None:
        self._by_key: dict[tuple[str, str], FinancialMetric] = {}
        for m in metrics:
            self._by_key[(m.number.period, m.kind.value)] = m

    # ------------------------------------------------------------------

    def add(self, metric: FinancialMetric) -> None:
        self._by_key[(metric.number.period, metric.kind.value)] = metric

    def locate(self, kind: str, period: str) -> EvidenceSpan | None:
        """按 ``(period, kind)`` 取回证据坐标。"""
        m = self._by_key.get((period, kind))
        return m.number.evidence if m else None

    def periods(self) -> list[str]:
        return sorted({p for p, _ in self._by_key})

    def metrics_of(self, period: str) -> list[FinancialMetric]:
        return [m for (p, _), m in sorted(self._by_key.items()) if p == period]

    # ------------------------------------------------------------------

    def to_markdown(self, kind: str, period: str) -> str:
        """渲染成可粘贴进报告的溯源脚注。"""
        m = self._by_key.get((period, kind))
        if m is None:
            return f"> `{kind}` ({period}) 未找到。"
        ev = m.number.evidence
        loc = f"第 {ev.page} 页"
        if ev.cell is not None:
            loc += f" · 表格 R{ev.cell[0]}C{ev.cell[1]}"
        return (
            f"> **{m.label}** = {m.number.value:,.2f} {m.number.unit}\n"
            f"> 来源：{loc} · 证据等级 `{ev.quality.name}`\n"
            f"> 原文：`{ev.snippet.strip()[:120]}`"
        )

    def coverage(self, period: str) -> dict[str, float]:
        """证据覆盖率统计 —— 低于阈值说明抽取质量不达标。"""
        metrics = self.metrics_of(period)
        if not metrics:
            return {"total": 0.0, "direct": 0.0, "traceable": 0.0}
        n = len(metrics)
        direct = sum(
            1
            for m in metrics
            if m.number.evidence.quality
            in (EvidenceQuality.DIRECT_QUOTE, EvidenceQuality.TABLE_CELL)
        )
        traceable = sum(1 for m in metrics if m.number.evidence.is_trustworthy)
        return {
            "total": float(n),
            "direct": round(direct / n, 3),
            "traceable": round(traceable / n, 3),
        }

    def export_json(self) -> str:
        """导出为 JSON，供前端或下游消费。"""
        payload = [
            {
                "period": p,
                "metric": m.kind.value,
                "label": m.label,
                "value": m.number.value,
                "unit": m.number.unit,
                "evidence": {
                    "page": m.number.evidence.page,
                    "bbox": list(m.number.evidence.bbox),
                    "cell": list(m.number.evidence.cell)
                    if m.number.evidence.cell
                    else None,
                    "quality": m.number.evidence.quality.name,
                    "snippet": m.number.evidence.snippet,
                },
            }
            for (p, _), m in sorted(self._by_key.items())
        ]
        return json.dumps(payload, ensure_ascii=False, indent=2)
