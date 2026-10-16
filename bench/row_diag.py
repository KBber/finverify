"""验证行重建假设：逻辑行被 find_tables 拆成多行，数值落在后续行。"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finverify.ingestion import load_document  # noqa: E402

PDF = pathlib.Path(sys.argv[1])
PAGE = int(sys.argv[2])
ROW = int(sys.argv[3])
SPAN = int(sys.argv[4]) if len(sys.argv) > 4 else 6

doc = load_document(PDF)
out: list[str] = [f"===== {PDF.name}  p{PAGE} r{ROW} 起 {SPAN} 行 =====", ""]

for pt in doc.tables:
    if pt.page != PAGE:
        continue
    out.append(f"表 bbox={tuple(round(v,1) for v in pt.bbox)} 共 {len(pt.rows)} 行")
    out.append("")
    for r in pt.rows:
        if ROW <= r.row_index < ROW + SPAN:
            filled = sum(1 for v in r.values if v.strip())
            out.append(
                f"  r{r.row_index:<4} 非空{filled}/{len(r.values)}  "
                f"{[v[:30] for v in r.values]}"
            )
            out.append(f"        cells={[None if c is None else tuple(round(v) for v in c) for c in r.cells]}")
    break

dest = pathlib.Path(__file__).with_name("row_diag.txt")
dest.write_text("\n".join(out), encoding="utf-8")
print(f"written -> {dest}")
