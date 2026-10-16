"""编排层 —— 把解析、抽取、校验串成一次可复现的审计。

    结束语: 打印 -> markdown -> json

对使用者暴露的最小 API 是 :func:`audit`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from finverify.extraction import ExtractionTrace, extract_metrics
from finverify.ingestion import Document, load_document
from finverify.provenance import ProvenanceIndex
from finverify.schemas import FinancialMetric, ScopeKind, VerificationStatus
from finverify.scope import detect_scopes, dominant_scope
from finverify.validation import DEFAULT_RULES, AccountingRule, ValidationEngine

_STATUS_MARK = {
    VerificationStatus.PASS: "✅",
    VerificationStatus.FAIL: "❌",
    VerificationStatus.REVIEW_REQUIRED: "⚠️",
}


@dataclass(slots=True)
class AuditReport:
    """一次审计的完整结果。"""

    source: str
    period: str
    status: VerificationStatus
    metrics: list[FinancialMetric]
    rule_results: list  # list[RuleResult]
    trace: ExtractionTrace
    doc_summary: str
    warnings: list[str] = field(default_factory=list)
    scope: ScopeKind = ScopeKind.UNKNOWN
    """本次审计采用的口径。UNKNOWN 表示文档里未检测到三表标题（合成样本）。"""

    # ------------------------------------------------------------------
    @property
    def provenance(self) -> ProvenanceIndex:
        return ProvenanceIndex(self.metrics)

    def by_status(self, status: VerificationStatus) -> list:
        return [r for r in self.rule_results if r.status is status]

    # ------------------------------------------------------------------
    def to_text(self) -> str:
        L: list[str] = []
        mark = _STATUS_MARK[self.status]
        L.append("=" * 68)
        L.append(f" 财报数字审计报告  {mark} {self.status}")
        L.append("=" * 68)
        L.append(f" 数据源 : {self.source}")
        L.append(f" 报告期 : {self.period}")
        L.append(f" 解析   : {self.doc_summary}")
        L.append(f" 抽取   : {len(self.metrics)} 个指标，"
                 f"命中率 {self.trace.as_dict()['match_rate']:.0%}")
        L.append("")

        L.append("【勾稽校验】")
        for r in self.rule_results:
            L.append(f"  {_STATUS_MARK[r.status]} {r.status.value:<18} {r.rule_id}")
            L.append(f"     {r.detail}")
        L.append("")

        L.append("【可溯源指标】")
        for m in sorted(self.metrics, key=lambda x: (x.statement.value, x.kind.value)):
            ev = m.number.evidence
            cell = (f" 行{ev.cell[0]}列{ev.cell[1]}" if ev.cell else "")
            L.append(
                f"  {m.label:<24} {m.number.value:>18,.2f} {m.number.unit}"
                f"   ← 第{ev.page}页{cell}"
            )
        L.append("")

        if self.warnings:
            L.append("【警告】")
            for w in self.warnings:
                L.append(f"  ! {w}")
            L.append("")

        L.append("—" * 68)
        L.append(" 说明：REVIEW_REQUIRED 表示证据不足，需人工复核，")
        L.append("       不等同于数据错误。本工具只报告可验证的发现，不做推测。")
        return "\n".join(L)

    # ------------------------------------------------------------------
    def to_markdown(self) -> str:
        L = [f"# 财报数字审计报告 — {self.status.value}", ""]
        L.append(f"- **数据源**：{self.source}")
        L.append(f"- **报告期**：{self.period}")
        L.append(f"- **抽取**：{len(self.metrics)} 个指标，"
                 f"命中率 {self.trace.as_dict()['match_rate']:.0%}")
        L.append("")
        L.append("## 勾稽校验")
        L.append("")
        L.append("| 状态 | 规则 | 说明 |")
        L.append("|---|---|---|")
        for r in self.rule_results:
            L.append(f"| `{r.status.value}` | `{r.rule_id}` | {r.detail} |")
        L.append("")
        L.append("## 可溯源指标")
        L.append("")
        L.append("| 科目 | 数值（元） | 出处 |")
        L.append("|---|---:|---|")
        for m in sorted(self.metrics, key=lambda x: (x.statement.value, x.kind.value)):
            ev = m.number.evidence
            cell = f"第 {ev.page} 页" + (
                f" · 行{ev.cell[0]}列{ev.cell[1]}" if ev.cell else "")
            L.append(f"| {m.label} | {m.number.value:,.2f} | {cell} |")
        return "\n".join(L)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "period": self.period,
            "status": self.status.value,
            "document": self.doc_summary,
            "extraction_trace": self.trace.as_dict(),
            "rules": [
                {
                    "rule_id": r.rule_id,
                    "status": r.status.value,
                    "detail": r.detail,
                    "lhs": r.lhs_value,
                    "rhs": r.rhs_value,
                    "delta": r.delta,
                    "missing": [k.value for k in r.missing],
                }
                for r in self.rule_results
            ],
            "metrics": [
                {
                    "kind": m.kind.value,
                    "label": m.label,
                    "value": m.number.value,
                    "unit": m.number.unit,
                    "statement": m.statement.value,
                    "evidence": {
                        "page": m.number.evidence.page,
                        "bbox": list(m.number.evidence.bbox),
                        "cell": list(m.number.evidence.cell)
                        if m.number.evidence.cell else None,
                        "quality": m.number.evidence.quality.name,
                        "snippet": m.number.evidence.snippet,
                    },
                }
                for m in self.metrics
            ],
            "warnings": self.warnings,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def audit(
    pdf_path: str | Path,
    period: str = "2023",
    rules: tuple[AccountingRule, ...] = DEFAULT_RULES,
    strategy: str = "text",
    scope: ScopeKind | None = None,
) -> AuditReport:
    """对一份财报 PDF 执行完整审计流水线。

    Parameters
    ----------
    scope:
        指定报表口径。真实年报里合并报表与母公司报表的同名科目数值不同
        （实测某公司资产总计相差 1 亿），**混在一起勾稽必然不平**。
        传 ``ScopeKind.CONSOLIDATED`` 可只审计合并口径。

        默认为 ``None``：**自动选择口径** —— 若文档里检测到合并报表就用合并，
        否则退回母公司。若两者都没有（合成样本）则不过滤，保持旧行为。
    """
    doc: Document = load_document(pdf_path, strategy=strategy)

    effective = scope
    smap = None
    if effective is None:
        smap = detect_scopes(pdf_path)
        effective = dominant_scope(smap)
        if effective is None:
            effective = None  # 没找到三表标题 -> 不做口径过滤（旧行为）

    metrics, trace = extract_metrics(doc, period=period, scope=effective)

    engine = ValidationEngine(rules)
    results = engine.validate(metrics, period)

    warnings = list(doc.warnings)
    if effective is not None:
        warnings.append(f"口径: {effective.value}")
    if smap is not None:
        warnings.append(f"口径分布: {smap.summary()}")

    return AuditReport(
        source=str(pdf_path),
        period=period,
        status=engine.worst_status(results),
        metrics=metrics,
        rule_results=results,
        trace=trace,
        doc_summary=doc.summary(),
        warnings=warnings,
        scope=effective or ScopeKind.UNKNOWN,
    )
