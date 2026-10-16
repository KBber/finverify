"""判定 A/B 中 FAIL 退化是真问题还是假问题。

重建后多抽出的指标导致恒等式 FAIL，有两种可能：
  (A) 真阳性 —— 数据抽对了，恒等式确实不平（年报本身或口径问题）
  (B) 假阳性 —— 行重建把数值并错了

判据：看差异额是否**恰好等于某个会计口径的合理差额**，
以及重建出的那行标签/数值是否语义自洽。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import finverify.ingestion as I  # noqa: E402
from finverify.pipeline import audit  # noqa: E402

ORIG = I.load_document
DATA = pathlib.Path(sys.argv[1])
TARGETS = sys.argv[2:]

L: list[str] = []


def run(pdf, merge):
    import finverify.pipeline as P

    I.load_document = lambda p, strategy="text", merge_fragments=merge: ORIG(
        p, strategy, merge_fragments
    )
    P.load_document = I.load_document
    return audit(pdf, period="2023")


for name in TARGETS:
    pdf = next(DATA.glob(f"{name}*"), None)
    if pdf is None:
        L.append(f"!! 找不到 {name}")
        continue
    L.append(f"===== {pdf.name} =====")
    for merge in (False, True):
        rep = run(pdf, merge)
        tag = "重建关" if not merge else "重建开"
        L.append(f"  [{tag}] scope={rep.scope.value} status={rep.status.value}")
        got = {m.kind.value: m for m in rep.metrics}
        L.append(f"      指标 {len(got)}: {sorted(got)}")
        for r in rep.rule_results:
            d = f"{r.delta:,.2f}" if r.delta is not None else "-"
            L.append(f"      {r.status.value:<16} {r.rule_id:<14} Δ={d}")
        # 打印关键三元组，便于人工判断
        for k in ("total_assets", "total_liabilities", "total_equity",
                  "net_profit", "total_profit", "income_tax",
                  "net_cash_increase", "operating_cash_flow",
                  "investing_cash_flow", "financing_cash_flow"):
            m = got.get(k)
            if m:
                L.append(
                    f"      {k:<22} {m.number.value:>20,.2f} p{m.number.evidence.page}"
                )
    L.append("")

out = pathlib.Path(__file__).with_name("regression_check.txt")
out.write_text("\n".join(L), encoding="utf-8")
print(f"written -> {out}")
