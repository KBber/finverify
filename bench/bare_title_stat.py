"""统计 19 份真实年报里「带前缀标题」与「裸标题」是否共存。

为什么要先量一遍再改
------------------
688610 埃科光电的标题是裸的 ``资产负债表`` / ``利润表`` / ``现金流量表``，
一个前缀都没有，当前正则因此零命中。但这引出一个问题：
**裸标题到底该算合并口径还是母公司口径？**

  - 如果裸标题和「合并资产负债表」从不共存，说明带前缀的一定是母公司，
    裸的就是合并；
  - 如果某份年报两者都有，裸标题就不能想当然当合并，否则会拿母公司
    的数冒充合并。

这个脚本就是为了拿到这份证据，不靠推理。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import _paths  # noqa: E402

from finverify.ingestion import all_text_lines  # noqa: E402
from finverify.scope import _clean, match_title  # noqa: E402

DATA = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else _paths.data_dir()

BARE = ("资产负债表", "利润表", "现金流量表", "所有者权益变动表", "股东权益变动表")

L: list[str] = []
L.append(f"{'文件':<30} {'带前缀':>5} {'裸标题':>5}  明细")
L.append("-" * 100)

coexist = []
for pdf in sorted(DATA.glob("*.pdf")):
    prefixed, bare = [], []
    try:
        for pno, _bb, text in all_text_lines(pdf):
            if match_title(text) is not None:
                prefixed.append((pno, _clean(text)))
                continue
            t = _clean(text)
            if t in BARE:
                bare.append((pno, t))
    except Exception as exc:  # noqa: BLE001
        L.append(f"{pdf.name[:28]:<30} ERROR {exc}")
        continue

    detail = []
    for pno, t in prefixed:
        detail.append(f"p{pno}:{t}")
    for pno, t in bare:
        detail.append(f"p{pno}:[{t}]")
    if prefixed and bare:
        coexist.append(pdf.name)
    flag = "  <-- 共存!" if prefixed and bare else ""
    L.append(
        f"{pdf.name[:28]:<30} {len(prefixed):>5} {len(bare):>5}  "
        f"{'; '.join(detail[:6])}{flag}"
    )

L.append("")
L.append(f"带前缀与裸标题共存: {len(coexist)} 份")
for n in coexist:
    L.append(f"  {n}")
L.append("")
L.append("判读：若共存为 0，则裸标题可安全按合并口径处理；")
L.append("     若存在共存，裸标题必须保持独立口径，不能想当然当合并。")

out = pathlib.Path(__file__).with_name("bare_title_stat.txt")
out.write_text("\n".join(L), encoding="utf-8")
print("written ->", out)
