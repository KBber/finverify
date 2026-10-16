"""用重建后的抽取，取 688610 裸标题区的资产总计，跟摘要区披露值比对。

摘要区 p12：总资产 159,124.72 万元 = 1,591,247,200 元（合并口径）。
若报表区取到的数等于它，裸标题就是合并；否则是母公司。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finverify.pipeline import audit  # noqa: E402

PDF = pathlib.Path(sys.argv[1])
SUMMARY_TOTAL = 159_124.72 * 10_000  # 万元 -> 元

rep = audit(PDF, period="2023")

L = [f"{PDF.name}", f"摘要区披露总资产: {SUMMARY_TOTAL:,.2f} 元", ""]
assets = [m for m in rep.metrics if "assets" in m.kind.value or "equity" in m.kind.value]
for m in sorted(rep.metrics, key=lambda x: (x.number.evidence.page, x.kind.value)):
    e = m.number.evidence
    flag = ""
    if abs(m.number.value - SUMMARY_TOTAL) < 1.0:
        flag = "   <== 与摘要区一致"
    L.append(
        f"  p{e.page:>4} {m.kind.value:<22} {m.scope.value:<12} "
        f"{m.number.value:>20,.2f}{flag}"
    )

L.append("")
hit = [m for m in rep.metrics if abs(m.number.value - SUMMARY_TOTAL) < 1.0]
L.append(f"与摘要区一致的指标 {len(hit)} 条")
for m in hit:
    L.append(f"  p{m.number.evidence.page} {m.kind.value} {m.scope.value}")

pathlib.Path(__file__).with_name("scope_688610_compare.txt").write_text(
    "\n".join(L), encoding="utf-8"
)
print("written")
