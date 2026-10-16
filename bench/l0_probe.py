"""L0 探路：用一份真实 A 股年报测 finverify 解析器的真实失败率。

只回答一个问题：**解析器能不能读出真实年报里的标准科目？**
读不出的话，是哪类表格读不出、失败率多少。
"""

from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymupdf  # noqa: E402

from finverify import MetricKind  # noqa: E402
from finverify.ingestion import load_document  # noqa: E402
from finverify.pipeline import audit  # noqa: E402

PDF = pathlib.Path(sys.argv[1])

out: list[str] = []

doc = pymupdf.open(str(PDF))
out.append(f"文件: {PDF.name}")
out.append(f"页数: {doc.page_count}")
out.append(f"大小: {PDF.stat().st_size / 1024 / 1024:.2f} MB")
out.append("")

# ---- 表格密度：真实年报里有多少页能被识别成表格 ----
t0 = time.perf_counter()
parsed = load_document(PDF)
elapsed = time.perf_counter() - t0

total_rows = sum(len(t.rows) for t in parsed.tables)
pages_with_tables = {t.page for t in parsed.tables}
out.append(f"解析耗时: {elapsed:.1f} s")
out.append(f"识别到表格: {len(parsed.tables)} 张，覆盖 {len(pages_with_tables)}/{doc.page_count} 页")
out.append(f"识别到行数: {total_rows}")
out.append(f"解析层告警: {len(parsed.warnings)} 条")
for w in parsed.warnings[:5]:
    out.append(f"  - {w}")
out.append("")

# ---- 抽取层 ----
t1 = time.perf_counter()
report = audit(PDF, period="2023")
audit_elapsed = time.perf_counter() - t1

trace = report.trace.as_dict()
out.append(f"审计耗时: {audit_elapsed:.1f} s")
out.append(f"总状态: {report.status.value}")
out.append(f"看到行数: {trace['rows_seen']}")
out.append(f"匹配行数: {trace['rows_matched']}  命中率 {trace['match_rate']}")
out.append(f"数值被拒: {trace['numbers_rejected']}")
out.append("")
out.append(f"--- 抽出的指标 ({len(report.metrics)}) ---")
for m in report.metrics:
    ev = m.number.evidence
    out.append(
        f"  {m.kind.value:<24} {m.number.value:>20,.2f}  p{ev.page}"
    )
out.append("")

expected = {
    MetricKind.TOTAL_ASSETS,
    MetricKind.TOTAL_LIABILITIES,
    MetricKind.TOTAL_EQUITY,
    MetricKind.REVENUE,
    MetricKind.TOTAL_PROFIT,
    MetricKind.INCOME_TAX,
    MetricKind.NET_PROFIT,
    MetricKind.OPERATING_CASH_FLOW,
    MetricKind.INVESTING_CASH_FLOW,
    MetricKind.FINANCING_CASH_FLOW,
    MetricKind.NET_CASH_INCREASE,
}
got = {m.kind for m in report.metrics}
missing = sorted(k.value for k in expected - got)
out.append(f"--- 覆盖情况: {len(got & expected)}/{len(expected)} ---")
out.append(f"缺失指标: {missing or '无'}")
out.append("")
out.append("--- 勾稽校验 ---")
for r in report.rule_results:
    d = f"{r.delta:,.2f}" if r.delta is not None else "-"
    out.append(f"  {r.status.value:<16} {r.rule_id:<14} Δ={d}")
out.append("")
out.append("--- 未匹配的前 20 个标签（人工看失败模式）---")
for lab in trace["unmatched_labels"][:20]:
    out.append(f"  {lab!r}")

dest = pathlib.Path(__file__).with_name("l0_probe_out.txt")
dest.write_text("\n".join(out), encoding="utf-8")
print(f"written -> {dest}")
