"""查 603117 ST万林利润表：net_profit 抽到的是「净利润」还是「归母净利润」。

若两者不同，说明 NET_PROFIT 把两个口径混在一个桶里 ——
那会让「净利润 = 利润总额 − 所得税费用」在有少数股东权益的公司上必然不平。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import finverify.ingestion as I  # noqa: E402
from finverify.extraction import (  # noqa: E402
    _is_value_cell,
    classify_label,
    parse_number,
)
from finverify.scope import detect_scopes_cached  # noqa: E402

PDF = pathlib.Path(sys.argv[1])
smap = detect_scopes_cached(PDF)
doc = I.load_document(PDF, merge_fragments=True)

L = [f"{PDF.name}", "=" * 92]
for t in doc.tables:
    scope = smap.scope_for(t.page, t.bbox)
    if str(scope) not in ("ScopeKind.CONSOLIDATED", "consolidated"):
        continue
    for r in t.rows:
        joined = "".join(r.values)
        if not any(k in joined for k in ("净利润", "利润总额", "所得税")):
            continue
        cells = [f"[{i}]{v}" for i, v in enumerate(r.values) if v]
        hit = classify_label(joined)
        kind = hit[0].value if hit else None
        nums = [
            parse_number(r.values[i])[0]
            for i in range(len(r.values))
            if _is_value_cell(r.values[i])
        ]
        L.append(f"  p{t.page} [{scope}] {kind}")
        L.append(f"      {' | '.join(cells)[:110]}")
        L.append(f"      数值列: {[f'{n:,.2f}' for n in nums]}")

out = pathlib.Path(__file__).resolve().parent / "net_profit_603117.txt"
out.write_text("\n".join(L), encoding="utf-8")
print("written")
