"""逐条对比 3 份 FAIL 退化的年报：多出来的指标是哪来的，FAIL 的是哪条恒等式。

闸门关闭后指标 151 -> 166（+15），但 FAIL 20 -> 23（+3）。
指标变多可能是「多抽出了对的」，也可能是「抽错了」——
所以这里把每份的指标差异和失败的恒等式逐条打出来，人眼判读。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import finverify.ingestion as I  # noqa: E402
from finverify.pipeline import audit  # noqa: E402

ORIG = I.merge_wrapped_rows
DATA = pathlib.Path(sys.argv[1])
TARGETS = ("002825", "603117", "688610")

L: list[str] = []


def run(pdf: pathlib.Path, gate: bool):
    I.merge_wrapped_rows = lambda rows, _g=gate: ORIG(rows, height_gate=_g)
    return audit(pdf, period="2023")


def key_of(m):
    return (m.kind.value, m.statement.value, m.scope.value, m.number.period)


def where(m):
    e = m.number.evidence
    return f"p{e.page} bbox=({e.bbox[0]:.0f},{e.bbox[1]:.0f}) {e.snippet[:52]!r}"


for code in TARGETS:
    pdf = next(DATA.glob(f"{code}_*.pdf"))
    on = run(pdf, True)
    off = run(pdf, False)
    on_map = {key_of(m): m for m in on.metrics}
    off_map = {key_of(m): m for m in off.metrics}

    L.append("=" * 96)
    L.append(f"{pdf.name}   指标 {len(on.metrics)} -> {len(off.metrics)}")
    L.append("=" * 96)

    for k in sorted(set(off_map) - set(on_map)):
        m = off_map[k]
        L.append(f"  + 新增 {k[0]:<22} {k[1]:<12} {m.number.value!r}")
        L.append(f"      {where(m)}")
    for k in sorted(set(on_map) - set(off_map)):
        m = on_map[k]
        L.append(f"  - 消失 {k[0]:<22} {k[1]:<12} {m.number.value!r}")
        L.append(f"      {where(m)}")
    for k in sorted(set(on_map) & set(off_map)):
        a, b = on_map[k], off_map[k]
        if a.number.value != b.number.value:
            L.append(
                f"  ~ 改值 {k[0]:<22} {a.number.value!r} -> {b.number.value!r}"
            )
            L.append(f"      关: {where(b)}")

    L.append("")
    for tag, rep in (("闸门开", on), ("闸门关", off)):
        bad = [r for r in rep.rule_results if r.status.value == "FAIL"]
        L.append(f"  [{tag}] FAIL {len(bad)} 条:")
        for r in bad:
            L.append(f"      {r.rule_id}: {r.detail}")
    L.append("")

pathlib.Path(__file__).with_name("gate_regression_drill.txt").write_text(
    "\n".join(L), encoding="utf-8"
)
print("written")
