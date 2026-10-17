"""排查 688610 埃科光电口径识别零标记的原因。

249 页年报却一个三表标题都没抓到，说明标题文本不是
``match_title`` 期待的那种形态。这里把候选页的原始文本行
原样打出来，让人眼直接看，不靠正则猜。
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import _paths  # noqa: E402

from finverify.ingestion import all_text_lines  # noqa: E402
from finverify.scope import _clean, match_title  # noqa: E402

PDF = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else _paths.pdf_for("688610")

out: list[str] = []
lines = all_text_lines(PDF)
out.append(f"{PDF.name} 文本行 {len(lines)} 行")

hits = [(p, b, t) for p, b, t in lines if match_title(t)]
out.append(f"match_title 命中 {len(hits)} 条")
out.append("")

# 找所有含「资产负债表 / 利润表 / 现金流量表」的原始行，看看长什么样
KEY = re.compile(r"资产负债表|利润表|现金流量表|所有者权益变动表|股东权益变动表")
cand = [(p, b, t) for p, b, t in lines if KEY.search(t)]
out.append(f"含表名的原始行 {len(cand)} 条，展示前 40 条：")
out.append("")
for p, b, t in cand[:40]:
    ok = match_title(t)
    cleaned = _clean(t)
    out.append(
        f"  p{p:>4} y={b[1]:>6.1f} len={len(cleaned):>2} "
        f"{'命中' if ok else '未中'} | {t[:60]!r}"
    )
    if not ok:
        out.append(f"          _clean -> {cleaned!r}")

pathlib.Path(__file__).with_name("scope_688610.txt").write_text(
    "\n".join(out), encoding="utf-8"
)
print(f"written, 命中 {len(hits)}, 候选 {len(cand)}")
