"""行重建 A/B 对照：在真实年报上验证指标命中率与恒等式结果的变化。"""

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
    I.load_document = lambda path, strategy="text", merge_fragments=merge: ORIG(
        path, strategy, merge_fragments
    )
    import finverify.pipeline as P

    P.load_document = I.load_document
    t0 = time.perf_counter()
    rep = audit(pdf, period="2023")
    return rep, time.perf_counter() - t0


tot_off = tot_on = 0
n = 0
for pdf in sorted(DATA.glob("*.pdf")):
    if pdf.stat().st_size < 200_000:
        continue  # 跳过 4 页那种明显不完整的
    try:
        off, t1 = run(pdf, False)
        on, t2 = run(pdf, True)
    except Exception as exc:  # noqa: BLE001
        L.append(f"{pdf.name[:30]:<32} ERROR {exc}")
        continue
    n += 1
    tot_off += len(off.metrics)
    tot_on += len(on.metrics)
    f_off = sum(1 for r in off.rule_results if r.status.value == "FAIL")
    f_on = sum(1 for r in on.rule_results if r.status.value == "FAIL")
    mark = "  <-- 改善" if len(on.metrics) > len(off.metrics) else (
        "  <-- 退化" if len(on.metrics) < len(off.metrics) else ""
    )
    L.append(
        f"{pdf.name[:30]:<32} 指标 {len(off.metrics):>2}->{len(on.metrics):<2} "
        f"FAIL {f_off}->{f_on}  {t1:.0f}s->{t2:.0f}s{mark}"
    )

L.append("")
L.append(f"合计指标数: {tot_off} -> {tot_on}  (+{tot_on - tot_off}, "
         f"{n} 份年报)")
L.append("注意：重建可能同时带来「多抽出」与「抽错」两种变化，")
L.append("所以指标数不是唯一判据 —— 恒等式 FAIL 数与最终结论才是。")

out = pathlib.Path(__file__).with_name("ab_result.txt")
out.write_text("\n".join(L), encoding="utf-8")
print(f"written -> {out}")
