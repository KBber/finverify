"""看 603117 p88 的口径标记与表格 bbox 的真实几何关系。

嵌入式标题窄带修复没生效，得先量出标题 y 和表格 top 的差值。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import finverify.ingestion as I  # noqa: E402
from finverify.scope import detect_scopes_cached  # noqa: E402

PDF = pathlib.Path(sys.argv[1])
PAGE = 88
smap = detect_scopes_cached(PDF)
doc = I.load_document(PDF, merge_fragments=True)

L = [f"{PDF.name} p{PAGE}", "=" * 92]
L.append("该页口径标记 (y0):")
for m in smap.marks:
    if m.page == PAGE:
        L.append(f"    y0={m.y0:>7.1f}  {m.scope.value:<12} {m.title}")
if not any(m.page == PAGE for m in smap.marks):
    L.append("    (无)")
L.append(f"页首回退口径: {smap.page_scope.get(PAGE)}")
L.append("")
L.append("该页表格 bbox:")
for t in doc.tables:
    if t.page != PAGE:
        continue
    top, bottom = t.bbox[1], t.bbox[3]
    L.append(f"    bbox=({t.bbox[0]:.0f},{top:.0f},{t.bbox[2]:.0f},{bottom:.0f}) "
             f"高={bottom - top:.0f}  行数={len(t.rows)}")
    L.append(f"      scope_for -> {smap.scope_for(t.page, t.bbox)}")
    above = [m for m in smap.marks if m.page == PAGE and m.y0 < top]
    band = max(20.0, (bottom - top) * 0.05)
    inside = [m for m in smap.marks if m.page == PAGE and top <= m.y0 <= top + band]
    L.append(f"      上方标题数={len(above)}  窄带[{top:.1f},{top + band:.1f}]内标题数={len(inside)}")
    if t.rows:
        r0 = t.rows[0]
        L.append(f"      首行 bbox 顶={r0.row_bbox[1]:.1f}  文本={''.join(r0.values)[:70]!r}")

out = pathlib.Path(__file__).resolve().parent / "geom_603117_p88.txt"
out.write_text("\n".join(L), encoding="utf-8")
print("written")
