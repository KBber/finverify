"""查清合并开关下唯一一份 FAIL 变多的年报：603117 ST万林。

其余 18 份要么更好要么无变化，只有它 0 -> 1。
必须确认这多出来的一条是「真发现」还是「抽错」。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import finverify.ingestion as I  # noqa: E402
from finverify.pipeline import audit  # noqa: E402

ORIG = I.load_document
PDF = pathlib.Path(sys.argv[1])
L: list[str] = []


def run(merge: bool):
    I.load_document = lambda p, strategy="text", merge_fragments=merge: ORIG(
        p, strategy, merge_fragments
    )
    import finverify.pipeline as P

    P.load_document = I.load_document
    return audit(PDF, period="2023")


def key_of(m):
    return (m.kind.value, m.statement.value, m.number.period)


def where(m):
    e = m.number.evidence
    return f"p{e.page} {e.snippet[:60]!r}"


off, on = run(False), run(True)
om = {key_of(m): m for m in off.metrics}
nm = {key_of(m): m for m in on.metrics}

L.append(f"{PDF.name}  指标 {len(off.metrics)} -> {len(on.metrics)}")
L.append("=" * 92)
for k in sorted(set(nm) - set(om)):
    L.append(f"  + 新增 {k[0]:<22} {nm[k].number.value:>20,.2f}")
    L.append(f"      {where(nm[k])}")
for k in sorted(set(om) - set(nm)):
    L.append(f"  - 消失 {k[0]:<22} {om[k].number.value:>20,.2f}")
    L.append(f"      {where(om[k])}")
for k in sorted(set(om) & set(nm)):
    if om[k].number.value != nm[k].number.value:
        L.append(
            f"  ~ 改值 {k[0]:<22} {om[k].number.value:>20,.2f}"
            f" -> {nm[k].number.value:>20,.2f}"
        )
        L.append(f"      关: {where(nm[k])}")
L.append("")
for tag, rep in (("不合并", off), ("合并", on)):
    bad = [r for r in rep.rule_results if r.status.value == "FAIL"]
    L.append(f"  [{tag}] FAIL {len(bad)} 条:")
    for r in bad:
        L.append(f"      {r.rule_id}: {r.detail}")

out = pathlib.Path(__file__).resolve().parent / "merge_case_603117.txt"
out.write_text("\n".join(L), encoding="utf-8")
print("written")
