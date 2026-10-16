"""定向诊断：看缺失科目在真实年报的合并区间里到底是什么形态。"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finverify.ingestion import load_document  # noqa: E402
from finverify.scope import ScopeKind, detect_scopes_cached  # noqa: E402

PDF = pathlib.Path(sys.argv[1])
KEYWORD = sys.argv[2] if len(sys.argv) > 2 else "利润总额"

doc = load_document(PDF)
smap = detect_scopes_cached(PDF)

L: list[str] = [f"===== {PDF.name}  关键词: {KEYWORD} =====", ""]
L.append("[命中行（合并口径区间内）]")
n = 0
for pt in doc.tables:
    if smap.scope_for(pt.page, pt.bbox) is not ScopeKind.CONSOLIDATED:
        continue
    for r in pt.rows:
        joined = "".join(v for v in r.values if v)
        if KEYWORD in joined:
            n += 1
            L.append(f"  p{pt.page} r{r.row_index}")
            L.append(f"    values = {r.values!r}")
            L.append(f"    cells  = {len(r.cells)} 个")
            if n >= 6:
                break
    if n >= 6:
        break
if not n:
    L.append("  （合并口径区间内没有命中）")
    L.append("")
    L.append("[全文任意位置命中]")
    for pt in doc.tables:
        for r in pt.rows:
            joined = "".join(v for v in r.values if v)
            if KEYWORD in joined:
                sc = smap.scope_for(pt.page, pt.bbox)
                L.append(f"  p{pt.page} [{sc.value}] {r.values!r}")

L.append("")
L.append("[合并区间内的三表标记]")
for m in smap.marks:
    L.append(f"  p{m.page:<4} y0={m.y0:<8.1f} [{m.scope.value}] {m.title}")

dest = pathlib.Path(__file__).with_name("label_diag.txt")
dest.write_text("\n".join(L), encoding="utf-8")
print(f"written -> {dest}")
