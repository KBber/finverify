"""判定 688610 那套无前缀报表到底是合并口径还是母公司口径。

背景：688610 的三表标题是裸的 ``资产负债表`` / ``利润表`` / ``现金流量表`` /
``所有者权益变动表``，没有「合并」也没有「母公司」前缀，所以当前正则零命中，
整份文档被当成无口径，指标可能混着摘要表和附注取。

办法：年报开头的「主要会计数据」表会披露**合并口径**的总资产。
把那个数跟裸标题区的资产总计对一下：
    - 相等 -> 裸标题区就是合并口径；
    - 不等 -> 它是母公司口径（合并数在摘要表里另有一份）。

这样定规则靠证据，不靠「裸的应该就是合并」的想当然。
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finverify.ingestion import all_text_lines  # noqa: E402

PDF = pathlib.Path(sys.argv[1])
L: list[str] = []

NUM = re.compile(r"-?[\d,]+\.\d{2}|-?[\d,]{4,}")

# 找「主要会计数据」区域里带 总资产 / 资产总额 的行
L.append(f"{PDF.name}")
L.append("=" * 88)
L.append("--- 摘要区：含 总资产/资产总额 的行 ---")
summary_hits = []
for pno, bb, text in all_text_lines(PDF):
    if pno > 30:
        break
    t = text.replace(" ", "")
    if ("总资产" in t or "资产总额" in t) and "负债和" not in t:
        nums = NUM.findall(t)
        summary_hits.append((pno, bb[1], t[:50], nums))
        L.append(f"  p{pno:>3} y={bb[1]:>6.1f} {t[:46]!r} -> {nums[:3]}")
L.append("")

# 找裸标题所在页的资产总计
L.append("--- 裸标题区（p128-140）：含 资产总计 的行 ---")
stmt_hits = []
for pno, bb, text in all_text_lines(PDF):
    if not (128 <= pno <= 140):
        continue
    t = text.replace(" ", "")
    if "资产总计" in t or "资产合计" in t:
        nums = NUM.findall(t)
        stmt_hits.append((pno, bb[1], t[:50], nums))
        L.append(f"  p{pno:>3} y={bb[1]:>6.1f} {t[:46]!r} -> {nums[:3]}")
L.append("")

L.append("--- 判读 ---")
sv = {n.replace(",", "") for _, _, _, ns in summary_hits for n in ns}
tv = {n.replace(",", "") for _, _, _, ns in stmt_hits for n in ns}
L.append(f"摘要区候选值: {sorted(sv)[:6]}")
L.append(f"报表区候选值: {sorted(tv)[:6]}")
inter = sv & tv
L.append(f"交集: {sorted(inter) if inter else '无'}")
if inter:
    L.append("=> 数值一致：裸标题区就是**合并口径**，可安全按合并注册。")
else:
    L.append("=> 数值不一致：裸标题区不是合并口径，不能想当然注册为合并。")

pathlib.Path(__file__).with_name("scope_688610_judge.txt").write_text(
    "\n".join(L), encoding="utf-8"
)
print("written")
