"""复现 CI 失败：合成样本的现金流量表少了经营/投资两个净额。

CI 在 Linux(Noto CJK) 上报
    CF_BRIDGE 缺失构成项 [operating_cash_flow, investing_cash_flow]
本地 Windows 全绿，说明是字体行结构差异触发了某个改动。
这里把合成样本的现金流量表在不同开关下的原始行打出来对比。
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import finverify.ingestion as I  # noqa: E402
from finverify.extraction import _is_value_cell  # noqa: E402
from finverify.samples import AnnualReportData, build_sample_pdf  # noqa: E402

tmp = pathlib.Path(tempfile.mkdtemp()) / "clean.pdf"
build_sample_pdf(tmp, AnnualReportData())

L = [f"{tmp}", "=" * 92]
for merge in (False, True):
    L.append(f"--- merge_fragments={merge} ---")
    doc = I.load_document(tmp, merge_fragments=merge)
    for t in doc.tables:
        joined = "".join("".join(r.values) for r in t.rows)
        if "现金流量净额" not in joined and "现金及现金等价物" not in joined:
            continue
        L.append(f"  表 p{t.page} bbox={tuple(round(x) for x in t.bbox)} 共 {len(t.rows)} 行")
        for r in t.rows:
            cells = [f"[{i}]{v}" for i, v in enumerate(r.values) if v]
            flags = [
                ("Y" if _is_value_cell(r.values[i]) else "n")
                for i in range(len(r.values))
                if r.values[i]
            ]
            L.append(f"    r{r.row_index:<3} h={I._row_height(r):>4.1f} "
                     f"{'|'.join(cells)[:88]}")
            L.append(f"          数值格标记: {flags}")
    L.append("")

out = pathlib.Path(__file__).resolve().parent / "ci_repro_cf.txt"
out.write_text("\n".join(L), encoding="utf-8")
print("written")
