"""查 688610 p133 利润表：为什么 revenue / operating_cost / income_tax 取到 61.00 / 76.00。

取数逻辑是「标签右侧第一个能解析的单元格」，按理应拿到「本期发生额」。
把这一页的原始表格行原样打出来，看是不是行重建把列错位了。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import finverify.ingestion as I  # noqa: E402

PDF = pathlib.Path(sys.argv[1])
PAGE = 133
KEYS = ("营业收入", "营业成本", "所得税", "利润总额", "净利润")

L: list[str] = [f"{PDF.name} p{PAGE}", "=" * 92]

for merge in (False, True):
    doc = I.load_document(PDF, merge_fragments=merge)
    L.append(f"--- merge_fragments={merge} ---")
    for t in doc.tables:
        if t.page != PAGE:
            continue
        L.append(f"  表 bbox={tuple(round(x) for x in t.bbox)} 共 {len(t.rows)} 行")
        for r in t.rows:
            joined = "".join(r.values)
            if not any(k in joined for k in KEYS):
                continue
            cells = []
            for i, v in enumerate(r.values):
                if v:
                    cells.append(f"[{i}]{v}")
            L.append(f"    r{r.row_index:<3} {' | '.join(cells)}")
    L.append("")

pathlib.Path(__file__).with_name("col_688610_p133.txt").write_text(
    "\n".join(L), encoding="utf-8"
)
print("written")
