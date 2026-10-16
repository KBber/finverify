"""行高闸门 A/B 对照：证明这道闸门方向是反的，必须保持关闭。

背景：``merge_wrapped_rows`` 曾用「行高 < 本表中位数 75%」来判断碎片行，
以为能防住「一行完整数据吞掉下一行」。实测 18 份真实年报指标数 140 → 140，
一行都没多抽出来 —— 闸门把标签起始行判成非碎片，合并彻底失效。

本脚本固定破折号归零（真因修复），只切换 height_gate 一个变量。
"""

from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import finverify.ingestion as I  # noqa: E402
from finverify.pipeline import audit  # noqa: E402

ORIG_MERGE = I.merge_wrapped_rows
DATA = pathlib.Path(sys.argv[1])

L: list[str] = []


def run(pdf: pathlib.Path, gate: bool):
    def patched(rows, _gate=gate):
        return ORIG_MERGE(rows, height_gate=_gate)

    I.merge_wrapped_rows = patched
    t0 = time.perf_counter()
    rep = audit(pdf, period="2023")
    return rep, time.perf_counter() - t0


tot_on = tot_off = 0
n = 0
for pdf in sorted(DATA.glob("*.pdf")):
    if pdf.stat().st_size < 200_000:
        continue  # 跳过 4 页那种明显不完整的
    try:
        on, t1 = run(pdf, True)
        off, t2 = run(pdf, False)
    except Exception as exc:  # noqa: BLE001
        L.append(f"{pdf.name[:30]:<32} ERROR {exc}")
        continue
    n += 1
    tot_on += len(on.metrics)
    tot_off += len(off.metrics)
    f_on = sum(1 for r in on.rule_results if r.status.value == "FAIL")
    f_off = sum(1 for r in off.rule_results if r.status.value == "FAIL")
    mark = ""
    if len(off.metrics) > len(on.metrics) and f_off <= f_on:
        mark = "  <-- 关闸门更好"
    elif f_off > f_on:
        mark = "  <-- 关闸门 FAIL 变多"
    L.append(
        f"{pdf.name[:30]:<32} 指标 {len(on.metrics):>2}->{len(off.metrics):<2} "
        f"FAIL {f_on}->{f_off}  {t1:.0f}s->{t2:.0f}s{mark}"
    )

L.append("")
L.append(
    f"合计指标数: 闸门开 {tot_on} -> 闸门关 {tot_off} "
    f"({tot_off - tot_on:+d}, {n} 份年报)"
)
L.append("判据：恒等式 FAIL 数不上升、且指标数上升，才算真改善。")

out = pathlib.Path(__file__).with_name("ab_gate_result.txt")
out.write_text("\n".join(L), encoding="utf-8")
print(f"written -> {out}")
