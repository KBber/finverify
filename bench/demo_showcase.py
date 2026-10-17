"""跑完整 demo 并把三份样本的审计结果落成 UTF-8 报告。

控制台是 GBK，直接跑 demo 会在 ✅/⚠️ 等符号上炸 UnicodeEncodeError
（这本身是 v0.3.0 修过的 bug，但打印到管道时仍要留意），
所以让程序自己写文件。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finverify.pipeline import audit  # noqa: E402

SAMPLES = [
    ("正常样本", ROOT / "samples" / "demo_annual_report.pdf", "三表完整、勾稽自洽"),
    (
        "负样本 A：所有者权益多记 5,000 万",
        ROOT / "samples" / "demo_injected_error.pdf",
        "数字在视觉上与正常数字完全一致",
    ),
    (
        "负样本 B：所得税少记 1,200 万",
        ROOT / "samples" / "demo_injected_error_tax.pdf",
        "同上，传统 RAG 不会发现",
    ),
]

L: list[str] = ["=" * 78, "finverify v0.5.0 demo —— 三份样本的完整审计结果", "=" * 78, ""]

for title, pdf, note in SAMPLES:
    rep = audit(pdf, period="2023")
    L.append(f"【{title}】")
    L.append(f"  {note}")
    L.append("")
    L.append(f"  数据源 : {pdf.name}")
    L.append(f"  解析   : {rep.doc_summary}")
    L.append(f"  口径   : {rep.scope.value}")
    L.append(f"  结论   : {rep.status.value}")
    L.append("")
    L.append("  勾稽校验")
    for r in rep.rule_results:
        icon = {"PASS": "PASS", "FAIL": "FAIL", "REVIEW_REQUIRED": "REVIEW"}[
            r.status.value
        ]
        L.append(f"    [{icon:<6}] {r.rule_id}")
        L.append(f"             {r.detail}")
    L.append("")
    L.append(f"  可溯源指标（{len(rep.metrics)} 条，点任一条可跳原文高亮）")
    for m in rep.metrics:
        e = m.number.evidence
        L.append(
            f"    {m.label:<24} {m.number.value:>20,.2f} 元"
            f"   p{e.page}"
            + (f" [{e.quality.value}]" if e.quality else "")
        )
    L.append("")
    L.append("-" * 78)
    L.append("")

L.append("三份样本的 HTML 报告（双击即开，无需安装、无需联网）：")
for name in ("demo_report.html", "demo_report_tax.html", "demo_clean.html"):
    p = ROOT / "samples" / name
    L.append(f"  samples\\{name:<26} {p.stat().st_size / 1024:>7.1f} KB")

out = pathlib.Path(__file__).with_name("demo_showcase.txt")
out.write_text("\n".join(L), encoding="utf-8")
print("written ->", out)
