"""细看两个异常样本：836263 中航泰达（带前缀与裸标题共存）、873576 天力复合（一个都没命中）。

共存说明「裸标题 == 合并」这个假设不成立，必须看清裸标题到底是什么。
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

TARGETS = ["836263", "873576"]
KEY = re.compile(r"资产负债表|利润表|现金流量表|所有者权益变动表|股东权益变动表")

L: list[str] = []
for code in TARGETS:
    pdf = _paths.pdf_for(code)
    L.append("=" * 90)
    L.append(f"{pdf.name}")
    L.append("=" * 90)
    lines = all_text_lines(pdf)
    L.append(f"文本行 {len(lines)}")
    L.append("")
    L.append("--- 含表名的原始行（全部） ---")
    for pno, bb, text in lines:
        if not KEY.search(text):
            continue
        t = _clean(text)
        ok = match_title(text)
        mark = "命中" if ok else "未中"
        short = "短" if len(t) <= 24 else "长"
        L.append(f"  p{pno:>4} y={bb[1]:>6.1f} len={len(t):>2} {short} {mark} | {text[:70]!r}")
    L.append("")

pathlib.Path(__file__).with_name("bare_title_detail.txt").write_text(
    "\n".join(L), encoding="utf-8"
)
print("written")
