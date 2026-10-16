"""最终对照：完全不合并 vs 合并（行高闸门关闭）。

这是决定 ``load_document(merge_fragments=...)`` 默认值的依据。
之前的 ab_merge 跑在坏的 eval 集上（873576 是 4 页公告空壳），
且闸门方向反了，结论都不能直接用。

判据（不只看指标数）：
    FAIL 数不上升 + 指标数上升 = 真改善；
    指标涨但 FAIL 也涨 = 多抽出来的和抽错的一样多，不算改善。
"""

from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import finverify.ingestion as I  # noqa: E402
from finverify.pipeline import audit  # noqa: E402

ORIG = I.load_document
DATA = pathlib.Path(sys.argv[1])

L: list[str] = []


def run(pdf: pathlib.Path, merge: bool):
    I.load_document = lambda p, strategy="text", merge_fragments=merge: ORIG(
        p, strategy, merge_fragments
    )
    import finverify.pipeline as P

    P.load_document = I.load_document
    t0 = time.perf_counter()
    rep = audit(pdf, period="2023")
    return rep, time.perf_counter() - t0


def fails(rep) -> int:
    return sum(1 for r in rep.rule_results if r.status.value == "FAIL")


def reviews(rep) -> int:
    return sum(1 for r in rep.rule_results if r.status.value == "REVIEW_REQUIRED")


tot_off = tot_on = 0
f_off = f_on = 0
r_off = r_on = 0
n = 0
worse, better, same = [], [], []

for pdf in sorted(DATA.glob("*.pdf")):
    try:
        off, t1 = run(pdf, False)
        on, t2 = run(pdf, True)
    except Exception as exc:  # noqa: BLE001
        L.append(f"{pdf.name[:30]:<32} ERROR {exc}")
        continue
    n += 1
    tot_off += len(off.metrics)
    tot_on += len(on.metrics)
    f_off += fails(off)
    f_on += fails(on)
    r_off += reviews(off)
    r_on += reviews(on)
    dm, df = len(on.metrics) - len(off.metrics), fails(on) - fails(off)
    if df > 0:
        worse.append(pdf.name)
    elif dm > 0 and df <= 0:
        better.append(pdf.name)
    else:
        same.append(pdf.name)
    mark = ""
    if df > 0:
        mark = "  <-- FAIL 变多"
    elif dm > 0:
        mark = "  <-- 更好"
    elif dm < 0:
        mark = "  <-- 指标变少"
    L.append(
        f"{pdf.name[:30]:<32} 指标 {len(off.metrics):>2}->{len(on.metrics):<2} "
        f"FAIL {fails(off)}->{fails(on)} REVIEW {reviews(off)}->{reviews(on)}"
        f"  {t1:.0f}s->{t2:.0f}s{mark}"
    )

L.append("")
L.append(f"份数 {n}")
L.append(
    f"指标   {tot_off} -> {tot_on}  ({tot_on - tot_off:+d})"
)
L.append(f"FAIL   {f_off} -> {f_on}  ({f_on - f_off:+d})")
L.append(f"REVIEW {r_off} -> {r_on}  ({r_on - r_off:+d})")
L.append("")
L.append(f"FAIL 变多: {len(worse)} 份 {worse}")
L.append(f"更好:     {len(better)} 份")
L.append(f"无变化:   {len(same)} 份")
L.append("")
L.append("判读：只有「指标上升且 FAIL 不上升」才算真改善。")

out = pathlib.Path(__file__).with_name("ab_merge_final.txt")
out.write_text("\n".join(L), encoding="utf-8")
print(f"written -> {out}")
